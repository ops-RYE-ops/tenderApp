#!/usr/bin/env python3
"""
test_ratebook_units.py — rate-book column headers state the unit they are costed on.

A client (Treetop, Sep 2026) read the Portfolio rate book and had to ask what the
STANDING / CAPACITY / NETWORK numbers were: 442.586 with no unit looks like an annual
figure, and is actually p/day. The four non-commodity columns shipped with no unit at
all while their neighbours (EAC kWh, Day p/kWh) carried one.

The fix labels them — but from the payload, NOT hardcoded. charge_basis is overridable
per tender, so a header asserting "p/day" over a charge costed as gbp/month would state
a falsehood on a client-facing page. That is the same class of fault as the p/kWh network
mis-cost of 2 Sep, just in the presentation layer instead of the engine.

Guards:
  - compute_offer already emits chargeBasis per offer, lowercased (no engine change)
  - default bases produce p/day, p/kVA/day, p/day, p/day
  - a charge_basis OVERRIDE moves the header with it
  - energy columns are untouched (always p/kWh)
  - a payload with no chargeBasis degrades to a bare label, never "undefined"

Renders fixtures for tests/dom_ratebook_units.js (skipped, not failed, without jsdom).
Headless, network-free. Run from the repo root: python3 tests/test_ratebook_units.py
Prints 'ALL RATEBOOK-UNITS CHECKS PASSED' and exits 0 when green.
"""
import copy, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, ROOT)
import build_dashboard as bd

WORK = os.path.join(HERE, "_work")
EXAMPLE = os.path.join(ROOT, "schema", "examples", "tender.example.json")
FAILURES = []


def check(label, ok, extra=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  [{extra}]" if extra else ""))
    if not ok:
        FAILURES.append(label)


def tender(basis=None):
    """Example tender with a real per-kVA capacity charge on every line."""
    t = copy.deepcopy(json.load(open(EXAMPLE)))
    for s in t["sites"]:
        s["kva"] = 100.0
    for q in t["quotes"]:
        for l in q["lines"]:
            l["capacityCharge"] = 11.6712
    for l in t.get("incumbent", {}).get("lines", []):
        l["capacityCharge"] = 9.5
    if basis:
        t["charge_basis"] = basis
    return t


def test_payload():
    print("1) the basis is already on every offer — no engine change needed")
    pf = bd.build_render_payload(tender())
    offers = pf["offers"] + ([pf["incumbent"]] if pf.get("incumbent") else [])
    check("every offer carries a chargeBasis", all(isinstance(o.get("chargeBasis"), dict) for o in offers),
          [type(o.get("chargeBasis")).__name__ for o in offers])
    cb = offers[0]["chargeBasis"]
    check("it covers all four non-commodity charges",
          set(cb) == {"standingCharge", "capacityCharge", "networkCharge", "meterCharge"},
          sorted(cb))
    check("defaults are the documented ones",
          cb == {"standingCharge": "p/day", "capacityCharge": "p/kva/day",
                 "networkCharge": "p/day", "meterCharge": "p/day"}, json.dumps(cb))
    check("values are lowercased for a stable lookup", all(v == v.lower() for v in cb.values()))
    check("it carries NO energy key, so energy headers cannot be double-suffixed",
          not {"unitRate", "dayRate", "nightRate"} & set(cb))

    print("\n2) an override travels with the charge")
    cb2 = bd.build_render_payload(tender({"standingCharge": "GBP/month"}))["offers"][0]["chargeBasis"]
    check("override reaches the offer, case-normalised",
          cb2["standingCharge"] == "gbp/month", cb2["standingCharge"])
    check("the other charges keep their defaults",
          cb2["networkCharge"] == "p/day" and cb2["capacityCharge"] == "p/kva/day")


def render(t, name):
    os.makedirs(WORK, exist_ok=True)
    path = os.path.join(WORK, name)
    open(path, "w", encoding="utf-8").write(bd.render_tender(t))
    return path


def main():
    test_payload()
    print("\n3) fixtures for the DOM half")
    render(tender(), "_units_default.html")
    render(tender({"standingCharge": "gbp/month", "networkCharge": "p/kwh"}), "_units_override.html")
    # A payload that predates the field: the template must degrade, not print "undefined".
    html = bd.render_tender(tender())
    n = html.count('"chargeBasis":')
    check("fixture carries the field to strip, once per offer", n >= 2, n)
    stripped = html.replace('"chargeBasis":', '"chargeBasisRemoved":')
    check("every occurrence stripped", '"chargeBasis":' not in stripped)
    open(os.path.join(WORK, "_units_legacy.html"), "w", encoding="utf-8").write(stripped)
    check("three fixtures written", True)

    print("\n4) DOM half")
    js = os.path.join(HERE, "dom_ratebook_units.js")
    r = subprocess.run(["node", js], cwd=ROOT, capture_output=True, text=True)
    print(r.stdout.rstrip() or r.stderr.rstrip())
    if r.returncode == 2:
        print("  SKIP  jsdom not installed (npm i jsdom)")
    elif r.returncode != 0:
        FAILURES.append("dom_ratebook_units.js")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED: " + "; ".join(FAILURES))
        sys.exit(1)
    print("ALL RATEBOOK-UNITS CHECKS PASSED")


if __name__ == "__main__":
    main()
