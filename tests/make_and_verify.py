#!/usr/bin/env python3
"""
Headless Phase-0 verification. No network, no API key needed.

1. Synthesises a two-sheet (12m/24m) supplier xlsx with a single-rate site and a
   two-rate + kVA site, plus a RYE DB CSV of our site names.
2. Runs the deterministic extractor -> CSVs + canonical extractResult JSON.
3. Validates the extractResult against schema/tender.schema.json (#/$defs/extractResult).
4. Assembles a full canonical tender (the /assemble step, inline here) and
   validates it against the top-level tender schema.
5. Spot-checks that values passed through verbatim and single/split logic is right.
6. Exercises map_headers.inspect_file + build_payload (the pure /inspect + /map
   prep) and asserts the LLM payload carries headers + samples only.

Exit code 0 = all green.
"""
import csv
import datetime
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

import openpyxl
from jsonschema import Draft202012Validator

import process_quote as pq
import map_headers as mh

WORK = os.path.join(HERE, "_work")
os.makedirs(WORK, exist_ok=True)

MPAN_A = "1200035438587"   # single-rate, no kVA
MPAN_B = "1200035000000"   # two-rate, kVA 100

HEADERS = ["Site name", "Meter Point", "EAC (kWh)", "Start Date", "Standard",
           "Day", "Night", "Standing Charge", "DUoS", "Meter Charge", "kVa Capacity"]

# rows per sheet: (site, mpan, eac, start, standard, day, night, sc, duos, meter, kva)
SHEETS = {
    "12m": [
        ["Site A addr", MPAN_A, 50000, datetime.datetime(2026, 8, 1), 24.5, None, None, 45.2, 1.1, 0.5, None],
        ["Site B addr", MPAN_B, 120000, datetime.datetime(2026, 8, 1), None, 26.1, 18.3, 60.0, 2.0, 0.5, 100],
    ],
    "24m": [
        ["Site A addr", MPAN_A, 50000, datetime.datetime(2026, 8, 1), 23.9, None, None, 45.2, 1.1, 0.5, None],
        ["Site B addr", MPAN_B, 120000, datetime.datetime(2026, 8, 1), None, 25.4, 17.8, 60.0, 2.0, 0.5, 100],
    ],
}


def make_source():
    path = os.path.join(WORK, "testco-quote.xlsx")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in SHEETS.items():
        ws = wb.create_sheet(name)
        ws.append(HEADERS)
        for r in rows:
            ws.append(r)
    wb.save(path)
    return path


