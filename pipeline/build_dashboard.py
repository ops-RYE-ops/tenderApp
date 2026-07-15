#!/usr/bin/env python3
"""
build_dashboard.py — deterministic tender-dashboard builder.

Reads a tender config (JSON) pointing at processed-quote CSVs (the output of
the quote-processing skill), computes standardised annual costs per site and
per offer, and injects the result into the RYE dashboard template to produce
a single self-contained client-facing HTML file.

All numbers in the dashboard come from this script — never hand-edit the HTML.

Usage:
    python3 build_dashboard.py TENDER_CONFIG.json OUTPUT.html [--template PATH]

CSV schema expected (quote-processing target schema):
    siteName, mpxn, updatedEac, supplyStartDate, unitRate, dayRate, nightRate,
    standingCharge, capacityCharge, networkCharge, meterCharge, kva

Tender config format: see examples/tender-example.json and SKILL.md.
"""

import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

# Shared with the extractor so the two never diverge on schema or parsing.
from rye_quote_core import TARGET_FIELDS, parse_num

# Default annualisation basis for each charge. Override per tender (or per
# quote) via "charge_basis" in the config when the supplier quotes in
# different units — check the source quote's headers for unit hints.
DEFAULT_BASIS = {
    "standingCharge": "p/day",
    "capacityCharge": "p/kva/day",
    "networkCharge": "p/day",
    "meterCharge": "p/day",
}

VALID_BASIS = {"p/day", "p/kwh", "p/kva/day", "p/mpan/day", "gbp/year", "gbp/month", "gbp/day"}

COMPONENT_LABELS = [
    ("energy", "Energy"),
    ("standing", "Standing charge"),
    ("capacity", "Capacity"),
    ("network", "Network"),
    ("meter", "Metering"),
]


def load_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"ERROR: {path} contains no data rows")
    missing = [c for c in ("mpxn", "updatedEac") if c not in rows[0]]
    if missing:
        raise SystemExit(
            f"ERROR: {path} is missing column(s) {missing} — expected the "
            f"quote-processing target schema: {', '.join(TARGET_FIELDS)}"
        )
    return rows


def annualise(value, basis, eac, kva, warnings, charge_name, site):
    """Convert a quoted charge into £/year according to its declared basis."""
    if value is None:
        return 0.0
    if basis in ("p/day", "p/mpan/day"):
        return value * 365 / 100
    if basis == "p/kwh":
        return value * (eac or 0) / 100
    if basis == "p/kva/day":
        if not kva:
            warnings.add(
                f"{charge_name} quoted per kVA but no kVA figure for "
                f"'{site}' — excluded from that site's total"
            )
            return 0.0
        return value * kva * 365 / 100
    if basis == "gbp/day":
        return value * 365
    if basis == "gbp/year":
        return value
    if basis == "gbp/month":
        return value * 12
    raise SystemExit(f"ERROR: unknown charge basis '{basis}' (valid: {sorted(VALID_BASIS)})")



def offer_category(entry):
    """Classify an offer as 'coterminous' or 'fixed' for the dashboard filter.

    Uses an explicit per-quote "category" if given (any label is allowed), else
    infers from the term wording: bespoke / aligned / 'fixed to <date>' offers
    are treated as coterminous; '<n> months' offers as fixed-length. Returns ""
    when it can't tell (the offer then only shows under the 'All' filter).
    """
    c = (entry.get("category") or "").strip().lower()
    if c in ("coterminous", "cot", "co-terminous", "coterminus", "aligned"):
        return "coterminous"
    if c in ("fixed", "fixed-length", "fixed length", "fixed-term", "fixed term", "standard"):
        return "fixed"
    if c:
        return c  # allow bespoke custom categories
    t = (entry.get("term") or "").lower()
    if any(k in t for k in ("bespoke", "coterm", "co-term", "aligned", "fixed to")):
        return "coterminous"
    if re.search(r"\d+\s*month", t):
        return "fixed"
    return ""


