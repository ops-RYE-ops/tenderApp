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

    print("3) cost engine: weekend split is conditional + renormalised (flat-week)")
    csv_path = written[0][0]
    entry = {"_csv_path": csv_path, "_id": "testco-12m", "supplier": "Testco", "term": "12 months"}

    # Explicit weekend_split=0 → weekend rate shown but NOT costed (day/night only).
    off0 = bd.compute_offer(dict(entry), {"day_split": 0.7, "weekend_split": 0})
    energy0 = off0["sites"][0]["costs"]["energy"]
    # (20*0.7 + 10*0.3) * 100000/100 = 17000
    check(abs(energy0 - 17000.0) < 0.01, "weekend_split=0 → energy = day/night only (£17,000)")
    check(any("not costed" in w.lower() for w in off0["warnings"]),
          "a warning is raised that the weekend rate is not costed")

    # Explicit weekend_split=0.2, renormalised: weekend carved out, weekday split day/night.
    off1 = bd.compute_offer(dict(entry), {"day_split": 0.7, "weekend_split": 0.2})
    energy1 = off1["sites"][0]["costs"]["energy"]
    # (20*0.7*0.8 + 10*0.3*0.8 + 5*0.2) * 1000 = (11.2 + 2.4 + 1.0)*1000 = 14600
    check(abs(energy1 - 14600.0) < 0.01, "weekend_split=0.2 (renormalised) → £14,600")
    check(not any("not costed" in w.lower() for w in off1["warnings"]),
          "no 'not costed' warning once a split is provided")
    check(any("flat-week" in w.lower() for w in off1["warnings"]),
          "the flat-week weekend assumption is footnoted")

    # Standing default (no weekend_split key) → flat-week 2/7, weekend IS costed.
    off2 = bd.compute_offer(dict(entry), {"day_split": 0.7})
    energy2 = off2["sites"][0]["costs"]["energy"]
    wk = 2 / 7
    expect2 = (20 * 0.7 * (1 - wk) + 10 * 0.3 * (1 - wk) + 5 * wk) * 100000 / 100
    check(abs(energy2 - expect2) < 0.01,
          f"default weekend_split=2/7 → weekend costed on flat-week (£{expect2:,.2f})")

    print("4) a plain day/night offer (no weekend rate) is unaffected by the default")
    two_band = os.path.join(WORK, "two-band.csv")
    with open(two_band, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=core.TARGET_FIELDS)
        w.writeheader()
        w.writerow({"siteName": "S1", "mpxn": MPAN, "updatedEac": 100000,
                    "dayRate": 20.0, "nightRate": 10.0, "standingCharge": 40.0})
    off3 = bd.compute_offer(
        {"_csv_path": two_band, "_id": "tb", "supplier": "Testco", "term": "12 months"},
        {"day_split": 0.7})  # default weekend_split, but no weekend rate present
    energy3 = off3["sites"][0]["costs"]["energy"]
    check(abs(energy3 - 17000.0) < 0.01, "no weekend rate → day/night only (£17,000), split ignored")

    print("\nALL WEEKEND CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
