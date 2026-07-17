#!/usr/bin/env python3
"""
test_ui.py — the team UI foundations: access gate, static app, supplier list.

Headless and network-free (the Retool DB is monkeypatched). Proves:
  - with TEAM_ACCESS_KEY unset the gate is OPEN (local dev + existing tests);
  - with it set, /api routes 401 without / with a wrong X-RYE-Key, 200 with the
    right one, and /api/health + / stay open (diagnostics);
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


def test_gate_open_when_unset():
    print("gate: TEAM_ACCESS_KEY unset -> open")
    os.environ.pop("TEAM_ACCESS_KEY", None)
    client = TestClient(main.app)
    r = client.get("/api/auth-check")
    check("auth-check reachable with no key configured", r.status_code == 200)
    check("auth-check reports gated=false", r.json().get("gated") is False)


def test_gate_enforced_when_set():
    print("gate: TEAM_ACCESS_KEY set -> enforced on /api, not on diagnostics")
    os.environ["TEAM_ACCESS_KEY"] = "test-key-123"
    try:
        client = TestClient(main.app)
        check("no header -> 401", client.get("/api/auth-check").status_code == 401)
        check("wrong key -> 401",
              client.get("/api/auth-check", headers={"X-RYE-Key": "nope"}).status_code == 401)
        r = client.get("/api/auth-check", headers={"X-RYE-Key": "test-key-123"})
        check("right key -> 200", r.status_code == 200)
        check("auth-check reports gated=true", r.json().get("gated") is True)
        check("/api/suppliers gated too",
              client.get("/api/suppliers").status_code == 401)
        check("/api/health stays open", client.get("/api/health").status_code == 200)
        check("/ stays open", client.get("/").status_code == 200)
    finally:
        os.environ.pop("TEAM_ACCESS_KEY", None)


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
    test_gate_open_when_unset()
    test_gate_enforced_when_set()
    test_static_app_served()
    test_suppliers_endpoint()
    test_confirm_normalises_supplier()
    if FAILURES:
        print(f"\n{len(FAILURES)} CHECK(S) FAILED")
        sys.exit(1)
    print("\nALL UI CHECKS PASSED")
