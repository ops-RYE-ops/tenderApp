#!/usr/bin/env python3
"""
test_fee_chargeable.py — rye_fee.chargeable_sites (charge the flat fee on a
subset of the tender's supply points).

Why this exists: RYE sometimes charges list price on fewer supply points than the
tender covers (e.g. 10 of 14). That used to be expressed by back-solving an
equivalent whole-portfolio discount by hand (28.57%), which rounded to a wrong-
looking 29% on the client dashboard and an annual total £1 out. `chargeable_sites`
states the count directly; the annual total stays exact and the blended per-supply
figure is derived for display.

The first check is the important one: with no chargeable_sites the payload must be
byte-identical to the old behaviour, because every already-published client link
re-renders live from this code.

Run from the repo root:

    python3 tests/test_fee_chargeable.py

Prints 'ALL FEE CHARGEABLE CHECKS PASSED' and exits 0 when green.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

import build_dashboard as bd

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        FAILURES.append(label)


def main():
    print("1. No chargeable_sites -> unchanged behaviour (14 supply points, 80% off £90)")
    f = bd._fee_block({"list_price_site_month": 90, "discount_pct": 80}, 14, 50000.0)
    check("perSiteMonth", f["perSiteMonth"], 18.0)
    check("chargeableSites defaults to all", f["chargeableSites"], 14)
    check("blended == charged rate", f["blendedPerSiteMonth"], 18.0)
    check("discountPct", f["discountPct"], 80)
    check("annual", f["annual"], 18.0 * 14 * 12)
    check("netSaving", f["netSaving"], round(50000.0 - 18.0 * 14 * 12, 2))

    print("\n2. The real case: list £90, charge 10 of 14, no per-supply discount")
    f = bd._fee_block({"list_price_site_month": 90, "discount_pct": 0,
                       "chargeable_sites": 10}, 14, 32819.0)
    check("charged rate stays at list", f["perSiteMonth"], 90.0)
    check("chargeableSites", f["chargeableSites"], 10)
    check("totalSites", f["totalSites"], 14)
    check("annual is exact, not 10801", f["annual"], 10800.0)
    check("blended per supply", f["blendedPerSiteMonth"], 64.29)
    check("portfolio discount shown to client", f["discountPct"], 29)
    check("netSaving", f["netSaving"], round(32819.0 - 10800.0, 2))
    check("netSavingPerSite is across ALL supplies",
          f["netSavingPerSite"], round((32819.0 - 10800.0) / 14, 2))

    print("\n3. A per-supply discount and a chargeable cap compose")
    f = bd._fee_block({"list_price_site_month": 90, "discount_pct": 50,
                       "chargeable_sites": 10}, 14, None)
    check("charged rate", f["perSiteMonth"], 45.0)
    check("annual", f["annual"], 45.0 * 10 * 12)
    check("blended", f["blendedPerSiteMonth"], round(45.0 * 10 * 12 / (12 * 14), 2))
    check("netSaving is None with no baseline", f["netSaving"], None)

    print("\n4. Out-of-range / junk counts fall back to every supply point")
    for bad in (None, "", "ten", -3, 99):
        f = bd._fee_block({"list_price_site_month": 90, "discount_pct": 0,
                           "chargeable_sites": bad}, 14, None)
        check(f"chargeable_sites={bad!r} -> all 14", f["chargeableSites"], 14)
        check(f"chargeable_sites={bad!r} -> annual", f["annual"], 90.0 * 14 * 12)

    print("\n5. Explicit annual still wins over everything")
    f = bd._fee_block({"list_price_site_month": 90, "annual": 12000,
                       "chargeable_sites": 10}, 14, None)
    check("annual honoured", f["annual"], 12000)
    check("charged rate back-solved from the chargeable count",
          f["perSiteMonth"], round(12000 / (12 * 10), 2))

    print("\n6. A combined tender uses the same helper (no drift between paths)")
    tender = {"rye_fee": {"list_price_site_month": 90, "discount_pct": 0,
                          "chargeable_sites": 10}}
    fuel_payloads = [
        {"sites": [{"eac": 1000} for _ in range(9)], "offers": [], "incumbent": None},
        {"sites": [{"eac": 500} for _ in range(5)], "offers": [], "incumbent": None},
    ]
    fee, commission = bd._tender_level_charge(tender, fuel_payloads)
    check("commission not set", commission, None)
    check("totalSites across fuels", fee["totalSites"], 14)
    check("chargeableSites", fee["chargeableSites"], 10)
    check("annual", fee["annual"], 10800.0)
    check("blended", fee["blendedPerSiteMonth"], 64.29)

    if FAILURES:
        print(f"\n{len(FAILURES)} CHECK(S) FAILED")
        return 1
    print("\nALL FEE CHARGEABLE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