def make_db_csv():
    path = os.path.join(WORK, "rye-db.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["mpxn", "siteName"])
        w.writerow([MPAN_A, "Head Office"])
        w.writerow([MPAN_B, "Warehouse 1"])
    return path


MAPPING = {
    "sheets": ["12m", "24m"],
    "header_row": 1,
    "split_output_by_sheet": True,
    "output_prefix": "testco",
    "supplier": "Testco",
    "term_labels": {"12m": "12 months", "24m": "24 months"},
    "category": "fixed",
    "columns": {
        "siteName": "Site name",
        "mpxn": "Meter Point",
        "updatedEac": "EAC (kWh)",
        "supplyStartDate": "Start Date",
        "unitRate": {"single": "Standard"},
        "dayRate": {"split": "Day"},
        "nightRate": {"split": "Night"},
        "standingCharge": "Standing Charge",
        "capacityCharge": None,
        "networkCharge": "DUoS",
        "meterCharge": "Meter Charge",
        "kva": "kVa Capacity",
    },
    "rate_logic": "auto",
    "db_lookup": {"mpxn_col": "mpxn", "name_col": "siteName"},
}


def load_schema():
    with open(os.path.join(ROOT, "schema", "tender.schema.json")) as f:
        return json.load(f)


def sub_validator(schema, ref):
    """Validator for a $defs sub-schema, keeping $defs in scope for internal $refs."""
    return Draft202012Validator({"$defs": schema["$defs"], "$ref": ref})


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def main():
    os.environ["QP_NO_RUN_SUBDIR"] = "1"  # flat output for the test
    src = make_source()
    db = make_db_csv()
    schema = load_schema()

    print("1) extraction")
    written, extract, unmatched = pq.run(
        src, MAPPING, WORK, db_csv=db, emit_csv=True)
    check(not unmatched, "every mpxn matched a DB site name")
    check(len(extract["quotes"]) == 2, "two quote-terms emitted (12m + 24m)")
    check(len(extract["sites"]) == 2, "two unique sites (deduped across sheets)")
    check(len(written) == 2, "two CSVs written alongside the JSON")

    print("2) validate extractResult against schema")
    sub_validator(schema, "#/$defs/extractResult").validate(extract_clean(extract))
    check(True, "extractResult is schema-valid")

    print("3) spot-check values passed through verbatim + rate logic")
    q12 = next(q for q in extract["quotes"] if q["term"] == "12 months")
    la = next(l for l in q12["lines"] if l["mpxn"] == MPAN_A)
    lb = next(l for l in q12["lines"] if l["mpxn"] == MPAN_B)
    check(la["unitRate"] == 24.5 and la["dayRate"] is None and la["nightRate"] is None,
          "single-rate site: unitRate=24.5, day/night null")
    check(lb["dayRate"] == 26.1 and lb["nightRate"] == 18.3 and lb["unitRate"] is None,
          "two-rate site: day=26.1, night=18.3, unitRate null")
    check(isinstance(la["mpxn"], str) and la["mpxn"] == MPAN_A and "." not in la["mpxn"],
          "mpxn is a clean string (no trailing .0)")
    check(all(isinstance(l[f], (float, int, type(None))) for l in (la, lb)
              for f in pq.LINE_RATE_FIELDS),
          "all rate values are numbers or null (typed)")
    check(la["supplyStartDate"] == "2026-08-01", "date normalised to YYYY-MM-DD")

    sa = next(s for s in extract["sites"] if s["mpxn"] == MPAN_A)
    sb = next(s for s in extract["sites"] if s["mpxn"] == MPAN_B)
    check(sa["site_name"] == "Head Office" and sb["site_name"] == "Warehouse 1",
          "site names came from RYE DB, not supplier address")
    check(sa["eac"] == 50000 and sb["eac"] == 120000, "EAC numeric on sites[]")
    check(sb["kva"] == 100 and sa["kva"] is None, "kVA on the capacity site only")
    check(sa["eac_source"] == "quote", "eac_source provenance recorded")

    print("4) assemble a full tender and validate the spine")
    tender = assemble(extract, schema)
    Draft202012Validator(schema).validate(tender)
    check(True, "full canonical tender is schema-valid")

    print("5) map_headers /inspect + /map payload (pure, no network)")
    insp = mh.inspect_file(src)
    check([s["name"] for s in insp["sheets"]] == ["12m", "24m"], "inspect saw both sheets")
    check(insp["sheets"][0]["header_row_best_guess"] == 1, "header row guessed as row 1")
    payload = mh.build_payload(insp, sample_rows=3)
    blob = json.dumps(payload).lower()
    # The payload must carry headers + a few samples, and must NOT be the whole table.
    check("meter point" in blob and "standing charge" in blob, "payload carries the header labels")
    check(len(payload["sheets"][0]["sample_data_rows"]) <= 3, "payload capped at sample_rows")
    check(mh.mapping_tool_schema()["properties"]["columns"]["properties"].keys()
          == {f: 0 for f in mh.TARGET_FIELDS}.keys(), "tool schema covers all target fields")

    print("\nALL CHECKS PASSED")
    print(f"  canonical JSON: {extract['_json_path']}")
    print(f"  CSVs: " + ", ".join(os.path.basename(p) for p, _ in written))
    return 0


def extract_clean(extract):
    """Drop the internal _json_path helper before schema validation."""
    return {k: v for k, v in extract.items() if not k.startswith("_")}


def assemble(extract, schema):
    """Minimal inline /assemble: extractResult + incumbent + meta -> tender."""
    incumbent = {
        "supplier": "British Gas",
        "term": "current",
        "lines": [
            {"mpxn": MPAN_A, "unitRate": 27.0, "standingCharge": 50.0},
            {"mpxn": MPAN_B, "dayRate": 29.0, "nightRate": 20.0, "standingCharge": 65.0},
        ],
    }
    return {
        "id": str(uuid.uuid4()),
        "client_name": "Testco UK",
        "tender_label": "Electricity tender — July 2026",
        "utility": "electricity",
        "status": "draft",
        "version": 1,
        "created_at": datetime.datetime.now(datetime.timezone.utc)
                      .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "created_by": "rory@rye.energy",
        "expires_at": "2026-07-31",
        "day_split": 0.7,
        "url_uuid": str(uuid.uuid4()),
        "slug": "testco-uk",
        "dashboard_url": None,
        "recommended": {"supplier": "Testco", "term": "12 months"},
        "rye_fee": {"list_price_site_month": 90, "discount_pct": 80,
                    "label": "RYE fee (year 1, 80% discount)"},
        "sites": extract_clean(extract)["sites"],
        "incumbent": incumbent,
        "quotes": extract_clean(extract)["quotes"],
        "notes": ["Synthetic fixture for Phase-0 verification."],
    }


if __name__ == "__main__":
    sys.exit(main())
