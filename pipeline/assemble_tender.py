#!/usr/bin/env python3
"""
assemble_tender.py — the /assemble step, as real code.

Takes one or more `extractResult` documents (each the output of ONE
process_quote.py run over ONE supplier file) plus the incumbent contract and the
tender meta, and stitches them into a single valid canonical tender JSON
document (schema/tender.schema.json). This is the step that turns several
single-supplier extracts into the one comparable object the cost engine and the
dashboard read.

What it does, and — just as important — what it does NOT do:

  * It MERGES sites (deduped on mpxn), CONCATENATES quotes, ATTACHES the
    incumbent, and STAMPS the meta (id / version / status / timestamps / link
    fields). That is all structural.
  * It NEVER touches a rate, EAC, kVA or meter point value. Those are moved
    verbatim by the deterministic extractor and passed straight through here.
    "AI maps, code moves numbers" — assemble doesn't even move numbers, it only
    arranges the objects the extractor already produced. So there is no place
    for a value to change during assembly.

Design decisions carried from the schema / handover:

  * EAC and kVA live on sites[], once. When the same mpxn appears in more than
    one extract, its site facts are merged with a provenance preference
    (db > manual > quote) and null-filling, so a supplier-restated EAC can never
    silently override RYE's figure. See _merge_site.
  * Version, never overwrite. `version` defaults to 1; a re-save passes the
    prior id + version+1. `id` stays constant across versions; `url_uuid` can be
    rotated independently to revoke a leaked link.
  * `recommended` is NOT computed here. Choosing the lead offer needs the cost
    engine (cheapest full-coverage) or a human call; the team sets it at the
    assemble step. Assemble only carries it through if given.

Kept as importable functions (assemble / merge_sites / merge_quotes) so the
Vercel /assemble endpoint can `import assemble_tender` and call it directly,
matching the spec's "functions import the scripts, never paraphrase them"
principle. A thin CLI is provided for headless use.

Usage:
    python3 assemble_tender.py \
        --client "Amorino UK" \
        --label  "Electricity tender — July 2026" \
        --extract edf.extract.json --extract drax.extract.json \
        [--incumbent incumbent.json] \
        [--out tender.json] \
        [--utility electricity] [--status draft] [--version 1] \
        [--id UUID] [--url-uuid UUID] [--slug amorino-uk] \
        [--expires-at 2026-07-31] [--day-split 0.7] \
        [--created-by rory@rye.energy] \
        [--recommended-supplier EDF] [--recommended-term "12 months"] \
        [--fee-list-price 90] [--fee-discount 80] [--fee-label "..."] \
        [--note "..."] [--note "..."]

The incumbent JSON is an object matching the schema's #/$defs/incumbent
({ "supplier": ..., "lines": [...] }); pass none for an unknown incumbent.
"""
import argparse
import csv
import datetime
import json
import os
import re
import sys
import uuid

from rye_quote_core import (
    DAY_SPLIT_DEFAULT, WEEKEND_SPLIT_DEFAULT, parse_num,
    normalise_fuel, infer_fuel_from_mpxn,
)

# Provenance ranking for site facts: prefer RYE reference data, then an operator
# figure, then whatever the supplier stated. Higher wins on conflict.
_EAC_SOURCE_RANK = {"db": 3, "manual": 2, "quote": 1}

# The incumbent line's rate fields — mirrors the schema #/$defs/line rate props
# (and process_quote.LINE_RATE_FIELDS). A test asserts they stay in sync.
_INCUMBENT_RATE_FIELDS = [
    "unitRate", "dayRate", "nightRate", "weekendRate", "standingCharge",
    "capacityCharge", "networkCharge", "meterCharge",
]


