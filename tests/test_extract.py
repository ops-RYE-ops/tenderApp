#!/usr/bin/env python3
"""
test_extract.py — the /extract step: deterministic value pass-through + matching.

Headless and network-free. /extract is a thin wrapper over process_quote.run, so
these checks prove the ENDPOINT wiring: mapping parsing, the extractResult shape
and counts, verbatim value pass-through (code moves numbers), the optional
site-reference join, unmatched-MPxN flagging, and input validation. Run from the
repo root:

    python3 tests/test_extract.py

Prints 'ALL EXTRACT CHECKS PASSED' and exits 0 when green.
"""
import json
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


def check(name, cond):
    if not cond:
        raise AssertionError(f"FAILED: {name}")
    print(f"  ok: {name}")


def _make_quote(path):
    """A single-sheet single-rate quote: title block, then header at row 3."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fixed 12m"
    ws.append(["Acme Broker — Client Quote"])
    ws.append([])
    ws.append(["MPAN", "EAC (kWh)", "Start Date", "Unit Rate (p/kWh)", "Standing Charge (p/day)"])
    ws.append(["1200000000001", "50000", "2026-08-01", "24.5", "45.2"])
    ws.append(["1200000000002", "80000", "2026-08-01", "23.9", "45.2"])
    wb.save(path)


MAPPING = {
    "header_row": 3,
    "output_prefix": "acme",
    "supplier": "Acme",
    "category": "fixed",
    "term": "12 months",
    "columns": {
        "siteName": None,
        "mpxn": {"single": "MPAN"},
        "updatedEac": {"single": "EAC (kWh)"},
        "supplyStartDate": {"single": "Start Date"},
        "unitRate": {"single": "Unit Rate (p/kWh)"},
        "dayRate": {"split": "__none__"},
        "nightRate": {"split": "__none__"},
        "standingCharge": {"single": "Standing Charge (p/day)"},
    },
}


def test_extract_basic():
    print("/api/extract — value pass-through + counts")
    client = TestClient(main.app)
    f = tempfile.mktemp(suffix=".xlsx")
    _make_quote(f)
    with open(f, "rb") as fh:
        r = client.post(
            "/api/extract",
            files={"file": (os.path.basename(f), fh.read(),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"mapping": json.dumps(MAPPING), "supplier": "Acme"},
        )
    check("returns 200", r.status_code == 200)
    body = r.json()
    er = body["extract_result"]
    check("two sites extracted", body["counts"]["sites"] == 2)
    check("one quote, two lines", body["counts"]["quotes"] == 1 and body["counts"]["lines"] == 2)
    check("no site reference => nothing unmatched", body["unmatched_mpxn"] == [])

    line0 = er["quotes"][0]["lines"][0]
    check("unit rate copied VERBATIM as a number", line0["unitRate"] == 24.5)
    check("standing charge copied verbatim", line0["standingCharge"] == 45.2)
    check("mpxn kept as a string", er["sites"][0]["mpxn"] == "1200000000001")
    check("EAC lives on the site, parsed to a number", er["sites"][0]["eac"] == 50000.0)
    check("eac provenance recorded as 'quote'", er["sites"][0]["eac_source"] == "quote")
    check("single-rate: day/night stay null", line0["dayRate"] is None and line0["nightRate"] is None)
    os.unlink(f)


def test_site_reference_and_unmatched():
    print("/api/extract — site-reference join + unmatched flagging")
    client = TestClient(main.app)
    f = tempfile.mktemp(suffix=".xlsx")
    _make_quote(f)
    # Reference names only the FIRST meter point; the second must be flagged.
    ref = tempfile.mktemp(suffix=".csv")
    with open(ref, "w", encoding="utf-8") as fh:
        fh.write("mpxn,siteName\n1200000000001,Head Office\n")

    with open(f, "rb") as qf, open(ref, "rb") as rf:
        r = client.post(
            "/api/extract",
            files={
                "file": (os.path.basename(f), qf.read(),
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                "site_reference": (os.path.basename(ref), rf.read(), "text/csv"),
            },
            data={"mapping": json.dumps(MAPPING), "supplier": "Acme"},
        )
    check("returns 200", r.status_code == 200)
    body = r.json()
    check("site_reference_used flag is true", body["site_reference_used"] is True)
    check("the unmatched meter point is flagged", body["unmatched_mpxn"] == ["1200000000002"])
    names = {s["mpxn"]: s["site_name"] for s in body["extract_result"]["sites"]}
    check("matched site gets RYE's name", names["1200000000001"] == "Head Office")
    os.unlink(f)
    os.unlink(ref)


def test_db_eac_kva_override():
    print("/api/extract — sites.csv EAC/kVA override with 'db' provenance")
    client = TestClient(main.app)
    f = tempfile.mktemp(suffix=".xlsx")
    _make_quote(f)  # quote EACs: MPAN1=50000, MPAN2=80000; no kVA column
    # sites.csv gives DB EAC + kVA for the FIRST meter only.
    ref = tempfile.mktemp(suffix=".csv")
    with open(ref, "w", encoding="utf-8") as fh:
        fh.write("mpxn,siteName,eac,kva\n1200000000001,Head Office,55555,100\n")

    with open(f, "rb") as qf, open(ref, "rb") as rf:
        r = client.post(
            "/api/extract",
            files={
                "file": (os.path.basename(f), qf.read(),
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                "site_reference": (os.path.basename(ref), rf.read(), "text/csv"),
            },
            data={"mapping": json.dumps(MAPPING), "supplier": "Acme"},
        )
    check("returns 200", r.status_code == 200)
    sites = {s["mpxn"]: s for s in r.json()["extract_result"]["sites"]}
    db_site = sites["1200000000001"]
    check("DB EAC overrides the quote value", db_site["eac"] == 55555.0)
    check("DB kVA is applied", db_site["kva"] == 100.0)
    check("provenance stamped 'db' for the DB site", db_site["eac_source"] == "db")
    quote_site = sites["1200000000002"]
    check("meter absent from sites.csv keeps the quote EAC", quote_site["eac"] == 80000.0)
    check("and keeps 'quote' provenance", quote_site["eac_source"] == "quote")
    os.unlink(f)
    os.unlink(ref)


def test_validation():
    print("/api/extract — input validation")
    client = TestClient(main.app)
    f = tempfile.mktemp(suffix=".xlsx")
    _make_quote(f)
    data = open(f, "rb").read()
    fname = os.path.basename(f)
    ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    r = client.post("/api/extract",
                    files={"file": (fname, data, ctype)},
                    data={"mapping": "{not json"})
    check("invalid mapping JSON => 400", r.status_code == 400)

    r = client.post("/api/extract",
                    files={"file": (fname, data, ctype)},
                    data={"mapping": json.dumps({"header_row": 3})})  # no columns
    check("mapping without columns => 400", r.status_code == 400)

    r = client.post("/api/extract",
                    files={"file": ("notes.txt", b"hello", "text/plain")},
                    data={"mapping": json.dumps(MAPPING)})
    check("non-spreadsheet upload => 400", r.status_code == 400)
    os.unlink(f)


if __name__ == "__main__":
    test_extract_basic()
    test_site_reference_and_unmatched()
    test_db_eac_kva_override()
    test_validation()
    print("ALL EXTRACT CHECKS PASSED")
