#!/usr/bin/env python3
"""
test_fuel.py — fuel awareness + the mixed-fuel guard (increment A).

Covers the step-one work for combined gas + electricity tenders:
  1. fuel normalisation + MPAN/MPRN inference helpers
  2. the extractor reads a per-row Fuel column onto lines/sites and a per-row
     Supplier column onto lines (multi-supplier within one sheet)
  3. distinct_fuels() — explicit fuel, and the inference safety net
  4. /api/cost and /api/assemble REFUSE a mixed gas+electricity tender (422),
     via explicit fuel AND via meter-point inference when no Fuel column exists
  5. a single-fuel tender is unaffected, and lines carrying fuel/supplier still
     validate against the schema.

Headless, network-free. Run from the repo root:  python3 tests/test_fuel.py
Prints 'ALL FUEL CHECKS PASSED' and exits 0 when green.
"""
import csv
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
import process_quote as pq  # noqa: E402
import rye_quote_core as core  # noqa: E402
import main  # noqa: E402

FAILURES = []
MPAN_A = "1200000000001"   # 13-digit electricity MPAN
MPAN_B = "1200000000002"
MPRN_G = "9106810506"      # 10-digit gas MPRN


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


def test_helpers():
    print("1) fuel helpers")
    check("normalise Electricity/Gas", core.normalise_fuel("Electricity") == "electricity"
          and core.normalise_fuel("Gas") == "gas")
    check("normalise tolerant (elec/power/NG)", core.normalise_fuel("elec") == "electricity"
          and core.normalise_fuel("power") == "electricity" and core.normalise_fuel("NG") == "gas")
    check("normalise blank/unknown -> None", core.normalise_fuel("") is None
          and core.normalise_fuel("water") is None and core.normalise_fuel(None) is None)
    check("infer MPAN(13)->electricity", core.infer_fuel_from_mpxn(MPAN_A) == "electricity")
    check("infer MPRN(10)->gas", core.infer_fuel_from_mpxn(MPRN_G) == "gas")
    check("infer non-numeric -> None", core.infer_fuel_from_mpxn("Meter Point") is None)


def test_extractor_reads_fuel_and_supplier():
    print("2) extractor reads per-row Fuel + Supplier")
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["mpxn", "Supplier", "Fuel", "unitRate", "standingCharge"])
        w.writerow([MPAN_A, "Tem", "Electricity", "24.5", "45.0"])
        w.writerow([MPRN_G, "Yu Energy", "Gas", "6.5", "30.0"])
    mapping = {
        "header_row": 1, "output_prefix": "t", "supplier": "Mixed file",
        "columns": {
            "mpxn": "mpxn", "unitRate": {"single": "unitRate"},
            "standingCharge": "standingCharge", "fuel": "Fuel", "supplier": "Supplier",
        },
    }
    out = tempfile.mkdtemp(prefix="rye-fueltest-")
    _w, extract, _u = pq.run(path, mapping, out, emit_csv=False)
    lines = {ln["mpxn"]: ln for q in extract["quotes"] for ln in q["lines"]}
    sites = {s["mpxn"]: s for s in extract["sites"]}
    check("elec line carries fuel=electricity", lines[MPAN_A].get("fuel") == "electricity")
    check("gas line carries fuel=gas", lines[MPRN_G].get("fuel") == "gas")
    check("line carries per-row supplier", lines[MPAN_A].get("supplier") == "Tem"
          and lines[MPRN_G].get("supplier") == "Yu Energy")
    check("site carries fuel", sites[MPAN_A].get("fuel") == "electricity"
          and sites[MPRN_G].get("fuel") == "gas")
    os.unlink(path)


def _tender(sites, quotes):
    return {"sites": sites, "quotes": quotes}


