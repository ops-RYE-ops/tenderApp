# RYE Tender Tool — session handover

Snapshot for picking this up in a fresh session. Read this + `PHASE0-NOTES.md` +
the build spec and you're oriented.

## What this project is

A self-serve internal tool that turns supplier energy quotes (+ incumbent data)
into a client-ready tender comparison dashboard, delivered as a per-client link.
It productises two existing Cowork skills — `quote-processing` (extraction) and
`quote-to-dashboard` (cost engine + HTML) — into a hosted, team-accessible app.

Guiding principle throughout: **AI maps, code moves numbers.** The LLM only ever
proposes column mappings from a supplier's header row; it never sees or emits a
rate, EAC or meter point. Deterministic Python copies the actual values. That
separation is what makes the output safe to send to a client.

- Build spec (source of truth): Google Doc "RYE-tender-tool-build-spec.md"
  (Vercel version), id `1jzm2jEgGaur_q4kFm5zVE5n5-stfR_jwVNzyuzhSfcg`. Read via
  the Google Drive connector.
- Repo: `~/dev/tenderApp`, pushed to https://github.com/ops-RYE-ops/tenderApp (`main`).
- Stack (locked): Vercel Pro (Python FastAPI functions + static CDN), Claude via
  Vercel AI Gateway (BYOK), Retool DB (canonical tender store + mapping cache),
  company Postgres (site reference, read-only), unguessable-link access (no login).

## Done so far

**Phase 0 — foundations (complete, verified, committed).**
- `schema/tender.schema.json` — the finalised canonical tender JSON schema (the
  spine). Superset of the processed-CSV line schema + old tender.json + the
  tender metadata (id/version/status/expiry/URL, sites[], inlined quote lines).
- `pipeline/process_quote.py` — the existing deterministic extractor, extended to
  also emit the canonical `extractResult` JSON alongside its CSVs. Value-moving
  core untouched.
- `pipeline/map_headers.py` — the single LLM touchpoint (header mapping only).
  Sends headers + a few sample rows, never full data. Transport is env-swappable
  (`ANTHROPIC_BASE_URL` → route via Vercel AI Gateway vs direct API; no code change).
- `schema/retool_tables.sql` — DDL for the two Retool tables (ready to run).
- `schema/examples/` — a valid extractResult and a valid full tender instance.

**Post-Phase-0 refactor (complete, verified, committed).**
- `pipeline/rye_quote_core.py` — single home for `parse_num` + `TARGET_FIELDS`.
  `process_quote.py`, `map_headers.py` and `build_dashboard.py` all import it, so
  extraction and the cost engine can't drift. The test asserts they share the
  same object.
- `pipeline/build_dashboard.py` + `assets/dashboard_template.html` — brought into
  the repo from the quote-to-dashboard skill and wired onto the shared core.

## Key design decisions (don't relitigate without reason)

- **EAC and kVA live on `sites[]`, once** — they're meter facts, not per-offer;
  the comparison must use one consumption basis. Quote lines carry only rates,
  keyed by `mpxn`. `sites[].eac_source` records provenance (db/quote/manual).
- **Line values are typed numbers (or null), parsed once** via the shared
  `parse_num`. `mpxn` stays a string. Units are NOT converted; annualisation basis
  is declared separately in `charge_basis`.
- **Naming is intentionally mixed**: tender/meta fields are snake_case; fields
  inside a line keep the fixed CSV names (camelCase) so lines round-trip through
  the scripts unchanged.
- **`extractResult` is distinct from a full tender**: one extract run = one
  supplier file → `{sites, quotes}`. `/assemble` stitches several runs + incumbent
  + meta into a full tender.

## How to run / verify

```bash
cd ~/dev/tenderApp
source .venv/bin/activate          # needed for any of this project's Python
python3 tests/make_and_verify.py   # expect: ALL CHECKS PASSED (24 checks, no network)
```

The test synthesises a quote, runs extraction, validates against the schema,
assembles a full tender, renders a dashboard, and asserts no parse_num drift.

## Next steps (in priority order)

1. **`assemble_tender.py`** — promote the /assemble step from test-only into real
   code: merge several extract runs + incumbent + meta → full canonical tender
   JSON. Completes the headless "quotes in, tender JSON out" pipeline. **No Vercel
   dependency — do this next.**
2. **Run the Retool DDL** — once the Retool DB region is confirmed UK/EU, run
   `schema/retool_tables.sql` in Retool.
3. **Phase 1 (Vercel backend)** — FastAPI functions wrapping the scripts
   (/inspect, /map, /extract, /assemble, /render). **Gated on the Vercel answers.**

## Open checks / blockers

- Awaiting Vercel answers (Rory's email): Python runtime + libs within limits,
  AI Gateway BYOK, external DB connections, custom domains + noindex, EU region +
  GDPR DPA, seat model, spend cap, startup discount.
- Retool DB region must be confirmed UK/EU before creating tables.
- Company Postgres may be IP-firewalled — if so, either a Vercel static IP
  (~$100/mo) or sync site-reference data into Retool DB and read it from there.

## Environment gotchas learned this session

- macOS Python is externally-managed: use the venv (`source .venv/bin/activate`),
  or `pip install --break-system-packages` as a last resort.
- Run scripts from repo root so same-dir imports resolve.
- If git says `.git/index.lock: File exists` and no git command is actually
  running, `rm -f .git/index.lock` and retry.
- Rory is learning git/Python — explain steps plainly, no assumed jargon.