def _norm_mpxn(v):
    """Normalise a meter point to a bare string key (drops a stray trailing .0)."""
    s = str(v or "").strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def distinct_fuels(tender):
    """Canonical fuels present in a tender ('electricity'/'gas'), for the mixed-fuel guard.

    Reads the explicit per-line/site `fuel` first; where absent, infers from the
    meter-point format (MPAN vs MPRN) so a mixed tender is caught even when no Fuel
    column was mapped. len(result) > 1 means gas and electricity share one tender,
    which cannot be costed together.
    """
    fuels = set()
    for site in tender.get("sites", []):
        f = normalise_fuel(site.get("fuel")) or infer_fuel_from_mpxn(site.get("mpxn"))
        if f:
            fuels.add(f)
    for q in tender.get("quotes", []):
        for ln in q.get("lines", []):
            f = normalise_fuel(ln.get("fuel")) or infer_fuel_from_mpxn(ln.get("mpxn"))
            if f:
                fuels.add(f)
    return fuels


def mixed_fuel_detail(fuels):
    """Client-safe refusal message for a tender that mixes fuels (the step-one guard)."""
    listed = " and ".join(sorted(fuels)) or "more than one fuel"
    return (
        f"This tender mixes {listed}. Electricity and gas can't be compared in one "
        "tender -- gas unit rates are far lower, so a blended rate and saving would be "
        "meaningless. Put each fuel in its own tender for now (combined gas + "
        "electricity tenders are coming)."
    )


def incumbent_from_sites_csv(path, client_name=None, mpxns=None, dbl=None):
    """Build the tender `incumbent` block from RYE's sites.csv.

    The same export that feeds site reference at /extract carries the client's
    current contract in its rate columns; this reads them into an incumbent block,
    keyed on MPAN. A row becomes an incumbent line only if it has at least one
    non-null rate — pure site-reference rows (name/EAC only, no rates) are skipped,
    so a sites.csv with no incumbent data yields None (an unknown incumbent, which
    the schema allows).

    Optionally restricts to a client (clientName column) and/or the tender's meter
    points (`mpxns`). Supplier follows RYE's rule: the single distinct
    incumbentSupplier if there's exactly one, 'Various' if several, 'Unknown' if
    lines exist but none name a supplier. Values move through the shared parse_num
    — code moves numbers, same as everywhere else.
    """
    dbl = dbl or {}
    mpxn_col = dbl.get("mpxn_col", "mpxn")
    client_col = dbl.get("client_col", "clientName")
    supplier_col = dbl.get("incumbent_supplier_col", "incumbentSupplier")
    want = {_norm_mpxn(m) for m in mpxns} if mpxns is not None else None

    lines, suppliers = [], set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        if mpxn_col not in fields:
            raise SystemExit(f"sites.csv must contain a '{mpxn_col}' column. Found: {fields}")
        for r in reader:
            if client_name and client_col in fields:
                if (r.get(client_col) or "").strip().lower() != client_name.strip().lower():
                    continue
            m = _norm_mpxn(r.get(mpxn_col))
            if not m or (want is not None and m not in want):
                continue
            rates = {f: parse_num(r.get(f)) for f in _INCUMBENT_RATE_FIELDS}
            if not any(v is not None for v in rates.values()):
                continue  # no incumbent data on this row — a site-reference-only row
            line = {"mpxn": m}
            sd = (r.get("supplyStartDate") or "").strip()
            if sd:
                line["supplyStartDate"] = sd
            line.update(rates)
            lines.append(line)
            sup = (r.get(supplier_col) or "").strip() if supplier_col in fields else ""
            if sup:
                suppliers.add(sup)

    if not lines:
        return None
    if len(suppliers) == 1:
        supplier = next(iter(suppliers))
    elif len(suppliers) > 1:
        supplier = "Various"
    else:
        supplier = "Unknown"
    return {"supplier": supplier, "lines": lines}


