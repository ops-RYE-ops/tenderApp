#!/usr/bin/env python3
"""
Focused test for pipeline/assemble_tender.py — the /assemble step.

Covers the job the inline fixture in make_and_verify.py never exercised: merging
SEVERAL extract runs into one tender (dedupe sites on mpxn, concat quotes),
site-fact provenance, versioning, slug derivation, and schema validity. No
network, no API key. Exit 0 = all green.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

import assemble_tender as at

MPAN_A = "1200035438587"
MPAN_B = "1200035000000"
MPAN_C = "1200099999999"


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def extract_edf():
    """EDF file: sites A (quote EAC) + B, one 12-month offer."""
    return {
        "sites": [
            {"mpxn": MPAN_A, "site_name": "Head Office", "eac": 50000.0, "kva": None, "eac_source": "quote"},
            {"mpxn": MPAN_B, "site_name": "Warehouse 1", "eac": 120000.0, "kva": 100.0, "eac_source": "quote"},
        ],
        "quotes": [
            {"supplier": "EDF", "term": "12 months", "category": "fixed", "lines": [
                {"mpxn": MPAN_A, "unitRate": 24.5, "standingCharge": 45.2},
                {"mpxn": MPAN_B, "dayRate": 26.1, "nightRate": 18.3, "standingCharge": 60.0},
            ]},
        ],
        "_json_path": "/tmp/edf.extract.json",  # internal helper key — must be dropped
    }


def extract_drax():
    """Drax file: overlapping sites A+B plus a NEW site C, one 24-month offer.

    Site A here carries a DB-sourced name + kVA the EDF extract lacked, so the
    merge should prefer Drax's site_name/eac_source for A and fill A's kva.
    """
    return {
        "sites": [
            {"mpxn": MPAN_A, "site_name": "Head Office (RYE ref)", "eac": 50000.0, "kva": 69.0, "eac_source": "db"},
            {"mpxn": MPAN_B, "site_name": "Warehouse 1", "eac": 120000.0, "kva": 100.0, "eac_source": "quote"},
            {"mpxn": MPAN_C, "site_name": "Depot", "eac": 30000.0, "kva": None, "eac_source": "quote"},
        ],
        "quotes": [
            {"supplier": "Drax", "term": "24 months", "category": "fixed", "lines": [
                {"mpxn": MPAN_A, "unitRate": 23.9, "standingCharge": 45.2},
                {"mpxn": MPAN_B, "dayRate": 25.4, "nightRate": 17.8, "standingCharge": 60.0},
                {"mpxn": MPAN_C, "unitRate": 22.0, "standingCharge": 40.0},
            ]},
        ],
    }


def main():
    edf, drax = extract_edf(), extract_drax()

    print("1) merge two extracts into one tender")
    meta = {
        "client_name": "Amorino UK",
        "tender_label": "Electricity tender — July 2026",
        "created_by": "rory@rye.energy",
        "expires_at": "2026-07-31",
    }
    tender = at.assemble([edf, drax], meta)

    check(len(tender["quotes"]) == 2, "both offers concatenated (EDF 12m + Drax 24m)")
    check([q["supplier"] for q in tender["quotes"]] == ["EDF", "Drax"],
          "quote order preserved across extracts")
    check(len(tender["sites"]) == 3, "sites deduped on mpxn (A, B once each) + new C")
    check([s["mpxn"] for s in tender["sites"]] == [MPAN_A, MPAN_B, MPAN_C],
          "sites kept in first-seen order")

    print("2) site-fact provenance on merge")
    site_a = next(s for s in tender["sites"] if s["mpxn"] == MPAN_A)
    check(site_a["eac_source"] == "db", "higher-provenance (db) wins for a duplicated site")
    check(site_a["site_name"] == "Head Office (RYE ref)", "db record's site_name wins")
    check(site_a["kva"] == 69.0, "kva filled from the record that had it")
    check(site_a["eac"] == 50000.0, "eac unchanged (both agreed)")

    print("3) internal helper keys dropped")
    blob = json.dumps(tender)
    check("_json_path" not in blob, "extract's _json_path never leaks into the tender")

    print("4) meta defaults + slug derivation")
    check(tender["status"] == "draft" and tender["version"] == 1, "defaults: draft / v1")
    check(tender["utility"] == "electricity", "utility defaults to electricity")
    check(tender["slug"] == "amorino-uk", "slug derived from client_name")
    check(tender["id"] and tender["url_uuid"] and tender["id"] != tender["url_uuid"],
          "id and url_uuid both stamped and distinct")
    check(tender["created_at"].endswith("Z"), "created_at is an RFC3339 Z timestamp")
    check(tender["incumbent"] is None, "incumbent is null when not supplied")

    print("5) recommended is carried through, never computed")
    check("recommended" not in tender, "no recommended offer unless explicitly set")
    t2 = at.assemble([edf], {**meta, "recommended_supplier": "EDF",
                             "recommended_term": "12 months"})
    check(t2["recommended"] == {"supplier": "EDF", "term": "12 months"},
          "explicit recommended offer carried through")

    print("6) versioning: id stable, version increments, url_uuid rotatable")
    v2 = at.assemble([edf, drax], {**meta, "id": tender["id"],
                                   "version": 2, "url_uuid": tender["url_uuid"]})
    check(v2["id"] == tender["id"] and v2["version"] == 2,
          "re-save keeps id, bumps version (version-never-overwrite)")
    v2_rot = at.assemble([edf, drax], {**meta, "id": tender["id"], "version": 3})
    check(v2_rot["url_uuid"] != tender["url_uuid"],
          "omitting url_uuid mints a fresh one (link rotation / revoke)")

    print("7) full tender validates against the canonical schema")
    at.validate_tender(tender)
    at.validate_tender(t2)
    check(True, "assembled tenders are schema-valid")

    print("8) guards on bad input")
    for bad, why in [
        (lambda: at.assemble([], meta), "empty extract list rejected"),
        (lambda: at.assemble([edf], {"tender_label": "x"}), "missing client_name rejected"),
        (lambda: at.assemble([{"sites": [], "quotes": []}], meta), "no-quotes tender rejected"),
    ]:
        try:
            bad()
            raise AssertionError(f"expected failure: {why}")
        except (ValueError, Exception) as e:
            if isinstance(e, AssertionError) and "expected failure" in str(e):
                raise
            check(True, why)

    print("\nALL ASSEMBLE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