def compute_offer(entry, tender, incumbent=False):
    csv_path = entry["_csv_path"]
    rows = load_csv(csv_path)
    day_split = float(tender.get("day_split", 0.7))
    basis = {**DEFAULT_BASIS, **tender.get("charge_basis", {}), **entry.get("charge_basis", {})}
    for b in basis.values():
        if b not in VALID_BASIS:
            raise SystemExit(f"ERROR: unknown charge basis '{b}' (valid: {sorted(VALID_BASIS)})")

    warnings = set()
    sites = []
    for row in rows:
        name = (row.get("siteName") or "").strip() or (row.get("mpxn") or "").strip()
        mpxn = (row.get("mpxn") or "").strip()
        eac = parse_num(row.get("updatedEac"))
        kva = parse_num(row.get("kva"))
        unit, day, night = (parse_num(row.get(k)) for k in ("unitRate", "dayRate", "nightRate"))

        if eac is None:
            warnings.add(f"No EAC for '{name}' — energy cost treated as £0")
            eac = 0

        # Energy: single-rate wins if present, else day/night with the split.
        if unit is not None:
            energy = unit * eac / 100
            split_used = False
        elif day is not None or night is not None:
            d = day if day is not None else night
            n = night if night is not None else day
            energy = (d * eac * day_split + n * eac * (1 - day_split)) / 100
            split_used = True
        else:
            warnings.add(f"No unit/day/night rate for '{name}' — energy cost treated as £0")
            energy, split_used = 0.0, False

        costs = {
            "energy": energy,
            "standing": annualise(parse_num(row.get("standingCharge")), basis["standingCharge"], eac, kva, warnings, "Standing charge", name),
            "capacity": annualise(parse_num(row.get("capacityCharge")), basis["capacityCharge"], eac, kva, warnings, "Capacity charge", name),
            "network": annualise(parse_num(row.get("networkCharge")), basis["networkCharge"], eac, kva, warnings, "Network charge", name),
            "meter": annualise(parse_num(row.get("meterCharge")), basis["meterCharge"], eac, kva, warnings, "Meter charge", name),
        }
        sites.append({
            "name": name,
            "mpxn": mpxn,
            "eac": eac,
            "kva": kva,
            "splitUsed": split_used,
            "rates": {k: parse_num(row.get(k)) for k in
                      ("unitRate", "dayRate", "nightRate", "standingCharge",
                       "capacityCharge", "networkCharge", "meterCharge")},
            "startDate": (row.get("supplyStartDate") or "").strip(),
            "costs": {k: round(v, 2) for k, v in costs.items()},
            "total": round(sum(costs.values()), 2),
        })

    totals = {k: round(sum(s["costs"][k] for s in sites), 2) for k, _ in COMPONENT_LABELS}
    total = round(sum(totals.values()), 2)
    eac_total = sum(s["eac"] or 0 for s in sites)
    non_comm = sum(totals[k] for k in ("standing", "capacity", "network", "meter"))
    # Consumption-weighted means, standardised to p/kWh — the client-facing
    # comparison currency (mirrors RYE's forecasted-savings sheet).
    per_kwh = {
        "unit": round(totals["energy"] * 100 / eac_total, 2) if eac_total else None,
        "nonCommodity": round(non_comm * 100 / eac_total, 2) if eac_total else None,
        "effective": round(total * 100 / eac_total, 2) if eac_total else None,
    }
    return {
        "id": entry["_id"],
        "supplier": entry["supplier"],
        "term": entry.get("term", ""),
        "category": offer_category(entry),
        "isIncumbent": incumbent,
        "sites": sites,
        "totals": totals,
        "total": total,
        "eac": eac_total,
        "perKwh": per_kwh,
        "warnings": sorted(warnings),
        "chargeBasis": basis,
    }


