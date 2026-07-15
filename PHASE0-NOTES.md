# Phase 0 — notes & decisions

What's done: the canonical tender JSON schema is finalised, and `process_quote.py`
now emits it (plus the optional Claude mapping call). The Retool DDL is included
as the immediate next step. Everything is verified headless.

## Schema decisions worth knowing (the non-obvious calls)

**EAC and kVA live on `sites[]`, once — not on every quote line.** They are facts
about the meter (consumption, agreed capacity), not about a supplier's offer, and
a like-for-like comparison must use one consumption basis across all offers. Quote
lines therefore carry only offered rates, keyed by `mpxn`; the renderer joins
line → site on `mpxn`. `sites[].eac_source` records provenance (`db` / `quote` /
`manual`) so a supplier-restated EAC can never silently override RYE's figure.

**Line values are typed numbers (or `null`), parsed once.** `mpxn` stays a string
(an identifier, never arithmetic). Every rate/charge is run through `parse_num`,
which is byte-for-byte the parser `build_dashboard.py` already uses — so the number
stored in the canonical JSON is exactly the number the cost engine computes on.
There is no second interpretation of a cell downstream. `null` means "not quoted".
Units are **not** converted; the annualisation basis is declared separately in
`charge_basis`.

**Naming looks mixed on purpose.** Tender/meta fields are `snake_case`
(`client_name`, `day_split`, `expires_at`) to match the spec and the Postgres
columns. Fields *inside a line* keep the fixed CSV field names (`unitRate`,
`standingCharge`, …) so lines round-trip through the existing scripts with zero
renaming. Don't "tidy" one to match the other.

**`extractResult` is a distinct shape from a full tender.** One extract run = one
supplier file → `{ sites, quotes }`. `/assemble` concatenates the quotes from
several runs, merges sites (deduped on `mpxn`), attaches the incumbent + meta, and
stamps `id`/`version`/`status` to form a valid tender. Keeping them separate is why
the extractor stays single-file and headless-testable.

**Single vs two-rate exclusivity is enforced in code, never by hand.** A line has
either `unitRate` set (single) or `dayRate`/`nightRate` set (two-rate), never both
— inherited from the existing `process_rows` logic, now carried into the JSON.

## What I did NOT touch

The value-moving core of `process_quote.py` is unchanged — I only *added* canonical
emission around it and an optional `--auto-map` hook. The CSVs it always produced
are still produced (so today's `build_dashboard.py` keeps working unchanged). This
respects the "deterministic scripts are the single source of truth" principle.

One deliberate TODO: `parse_num` is currently duplicated in `process_quote.py` and
`build_dashboard.py`. Before Phase 1 it should be factored into a shared module so
the two can never drift. Flagged in-code.

## Immediate next step (drops out for free)

`schema/retool_tables.sql` creates the two tables. `tenders.payload` holds the full
canonical tender; the scalar columns are denormalised copies of top-level payload
fields so the register lists/filters without opening JSONB. `(id, version)` is the
PK — version, never overwrite — and `tenders_latest` is the view the register reads.
Run it against the Retool DB once the region check clears.

## Blocked-on / assumptions to confirm

- The Vercel AI Gateway question doesn't block this: `map_headers.py` picks up
  `ANTHROPIC_BASE_URL` if set, so routing via the Gateway (for spend monitoring) vs
  the direct API is an env-var change, no code change.
- `ANTHROPIC_MODEL` defaults to `claude-sonnet-5`; adjust if you'd rather map with a
  cheaper/faster model — mapping is short, structured, and cache-suppressed on repeat
  layouts.
