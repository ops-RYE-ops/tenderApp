#!/usr/bin/env python3
"""
test_fee_net_sign.py — the SINGLE-FUEL fee card's net line, when the fee turns a
result negative.

Guards a bug found on a live client dashboard (Public House Group, 17 Sep 2026),
where the recommendation card read:

    FORECASTED ANNUAL INCREASE   £10,642        <- correct
    NET SAVING AFTER RYE FEE     £-18,202       <- two things wrong

  1. the LABEL never flipped — it said "saving" over an increase. saveWord() was
     written for the combined-tender card in Sep 2026 and never back-ported here,
     so the gross line flipped correctly and the fee net line below it did not;
  2. gbp() built "£" + a negative number, putting the minus INSIDE the currency
     ("£-18,202"). Every other call site happened to pass Math.abs first, so it
     never showed until a tender went negative WITH a fee on it.

gbp() is now hardened to emit "−£18,202" whatever it is handed, and the fee net
line derives its wording. Both the initial render and the live fee slider are
covered, because dragging the slider re-wrote the value and would have restored a
stale label.

Renders a fixture where the offer is DEARER than the incumbent and a flat fee is
charged, asserts the static markers here, then hands tests/_work/_fee_net_sign.html
to tests/dom_fee_net_sign.js for the behavioural half (skipped, not failed, when
node/jsdom aren't installed).

Headless, network-free. Run from the repo root: python3 tests/test_fee_net_sign.py
Prints 'ALL FEE-NET-SIGN CHECKS PASSED' and exits 0 when green.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, ROOT)

import build_dashboard as bd

WORK = os.path.join(HERE, "_work")
FIXTURE = os.path.join(WORK, "_fee_net_sign.html")
FAILURES = []


def check(label, ok, extra=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  [{extra}]" if extra else ""))
    if not ok:
        FAILURES.append(label)


def _tender(n=7):
    """One fuel, n supply points, offer DEARER than incumbent, flat fee charged."""
    sites, lines, inc_lines = [], [], []
    for k in range(n):
        mp = str(1200000000000 + k)
        sites.append({"mpxn": mp, "site_name": f"Site {k + 1}", "eac": 120000.0,
                      "kva": None, "eac_source": "quote"})
        lines.append({"mpxn": mp, "unitRate": 22.51, "standingCharge": 60.0,
                      "networkCharge": 12.0, "meterCharge": 5.0})
        inc_lines.append({"mpxn": mp, "unitRate": 22.03, "standingCharge": 40.0})
    return {
        "id": "fee-net-sign", "version": 1, "status": "draft",
        "client_name": "Testco", "tender_label": "Fee net sign",
        "utility": "electricity", "sites": sites,
        "quotes": [{"supplier": "Capture Energy", "term": "12 months",
                    "category": "fixed", "featured": True, "lines": lines}],
        "incumbent": {"supplier": "Various", "term": "current", "lines": inc_lines},
        "recommended": {"supplier": "Capture Energy", "term": "12 months"},
        "rye_fee": {"list_price_site_month": 90, "discount_pct": 0},
    }


def test_engine():
    print("1) engine — the fixture really is a net increase")
    p = bd.build_render_payload(_tender())
    rec = next(o for o in p["offers"] if o["id"] == p["recommendedId"])
    gross = p["incumbent"]["total"] - rec["total"]
    check("gross is an increase (offer dearer than incumbent)", gross < 0, round(gross, 2))
    check("fee is charged on every supply point", p["fee"]["annual"] == 90 * 7 * 12,
          p["fee"]["annual"])
    check("net saving is negative", p["fee"]["netSaving"] < 0, p["fee"]["netSaving"])
    check("net per supply is negative", p["fee"]["netSavingPerSite"] < 0,
          p["fee"]["netSavingPerSite"])


def test_rendered_markers():
    print("2) rendered HTML — no '£-', and the wording is derived")
    html = bd.render_tender(_tender())
    os.makedirs(WORK, exist_ok=True)
    with open(FIXTURE, "w", encoding="utf-8") as fh:
        fh.write(html)
    check("gbp() never builds a minus inside the currency",
          'const gbp = v => (v < 0 ? "−£" : "£")' in html)
    check("the fee net LABEL is derived, not hardcoded 'Net saving after'",
          "Net saving after ${esc(fee.label)}" not in html
          and "Net ${saveWord(fee.netSaving)} after" in html)
    check("the fee net VALUE is absolute (the label carries the direction)",
          "${gbp(Math.abs(fee.netSaving))}" in html)
    check("net per supply is absolute too",
          "${gbp(Math.abs(fee.netSavingPerSite))}" in html)
    check("the fee net tone is derived", "${saveTone(fee.netSaving)}" in html)
    check("the live slider re-derives the label, not just the number",
          'el("net-saving-label").textContent = `Net ${saveWord(net)} after' in html)
    check("no literal '£-' anywhere in the rendered page", "£-" not in html)


def test_dom_behaviour():
    print("3) jsdom — the label, the tone and the slider (optional)")
    if not os.path.isdir(os.path.join(ROOT, "node_modules", "jsdom")):
        print("  SKIP  node_modules/jsdom not installed (npm i jsdom to run)")
        return
    r = subprocess.run(["node", os.path.join(HERE, "dom_fee_net_sign.js")],
                       cwd=ROOT, capture_output=True, text=True)
    for line in (r.stdout or "").splitlines():
        print("  " + line if not line.startswith(("  ", "-")) else line)
    if r.returncode == 2:
        print("  SKIP  fixture missing")
        return
    check("dom_fee_net_sign.js green", r.returncode == 0,
          (r.stderr or "").strip()[:200])


def main():
    test_engine()
    test_rendered_markers()
    test_dom_behaviour()
    if FAILURES:
        print(f"\n{len(FAILURES)} CHECK(S) FAILED")
        return 1
    print("\nALL FEE-NET-SIGN CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
