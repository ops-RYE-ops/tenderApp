#!/usr/bin/env python3
"""
Focused test for the weekendRate band (added end-to-end).

Covers: extraction captures a distinct weekend rate as a split-mode line; the
extractResult stays schema-valid; and the cost engine's deliberate default —
capture + WARN but do not silently cost a weekend rate unless a weekend_split is
set, and cost it correctly when one is. No network. Exit 0 = all green.
"""
import csv
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

import openpyxl
from jsonschema import Draft202012Validator

import process_quote as pq
import build_dashboard as bd
import rye_quote_core as core

WORK = os.path.join(HERE, "_work")
os.makedirs(WORK, exist_ok=True)
MPAN = "1200099999001"


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def sub_validator(schema, ref):
    return Draft202012Validator({"$defs": schema["$defs"], "$ref": ref})


def main():
    check("weekendRate" in core.TARGET_FIELDS, "weekendRate is in the shared TARGET_FIELDS")
    check("weekendRate" in pq.LINE_RATE_FIELDS, "weekendRate is a canonical line rate field")

    print("1) extraction captures a distinct weekend rate (split mode)")
    src = os.path.join(WORK, "weekend-quote.xlsx")
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "12m"
    ws.append(["Meter Point", "EAC (kWh)", "Day", "Night", "Weekend", "Standing Charge"])
    ws.append([MPAN, 100000, 20.0, 10.0, 5.0, 40.0])
    wb.save(src)
    mapping = {
        "sheets": ["12m"], "header_row": 1, "split_output_by_sheet": True,
        "output_prefix": "wknd", "supplier": "Testco", "term_labels": {"12m": "12 months"},
        "columns": {
            "mpxn": "Meter Point", "updatedEac": "EAC (kWh)",
            "unitRate": {"single": "__none__"},
            "dayRate": {"split": "Day"}, "nightRate": {"split": "Night"},
            "weekendRate": {"split": "Weekend"},
            "standingCharge": "Standing Charge",
        },
    }
    os.environ["QP_NO_RUN_SUBDIR"] = "1"
    written, extract, _ = pq.run(src, mapping, WORK, emit_csv=True)
    line = extract["quotes"][0]["lines"][0]
    check(line["weekendRate"] == 5.0, "weekend rate extracted verbatim (5.0)")
    check(line["dayRate"] == 20.0 and line["nightRate"] == 10.0, "day/night still extracted")
    check(line["unitRate"] is None, "unitRate null on a split (multi-rate) line")

    print("2) extractResult still schema-valid with the new field")
    with open(os.path.join(ROOT, "schema", "tender.schema.json")) as f:
        schema = json.load(f)
    clean = {k: v for k, v in extract.items() if not k.startswith("_")}
    sub_validator(schema, "#/$defs/extractResult").validate(clean)
    check(True, "extractResult validates")

    print("3) cost engine: weekend NOT costed (and warned) unless a split is set")
    csv_path = written[0][0]
    entry = {"_csv_path": csv_path, "_id": "testco-12m", "supplier": "Testco", "term": "12 months"}

    off0 = bd.compute_offer(dict(entry), {"day_split": 0.7})  # no weekend_split
    energy0 = off0["sites"][0]["costs"]["energy"]
    # day/night only: (20*0.7 + 10*0.3) * 100000/100 = 17000
    check(abs(energy0 - 17000.0) < 0.01, "without weekend_split, energy = day/night only (£17,000)")
    check(any("weekend rate" in w.lower() for w in off0["warnings"]),
          "a warning is raised that the weekend rate is not costed")

    off1 = bd.compute_offer(dict(entry), {"day_split": 0.7, "weekend_split": 0.2})
    energy1 = off1["sites"][0]["costs"]["energy"]
    # (20*0.7 + 10*0.1)*1000 + 5*0.2*1000 = 15000 + 1000 = 16000
    check(abs(energy1 - 16000.0) < 0.01, "with weekend_split=0.2, weekend is costed (£16,000)")
    check(not any("weekend rate" in w.lower() for w in off1["warnings"]),
          "no weekend warning once a split is provided")

    print("\nALL WEEKEND CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