def diagnose_sites_csv(path, client_name=None, mpxns=None, dbl=None):
    """Explain why a sites.csv yielded no incumbent — for an actionable warning.

    Read-only companion to incumbent_from_sites_csv (never raises on a normal file).
    The common footgun is a client-name mismatch: the tender's client name doesn't
    equal the sites.csv `clientName` column, so every row is filtered out silently.
    Returns counts to turn that into a clear message:
      total_rows      — data rows in the file
      clients         — sorted distinct clientName values seen
      client_matches  — rows whose clientName matches client_name (all rows if the
                        file has no clientName column, or no client_name was given)
      mpxn_matches    — of client_matches, rows whose mpxn is in the tender
      rate_rows       — of mpxn_matches, rows carrying at least one incumbent rate
    """
    dbl = dbl or {}
    mpxn_col = dbl.get("mpxn_col", "mpxn")
    client_col = dbl.get("client_col", "clientName")
    want = {_norm_mpxn(m) for m in mpxns} if mpxns is not None else None
    total_rows = client_matches = mpxn_matches = rate_rows = 0
    clients = set()
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            has_client = client_col in fields
            for r in reader:
                if not any((v or "").strip() for v in r.values()):
                    continue  # skip fully blank rows
                total_rows += 1
                cval = (r.get(client_col) or "").strip() if has_client else ""
                if cval:
                    clients.add(cval)
                if client_name and has_client:
                    if cval.lower() != client_name.strip().lower():
                        continue
                client_matches += 1
                m = _norm_mpxn(r.get(mpxn_col))
                if not m or (want is not None and m not in want):
                    continue
                mpxn_matches += 1
                if any(parse_num(r.get(fld)) is not None for fld in _INCUMBENT_RATE_FIELDS):
                    rate_rows += 1
    except (OSError, csv.Error):
        pass
    return {
        "total_rows": total_rows,
        "clients": sorted(clients),
        "client_matches": client_matches,
        "mpxn_matches": mpxn_matches,
        "rate_rows": rate_rows,
    }


BENCHMARK_SUPPLIER = "Market benchmark"


def incumbent_from_benchmark(mpxns, unit_rate, standing_charge=None,
                             as_at=None, source=None, supplier=None,
                             gas_unit_rate=None, gas_standing_charge=None,
                             fuel_of=None):
    """Build a BENCHMARK baseline block from one operator-entered rate card.

    For the common-enough case where RYE has no incumbent rates for a client but
    still needs to show a saving: the operator types a market-average unit rate
    (and optionally a standing charge), and that single card is applied to every
    meter in the tender. The result is the same shape as
    `incumbent_from_sites_csv`, so it flows through the cost engine, the
    comparison table and the savings maths untouched — the only difference is
    `kind: "benchmark"`, which the dashboard uses to label the baseline honestly
    and to invite the client to send their real rates.

    Deliberately coarse: two numbers, not a full rate card. A benchmark is
    indicative, and inventing an 'average' capacity or network charge per meter
    would dress up a guess as precision. Non-commodity costs are broadly similar
    between suppliers, so they largely cancel out of the saving anyway.

    Returns None when no usable unit rate was given (mirrors
    incumbent_from_sites_csv returning None for an unknown incumbent).
    """
    ur = parse_num(unit_rate)
    sc = parse_num(standing_charge)
    gur = parse_num(gas_unit_rate)
    gsc = parse_num(gas_standing_charge)

    keys = sorted({_norm_mpxn(m) for m in (mpxns or []) if _norm_mpxn(m)})
    if not keys:
        return None

    # Each meter is benchmarked at its OWN fuel's rate: electricity meters take the
    # electricity rate, gas meters the gas rate. In a single-fuel tender either
    # field applies to that fuel. In a combined tender a fuel with no rate gets no
    # benchmark line at all (better than benchmarking gas at an electricity rate).
    fmap = {_norm_mpxn(k): v for k, v in (fuel_of or {}).items() if _norm_mpxn(k)}

    def _fuel(m):
        return fmap.get(m) or infer_fuel_from_mpxn(m) or "electricity"

    single_fuel = len({_fuel(m) for m in keys}) == 1

    lines = []
    for m in keys:
        if _fuel(m) == "gas":
            u = gur if gur is not None else (ur if single_fuel else None)
            sch = gsc if gsc is not None else (sc if single_fuel else None)
        else:
            u = ur if ur is not None else (gur if single_fuel else None)
            sch = sc if sc is not None else (gsc if single_fuel else None)
        if u is None:
            continue
        line = {"mpxn": m}
        line.update({f: None for f in _INCUMBENT_RATE_FIELDS})
        line["unitRate"] = u
        if sch is not None:
            line["standingCharge"] = sch
        lines.append(line)

    if not lines:
        return None

    block = {
        "supplier": (supplier or "").strip() or BENCHMARK_SUPPLIER,
        "term": "benchmark",
        "kind": "benchmark",
        "lines": lines,
    }
    if as_at:
        block["as_at"] = str(as_at).strip()
    if source:
        block["source"] = str(source).strip()
    return block


