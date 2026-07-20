#!/usr/bin/env python3
"""
test_publish.py — Phase 3: publish, the public client link, and revoke.

Headless (DB helpers monkeypatched). Proves: /api/publish mints an unguessable
link + bumps the version + marks published; the public /d/<slug>/<uuid> route
serves the dashboard (noindex) only when the LATEST version still carries that
uuid and is published; expired tenders show the expired page; and /api/revoke
rotates the uuid so the old link stops resolving. Run from the repo root:

    python3 tests/test_publish.py

Prints 'ALL PUBLISH CHECKS PASSED' and exits 0 when green.
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


TID = "11111111-1111-4111-8111-111111111111"


def _tender(**over):
    t = {
        "id": TID, "client_name": "Amorino UK", "tender_label": "Electricity — Jul 2026",
        "utility": "electricity", "status": "draft", "version": 1,
        "created_at": "2026-07-20T09:00:00Z", "created_by": "x@rye.energy",
        "sites": [{"mpxn": "1200000000001", "site_name": "A", "eac": 1000.0, "eac_source": "quote"}],
        "quotes": [{"supplier": "EDF", "term": "12m", "featured": True,
                    "lines": [{"mpxn": "1200000000001", "unitRate": 20.0}]}],
    }
    t.update(over)
    return t


def main_test():
    client = TestClient(main.app)
    store = {}
    main._next_version = lambda tid: 2                       # type: ignore
    main._write_tender = lambda t: store.update(latest=dict(t))  # type: ignore

    print("1) publish mints a link, bumps the version, marks published")
    main._get_tender = lambda tid, v=None: _tender() if tid == TID else None  # type: ignore
    r = client.post("/api/publish", json={"tender_id": TID})
    check("publish -> 200", r.status_code == 200)
    body = r.json()
    check("status published", body["status"] == "published")
    check("version bumped to 2", body["version"] == 2)
    check("returns a /d/<slug>/<uuid> link", "/d/amorino-uk/" in body["url"])
    check("url_uuid present", bool(body["url_uuid"]))
    published = store["latest"]

    print("2) the public route serves the published dashboard, noindex, no auth")
    main._get_tender_by_uuid = lambda u: published if u == published["url_uuid"] else None  # type: ignore
    rr = client.get(f"/d/amorino-uk/{published['url_uuid']}")
    check("public route -> 200", rr.status_code == 200)
    check("noindex header set", "noindex" in rr.headers.get("x-robots-tag", ""))
    check("renders the dashboard", "html" in rr.text.lower())

    print("3) a wrong/unknown uuid -> unavailable page (404), not the dashboard")
    main._get_tender_by_uuid = lambda u: None  # type: ignore
    r404 = client.get("/d/amorino-uk/does-not-exist")
    check("unknown uuid -> 404", r404.status_code == 404)
    check("shows an 'unavailable' message", "no longer active" in r404.text.lower())

    print("4) an expired tender shows the expired page (not the pricing)")
    expired = _tender(status="published", url_uuid="u-exp", expires_at="2020-01-01")
    main._get_tender_by_uuid = lambda u: expired  # type: ignore
    rexp = client.get("/d/amorino-uk/u-exp")
    check("expired -> 200 with expired message", rexp.status_code == 200 and "expired" in rexp.text.lower())

    print("5) revoke rotates the uuid so the old link dies")
    main._get_tender = lambda tid, v=None: dict(published)  # type: ignore  (latest = published)
    rv = client.post("/api/revoke", json={"tender_id": TID})
    check("revoke -> 200, back to draft", rv.status_code == 200 and rv.json()["status"] == "draft")
    revoked_latest = store["latest"]
    check("uuid rotated", revoked_latest["url_uuid"] != published["url_uuid"])
    # old link: latest version now has a different uuid -> public route denies it
    main._get_tender_by_uuid = lambda u: revoked_latest  # type: ignore  (id lookup returns latest)
    rold = client.get(f"/d/amorino-uk/{published['url_uuid']}")
    check("old link -> 404 after revoke", rold.status_code == 404)

    if FAILURES:
        print(f"\n{len(FAILURES)} CHECK(S) FAILED")
        return 1
    print("\nALL PUBLISH CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main_test())
