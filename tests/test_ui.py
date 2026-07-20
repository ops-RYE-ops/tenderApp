#!/usr/bin/env python3
"""
test_ui.py — the team UI foundations: open access, static app, supplier list.

Headless and network-free (the Retool DB is monkeypatched). Proves:
  - the team gate: when TEAM_ACCESS_KEY is set, /api + /app need HTTP Basic auth,
    while the public client route (/d/*) and /api/health stay open; unset = open;
  - the static wizard is served at /app/;
  - /api/suppliers lists distinct cached suppliers and degrades without a DB;
  - supplier names are whitespace-normalised on save (cache hygiene).

Run from the repo root:

    python3 tests/test_ui.py

Prints 'ALL UI CHECKS PASSED' and exits 0 when green.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

FAILURES = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


def test_team_gate():
    print("access: Basic-auth gate on /api + /app; public client route + health exempt")
    import base64
    os.environ["TEAM_ACCESS_KEY"] = "s3cret"
    good = {"Authorization": "Basic " + base64.b64encode(b"rye:s3cret").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"rye:nope").decode()}
    try:
        client = TestClient(main.app)
        check("no auth -> 401", client.get("/api/suppliers").status_code == 401)
        check("401 sends a Basic challenge",
              "basic" in client.get("/api/suppliers").headers.get("www-authenticate", "").lower())
        check("wrong password -> 401", client.get("/api/suppliers", headers=bad).status_code == 401)
        check("correct password -> 200", client.get("/api/suppliers", headers=good).status_code == 200)
        check("/api/health exempt", client.get("/api/health").status_code == 200)
        root = client.get("/", follow_redirects=False)
        check("/ exempt + redirects to /app/", root.status_code in (307, 308) and root.headers.get("location", "").startswith("/app"))
        check("public /d/* not gated (no 401)", client.get("/d/foo/bar").status_code != 401)
    finally:
        os.environ.pop("TEAM_ACCESS_KEY", None)

    # Unset key => open (local dev + the rest of the suite run ungated).
    client = TestClient(main.app)
    check("no key configured -> /api open", client.get("/api/suppliers").status_code == 200)


def test_static_app_served():
    print("static: the wizard is served at /app/")
    client = TestClient(main.app)
    r = client.get("/app/")
    check("/app/ returns 200", r.status_code == 200)
    check("/app/ is the wizard shell", "RYE Tender Tool" in r.text)
    check("noindex set on the team UI", "noindex" in r.text)
    check("/app/app.js served", client.get("/app/app.js").status_code == 200)
    check("/app/app.css served", client.get("/app/app.css").status_code == 200)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_a, **_k):
        pass

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)

    def commit(self):
        pass

    def close(self):
        pass


def test_suppliers_endpoint():
    print("suppliers: distinct list from the cache, graceful without a DB")
    client = TestClient(main.app)

    orig = main._db_connect
    main._db_connect = lambda: _FakeConn([("Octopus",), ("UrbanChain",)])
    try:
        r = client.get("/api/suppliers")
        check("lists cached suppliers", r.json().get("suppliers") == ["Octopus", "UrbanChain"])
    finally:
        main._db_connect = orig

    main._db_connect = lambda: None
    try:
        r = client.get("/api/suppliers")
        body = r.json()
        check("no DB -> empty list + note", body.get("suppliers") == [] and "note" in body)
    finally:
        main._db_connect = orig


def test_tenders_register():
    print("register: /api/tenders lists latest-per-id, degrades without a DB")
    client = TestClient(main.app)
    sample = [{
        "id": "11111111-1111-4111-8111-111111111111", "client_name": "Amorino UK",
        "tender_label": "Electricity tender", "utility": "electricity", "status": "draft",
        "version": 2, "created_at": "2026-07-17T10:00:00Z", "created_by": "x@rye.energy",
        "expires_at": None, "slug": "amorino-uk", "url_uuid": None, "dashboard_url": None,
        "sites": 3, "quotes": 2, "recommended_supplier": "Octopus",
    }]
    orig = main._list_tenders
    main._list_tenders = lambda: sample
    try:
        r = client.get("/api/tenders")
        check("lists tenders", r.json().get("tenders", [{}])[0].get("client_name") == "Amorino UK")
        check("counts + recommended surfaced",
              r.json()["tenders"][0]["quotes"] == 2 and r.json()["tenders"][0]["recommended_supplier"] == "Octopus")
    finally:
        main._list_tenders = orig

    main._list_tenders = lambda: None
    try:
        r = client.get("/api/tenders")
        body = r.json()
        check("no DB -> empty list + note", body.get("tenders") == [] and "note" in body)
    finally:
        main._list_tenders = orig


def test_confirm_normalises_supplier():
    print("confirm: supplier whitespace is normalised before the cache write")
    client = TestClient(main.app)
    saved = {}

    def fake_put(supplier, fingerprint, mapping, confirmed_by):
        saved.update(supplier=supplier, fingerprint=fingerprint)

    orig = main._cache_put
    main._cache_put = fake_put
    try:
        r = client.post("/api/map/confirm", json={
            "supplier": "  Urban   Chain ",
            "layout_fingerprint": "abc123",
            "mapping": {"columns": {"mpxn": "MPAN"}},
        })
        check("confirm succeeds", r.status_code == 200)
        check("supplier saved as 'Urban Chain'", saved.get("supplier") == "Urban Chain")
        check("response echoes the normalised name", r.json().get("supplier") == "Urban Chain")
        r2 = client.post("/api/map/confirm", json={
            "supplier": "   ",
            "layout_fingerprint": "abc123",
            "mapping": {"columns": {"mpxn": "MPAN"}},
        })
        check("blank supplier -> 400", r2.status_code == 400)
    finally:
        main._cache_put = orig


if __name__ == "__main__":
    test_team_gate()
    test_static_app_served()
    test_suppliers_endpoint()
    test_tenders_register()
    test_confirm_normalises_supplier()
    if FAILURES:
        print(f"\n{len(FAILURES)} CHECK(S) FAILED")
        sys.exit(1)
    print("\nALL UI CHECKS PASSED")
