#!/usr/bin/env python3
"""
test_stacked.py — refuse a sheet that stacks two rate tables in one sheet.

Bad data hygiene (a single-rate block and a day/night block under separate header
rows in the SAME sheet) can't be described by one header row, so the tool detects
the repeated header and REFUSES with guidance to split it — rather than silently
mis-reading one block. A clean single-table sheet is unaffected. Run from repo root:

    python3 tests/test_stacked.py

Prints 'ALL STACKED CHECKS PASSED' and exits 0 when green.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

import openpyxl

import map_headers as mh
import process_quote as pq

FAILURES = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


def _xlsx(rows, title="Sheet1"):
    path = tempfile.mktemp(suffix=".xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


MAPPING = {
    "sheets": ["Sheet1"], "header_row": 1, "split_output_by_sheet": False,
    "output_prefix": "t", "supplier": "Testco",
    "columns": {"mpxn": "Meter Point", "updatedEac": "EAC",
                "unitRate": {"single": "Standard"}, "standingCharge": "Standing Charge"},
}


def main():
    os.environ["QP_NO_RUN_SUBDIR"] = "1"

    print("1) a sheet with two stacked tables is detected + refused")
    stacked = _xlsx([
        ["Meter Point", "EAC", "Standard", "Standing Charge"],
        ["1200000000001", 40000, 24.5, 40.0],
        ["1200000000002", 50000, 24.6, 41.0],
        [],
        ["Meter Point", "EAC", "All Year - Day", "All Year - Night", "Standing Charge"],
        ["1200000000003", 60000, 25.4, 18.2, 60.0],
    ])
    extra = mh.stacked_tables_in_sheet(stacked, "Sheet1")
    check("detector finds the repeated header block", bool(extra))
    try:
        pq.run(stacked, MAPPING, tempfile.mkdtemp(), emit_csv=False)
        check("run() refuses a stacked sheet", False)
    except SystemExit as e:
        check("run() refuses a stacked sheet", True)
        check("error explains the problem + how to fix", "more than one table" in str(e).lower())
    os.unlink(stacked)

    print("2) a clean single-table sheet is unaffected")
    clean = _xlsx([
        ["Meter Point", "EAC", "Standard", "Standing Charge"],
        ["1200000000001", 40000, 24.5, 40.0],
        ["1200000000002", 50000, 24.6, 41.0],
        ["1200000000003", 60000, 24.7, 42.0],
    ])
    check("detector finds no repeat", mh.stacked_tables_in_sheet(clean, "Sheet1") == [])
    check("inspect flags clean sheet as single-table",
          mh.inspect_file(clean)["sheets"][0]["extra_header_rows"] == [])
    _, ext, _ = pq.run(clean, MAPPING, tempfile.mkdtemp(), emit_csv=False)
    check("clean sheet extracts its rows", len(ext["quotes"][0]["lines"]) == 3)
    os.unlink(clean)

    if FAILURES:
        print(f"\n{len(FAILURES)} CHECK(S) FAILED")
        return 1
    print("\nALL STACKED CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
