#!/usr/bin/env python3
"""
test_timeline.py — the optional client-facing SAVINGS TIMELINE tab.

Requested by a client (Public House Group, Sep 2026) who wanted to see the saving
month by month as each supply point moves onto the new contract, rather than one
annual figure. The point it exists to make: in a portfolio whose supplies switch on
DIFFERENT dates, the headline annual saving is a RUN RATE that only applies once the
last supply is live, so the first twelve months deliver less than the Summary tab's
headline. This tab is the only place that gap is visible, and a client will notice
two different numbers — so the arithmetic behind it has to be right.

Guards:
  - the block is absent unless the operator ticked it AND there is a baseline
  - staggered starts ramp the monthly figure up, one step per switch date
  - first-year total < run rate when staggered, == when everything starts together
  - the term total is the cumulative of the last month, over the QUOTED term
  - an offer DEARER than the incumbent produces negative figures, so the template
    can flip the wording (the £-18,202 lesson from 17 Sep)
  - a supply with no start date is surfaced in notes, never silently dropped

Renders a staggered fixture and hands tests/_work/_timeline.html to
tests/dom_timeline.js for the behavioural half (skipped, not failed, without jsdom).

Headless, network-free. Run from the repo root: python3 tests/test_timeline.py
Prints 'ALL TIMELINE CHECKS PASSED' and exits 0 when green.
"""
import copy
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, ROOT)

import build_dashboard as bd

WORK = os.path.join(HERE, "_work")
FIXTURE = os.path.join(WORK, "_timeline.html")
EXAMPLE = os.path.join(ROOT, "schema", "examples", "tender.example.json")
FAILURES = []


