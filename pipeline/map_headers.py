#!/usr/bin/env python3
"""
map_headers.py — the ONE place the LLM is involved. AI maps, code moves numbers.

Given a supplier quote, this proposes the mapping.json that process_quote.py then
uses to move values deterministically. The model only ever sees the header
candidate rows plus a few sample data rows — never the full rate table — and it
only ever returns column-name -> field associations. It cannot emit a rate, EAC
or meter point into the pipeline; process_quote.py copies those from the source.

Backend endpoints this backs:
  * /inspect  -> inspect_file()          (pure, no network)
  * /map      -> propose_mapping()        (the single LLM call)

LLM transport is env-swappable so the OUTSTANDING Vercel AI Gateway question
changes nothing here:
  ANTHROPIC_API_KEY   required for a live call
  ANTHROPIC_BASE_URL  optional — point at the Vercel AI Gateway to route
                      through it (spend monitoring); unset = direct Anthropic API
  ANTHROPIC_MODEL     optional — default 'claude-sonnet-5'

CLI:
    python3 map_headers.py SOURCE [--supplier NAME] [--sample-rows N]
                                  [--dry-run] [--out mapping.json]
    --dry-run prints the inspection + the EXACT request that would be sent
    (proving the payload is headers + samples only) and makes no network call.
"""
import argparse
import json
import os
import re
import sys

from rye_quote_core import TARGET_FIELDS

# Keywords that hint a row is the real header row (not a metadata/branding row).
HEADER_HINTS = [
    "mpan", "mprn", "mpxn", "meter", "supply number", "eac", "aq", "consumption",
    "rate", "standing", "day", "night", "peak", "unit", "kva", "capacity",
    "duos", "network", "site", "address", "start", "charge",
]

DEFAULT_MODEL = "claude-sonnet-5"


# --- /inspect : pure, no network -------------------------------------------

def _cell(v):
    return "" if v is None else str(v).strip()


def _score_header_row(cells):
    """Heuristic score: many non-empty cells + keyword hits => likely header."""
    non_empty = [c for c in cells if c != ""]
    if not non_empty:
        return 0
    hits = sum(1 for c in non_empty if any(h in c.lower() for h in HEADER_HINTS))
    return len(non_empty) + 3 * hits


def _sheet_grid(path, sheet, max_rows):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
        grid = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= max_rows:
                break
            grid.append([_cell(c) for c in row])
        return grid
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [[_cell(c) for c in r] for r in list(__import__("csv").reader(f))[:max_rows]]


def inspect_file(path, max_rows=15):
    """Sheet names, first ~max_rows rows, and ranked header-row candidates.

    This is exactly what the /inspect endpoint returns and is the ONLY view of
    the file the mapping step gets beyond a handful of sample rows.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        sheet_names = wb.sheetnames
    else:
        sheet_names = [None]  # single logical sheet for csv

    sheets = []
    for name in sheet_names:
        grid = _sheet_grid(path, name, max_rows)
        scored = sorted(
            ((_score_header_row(r), idx + 1) for idx, r in enumerate(grid)),
            reverse=True,
        )
        candidates = [row for score, row in scored[:3] if score > 0]
        best = candidates[0] if candidates else 1
        sheets.append({
            "name": name,
            "header_row_candidates": candidates,
            "header_row_best_guess": best,
            "headers": grid[best - 1] if best - 1 < len(grid) else [],
            "first_rows": grid,
        })
    return {"path": os.path.basename(path), "sheets": sheets}


# --- /map : the single LLM call --------------------------------------------

def build_payload(inspection, sample_rows=3):
    """Minimal, non-sensitive payload sent to the model: per sheet, the guessed
    header row + up to `sample_rows` data rows. No full rate table ever leaves."""
    out = {"file": inspection["path"], "sheets": []}
    for s in inspection["sheets"]:
        best = s["header_row_best_guess"]
        data = s["first_rows"][best:best + sample_rows]
        out["sheets"].append({
            "name": s["name"],
            "header_row_candidates": s["header_row_candidates"][:3],
            "headers": s["headers"],
            "sample_data_rows": data,
        })
    return out


def _column_spec_schema():
    # A target field maps to: null | "Header" | {single:"Header"} | {split:"Header"}
    return {
        "oneOf": [
            {"type": "null"},
            {"type": "string"},
            {"type": "object", "properties": {"single": {"type": "string"}},
             "required": ["single"], "additionalProperties": False},
            {"type": "object", "properties": {"split": {"type": "string"}},
             "required": ["split"], "additionalProperties": False},
        ]
    }


def mapping_tool_schema():
    """input_schema for the forced tool call — the model returns a mapping.json."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["header_row", "output_prefix", "columns"],
        "properties": {
            "sheets": {"type": "array", "items": {"type": "string"},
                       "description": "Sheet names that each hold a term; omit for single-sheet/CSV."},
            "header_row": {"type": "integer", "minimum": 1,
                           "description": "1-based row holding the source headers."},
            "split_output_by_sheet": {"type": "boolean"},
            "output_prefix": {"type": "string"},
            "supplier": {"type": "string"},
            "term_labels": {"type": "object", "additionalProperties": {"type": "string"},
                            "description": "sheet name -> client-facing term label."},
            "category": {"type": "string"},
            "columns": {
                "type": "object",
                "additionalProperties": False,
                "properties": {f: _column_spec_schema() for f in TARGET_FIELDS},
            },
            "charge_basis": {"type": "object", "additionalProperties": {"type": "string"}},
            "db_lookup": {"type": "object"},
        },
    }