def apply_site_reference(sites, csv_path, dbl=None):
    """Overlay RYE's sites.csv (site name + authoritative EAC/kVA) onto merged sites.

    Makes the site reference authoritative at ASSEMBLE time, not just at /extract:
    RYE's site name and consumption win even when the sites.csv wasn't supplied when
    the quote was first extracted (e.g. it was added later, or the extract ran
    first). Idempotent with /extract's own override — re-applying the same file is a
    no-op. Same rules as process_quote: the site name always wins; EAC and kVA, where
    the export provides them, override the quote and stamp `eac_source: "db"`.

    Mutates `sites` in place and returns the number of sites matched. Reuses
    `process_quote.build_site_lookup` so the column contract + MPAN keying stay
    identical to the extract path (one definition, no drift).
    """
    from process_quote import build_site_lookup, clean_id

    lookup = build_site_lookup(csv_path, dbl or {})
    matched = 0
    for s in sites:
        db = lookup.get(clean_id(s.get("mpxn")))
        if not db:
            continue
        matched += 1
        if db.get("site_name"):
            s["site_name"] = db["site_name"]
        if db.get("eac") is not None:
            s["eac"] = db["eac"]
            s["eac_source"] = "db"
        if db.get("kva") is not None:
            s["kva"] = db["kva"]
    return matched


