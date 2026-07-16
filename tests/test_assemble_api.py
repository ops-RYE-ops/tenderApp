#!/usr/bin/env python3
"""
test_assemble_api.py — the /assemble step: incumbent-from-sites.csv + the endpoint.

Headless. The pure merge/versioning logic is covered by test_assemble.py; this
covers the NEW pieces: building the incumbent block from sites.csv (supplier
one/Various/Unknown/None, skip-empty, client + mpxn filters), the drift guard on
the incumbent rate fields, and the /api/assemble endpoint (assemble → validate →
versioned DB write, with the DB mocked). Run from the repo root:

    python3 tests/test_assemble_api.py

Prints 'ALL ASSEMBLE-API CHECKS PASSED' and exits 0 when green.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

import assemble_tender as at  # noqa: E402
import main  # noqa: E402

MPAN_A = "1200035438587"
MPAN_B = "1200035000000"


def check(name, cond):
    if not cond:
        raise AssertionError(f"FAILED: {name}")
    print(f"  ok: {name}")


def _example_extract():
    with open(os.path.join(ROOT, "schema", "examples", "extract-result.example.json")) as f:
        return json.load(f)


def _sites_csv(rows, header=None):
    header = header or ("clientName,siteName,mpxn,eac,supplyStartDate,unitRate,dayRate,"
                        "nightRate,weekendRate,standingCharge,capacityCharge,networkCharge,"
                        "meterCharge,kva,incumbentSupplier")
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        for r in rows:
            f.write(r + "\n")
    return path


def test_rate_fields_in_sync_with_schema():
    print("incumbent rate fields stay in sync with the schema")
    schema = json.load(open(os.path.join(ROOT, "schema", "tender.schema.json")))
    line_props = set(schema["$defs"]["line"]["properties"]) - {"mpxn", "supplyStartDate"}
    check("_INCUMBENT_RATE_FIELDS == schema line rate props",
          set(at._INCUMBENT_RATE_FIELDS) == line_props)


def test_incumbent_builder():
    print("incumbent_from_sites_csv — supplier rule, skip-empty, filters")
    # One supplier, one site-ref-only row (no rates) that must be skipped.
    p = _sites_csv([
        f"Amorino,Head Office,{MPAN_A},50000,2026-08-01,22.5,,,,45.0,,,,,British Gas",
        f"Amorino,Warehouse 1,{MPAN_B},120000,2026-08-01,,26.0,18.0,,60.0,,,,100,British Gas",
        "Amorino,RefOnly,9999999999999,8000,,,,,,,,,,,British Gas",
    ])
    inc = at.incumbent_from_sites_csv(p, client_name="Amorino")
    check("single supplier used verbatim", inc["supplier"] == "British Gas")
    check("only rows with rate data become lines", len(inc["lines"]) == 2)
    check("rate value copied verbatim", inc["lines"][0]["unitRate"] == 22.5)
    check("two-rate line keeps day/night", inc["lines"][1]["dayRate"] == 26.0 and inc["lines"][1]["nightRate"] == 18.0)
    os.unlink(p)

    # Multiple suppliers -> "Various".
    p = _sites_csv([
        f"Amorino,A,{MPAN_A},50000,,22.5,,,,45.0,,,,,British Gas",
        f"Amorino,B,{MPAN_B},120000,,23.0,,,,50.0,,,,,EDF",
    ])
    check("multiple suppliers -> Various", at.incumbent_from_sites_csv(p)["supplier"] == "Various")
    os.unlink(p)

    # Rates but no supplier named -> "Unknown".
    p = _sites_csv([f"Amorino,A,{MPAN_A},50000,,22.5,,,,45.0,,,,,"])
    check("rates but no supplier -> Unknown", at.incumbent_from_sites_csv(p)["supplier"] == "Unknown")
    os.unlink(p)

    # No rate data anywhere -> None (unknown incumbent).
    p = _sites_csv([f"Amorino,A,{MPAN_A},50000,,,,,,,,,,,British Gas"])
    check("no rate data -> None", at.incumbent_from_sites_csv(p) is None)
    os.unlink(p)

    # client + mpxn filters.
    p = _sites_csv([
        f"Amorino,A,{MPAN_A},50000,,22.5,,,,45.0,,,,,British Gas",
        f"OtherCo,X,{MPAN_B},120000,,99.0,,,,99.0,,,,,EDF",
    ])
    inc = at.incumbent_from_sites_csv(p, client_name="Amorino")
    check("client filter drops other clients' meters", len(inc["lines"]) == 1 and inc["lines"][0]["mpxn"] == MPAN_A)
    inc = at.incumbent_from_sites_csv(p, mpxns=[MPAN_B])
    check("mpxn filter keeps only requested meters", len(inc["lines"]) == 1 and inc["lines"][0]["mpxn"] == MPAN_B)
    os.unlink(p)


def _post_assemble(client, extracts, meta, sites_path=None, persist=False):
    files = {}
    data = {"extracts": json.dumps(extracts), "meta": json.dumps(meta), "persist": str(persist).lower()}
    if sites_path:
        files["sites_csv"] = (os.path.basename(sites_path), open(sites_path, "rb").read(), "text/csv")
    if not files:
        # multipart is required for File(...) endpoints; send an empty dummy part-free form
        return client.post("/api/assemble", data=data)
    return client.post("/api/assemble", data=data, files=files)


def test_endpoint():
    print("/api/assemble — assemble + validate + versioned write")
    client = TestClient(main.app)
    extract = _example_extract()
    meta = {"client_name": "Amorino UK", "tender_label": "Electricity — Jul 2026",
            "created_by": "rory@rye.energy"}
    sites = _sites_csv([
        f"Amorino UK,Head Office,{MPAN_A},50000,2026-08-01,22.5,,,,45.0,,,,,British Gas",
        f"Amorino UK,Warehouse 1,{MPAN_B},120000,2026-08-01,,26.0,18.0,,60.0,,,,100,British Gas",
    ])

    # --- persist=false: assemble + validate, no DB ---
    r = _post_assemble(client, extract, meta, sites_path=sites, persist=False)
    check("persist=false returns 200", r.status_code == 200)
    body = r.json()
    check("not persisted when persist=false", body["persisted"] is False)
    check("tender validated (has a uuid id)", len(body["id"]) == 36)
    check("incumbent built from sites.csv", body["incumbent_supplier"] == "British Gas")
    check("incumbent lines counted", body["counts"]["incumbent_lines"] == 2)
    check("first version defaults to 1", body["version"] == 1)
    check("tender embeds the incumbent block", body["tender"]["incumbent"]["supplier"] == "British Gas")

    # --- persist=true: DB mocked, versioning honoured ---
    writes = {}
    main._next_version = lambda tid: 4                       # type: ignore
    main._write_tender = lambda t: writes.update(t=t)        # type: ignore
    meta_v = dict(meta, id="11111111-1111-1111-1111-111111111111")
    r = _post_assemble(client, extract, meta_v, sites_path=sites, persist=True)
    body = r.json()
    check("persist=true returns 200", r.status_code == 200)
    check("reported as persisted", body["persisted"] is True)
    check("version bumped to next (4)", body["version"] == 4)
    check("the written tender matches the returned id", writes["t"]["id"] == body["id"])

    # --- Various supplier raises a warning ---
    sites_mixed = _sites_csv([
        f"Amorino UK,A,{MPAN_A},50000,,22.5,,,,45.0,,,,,British Gas",
        f"Amorino UK,B,{MPAN_B},120000,,23.0,,,,50.0,,,,,EDF",
    ])
    r = _post_assemble(client, extract, meta, sites_path=sites_mixed, persist=False)
    body = r.json()
    check("mixed incumbents -> Various", body["incumbent_supplier"] == "Various")
    check("a warning is surfaced for Various", any("Various" in w for w in body["warnings"]))
    os.unlink(sites_mixed)

    # --- validation errors ---
    r = client.post("/api/assemble", data={"extracts": "[]", "meta": json.dumps(meta)})
    check("empty extracts -> 400", r.status_code == 400)
    r = client.post("/api/assemble", data={"extracts": json.dumps(extract), "meta": json.dumps({"client_name": "X"})})
    check("meta missing tender_label -> 400", r.status_code == 400)

    os.unlink(sites)


if __name__ == "__main__":
    test_rate_fields_in_sync_with_schema()
    test_incumbent_builder()
    test_endpoint()
    print("ALL ASSEMBLE-API CHECKS PASSED")