def test_distinct_fuels():
    print("3) distinct_fuels — explicit + inference")
    single = _tender(
        [{"mpxn": MPAN_A, "fuel": "electricity"}, {"mpxn": MPAN_B, "fuel": "electricity"}],
        [{"supplier": "EDF", "lines": [{"mpxn": MPAN_A, "fuel": "electricity"}]}])
    check("single fuel -> len 1", at.distinct_fuels(single) == {"electricity"})
    mixed = _tender(
        [{"mpxn": MPAN_A, "fuel": "electricity"}, {"mpxn": MPRN_G, "fuel": "gas"}], [])
    check("explicit mixed -> {electricity, gas}", at.distinct_fuels(mixed) == {"electricity", "gas"})
    inferred = _tender([{"mpxn": MPAN_A}, {"mpxn": MPRN_G}], [])  # no explicit fuel
    check("inference catches mixed with no Fuel column",
          at.distinct_fuels(inferred) == {"electricity", "gas"})
    # The canonical example tender (all electricity) must NOT trip the guard.
    with open(os.path.join(ROOT, "schema", "examples", "tender.example.json")) as f:
        example = json.load(f)
    check("example tender reads as single fuel", len(at.distinct_fuels(example)) == 1)


def _mixed_extract(explicit=True):
    el = {"mpxn": MPAN_A, "unitRate": 25.0, "standingCharge": 40.0}
    ga = {"mpxn": MPRN_G, "unitRate": 6.5, "standingCharge": 30.0}
    se = {"mpxn": MPAN_A, "site_name": "E1", "eac": 100000.0, "eac_source": "quote"}
    sg = {"mpxn": MPRN_G, "site_name": "G1", "eac": 50000.0, "eac_source": "quote"}
    if explicit:
        el["fuel"] = ga["fuel"] = None  # placeholder; set below
        el["fuel"], ga["fuel"] = "electricity", "gas"
        se["fuel"], sg["fuel"] = "electricity", "gas"
    return {"sites": [se, sg],
            "quotes": [{"supplier": "EDF", "term": "24 months", "lines": [el, ga]}]}


def test_benchmark_per_fuel():
    print("6) market benchmark applies the right rate to each fuel")
    A, Bb, Gg = "1200000000001", "1200000000002", "9106810506"
    fmap = {A: "electricity", Bb: "electricity", Gg: "gas"}
    blk = at.incumbent_from_benchmark([A, Bb, Gg], 27.7, standing_charge=450,
        gas_unit_rate=7.0, gas_standing_charge=120, fuel_of=fmap)
    by = {ln["mpxn"]: ln for ln in blk["lines"]}
    check("electricity meter benchmarked at electricity rate", by[A]["unitRate"] == 27.7)
    check("gas meter benchmarked at gas rate, not electricity", by[Gg]["unitRate"] == 7.0)
    # combined tender, gas rate omitted -> gas gets NO benchmark line (never the elec rate)
    blk2 = at.incumbent_from_benchmark([A, Gg], 27.7, gas_unit_rate=None, fuel_of={A: "electricity", Gg: "gas"})
    mpxns2 = {ln["mpxn"] for ln in blk2["lines"]}
    check("combined + no gas rate -> gas meter has no benchmark line", Gg not in mpxns2 and A in mpxns2)
    # single-fuel gas tender: the electricity field alone still benchmarks the gas meters
    blk3 = at.incumbent_from_benchmark([Gg], 7.0, fuel_of={Gg: "gas"})
    check("gas-only tender: single field applies to gas", blk3["lines"][0]["unitRate"] == 7.0)


def test_endpoint_guards():
    print("4) /api/cost + /api/assemble route a combined tender per fuel")
    client = TestClient(main.app)
    extract = {"sites": [
        {"mpxn": MPAN_A, "site_name": "E1", "eac": 100000.0, "eac_source": "quote", "fuel": "electricity"},
        {"mpxn": MPRN_G, "site_name": "G1", "eac": 50000.0, "eac_source": "quote", "fuel": "gas"}],
        "quotes": [
        {"supplier": "EDF", "term": "24 months", "lines": [
            {"mpxn": MPAN_A, "unitRate": 25.0, "standingCharge": 40.0, "fuel": "electricity"}]},
        {"supplier": "British Gas", "term": "24 months", "lines": [
            {"mpxn": MPRN_G, "unitRate": 6.5, "standingCharge": 30.0, "fuel": "gas"}]}]}

    r = client.post("/api/cost", data={"extracts": json.dumps([extract])})
    check("cost combined -> 200 (no longer refused)", r.status_code == 200)
    j = r.json()
    check("cost reports both fuels in order", j.get("fuels") == ["electricity", "gas"])
    byfuel = {o["supplier"]: o["fuel"] for o in j["offers"]}
    check("electricity offer tagged electricity", byfuel.get("EDF") == "electricity")
    check("gas offer tagged gas", byfuel.get("British Gas") == "gas")
    check("cheapest marked once per fuel", sum(1 for o in j["offers"] if o["cheapest"]) == 2)

    r = client.post("/api/assemble", data={
        "extracts": json.dumps([extract]),
        "meta": json.dumps({"client_name": "Mixed Co", "tender_label": "Mixed"}),
        "persist": "false"})
    check("assemble combined -> 200 (saved, not refused)", r.status_code == 200)
    check("assemble ok flag true", r.json().get("ok") is True)


