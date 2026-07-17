#!/usr/bin/env python3
"""
test_render.py — the /render step: canonical tender -> dashboard HTML.

Headless and network-free. Covers the canonical->CSV adapter
(build_dashboard.render_tender) and the /api/render endpoint (inline tender JSON,
fetch-by-id with the DB mocked, and the 400/404 paths). The cost engine itself is
covered by make_and_verify; this proves the bridge + endpoint wiring. Run from the
repo root:

    python3 tests/test_render.py

Prints 'ALL RENDER CHECKS PASSED' and exits 0 when green.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

import build_dashboard as bd  # noqa: E402
import main  # noqa: E402


def check(name, cond):
    if not cond:
        raise AssertionError(f"FAILED: {name}")
    print(f"  ok: {name}")


def _example_tender():
    with open(os.path.join(ROOT, "schema", "examples", "tender.example.json")) as f:
        return json.load(f)


def test_adapter():
    print("render_tender — canonical tender -> dashboard HTML")
    tender = _example_tender()
    html = bd.render_tender(tender)
    check("HTML is produced", isinstance(html, str) and len(html) > 1000)
    check("template placeholder was filled", "__TENDER_DATA__" not in html)
    check("client name injected", tender["client_name"] in html)
    # A tender with no sites[] still renders (join just yields blank meter facts).
    t2 = dict(tender)
    t2["sites"] = []
    check("renders even with empty sites[]", "__TENDER_DATA__" not in bd.render_tender(t2))


def test_endpoint():
    print("/api/render — inline JSON, fetch-by-id, error paths")
    client = TestClient(main.app)
    tender = _example_tender()

    # --- inline tender JSON ---
    r = client.post("/api/render", json={"tender": tender})
    check("inline tender returns 200", r.status_code == 200)
    check("response is HTML", r.headers["content-type"].startswith("text/html"))
    check("HTML carries the client", tender["client_name"] in r.text)

    # --- fetch by id (DB mocked) ---
    main._get_tender = lambda tid, version=None: tender if tid == "known" else None  # type: ignore
    r = client.post("/api/render", json={"tender_id": "known"})
    check("fetch-by-id returns 200", r.status_code == 200)
    check("fetched tender rendered to HTML", tender["client_name"] in r.text)

    r = client.post("/api/render", json={"tender_id": "missing"})
    check("unknown id returns 404", r.status_code == 404)

    # --- validation ---
    r = client.post("/api/render", json={"tender_id": "known", "tender": tender})
    check("both id and inline -> 400", r.status_code == 400)
    r = client.post("/api/render", json={})
    check("neither id nor inline -> 400", r.status_code == 400)
    r = client.post("/api/render", json={"tender": {"client_name": "X", "tender_label": "Y", "quotes": []}})
    check("tender with no quotes -> 400", r.status_code == 400)


if __name__ == "__main__":
    test_adapter()
    test_endpoint()
    print("ALL RENDER CHECKS PASSED")
