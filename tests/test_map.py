#!/usr/bin/env python3
"""
test_map.py — the /map step: layout fingerprint, sample values, cache-vs-LLM.

Headless and network-free. The single LLM call (map_headers.propose_mapping) and
the Retool cache (main._cache_get / _cache_put) are monkeypatched, so this proves
the ENDPOINT wiring — fingerprint → cache lookup → LLM fallback → sample values →
confirm/save — without an API key or a database. Run from the repo root:

    python3 tests/test_map.py

Prints 'ALL MAP CHECKS PASSED' and exits 0 when green.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, ROOT)

import openpyxl  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import map_headers as mh  # noqa: E402


def _make_quote(path, sites):
    """A minimal broker-style quote: a title row, a gap, then the header + rows."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Rates"
    ws.append(["Acme Broker — Client Quote"])
    ws.append([])
    ws.append(["Site Name", "MPAN", "EAC (kWh)", "Unit Rate (p/kWh)", "Standing Charge (p/day)"])
    for row in sites:
        ws.append(row)
    wb.save(path)


# A mapping shaped exactly like map_headers/process_quote expect.
SAMPLE_MAPPING = {
    "header_row": 3,
    "output_prefix": "acme",
    "supplier": "Acme",
    "columns": {
        "siteName": "Site Name",
        "mpxn": "MPAN",
        "updatedEac": "EAC (kWh)",
        "unitRate": {"single": "Unit Rate (p/kWh)"},
        "standingCharge": "Standing Charge (p/day)",
        "dayRate": {"split": "__none__"},
        "nightRate": {"split": "__none__"},
    },
}


def check(name, cond):
    if not cond:
        raise AssertionError(f"FAILED: {name}")
    print(f"  ok: {name}")


def test_fingerprint_and_samples():
    print("fingerprint + sample values")
    f1 = tempfile.mktemp(suffix=".xlsx")
    f2 = tempfile.mktemp(suffix=".xlsx")
    _make_quote(f1, [["Shop A", "1200001", "50000", "24.5", "45.1"],
                     ["Shop B", "1200002", "80000", "23.9", "45.1"]])
    # Same layout, a different client's numbers.
    _make_quote(f2, [["Cafe X", "9900009", "12000", "30.1", "60.0"]])

    insp1 = mh.inspect_file(f1)
    insp2 = mh.inspect_file(f2)
    fp1 = mh.layout_fingerprint(insp1)
    fp2 = mh.layout_fingerprint(insp2)
    check("header row detected under the title block", insp1["sheets"][0]["header_row_best_guess"] == 3)
    check("fingerprint is stable across differing values", fp1 == fp2)
    check("fingerprint is a short hex string", len(fp1) == 16 and all(c in "0123456789abcdef" for c in fp1))

    sv = mh.sample_values(insp1, SAMPLE_MAPPING)
    check("sample values pull from the mapped column", sv["unitRate"]["samples"] == ["24.5", "23.9"])
    check("mpxn samples read verbatim", sv["mpxn"]["samples"] == ["1200001", "1200002"])
    check("__none__ split band is not treated as mapped", "dayRate" not in sv)

    # A cosmetically different header forks the cache (correctly a new layout).
    f3 = tempfile.mktemp(suffix=".xlsx")
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Rates"
    ws.append(["Acme Broker — Client Quote"]); ws.append([])
    ws.append(["Site Name", "MPAN", "AQ (kWh)", "Unit Rate (p/kWh)", "Standing Charge (p/day)"])
    ws.append(["Shop A", "1200001", "50000", "24.5", "45.1"])
    wb.save(f3)
    check("a changed header yields a different fingerprint", mh.layout_fingerprint(mh.inspect_file(f3)) != fp1)

    for f in (f1, f2, f3):
        os.unlink(f)


def _upload(client, path, **data):
    with open(path, "rb") as fh:
        return client.post(
            "/api/map",
            files={"file": (os.path.basename(path), fh.read(),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data=data,
        )


def test_endpoint_paths(monkeypatch_env=None):
    print("/api/map cache-vs-LLM + /api/map/confirm")
    client = TestClient(main.app)
    f = tempfile.mktemp(suffix=".xlsx")
    _make_quote(f, [["Shop A", "1200001", "50000", "24.5", "45.1"]])

    llm_calls = {"n": 0}

    def fake_propose(inspection, supplier=None, sample_rows=3, model=None):
        llm_calls["n"] += 1
        return dict(SAMPLE_MAPPING, supplier=supplier or "Acme")

    # ---- cache HIT: mapping comes from the DB, LLM is never called ----
    main._cache_get = lambda supplier, fp: dict(SAMPLE_MAPPING)          # type: ignore
    mh.propose_mapping = fake_propose                                    # type: ignore
    r = _upload(client, f, supplier="Acme")
    check("cache hit returns 200", r.status_code == 200)
    body = r.json()
    check("cache hit is labelled source=cache", body["source"] == "cache" and body["cache_hit"] is True)
    check("cache hit does NOT call the LLM", llm_calls["n"] == 0)
    check("cache hit still returns sample values", body["sample_values"]["unitRate"]["samples"] == ["24.5"])
    check("cache hit returns the layout fingerprint", len(body["layout_fingerprint"]) == 16)

    # ---- cache MISS: falls through to the (mocked) LLM ----
    main._cache_get = lambda supplier, fp: None                         # type: ignore
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-not-real"               # gate check only; propose is mocked
    r = _upload(client, f, supplier="Acme")
    body = r.json()
    check("cache miss returns 200", r.status_code == 200)
    check("cache miss is labelled source=llm", body["source"] == "llm" and body["cache_hit"] is False)
    check("cache miss calls the LLM exactly once", llm_calls["n"] == 1)

    # ---- no supplier: cache is skipped, a note explains why ----
    r = _upload(client, f)  # no supplier
    body = r.json()
    check("no-supplier path still works via LLM", body["source"] == "llm")
    check("no-supplier path warns about skipped cache", any("supplier" in n for n in body["notes"]))

    # ---- cache miss AND no key: a clean 503, not a crash ----
    os.environ.pop("ANTHROPIC_API_KEY", None)
    r = _upload(client, f, supplier="Acme")
    check("miss + no key returns 503", r.status_code == 503)

    # ---- confirm: persists to the cache (capture the upsert) ----
    saved = {}

    def fake_put(supplier, fp, mapping, confirmed_by):
        saved.update(supplier=supplier, fp=fp, mapping=mapping, confirmed_by=confirmed_by)

    main._cache_put = fake_put                                          # type: ignore
    r = client.post("/api/map/confirm", json={
        "supplier": "Acme", "layout_fingerprint": "deadbeefdeadbeef",
        "mapping": SAMPLE_MAPPING, "confirmed_by": "rory@rye.energy",
    })
    check("confirm returns 200", r.status_code == 200)
    check("confirm reports saved", r.json()["saved"] is True)
    check("confirm upserts with the right key", saved["supplier"] == "Acme" and saved["fp"] == "deadbeefdeadbeef")
    check("confirm records who confirmed it", saved["confirmed_by"] == "rory@rye.energy")

    # ---- confirm rejects an empty mapping ----
    r = client.post("/api/map/confirm", json={
        "supplier": "Acme", "layout_fingerprint": "x", "mapping": {},
    })
    check("confirm rejects a mapping with no columns", r.status_code == 400)

    os.unlink(f)


if __name__ == "__main__":
    test_fingerprint_and_samples()
    test_endpoint_paths()
    print("ALL MAP CHECKS PASSED")