def test_single_fuel_unaffected_and_valid():
    print("5) single-fuel path unaffected + schema accepts fuel/supplier on lines")
    client = TestClient(main.app)
    single = {"sites": [{"mpxn": MPAN_A, "site_name": "A", "eac": 100000.0, "eac_source": "quote",
                         "fuel": "electricity"}],
              "quotes": [{"supplier": "EDF", "term": "12 months",
                          "lines": [{"mpxn": MPAN_A, "unitRate": 25.0, "standingCharge": 40.0,
                                     "fuel": "electricity", "supplier": "EDF"}]}]}
    r = client.post("/api/cost", data={"extracts": json.dumps([single])})
    check("single-fuel cost still 200", r.status_code == 200)

    tender = at.assemble([single], {"client_name": "Solo", "tender_label": "Elec"})
    at.validate_tender(tender)  # raises on failure
    check("tender with line fuel/supplier validates against schema", True)


def test_combined_render_payload():
    print("6) combined render payload — per-fuel isolation + one tender-level fee")
    import build_dashboard as bd
    tender = at.assemble([_mixed_extract(explicit=True)],
                         {"client_name": "Mixed Co", "tender_label": "Combined tender",
                          "rye_fee": {"per_site_month": 90}})
    payload = bd.build_render_payload(tender)
    check("multiFuel flag set", payload.get("multiFuel") is True)
    check("two fuel sections in order [electricity, gas]",
          [f["fuel"] for f in payload["fuels"]] == ["electricity", "gas"])
    elec = next(f for f in payload["fuels"] if f["fuel"] == "electricity")
    gas = next(f for f in payload["fuels"] if f["fuel"] == "gas")
    e_eff = elec["offers"][0]["perKwh"]["effective"]
    g_eff = gas["offers"][0]["perKwh"]["effective"]
    check(f"electricity effective ~ elec band ({e_eff}p > 15)", e_eff > 15)
    check(f"gas effective ~ gas band ({g_eff}p < 12) — NOT blended", g_eff < 12)
    check("each fuel costs only its own meters",
          len(elec["sites"]) == 1 and len(gas["sites"]) == 1)
    check("RYE fee computed ONCE at tender level (2 meters x 90 x 12 = 2160)",
          payload["fee"] and abs(payload["fee"]["annual"] - 2160) < 1)
    check("per-fuel sections carry no fee block",
          elec.get("fee") is None and gas.get("fee") is None)
    check("one shared market snapshot at top level", "market" in payload)
    html = bd.render_tender(tender)
    check("combined HTML fully injected (no placeholder)", "__TENDER_DATA__" not in html)
    check("combined HTML carries the in-Portfolio fuel switch", "switchPortfolioFuel(" in html and "MULTI" in html)
    check("combined HTML has the include-gas Summary toggle", "inc-gas" in html and "buildSummaryMulti" in html)
    check("combined HTML names both fuel sections", "Electricity" in html and "Gas" in html)


def run():
    test_helpers()
    test_extractor_reads_fuel_and_supplier()
    test_distinct_fuels()
    test_benchmark_per_fuel()
    test_endpoint_guards()
    test_single_fuel_unaffected_and_valid()
    test_combined_render_payload()
    if FAILURES:
        print(f"\n{len(FAILURES)} FUEL CHECK(S) FAILED")
        return 1
    print("\nALL FUEL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run())