def main(argv):
    # Parse positionals while consuming the value that follows --template,
    # so a template PATH is not mistaken for a positional argument.
    template_path = None
    args = []
    skip = False
    for i, a in enumerate(argv):
        if skip:
            skip = False
            continue
        if a == "--template":
            if i + 1 < len(argv):
                template_path = Path(argv[i + 1])
                skip = True
            continue
        if a.startswith("--"):
            continue
        args.append(a)
    if len(args) != 2:
        print(__doc__)
        raise SystemExit(1)
    config_path, out_path = Path(args[0]), Path(args[1])
    if template_path is None:
        template_path = Path(__file__).resolve().parent.parent / "assets" / "dashboard_template.html"

    tender = json.loads(config_path.read_text(encoding="utf-8"))
    base = config_path.resolve().parent

    def resolve(p):
        q = Path(p)
        return q if q.is_absolute() else (base / q)

    if not tender.get("quotes"):
        raise SystemExit("ERROR: tender config has no 'quotes' entries")

    offers = []
    seen_ids = set()
    for q in tender["quotes"]:
        q["_csv_path"] = resolve(q["csv"])
        slug = re.sub(r"[^a-z0-9]+", "-", f"{q['supplier']}-{q.get('term','')}".lower()).strip("-")
        while slug in seen_ids:
            slug += "-2"
        seen_ids.add(slug)
        q["_id"] = slug
        offers.append(compute_offer(q, tender))

    incumbent = None
    if tender.get("incumbent"):
        inc = tender["incumbent"]
        inc["_csv_path"] = resolve(inc["csv"])
        inc["_id"] = "incumbent"
        inc.setdefault("supplier", "Current contract")
        inc.setdefault("term", "current")
        incumbent = compute_offer(inc, tender, incumbent=True)

    # --- Site universe & comparability -------------------------------------
    site_index = {}  # mpxn -> {name, eac}
    for off in ([incumbent] if incumbent else []) + offers:
        for s in off["sites"]:
            site_index.setdefault(s["mpxn"], {"mpxn": s["mpxn"], "name": s["name"], "eac": s["eac"]})
    all_mpxns = set(site_index)

    global_warnings = []
    for off in offers + ([incumbent] if incumbent else []):
        missing = all_mpxns - {s["mpxn"] for s in off["sites"]}
        if missing:
            names = ", ".join(sorted(site_index[m]["name"] for m in missing))
            off["warnings"].append(f"Does not cover: {names}")
            global_warnings.append(
                f"{off['supplier']} ({off['term']}) does not cover all sites — "
                "totals are not directly comparable"
            )

    full_cover = [o for o in offers if len(o["sites"]) == len(all_mpxns)] or offers
    best = min(full_cover, key=lambda o: o["total"])
    for off in offers + ([incumbent] if incumbent else []):
        off["deltaVsIncumbent"] = round(off["total"] - incumbent["total"], 2) if incumbent else None

    # The recommendation is the story of the dashboard. Default: cheapest
    # full-coverage offer. Override via "recommended" when the call isn't purely
    # on price (e.g. term certainty, credit, green tariff).
    rec = best
    if tender.get("recommended"):
        r = tender["recommended"]
        matches = [o for o in offers
                   if o["supplier"].lower() == str(r.get("supplier", "")).lower()
                   and (not r.get("term") or str(r["term"]).lower() in o["term"].lower())]
        if not matches:
            raise SystemExit("ERROR: 'recommended' does not match any quote (check supplier/term)")
        rec = matches[0]

    # Optional RYE flat-fee block -> net saving after fees (no commission).
    # List price defaults to £90/site/month; discount_pct sets the starting
    # position of the dashboard's adjustable fee control.
    # The fee renders whenever it's configured — with an incumbent it also shows
    # the net saving after fee; without one it stands alone as a fee quote (site
    # count x per-site fee, adjustable via the dashboard's discount slider).
    fee = None
    if tender.get("rye_fee"):
        rf = tender["rye_fee"]
        n = len(all_mpxns)
        list_price = rf.get("list_price_site_month", 90.0)
        if rf.get("annual"):
            annual = rf["annual"]
            psm = round(annual / (12 * n), 2)
        else:
            psm = rf.get("per_site_month")
            if psm is None:
                psm = round(list_price * (1 - rf.get("discount_pct", 0) / 100), 2)
            annual = psm * n * 12
        # Net saving only exists when there's an incumbent baseline to net against.
        gross = incumbent["total"] - rec["total"] if incumbent else None
        fee = {
            "label": rf.get("label", "RYE fee"),
            "listPerSiteMonth": list_price,
            "perSiteMonth": psm,
            "discountPct": round((1 - psm / list_price) * 100) if list_price else 0,
            "annual": round(annual, 2),
            "netSaving": round(gross - annual, 2) if gross is not None else None,
            "netSavingPerSite": round((gross - annual) / n, 2) if gross is not None else None,
        }

    # --- Assumption footnotes (auto-built, always disclosed) ----------------
    assumptions = []
    if any(s["splitUsed"] for o in offers + ([incumbent] if incumbent else []) for s in o["sites"]):
        pct = round(float(tender.get("day_split", 0.7)) * 100)
        assumptions.append(
            f"Two-rate meters: annual consumption split {pct}% day / {100 - pct}% night. "
            "Adjustable — tell us if you have half-hourly data."
        )
    assumptions.append("Daily charges annualised over 365 days.")
    basis_used = {**DEFAULT_BASIS, **tender.get("charge_basis", {})}
    non_default = {k: v for k, v in basis_used.items() if v != DEFAULT_BASIS.get(k)}
    if non_default:
        assumptions.append(
            "Charge bases: " + "; ".join(f"{k} treated as {v}" for k, v in non_default.items()) + "."
        )
    assumptions.append(
        "Mean rates are consumption-weighted. Non-commodity covers standing, capacity, "
        "network and metering charges, standardised to p/kWh against current consumption."
    )
    # (Fee footnote is rendered live by the template so it always matches the
    # fee control's current value.)
    assumptions.append(
        "All figures are annual estimates from quoted rates and current estimated "
        "consumption (EAC/AQ), excluding VAT and CCL. Actual billing will vary with usage."
    )
    assumptions.extend(tender.get("notes", []))

    payload = {
        "client": tender.get("client_name", "Client"),
        "label": tender.get("tender_label", "Tender comparison"),
        "utility": tender.get("utility", "electricity"),
        "generated": date.today().isoformat(),
        "daySplitPct": round(float(tender.get("day_split", 0.7)) * 100),
        "sites": [site_index[m] for m in sorted(all_mpxns, key=lambda m: site_index[m]["name"])],
        "offers": offers,
        "incumbent": incumbent,
        "bestId": best["id"],
        "recommendedId": rec["id"],
        "fee": fee,
        "components": [
            {"key": k, "label": lbl} for k, lbl in COMPONENT_LABELS
            if any(o["totals"][k] for o in offers + ([incumbent] if incumbent else []))
        ],
        "assumptions": assumptions,
        "globalWarnings": sorted(set(global_warnings)),
    }

    template = template_path.read_text(encoding="utf-8")
    if "__TENDER_DATA__" not in template:
        raise SystemExit(f"ERROR: template {template_path} has no __TENDER_DATA__ placeholder")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(template.replace("__TENDER_DATA__", json.dumps(payload)), encoding="utf-8")

    # --- Verification summary (for the operator, not the client) ------------
    print(f"Dashboard written: {out_path}")
    print(f"Sites: {len(all_mpxns)}   Offers: {len(offers)}"
          + (f"   Incumbent: {incumbent['supplier']}" if incumbent else "   Incumbent: none"))
    print(f"{'OFFER':<38}{'TOTAL £/yr':>12}{'eff p/kWh':>11}{'vs current':>13}")
    rows = ([incumbent] if incumbent else []) + sorted(offers, key=lambda o: o["total"])
    for o in rows:
        delta = "" if o["deltaVsIncumbent"] is None or o["isIncumbent"] else f"{o['deltaVsIncumbent']:+,.0f}"
        tag = "  <- recommended" if o["id"] == rec["id"] else (" (current)" if o["isIncumbent"] else "")
        eff = o["perKwh"]["effective"]
        print(f"{(o['supplier'] + ' ' + o['term']):<38}{o['total']:>12,.0f}{(f'{eff:.2f}' if eff else '—'):>11}{delta:>13}{tag}")
    if fee and fee["netSaving"] is not None:
        print(f"Net saving after {fee['label']} (£{fee['annual']:,.0f}/yr): £{fee['netSaving']:,.0f}"
              f"  (£{fee['netSavingPerSite']:,.0f}/site)")
    elif fee:
        print(f"RYE fee {fee['label']} (no incumbent baseline): "
              f"£{fee['perSiteMonth']:,.2f}/site/month = £{fee['annual']:,.0f}/yr")
    for o in rows:
        for w in o["warnings"]:
            print(f"WARNING [{o['supplier']} {o['term']}]: {w}")
    print("\nSpot-check at least 2 site costs against the source CSVs before sending to the client.")


if __name__ == "__main__":
    main(sys.argv[1:])