def check(label, ok, extra=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  [{extra}]" if extra else ""))
    if not ok:
        FAILURES.append(label)


def tender(starts, term="24 months", dearer=False, show=True, benchmark=False):
    base = json.load(open(EXAMPLE))
    ps, pl, pi = base["sites"][0], base["quotes"][0]["lines"][0], base["incumbent"]["lines"][0]
    sites, lines, inc = [], [], []
    for k, st in enumerate(starts):
        mp = str(1200035438587 + k)
        sites.append(dict(ps, mpxn=mp, site_name=f"Site {k + 1}", eac=80000.0))
        lines.append(dict(pl, mpxn=mp, supplyStartDate=st))
        il = dict(pi, mpxn=mp)
        if dearer:
            il["unitRate"], il["standingCharge"] = 15.0, 10.0
        inc.append(il)
    t = copy.deepcopy(base)
    t["sites"] = sites
    t["quotes"] = [dict(base["quotes"][0], term=term, lines=lines)]
    t["incumbent"]["lines"] = inc
    if benchmark:
        t["incumbent"]["kind"] = "benchmark"
    t["recommended"] = {"supplier": base["quotes"][0]["supplier"], "term": term}
    if show:
        t["show_timeline"] = True
    return t


STAGGERED = ["2026-10-01", "2026-10-01", "2027-01-01", "2027-01-01",
             "2027-04-01", "2027-07-01", "2027-07-01"]


def test_gating():
    print("1) the block only appears when it is asked for AND measurable")
    check("absent when the operator did not tick it",
          bd.build_render_payload(tender(STAGGERED, show=False))["timeline"] is None)
    t = tender(STAGGERED)
    t.pop("incumbent")
    check("absent with no baseline to measure against",
          bd.build_render_payload(t)["timeline"] is None)
    check("present when ticked with a baseline",
          bd.build_render_payload(tender(STAGGERED))["timeline"] is not None)


def test_staggered():
    print("\n2) staggered starts — the run rate is NOT what lands in year one")
    tl = bd.build_render_payload(tender(STAGGERED))["timeline"]
    check("flagged as staggered", tl["staggered"] is True)
    check("one row per supply", len(tl["supplies"]) == 7, len(tl["supplies"]))
    check("months span the quoted term", len(tl["months"]) == 24 and tl["termMonths"] == 24)
    check("first month has only the first two supplies live", tl["months"][0]["live"] == 2,
          tl["months"][0]["live"])
    check("last month has them all", tl["months"][-1]["live"] == 7)
    check("monthly figure never goes backwards",
          all(b["monthly"] >= a["monthly"] - 0.01
              for a, b in zip(tl["months"], tl["months"][1:])))
    check("one step per distinct start date",
          len({m["monthly"] for m in tl["months"]}) == len({s["startDate"] for s in tl["supplies"]}),
          f'{len({m["monthly"] for m in tl["months"]})} steps')
    check("FIRST YEAR IS LESS THAN THE RUN RATE", tl["firstYear"] < tl["runRateAnnual"],
          f'{tl["firstYear"]:,.0f} vs {tl["runRateAnnual"]:,.0f}')
    check("run rate is the sum of every supply's annual saving",
          abs(tl["runRateAnnual"] - sum(s["annualSaving"] for s in tl["supplies"])) < 0.05)
    check("first year is the first twelve months' monthlies",
          abs(tl["firstYear"] - sum(m["monthly"] for m in tl["months"][:12])) < 0.05)
    check("term total is the last cumulative",
          abs(tl["totalOverTerm"] - tl["months"][-1]["cumulative"]) < 0.01)
    check("cumulative really is cumulative",
          abs(tl["months"][-1]["cumulative"] - sum(m["monthly"] for m in tl["months"])) < 0.05)
    check("allLiveFrom is the last switch date", tl["allLiveFrom"] == "2027-07-01",
          tl["allLiveFrom"])


def test_synchronised():
    print("\n3) everything starting together — no gap to explain")
    tl = bd.build_render_payload(tender(["2026-10-01"] * 3, "12 months"))["timeline"]
    check("not flagged as staggered", tl["staggered"] is False)
    check("first year equals the run rate", abs(tl["firstYear"] - tl["runRateAnnual"]) < 0.05,
          f'{tl["firstYear"]:,.2f}')
    check("every month is identical", len({m["monthly"] for m in tl["months"]}) == 1)


def test_increase():
    print("\n4) an offer DEARER than the incumbent stays negative for the template to flip")
    tl = bd.build_render_payload(
        tender(["2026-10-01", "2027-01-01", "2027-04-01"], dearer=True))["timeline"]
    check("run rate is negative", tl["runRateAnnual"] < 0, tl["runRateAnnual"])
    check("first year is negative", tl["firstYear"] < 0, tl["firstYear"])
    check("term total is negative", tl["totalOverTerm"] < 0, tl["totalOverTerm"])
    check("every month is negative", all(m["monthly"] < 0 for m in tl["months"]))


def test_missing_start():
    print("\n5) a supply with no start date is surfaced, not silently dropped")
    tl = bd.build_render_payload(
        tender(["2026-10-01", "", "2027-01-01"]))["timeline"]
    check("still counted as a supply", len(tl["supplies"]) == 3)
    check("a note explains it", any("no contract start date" in n for n in tl["notes"]),
          "; ".join(tl["notes"]))
    check("treated as live from the start of the term", tl["months"][0]["live"] == 2,
          tl["months"][0]["live"])


def test_rendered():
    print("\n6) rendered HTML — the tab is there and self-hides")
    html = bd.render_tender(tender(STAGGERED))
    os.makedirs(WORK, exist_ok=True)
    with open(FIXTURE, "w", encoding="utf-8") as fh:
        fh.write(html)
    check("timeline tab is offered", '"Savings timeline"' in html)
    check("pane is built", 'id="tab-timeline"' in html)
    check("two separate charts, never one with two y-axes",
          'id="tl-bars"' in html and 'id="tl-cum"' in html)
    check("wording and tone are derived from the sign",
          "function tlWord()" in html and "function tlTone()" in html)
    check("the flat monthly split is footnoted, not hidden",
          "spread evenly across the twelve months" in html)
    check("the injected data carries the block when ticked", '"timeline": {' in html)
    off = bd.render_tender(tender(STAGGERED, show=False))
    # The builder functions live in the template either way; what decides the tab
    # is the INJECTED DATA, so that is what to assert on.
    check("injected data has no timeline when not ticked", '"timeline": null' in off)


def test_dom():
    print("\n7) jsdom — tab, charts, schedule, tones (optional)")
    if not os.path.isdir(os.path.join(ROOT, "node_modules", "jsdom")):
        print("  SKIP  node_modules/jsdom not installed (npm i jsdom to run)")
        return
    r = subprocess.run(["node", os.path.join(HERE, "dom_timeline.js")],
                       cwd=ROOT, capture_output=True, text=True)
    for line in (r.stdout or "").splitlines():
        print("  " + line if not line.startswith(("  ", "-")) else line)
    if r.returncode == 2:
        print("  SKIP  fixture missing")
        return
    check("dom_timeline.js green", r.returncode == 0, (r.stderr or "").strip()[:200])


def main():
    test_gating(); test_staggered(); test_synchronised()
    test_increase(); test_missing_start(); test_rendered(); test_dom()
    if FAILURES:
        print(f"\n{len(FAILURES)} CHECK(S) FAILED")
        return 1
    print("\nALL TIMELINE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
