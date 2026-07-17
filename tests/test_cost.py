#!/usr/bin/env python3
"""
test_cost.py — the /api/cost ranking endpoint.

Headless and network-free (no DB, no LLM). Proves the endpoint costs the
extracted offers with the EXISTING engine and ranks them so the assemble screen
can show "cheapest first, tick up to 2": the cheapest FULL-COVERAGE offer is
flagged, a partial-coverage offer is never the cheapest and sorts last, and the
standing splits are applied. Run from the repo root:

    python3 tests/test_cost.py

Prints 'ALL COST CHECKS PASSED' and exits 0 when green.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

FAILURES = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


MP_A = "1200000000001"
MP_B = "1200000000002"


def _extract():
    """Two full-coverage offers (Drax cheaper) + one partial (single site)."""
    return {
        "sites": [
            {"mpxn": MP_A, "site_name": "A", "eac": 100000.0, "kva": None, "eac_source": "quote"},
            {"mpxn": MP_B, "site_name": "B", "eac": 50000.0, "kva": None, "eac_source": "quote"},
        ],
        "quotes": [
            {"supplier": "EDF", "term": "12 months", "category": "fixed", "lines": [
                {"mpxn": MP_A, "unitRate": 25.0, "standingCharge": 40.0},
                {"mpxn": MP_B, "unitRate": 25.0, "standingCharge": 40.0}]},
            {"supplier": "Drax", "term": "24 months", "category": "fixed", "lines": [
                {"mpxn": MP_A, "unitRate": 22.0, "standingCharge": 40.0},
                {"mpxn": MP_B, "unitRate": 22.0, "standingCharge": 40.0}]},
            {"supplier": "Partial", "term": "12 months", "category": "fixed", "lines": [
                {"mpxn": MP_A, "unitRate": 10.0, "standingCharge": 10.0}]},  # only site A
        ],
    }


def main_test():
    client = TestClient(main.app)

    print("1) ranks offers by all-in cost, cheapest full-coverage flagged")
    r = client.post("/api/cost", data={"extracts": json.dumps([_extract()])})
    check("returns 200", r.status_code == 200)
    body = r.json()
    check("site_count = 2", body["site_count"] == 2)
    check("standing weekend split applied (2/7)", abs(body["weekend_split"] - 2 / 7) < 1e-9)

    offers = body["offers"]
    check("one row per offer", len(offers) == 3)
    by_supplier = {o["supplier"]: o for o in offers}
    check("Drax is the cheapest (full coverage)", by_supplier["Drax"]["cheapest"] is True)
    check("EDF is not flagged cheapest", by_supplier["EDF"]["cheapest"] is False)
    check("Drax cheaper than EDF", by_supplier["Drax"]["annual_cost"] < by_supplier["EDF"]["annual_cost"])
    check("effective p/kWh reported", isinstance(by_supplier["Drax"]["effective_pkwh"], (int, float)))

    print("2) a partial-coverage offer is not 'cheapest' despite low absolute cost")
    check("Partial flagged as not covering all sites", by_supplier["Partial"]["covers_all_sites"] is False)
    check("Partial is NOT the cheapest", by_supplier["Partial"]["cheapest"] is False)
    check("full-coverage offers sort ahead of partial",
          [o["supplier"] for o in offers][:2] == ["Drax", "EDF"] and offers[-1]["supplier"] == "Partial")

    print("3) input guards")
    check("empty extracts -> 400", client.post("/api/cost", data={"extracts": "[]"}).status_code == 400)
    check("bad JSON -> 400", client.post("/api/cost", data={"extracts": "not json"}).status_code == 400)

    print("4) a degenerate offer (no priced rows) -> clear 422, not a cryptic engine crash")
    empty_offer = {"sites": [{"mpxn": MP_A, "site_name": "A", "eac": 100000.0, "eac_source": "quote"}],
                   "quotes": [{"supplier": "Yu Energy", "term": "to 16 Aug 2027", "lines": []}]}
    r = client.post("/api/cost", data={"extracts": json.dumps([empty_offer])})
    check("no-lines offer -> 422", r.status_code == 422)
    check("message names the offer + points at the mapping",
          "no priced rows" in r.json()["detail"] and "Yu Energy" in r.json()["detail"])

    if FAILURES:
        print(f"\n{len(FAILURES)} CHECK(S) FAILED")
        return 1
    print("\nALL COST CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main_test())
