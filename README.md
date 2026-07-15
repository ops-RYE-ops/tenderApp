# RYE tender tool

Phase-0 foundations for the tender dashboard tool (see the build spec). This is
the headless "quotes in, canonical JSON out" core that Phases 1–3 wrap in Vercel
Functions, a team UI, and static hosting.

```
schema/
  tender.schema.json              canonical tender JSON Schema (the spine)
  retool_tables.sql               Retool DB DDL (tenders + supplier_mappings)
  examples/                       a valid extractResult and a valid full tender
pipeline/
  process_quote.py                deterministic extractor -> CSVs + canonical JSON
  map_headers.py                  the single LLM call (header mapping only)
tests/
  make_and_verify.py              headless end-to-end check (no network)
PHASE0-NOTES.md                   schema decisions + what's next
```

## The one rule

AI maps, code moves numbers. `map_headers.py` is the only place the LLM is
involved, and it only ever names which source column feeds each field — it never
sees or emits a rate, EAC or meter point. `process_quote.py` copies the actual
values deterministically. That separation is what makes the output safe to send
to a client.

## Run it

Deterministic path (hand-written or cached mapping — no API key needed):

```bash
python3 pipeline/process_quote.py SOURCE.xlsx mapping.json OUT_DIR [RYE_DB.csv]
```

Writes the per-term CSVs (as before) **and** a canonical `*.extract.json`
(the `extractResult` shape) into a timestamped `run-.../` folder.

Auto-map path (Claude proposes the mapping first):

```bash
export ANTHROPIC_API_KEY=sk-...
# optional: route via Vercel AI Gateway instead of the direct API — no code change
# export ANTHROPIC_BASE_URL=https://gateway.ai.vercel.app/v1/...
python3 pipeline/process_quote.py SOURCE.xlsx OUT_DIR --auto-map --map-out mapping.json --supplier "EDF"
```

Inspect a file / preview the exact LLM payload without calling the API:

```bash
python3 pipeline/map_headers.py SOURCE.xlsx --dry-run
```

## Verify

```bash
python3 tests/make_and_verify.py     # exit 0 = all green, no network
```

Synthesises a two-sheet quote (single-rate + two-rate/kVA sites), runs the
extractor, validates the output against the schema, assembles a full tender and
validates the spine, and spot-checks that values pass through verbatim.
