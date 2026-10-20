#!/usr/bin/env python3
"""
test_summary_packages.py — combined Summary pairs gas by PACKAGE, never by term.

A canonical quote (one upload sheet) can carry both fuels; the render splits it into
one offer per fuel. Each per-fuel offer carries packageId = that quote's index in
tender.quotes, and the Summary pairs an electricity offer with the gas offer of the
same packageId. A term label is not an identity: several suppliers routinely quote
"12 months", so pairing on term would put supplier A's gas under supplier B's
electricity and report a package nobody quoted (review of feat/summary-pair-by-term).

Shape: Supplier A and Supplier B each quote BOTH fuels on "12 months" (two packages,
same term); Supplier D quotes electricity only on "12 months"; Supplier C quotes all
electricity but only one of two gas meters (partial gas); recommended is B.
Payload checks here; the rendered fixture tests/_work/_summary_packages.html goes to
tests/dom_summary_packages.js for the bars and headline (skipped without node/jsdom).

Headless, network-free. Run from the repo root: python3 tests/test_summary_packages.py
Prints 'ALL SUMMARY-PACKAGE CHECKS PASSED' and exits 0 when green.
"""
import os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, ROOT)
import assemble_tender as at  # noqa: E402
import build_dashboard as bd  # noqa: E402

FAILURES = []
MPANS = ["1200000000001", "1200000000002"]
MPRNS = ["9106810506", "9106810507"]
WORK = os.path.join(HERE, "_work")
FIXTURE = os.path.join(WORK, "_summary_packages.html")


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILURES.append(name)


def _lines(mpxns, unit, standing, fuel):
    return [{"mpxn": m, "unitRate": unit, "standingCharge": standing, "fuel": fuel} for m in mpxns]


def _tender(quotes=None):
    extract = {
        "sites": [{"mpxn": m, "site_name": f"Pub {i}", "eac": 100000.0, "eac_source": "quote",
                   "fuel": "electricity"} for i, m in enumerate(MPANS, 1)]
                 + [{"mpxn": m, "site_name": f"Pub {i} gas", "eac": 100000.0, "eac_source": "quote",
                     "fuel": "gas"} for i, m in enumerate(MPRNS, 1)],
        "quotes": quotes or [
            # A first, so a first-match-by-term bug would pick A's gas for everyone
            {"supplier": "Supplier A", "term": "12 months",
             "lines": _lines(MPANS, 24.00, 50.0, "electricity") + _lines(MPRNS, 6.10, 30.0, "gas")},
            {"supplier": "Supplier B", "term": "12 months",
             "lines": _lines(MPANS, 23.00, 50.0, "electricity") + _lines(MPRNS, 9.00, 30.0, "gas")},
            {"supplier": "Supplier D", "term": "12 months",
             "lines": _lines(MPANS, 25.00, 50.0, "electricity")},
            # C covers every electricity meter but only ONE of the two gas meters: its
            # gas is a partial offer and must not be used as C's twin
            {"supplier": "Supplier C", "term": "12 months",
             "lines": _lines(MPANS, 23.50, 50.0, "electricity") + _lines(MPRNS[:1], 5.00, 30.0, "gas")},
        ],
    }
    incumbent = {"supplier": "Various", "term": "current",
                 "lines": _lines(MPANS, 22.00, 45.0, "electricity") + _lines(MPRNS, 7.00, 35.0, "gas")}
    meta = {"client_name": "Package Co", "tender_label": "Package tender",
            "recommended_supplier": "Supplier B", "recommended_term": "12 months"}
    return at.assemble([extract], meta, incumbent)


def test_payload():
    print("1) payload — packageId ties each fuel's offer to its source quote")
    p = bd.build_render_payload(_tender())
    fu = {f["fuel"]: f for f in p["fuels"]}
    e = {o["supplier"]: o for o in fu["electricity"]["offers"]}
    g = {o["supplier"]: o for o in fu["gas"]["offers"]}
    check("every per-fuel offer carries a packageId",
          all(o.get("packageId") is not None for f in p["fuels"] for o in f["offers"]))
    check("A's electricity and gas share one package", e["Supplier A"]["packageId"] == g["Supplier A"]["packageId"])
    check("B's electricity and gas share one package", e["Supplier B"]["packageId"] == g["Supplier B"]["packageId"])
    check("A and B are different packages despite the same term",
          e["Supplier A"]["packageId"] != e["Supplier B"]["packageId"])
    check("D (electricity only) has no gas in its package",
          all(x["packageId"] != e["Supplier D"]["packageId"] for x in g.values()))
    check("C's partial gas is in the payload (kept, flagged) with C's packageId",
          g["Supplier C"]["packageId"] == e["Supplier C"]["packageId"]
          and len(g["Supplier C"]["sites"]) < len(fu["gas"]["sites"]))
    check("recommended is B in both fuels",
          fu["electricity"]["recommendedId"] == e["Supplier B"]["id"]
          and fu["gas"]["recommendedId"] == g["Supplier B"]["id"])


def test_single_fuel_unchanged():
    print("2) single-fuel payload carries no package pairing")
    t = _tender([{"supplier": "Supplier D", "term": "12 months",
                  "lines": _lines(MPANS, 25.00, 50.0, "electricity")}])
    t["sites"] = [s for s in t["sites"] if s["mpxn"] in MPANS]
    t["incumbent"]["lines"] = [l for l in t["incumbent"]["lines"] if l["mpxn"] in MPANS]
    t.pop("recommended", None)
    sp = bd.build_render_payload(t)
    check("single-fuel payload is not multiFuel", not sp.get("multiFuel"))
    check("single-fuel offers have packageId None", all(o.get("packageId") is None for o in sp["offers"]))


def test_render():
    print("3) render — fixture for the jsdom half")
    os.makedirs(WORK, exist_ok=True)
    with open(FIXTURE, "w", encoding="utf-8") as fh:
        fh.write(bd.render_tender(_tender()))
    check("fixture written", os.path.exists(FIXTURE))
    node, js = shutil.which("node"), os.path.join(HERE, "dom_summary_packages.js")
    if not node:
        print("  SKIP  jsdom half (node not installed)"); return
    r = subprocess.run([node, js], cwd=ROOT, capture_output=True, text=True)
    print("\n".join(l for l in r.stdout.splitlines() if "Not implemented" not in l))
    if "Cannot find module 'jsdom'" in r.stderr:
        print("  SKIP  jsdom half (jsdom not installed)"); return
    check("jsdom checks pass", r.returncode == 0, r.stderr.strip()[-200:])


if __name__ == "__main__":
    test_payload()
    test_single_fuel_unchanged()
    test_render()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED: " + "; ".join(FAILURES)); sys.exit(1)
    print("\nALL SUMMARY-PACKAGE CHECKS PASSED")