def _now_rfc3339_z():
    """Current UTC time as an RFC 3339 'Z' timestamp, matching created_at."""
    return (datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"))


def slugify(name):
    """Client name -> URL-safe slug matching the schema pattern.

    'Amorino UK' -> 'amorino-uk'. Guaranteed to satisfy
    ^[a-z0-9]+(?:-[a-z0-9]+)*$, or returns None if nothing usable remains.
    """
    s = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return s or None


def _clean_extract(extract):
    """Drop internal helper keys (e.g. _json_path) an extract may carry."""
    return {k: v for k, v in extract.items() if not k.startswith("_")}


def _rank(site):
    return _EAC_SOURCE_RANK.get(site.get("eac_source"), 0)


def _merge_site(existing, incoming):
    """Merge two site records for the same mpxn.

    The higher-provenance record wins the site_name and eac_source; each of eac
    and kva is taken from the higher-provenance record when it has a value, else
    filled from the other. Never fabricates: if both are null it stays null.
    """
    hi, lo = (existing, incoming) if _rank(existing) >= _rank(incoming) else (incoming, existing)
    merged = dict(hi)  # start from the higher-provenance record
    for field in ("eac", "kva"):
        if merged.get(field) is None and lo.get(field) is not None:
            merged[field] = lo.get(field)
    # site_name should never be empty; fall back to the other, then the mpxn.
    if not merged.get("site_name"):
        merged["site_name"] = lo.get("site_name") or merged.get("mpxn", "")
    return merged


def merge_sites(extracts):
    """Union of all extracts' sites, deduped on mpxn, in first-seen order."""
    by_mpxn = {}
    order = []
    for ex in extracts:
        for site in _clean_extract(ex).get("sites", []):
            m = site.get("mpxn")
            if not m:
                continue
            if m in by_mpxn:
                by_mpxn[m] = _merge_site(by_mpxn[m], site)
            else:
                by_mpxn[m] = dict(site)
                order.append(m)
    return [by_mpxn[m] for m in order]


def merge_quotes(extracts):
    """Concatenate quotes from every extract, preserving order.

    Each extract contributes its own supplier/term offers; assembling several
    single-supplier extracts is exactly how a multi-supplier tender is built.
    """
    quotes = []
    for ex in extracts:
        quotes.extend(_clean_extract(ex).get("quotes", []))
    return quotes


def _build_rye_fee(meta):
    """Assemble the rye_fee block from meta, omitting keys that weren't set."""
    if meta.get("rye_fee") is not None:
        return meta["rye_fee"]
    fee = {}
    for key in ("list_price_site_month", "discount_pct", "per_site_month",
                "annual", "label"):
        if meta.get(f"fee_{key}") is not None:
            fee[key] = meta[f"fee_{key}"]
        elif meta.get(key) is not None:
            fee[key] = meta[key]
    return fee or None


def _build_rye_commission(meta):
    """Assemble the rye_commission block from meta (p/kWh uplift), or None.

    Alternative to rye_fee (mutually exclusive). Accepts an explicit
    `rye_commission` dict, or a flattened `commission_p_kwh_uplift` (+ label)."""
    if meta.get("rye_commission") is not None:
        return meta["rye_commission"]
    if meta.get("commission_p_kwh_uplift") is not None:
        c = {"p_kwh_uplift": meta["commission_p_kwh_uplift"]}
        if meta.get("commission_included") is not None:
            c["included"] = bool(meta["commission_included"])
        if meta.get("commission_label") is not None:
            c["label"] = meta["commission_label"]
        return c
    return None


def _build_recommended(meta):
    """Carry through an explicitly chosen lead offer, or None. Never computed."""
    if meta.get("recommended") is not None:
        return meta["recommended"]
    supplier = meta.get("recommended_supplier")
    if not supplier:
        return None
    rec = {"supplier": supplier}
    if meta.get("recommended_term"):
        rec["term"] = meta["recommended_term"]
    return rec


def assemble(extracts, meta, incumbent=None):
    """Stitch extractResults + incumbent + meta into a canonical tender dict.

    extracts : list of extractResult dicts (or a single dict).
    meta      : dict of tender/meta fields. Required: client_name, tender_label.
                Optional (with sensible defaults): id, version, status, utility,
                created_at, created_by, expires_at, day_split, weekend_split,
                url_uuid, slug,
                dashboard_url, charge_basis, notes, recommended / rye_fee (and
                their flattened *_ variants), etc.
    incumbent : #/$defs/incumbent dict, or None when unknown.

    Returns the tender dict. Does not write to disk and does not validate — call
    validate_tender() for that (the CLI does both).
    """
    if isinstance(extracts, dict):
        extracts = [extracts]
    if not extracts:
        raise ValueError("assemble needs at least one extractResult")
    for key in ("client_name", "tender_label"):
        if not meta.get(key):
            raise ValueError(f"meta['{key}'] is required")

    sites = merge_sites(extracts)
    quotes = merge_quotes(extracts)
    if not quotes:
        raise ValueError("no quotes found across the supplied extracts")
    # Every offer carries when its rates were added (stamped at extract). Any
    # quote still missing it (a legacy tender re-opened for edit) falls back to
    # the tender's creation time, so the picker always has a date to rank on.
    _created = meta.get("created_at") or _now_rfc3339_z()
    for _q in quotes:
        _q.setdefault("added_at", _created)

    slug = meta.get("slug")
    if slug is None and meta.get("client_name"):
        slug = slugify(meta["client_name"])

    tender = {
        "id": meta.get("id") or str(uuid.uuid4()),
        "client_name": meta["client_name"],
        "tender_label": meta["tender_label"],
        "utility": meta.get("utility", "electricity"),
        "status": meta.get("status", "draft"),
        "version": int(meta.get("version", 1)),
        "created_at": meta.get("created_at") or _now_rfc3339_z(),
        "created_by": meta.get("created_by", "unknown@rye.energy"),
        "expires_at": meta.get("expires_at"),
        "day_split": meta.get("day_split", DAY_SPLIT_DEFAULT),
        "weekend_split": meta.get("weekend_split", WEEKEND_SPLIT_DEFAULT),
        "url_uuid": meta.get("url_uuid") or str(uuid.uuid4()),
        "slug": slug,
        "dashboard_url": meta.get("dashboard_url"),
        "sites": sites,
        "incumbent": incumbent,
        "quotes": quotes,
    }

    recommended = _build_recommended(meta)
    if recommended is not None:
        tender["recommended"] = recommended
    rye_commission = _build_rye_commission(meta)
    if rye_commission is not None:
        # Commission is charged INSTEAD of the flat fee; don't stamp both.
        tender["rye_commission"] = rye_commission
    else:
        rye_fee = _build_rye_fee(meta)
        if rye_fee is not None:
            tender["rye_fee"] = rye_fee
    if meta.get("charge_basis"):
        tender["charge_basis"] = meta["charge_basis"]
    if meta.get("notes"):
        tender["notes"] = list(meta["notes"])

    return tender


def validate_tender(tender, schema_path=None):
    """Validate a tender dict against schema/tender.schema.json.

    Raises jsonschema.ValidationError on failure. Imported lazily so the pure
    assemble() path has no hard dependency on jsonschema.
    """
    from jsonschema import Draft202012Validator
    if schema_path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        schema_path = os.path.join(os.path.dirname(here), "schema", "tender.schema.json")
    with open(schema_path) as f:
        schema = json.load(f)
    Draft202012Validator(schema).validate(tender)
    return True


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Assemble extractResults + incumbent + meta -> canonical tender JSON")
    p.add_argument("--extract", action="append", required=True, dest="extracts",
                   metavar="PATH", help="an extractResult JSON (repeat for multiple suppliers)")
    p.add_argument("--incumbent", help="incumbent contract JSON (#/$defs/incumbent); omit if unknown")
    p.add_argument("--out", help="write the tender JSON here (default: stdout)")
    p.add_argument("--no-validate", action="store_true", help="skip schema validation")
    # meta
    p.add_argument("--client", dest="client_name", required=True)
    p.add_argument("--label", dest="tender_label", required=True)
    p.add_argument("--utility", default="electricity")
    p.add_argument("--status", default="draft", choices=["draft", "published", "expired"])
    p.add_argument("--version", type=int, default=1)
    p.add_argument("--id", dest="id")
    p.add_argument("--url-uuid", dest="url_uuid")
    p.add_argument("--slug")
    p.add_argument("--expires-at", dest="expires_at")
    p.add_argument("--day-split", dest="day_split", type=float, default=DAY_SPLIT_DEFAULT)
    p.add_argument("--weekend-split", dest="weekend_split", type=float, default=WEEKEND_SPLIT_DEFAULT)
    p.add_argument("--created-by", dest="created_by", default="unknown@rye.energy")
    p.add_argument("--recommended-supplier", dest="recommended_supplier")
    p.add_argument("--recommended-term", dest="recommended_term")
    p.add_argument("--fee-list-price", dest="fee_list_price_site_month", type=float)
    p.add_argument("--fee-discount", dest="fee_discount_pct", type=float)
    p.add_argument("--fee-label", dest="fee_label")
    p.add_argument("--note", action="append", dest="notes", default=[])
    return p.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    extracts = [_load_json(p) for p in args.extracts]
    incumbent = _load_json(args.incumbent) if args.incumbent else None

    meta = {
        "client_name": args.client_name,
        "tender_label": args.tender_label,
        "utility": args.utility,
        "status": args.status,
        "version": args.version,
        "id": args.id,
        "url_uuid": args.url_uuid,
        "slug": args.slug,
        "expires_at": args.expires_at,
        "day_split": args.day_split,
        "weekend_split": args.weekend_split,
        "created_by": args.created_by,
        "recommended_supplier": args.recommended_supplier,
        "recommended_term": args.recommended_term,
        "fee_list_price_site_month": args.fee_list_price_site_month,
        "fee_discount_pct": args.fee_discount_pct,
        "fee_label": args.fee_label,
        "notes": args.notes,
    }

    tender = assemble(extracts, meta, incumbent=incumbent)
    if not args.no_validate:
        validate_tender(tender)

    text = json.dumps(tender, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote tender -> {args.out} "
              f"({len(tender['sites'])} sites, {len(tender['quotes'])} offer(s), "
              f"v{tender['version']}, status={tender['status']})")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
