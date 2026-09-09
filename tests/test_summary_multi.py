#!/usr/bin/env python3
"""
test_summary_multi.py — the COMBINED (gas + electricity) Summary tab.

Guards the two bugs found on RYE's first live combined tender (Public House
Group, Sep 2026), where the client-facing Summary:
  1. rendered NO offer bars — the MULTI path never called buildBars(), so the
     "All offers tendered" section the single-fuel dashboard shows was simply
     absent; and
  2. showed a NEGATIVE saving as a green "£-6,316" saving, with the whole
     tender's commission netted against an electricity-only gross, so the two
     KPIs on the card could not be reconciled.

Renders a fixture in that exact shape (electricity DEARER than incumbent, gas
saving, one tender-level commission), asserts the static markers here, then hands
tests/_work/_summary_multi.html to tests/dom_summary_multi.js for the
behavioural half (skipped, not failed, when node/jsdom aren't installed).

Headless, network-free. Run from the repo root: python3 tests/test_summary_multi.py
Prints 'ALL SUMMARY-MULTI CHECKS PASSED' and exits 0 when green.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, ROOT)

import assemble_tender as at  # noqa: E402
import build_dashboard as bd  # noqa: E402

FAILURES = []
MPANS = ["1200000000001", "1200000000002"]   # 13-digit -> electricity
MPRNS = ["9106810506", "9106810507"]         # 10-digit -> gas
WORK = os.path.join(HERE, "_work")
FIXTURE = os.path.join(WORK, "_summary_multi.html")


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILURES.append(name)


def _lines(mpxns, unit, standing, fuel):
    return [{"mpxn": m, "unitRate": unit, "standingCharge": standing, "fuel": fuel}
            for m in mpxns]


def _tender():
    """Electricity where the best offer is DEARER than the incumbent, gas where it
    saves — the shape that made the dashboard call a £6k increase a green saving."""
    extract = {
        "sites": [{"mpxn": m, "site_name": f"Pub {i}", "eac": 400000.0,
                   "eac_source": "quote", "fuel": "electricity"}
                  for i, m in enumerate(MPANS, 1)]
                 + [{"mpxn": m, "site_name": f"Pub {i} gas", "eac": 300000.0,
                     "eac_source": "quote", "fuel": "gas"}
                    for i, m in enumerate(MPRNS, 1)],
        "quotes": [
            {"supplier": "Yu Energy", "term": "Co-terminous 2029-11-21",
             "lines": _lines(MPANS, 22.40, 48.0, "electricity")},
            {"supplier": "SEFE", "term": "36 months",
             "lines": _lines(MPANS, 23.10, 50.0, "electricity")},
            {"supplier": "Tem", "term": "24 months",
             "lines": _lines(MPRNS, 6.40, 30.0, "gas")},
        ],
    }
    incumbent = {"supplier": "Various", "term": "current",
                 "lines": _lines(MPANS, 21.65, 45.0, "electricity")
                 + _lines(MPRNS, 8.90, 35.0, "gas")}
    meta = {"client_name": "Combined Co", "tender_label": "Combined tender",
            "rye_commission": {"p_kwh_uplift": 0.6, "included": False,
                               "label": "RYE commission"}}
    return at.assemble([extract], meta, incumbent)


def test_payload_shape():
    print("1) payload — electricity loses, gas saves, one tender-level commission")
    payload = bd.build_render_payload(_tender())
    fuels = {f["fuel"]: f for f in payload["fuels"]}
    def gross(f):
        rec = next(o for o in f["offers"] if o["id"] == f["recommendedId"])
        return f["incumbent"]["total"] - rec["total"]
    e, g = gross(fuels["electricity"]), gross(fuels["gas"])
    check("electricity gross is NEGATIVE (a cost increase)", e < 0, f"{e:.0f}")
    check("gas gross is positive", g > 0, f"{g:.0f}")
    check("combined gross is positive — the default view must not imply the reverse",
          e + g > 0, f"{e + g:.0f}")
    comm = payload["commission"]
    check("commission is tender-level (0.6p across 1.4 GWh = 8,400)",
          comm and abs(comm["annual"] - 8400) < 1, comm and comm["annual"])
    check("engine net saving is the COMBINED one",
          abs(comm["netSaving"] - (e + g - comm["annual"])) < 1, comm["netSaving"])


def test_rendered_markers():
    print("2) rendered HTML — Summary carries the bars + the scoped-charge helpers")
    html = bd.render_tender(_tender())
    os.makedirs(WORK, exist_ok=True)
    with open(FIXTURE, "w", encoding="utf-8") as fh:
        fh.write(html)
    check("Summary composes bars on the MULTI path",
          "buildSummaryMulti() + buildBarsMulti()" in html)
    check("gas bars block is hidden until the include-gas tick",
          'sectionClass: i === primaryIndex ? "" : "gas-bars"' in html
          and '.gas-bars' in html)
    check("saving/increase wording is derived, not hardcoded",
          "saveWord" in html and "saveTone" in html)
    check("no hardcoded green tone left on the combined KPIs",
          'class="kpi-value pos" id="m-gross"' not in html
          and 'class="kpi-value pos" id="m-net"' not in html)
    check("RYE's charge scopes to the fuels displayed", "function chargeOn(" in html)
    check("offer filters get unique ids per bars block",
          'id="offer-filter${esc(o_.idSuffix' in html)


def test_dom_behaviour():
    print("3) jsdom — the tick, the tone and the reconciliation (optional)")
    if not os.path.isdir(os.path.join(ROOT, "node_modules", "jsdom")):
        print("  SKIP  node_modules/jsdom not installed (npm i jsdom to run)")
        return
    r = subprocess.run(["node", os.path.join(HERE, "dom_summary_multi.js")],
                       cwd=ROOT, capture_output=True, text=True)
    for line in (r.stdout or "").splitlines():
        print("  " + line if not line.startswith(("  ", "-")) else line)
    if r.returncode == 2:
        print("  SKIP  fixture missing")
        return
    check("dom_summary_multi.js green", r.returncode == 0,
          (r.stderr or "").strip()[:200])


def run():
    test_payload_shape()
    test_rendered_markers()
    test_dom_behaviour()
    if FAILURES:
        print(f"\n{len(FAILURES)} SUMMARY-MULTI CHECK(S) FAILED")
        return 1
    print("\nALL SUMMARY-MULTI CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run())