SYSTEM_PROMPT = (
    "You map a UK energy supplier quote's column headers onto RYE's fixed target "
    "schema. You NEVER transcribe or emit a rate, EAC or meter point — you only name "
    "which source column feeds each target field. Match by MEANING, not exact string.\n"
    "Target fields: " + ", ".join(TARGET_FIELDS) + ".\n"
    "Rules: use {\"single\": H} for a standard/anytime rate source and {\"split\": H} "
    "for day/night sources so single-vs-two-rate logic can tell them apart. If the "
    "supplier has no day/night columns, set dayRate/nightRate to {\"split\": \"__none__\"}. "
    "Set a field to null if the supplier does not provide it. Gas is single-rate: map its "
    "standard rate to unitRate. Pick the header_row that actually holds MPAN/EAC/rate "
    "labels, not a metadata/branding row. If the term is encoded in sheet names, list them "
    "in 'sheets' with split_output_by_sheet true and give client-facing term_labels."
)


def _client():
    try:
        import anthropic
    except ImportError:
        raise SystemExit(
            "anthropic SDK not installed. `pip install anthropic`. "
            "(Not needed for --dry-run or the deterministic path.)")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY not set — required for a live mapping call.")
    kwargs = {"api_key": key}
    base = os.environ.get("ANTHROPIC_BASE_URL")
    if base:
        kwargs["base_url"] = base  # e.g. Vercel AI Gateway endpoint
    return anthropic.Anthropic(**kwargs)


def propose_mapping(inspection, supplier=None, sample_rows=3, model=None):
    """The single LLM call. Returns a mapping dict (validated shape via tool use)."""
    payload = build_payload(inspection, sample_rows=sample_rows)
    model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    user = (
        (f"Supplier: {supplier}\n" if supplier else "")
        + "Inspect this quote and return the mapping via the emit_mapping tool.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    client = _client()
    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        tools=[{
            "name": "emit_mapping",
            "description": "Return the mapping.json for this supplier quote.",
            "input_schema": mapping_tool_schema(),
        }],
        tool_choice={"type": "tool", "name": "emit_mapping"},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_mapping":
            mapping = dict(block.input)
            if supplier and "supplier" not in mapping:
                mapping["supplier"] = supplier
            mapping.setdefault("rate_logic", "auto")
            return mapping
    raise SystemExit("Model did not return an emit_mapping tool call.")


def autobuild(path, supplier=None, sample_rows=3, model=None):
    """inspect + propose -> ready-to-use mapping dict (called by process_quote --auto-map)."""
    inspection = inspect_file(path)
    return propose_mapping(inspection, supplier=supplier,
                           sample_rows=sample_rows, model=model)


def main(argv):
    p = argparse.ArgumentParser(description="Propose a mapping.json via Claude.")
    p.add_argument("source")
    p.add_argument("--supplier")
    p.add_argument("--sample-rows", type=int, default=3)
    p.add_argument("--dry-run", action="store_true",
                   help="print inspection + the exact request payload; make no API call")
    p.add_argument("--out", help="write the proposed mapping.json here")
    args = p.parse_args(argv)

    inspection = inspect_file(args.source)
    if args.dry_run:
        print("=== INSPECTION ===")
        print(json.dumps(inspection, ensure_ascii=False, indent=2))
        print("\n=== PAYLOAD THAT WOULD BE SENT TO CLAUDE (headers + samples only) ===")
        print(json.dumps(build_payload(inspection, args.sample_rows),
                         ensure_ascii=False, indent=2))
        print("\n(no API call made)")
        return
    mapping = propose_mapping(inspection, supplier=args.supplier,
                             sample_rows=args.sample_rows)
    out = json.dumps(mapping, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"wrote {args.out}")
    else:
        print(out)


if __name__ == "__main__":
    main(sys.argv[1:])
