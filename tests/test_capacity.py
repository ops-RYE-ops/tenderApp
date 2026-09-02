#!/usr/bin/env python3
"""
test_capacity.py — annualise() behaviour for per-kVA capacity charges.

Focused on the noise bug: a capacity charge of 0 quoted per kVA on a site with no
kVA figure must NOT raise a client-facing "excluded from that site's total" warning
(a zero charge is a no-op). A genuine NON-ZERO per-kVA charge with no kVA still
warns, because that's real undercosting worth surfacing. Run from the repo root:

    python3 tests/test_capacity.py

Prints 'ALL CAPACITY CHECKS PASSED' and exits 0 when green.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

import build_dashboard as bd

FAILURES = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


def main():
    print("1) a ZERO per-kVA charge with no kVA figure: no cost, no warning")
    w = set()
    cost = bd.annualise(0.0, "p/kva/day", 100000, None, w, "Capacity charge", "Cardiff")
    check("zero charge costs 0", cost == 0.0)
    check("no warning raised for a zero charge", not any("kVA" in x for x in w))

    print("2) a NON-ZERO per-kVA charge with no kVA figure: excluded + warned")
    w = set()
    cost = bd.annualise(1.5, "p/kva/day", 100000, None, w, "Capacity charge", "Cardiff")
    check("uncostable non-zero charge excluded (0)", cost == 0.0)
    check("warning raised for a real charge that can't be costed",
          any("no kVA figure" in x for x in w))

    print("3) a per-kVA charge WITH a kVA figure costs correctly")
    w = set()
    cost = bd.annualise(1.5, "p/kva/day", 100000, 100.0, w, "Capacity charge", "Cardiff")
    check("costed = value * kva * 365 / 100", abs(cost - (1.5 * 100.0 * 365 / 100)) < 1e-9)
    check("no warning when kVA present", not w)

    print("4) charge_basis is case/space-insensitive (p/kVA/day == p/kva/day)")
    import tempfile
    tender = {"client_name": "C", "tender_label": "T", "utility": "electricity",
              "sites": [{"mpxn": "1200000000001", "eac": 100000.0, "kva": 100.0}],
              "quotes": [{"supplier": "S", "term": "24 months", "featured": True,
                          "charge_basis": {"capacityCharge": " P/kVA/Day "},
                          "lines": [{"mpxn": "1200000000001", "unitRate": 22.0,
                                     "standingCharge": 2.5, "capacityCharge": 10.0,
                                     "fuel": "electricity"}]}]}
    pay = bd.build_render_payload(tender)
    off = pay["offers"][0]
    check("mixed-case p/kVA/day accepted and costed as p/kva/day",
          off["totals"]["capacity"] == round(10.0 * 100.0 * 365 / 100, 2))

    print("5) cache layer canonicalises a stored mapping's charge_basis")
    sys.path.insert(0, ROOT)
    import main as _m
    canon = _m._canon_charge_basis({"columns": {}, "charge_basis": {"capacityCharge": " P/kVA/Day "}})
    check("cached charge_basis lowercased/stripped on the boundary",
          canon["charge_basis"]["capacityCharge"] == "p/kva/day")
    check("mapping with no charge_basis is untouched",
          _m._canon_charge_basis({"columns": {}}) == {"columns": {}})

    if FAILURES:
        print(f"\n{len(FAILURES)} CHECK(S) FAILED")
        return 1
    print("\nALL CAPACITY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
