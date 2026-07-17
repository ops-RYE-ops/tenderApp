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

- Build spec (source of truth): Google Doc "RYE-tender-tool-build-spec.md",
  id `1jzm2jEgGaur_q4kFm5zVE5n5-stfR_jwVNzyuzhSfcg`. Read via the Google Drive connector.
- Repo: `~/dev/tenderApp` → https://github.com/ops-RYE-ops/tenderApp.
- Stack (locked): Vercel (Python FastAPI functions + static CDN), Claude via
  Vercel AI Gateway (BYOK, mapping only), Retool DB (canonical tender store +
  mapping cache), company Postgres (site reference, read-only), unguessable-link
  access (no login). **Currently building on a Vercel HOBBY account; must move to
  Pro before any real/commercial use** (see Open checks).

## Where we are right now (branch state)

`main` (= `origin/main`) is current through **PR #7**. The whole headless pipeline
is built, merged, and deployed: **map → extract → assemble**, plus the diagnostics
and `/inspect`. No feature branch is open. Merge history:

- PR #1 — Phase 1 spike: `/api/health`, `/api/db-check`, `/api/inspect`.
- PR #2/#3 — header-detection improvement + weekendRate band end-to-end.
- PR #4 — `/api/map` + `/api/map/confirm` (cached/LLM header mapping).
- PR #5 — map prompt fix (capacityCharge vs kva).
- PR #6 — `/api/extract` + sites.csv EAC/kVA override (db provenance).
- PR #7 — `/api/assemble` (incumbent from sites.csv + versioned tender write).
- PR #8 — `/api/render` (canonical tender → dashboard HTML, inline).

Live endpoints on `main`: `/api/health`, `/api/db-check`, `/api/inspect`,
`/api/map`, `/api/map/confirm`, `/api/extract`, `/api/assemble`. `/api/map` was
verified live on a preview (LLM → confirm → cache-hit round-trip). Vercel env vars
`ANTHROPIC_API_KEY` + `RETOOL_DATABASE_URL` are scoped to **Production + Preview**.

**`/api/render` merged (PR #8)** — the last backend endpoint. The headless pipeline
is now COMPLETE end-to-end (map → extract → assemble → render), verified with a full
run through the deployed app on the real UrbanChain quote (cache-hit map → sites.csv
EAC/kVA db-override → incumbent from sites.csv → schema-valid tender → HTML). What's
left is Phase 2 (team UI) and Phase 3 (static delivery + UUID links). Still on a
Vercel HOBBY account; move to Pro before any real/commercial use (see Open checks).

**Phase 2 UI — PR 1 in the working tree (branch `feat/team-ui` to be pushed).**
Decision (2026-07-17, discussed with Rory): the team UI is a **vanilla single-page
app in this repo** — no build step, no npm, served by the same FastAPI app at
`/app` — NOT a Retool app (permanent hand-maintenance seam outside git) and NOT
Next.js (framework churn the job doesn't need). Auth is a shared team key:
**new env var `TEAM_ACCESS_KEY`** (set in Vercel, Production + Preview, like the
others); when set, every /api route except /api/health requires it in an
`X-RYE-Key` header (middleware in main.py); unset = open, so local dev + tests
run unchanged. This PR: `web/` (index.html, app.css, app.js — RYE design system),
the key-gate middleware, `GET /api/auth-check` (unlock probe), `GET /api/suppliers`
(distinct cached suppliers → the controlled supplier dropdown, fixing the exact-
match cache-key hygiene issue below), supplier whitespace-normalisation on /map +
/map/confirm, `tests/test_ui.py`, and `tests/dom_smoke.js` (optional jsdom
walk-through of the whole wizard; needs Node + `npm i jsdom`). Wizard steps live:
unlock → tender basics → upload → map review/confirm (source chip shows
CACHED vs PROPOSED BY CLAUDE; per-field column dropdowns with sample values
recomputed client-side from /inspect; raw-JSON escape hatch; confirm saves to the
cache). Steps 4–6 (extract, assemble, publish) are visible but locked — next PRs.
The site-reference story for the UI is the spec's sidestep: a scheduled Retool
workflow syncing company Postgres → a `site_reference` table in Retool DB, read
by /extract & /assemble (kills the sites.csv upload AND the static-IP question);
not built yet.

Git workflow we're using: feature branch → `git push` → Vercel auto-builds a
**Preview** deployment → open a PR on GitHub → merge → `main` auto-deploys to
production. In the Vercel Deployments tab, switch the env filter from "Production"
to **All** to see branch previews. (Claude's sandbox can't push or write to `.git`;
it edits the working tree + runs the tests, then hands over paste-safe git commands.)

## Done so far

**Phase 0 — foundations (on main).**
- `schema/tender.schema.json` — canonical tender JSON schema (the spine).
- `pipeline/process_quote.py` — deterministic extractor; emits canonical
  `extractResult` JSON alongside its CSVs. Value-moving core untouched.
- `pipeline/map_headers.py` — the single LLM touchpoint (header mapping only);
  headers + a few sample rows, never full data. `/inspect` + `/map` logic lives here.
- `pipeline/rye_quote_core.py` — shared `parse_num` + `TARGET_FIELDS` so extractor
  and cost engine can't drift.
- `pipeline/build_dashboard.py` + `assets/dashboard_template.html` — cost engine + HTML.
- `schema/retool_tables.sql` — DDL. **Already applied** to the Retool DB (tables
  `tenders`, `supplier_mappings`, view `tenders_latest` exist). Schema only — no
  client data yet, pending EU-region confirmation.

**`assemble_tender.py` — the /assemble step as real code (on main).**
Merges N `extractResult` docs (dedupe sites on `mpxn` with provenance preference
db>manual>quote + null-fill; concat quotes) + incumbent + meta → a valid canonical
tender. Importable `assemble()` for the endpoint, plus a CLI. Moves NO values;
stamps meta (id/version/status/timestamps/url_uuid/slug). `recommended` carried
through, never computed. Completes the headless "quotes in → tender JSON out" pipeline.

**Vercel backend — Phase 1 (backend COMPLETE; only /render remains).**
All endpoints below are on `main` and deployed. `/api/extract` (PR #6) and
`/api/assemble` (PR #7) are detailed in Next steps; the earlier ones:
- `main.py` — the real FastAPI app (Vercel auto-detects `app` at root entrypoint).
- `/api/health` + `/api/db-check` — diagnostics; both green live. DB reached over
  SSL; connection string in Vercel env var `RETOOL_DATABASE_URL` (never in code).
- **`/api/inspect`** (on main, live) — upload a quote (.xlsx/.xlsm/.csv) → per-sheet
  ranked header-row candidates, best guess, first ~40 rows. Thin wrapper over
  `map_headers.inspect_file`. Pure, no network. Tested on 11 real supplier files.
- **`/api/map` + `/api/map/confirm`** (on main, PR #4 merged; verified live on a
  preview against a real UrbanChain quote) — the
  live Claude call. `/api/map`: inspect → compute layout fingerprint
  (`map_headers.layout_fingerprint`, a sha256 of the normalised header signature,
  values-independent) → cache lookup in `supplier_mappings` by (supplier,
  fingerprint); on a hit return the cached mapping and **skip the LLM**, on a miss
  call `map_headers.propose_mapping`. Returns `{source: cache|llm, mapping,
  sample_values, layout_fingerprint, ...}`; `sample_values` are read
  deterministically for the confirm screen and never returned to the model.
  `/api/map/confirm`: upserts a confirmed/overridden mapping to the cache so the
  next identical layout skips the LLM. Degrades gracefully with no DB (goes to the
  LLM) and returns a clean 503 on cache-miss-with-no-API-key. Covered by
  `tests/test_map.py` (fingerprint stability, cache-vs-LLM, confirm/save — all
  mocked, no network). **Both `ANTHROPIC_API_KEY` and `RETOOL_DATABASE_URL` must be
  scoped to Production AND Preview** — preview builds don't inherit Production-only
  vars, and a wrong scope shows up as the endpoint's own 503s ("… not set"). Sensitive
  vars can't be added to the Development environment (Vercel blocks it); that's fine,
  we don't need it. After changing a var's scope you must REDEPLOY the branch for it
  to take effect.
  - **Cache hygiene / supplier naming (for the Phase 2 UI):** the cache key is
    `(supplier, layout_fingerprint)` and the supplier match is EXACT. "UrbanChain",
    "Urban Chain" and "urbanchain" are three different keys → needless repeat LLM
    calls and duplicate rows. The new-tender UI should pick supplier from a
    controlled dropdown (or normalise the string server-side), never free text, so
    the cache actually pays off. Re-confirming the same supplier+fingerprint upserts
    (overwrites) the existing mapping row, which is how you correct a cached mapping.
  - **Live-test gotcha found:** Claude mapped a "KVA Charge (p/kVA/day)" column to
    `kva` (the capacity QUANTITY) instead of `capacityCharge` (the per-kVA price).
    Harmless here (values were 0) but would mis-cost a real capacity charge. Fixed by
    a rule in the `map_headers` SYSTEM_PROMPT distinguishing the two; the confirm
    screen is the backstop regardless.
- **Header detection improved** (on `feat/weekend-rate`) — scans 40 rows (not 15),
  rejects value/summary rows, rewards the row a wide consistent data block sits
  under. Correctly finds the header on all 11 sample files incl. the Octopus
  multisite "summary block above the table" layout (real header at row 21).
- **weekendRate band, end-to-end** (on `feat/weekend-rate`) — added to
  `TARGET_FIELDS`, the schema (line + `charge_basis` + top-level `weekend_split`),
  `process_quote` line fields + split detection, `build_dashboard` energy calc,
  and the `map_headers` prompt. Behaviour: weekend rate is **captured and shown**
  always; it is **only costed if `weekend_split` is set**, otherwise a warning is
  raised (we never invent a weekend consumption share). Peak/off-peak that are
  really day/night get mapped into those bands by the LLM, no weekend field used.

## Key design decisions (don't relitigate without reason)

- **EAC and kVA live on `sites[]`, once** — meter facts, not per-offer; one
  consumption basis across all offers. `sites[].eac_source` records provenance.
- **Line values are typed numbers (or null), parsed once** via shared `parse_num`.
  `mpxn` stays a string. Units are NOT converted; annualisation basis is in `charge_basis`.
- **Naming is intentionally mixed**: tender/meta fields snake_case; line fields
  keep the fixed CSV camelCase names so lines round-trip through the scripts unchanged.
- **`extractResult` is distinct from a full tender**: one extract run = one supplier
  file → `{sites, quotes}`. `/assemble` stitches several runs + incumbent + meta.
- **Effective rate = cost-first normalisation**: annualise every charge to £/yr per
  its `charge_basis`, sum across sites, divide by total kWh ×100 → all-in p/kWh.
  Non-commodity charges are spread over consumption so offers with different fee
  structures compare on one number. Multi-rate energy is a consumption-weighted
  blend (day_split / weekend_split / residual night). Charge-basis / split
  assumptions are the highest-risk area — always footnoted, never silently guessed.

## Real supplier layouts seen (informs /map)

- **"Client Quote" template** (Avant, Blank St, Chance, Rosslyn, Urban Chain): header
  row 9 under a metadata block; single-rate; has a Commission column (RYE is
  commission-free → leave it unmapped).
- **YU broker portal**: camelCase headers row 1; day/night/weekend bands; bundles an
  "Incumbent data" sheet (only in Rory's own compilations → ignore those sheets).
- **Octopus multisite**: "Quote summary" block up top, real rates table at row 21;
  standard/day/night/peak columns, mixed single & two-rate rows, electricity + gas,
  has Site names, charge bases stated in headers.
- **Salad Kitchen** (Salesforce export) + **Octopus CSV**: header row 1, single-rate.

## How to run / verify

```bash
cd ~/dev/tenderApp
source .venv/bin/activate          # macOS venv; needed for this project's Python
python3 tests/make_and_verify.py   # extraction→schema→assemble→dashboard, no drift
python3 tests/test_assemble.py     # multi-extract merge / dedupe / versioning
python3 tests/test_weekend.py      # weekend band: capture + warn-vs-cost
python3 tests/test_map.py          # /api/map: fingerprint, cache-vs-LLM, confirm (mocked)
python3 tests/test_extract.py      # /api/extract: value pass-through, site-ref join, 400s
python3 tests/test_assemble_api.py # /api/assemble: incumbent-from-sites.csv + endpoint (DB mocked)
python3 tests/test_render.py       # /api/render: canonical->HTML adapter + endpoint (DB mocked)
python3 tests/test_ui.py           # team UI: key gate, static /app, /suppliers, supplier norm
node tests/dom_smoke.js            # optional: jsdom walk of the whole wizard (npm i jsdom first)
```
All eight Python tests should print their "ALL … PASSED" line. No network needed (the LLM and DB
are mocked in test_map / test_assemble_api / test_render).
(Claude's Linux sandbox can't use the macOS `.venv`; install deps with
`pip install --break-system-packages fastapi openpyxl jsonschema psycopg2-binary python-multipart httpx` to run tests there.)

## Next steps (in priority order)

1. ~~**`/api/map`**~~ **DONE & merged** (PR #4; prompt fix PR #5). Cache-lookup by
   supplier + layout fingerprint in `supplier_mappings`; on a miss calls
   `map_headers.propose_mapping`; returns proposed mapping + sample values for
   confirm/override; `/api/map/confirm` saves confirmed mappings to the cache.
   `ANTHROPIC_API_KEY` + `RETOOL_DATABASE_URL` set in Vercel (Prod+Preview).
   Optional `ANTHROPIC_BASE_URL` routes via the AI Gateway (no code change).
   Verified live on a preview against a real UrbanChain quote (LLM → confirm →
   cache-hit round-trip all green).
2. ~~**`/api/extract`**~~ **DONE & merged** (PR #6).
   Thin wrapper over `process_quote.run`: multipart upload + confirmed `mapping`
   (JSON form field) + optional `site_reference` CSV → canonical `extractResult`
   ({sites, quotes}). No LLM. Returns counts + `unmatched_mpxn` (meter points with
   no site-reference match, surfaced not swallowed) + `site_reference_used`. Temp
   files cleaned up; `emit_csv=False` (endpoint returns JSON, not files). Covered by
   `tests/test_extract.py` (verbatim value pass-through, site-ref join + unmatched
   flagging, 400 validation) and smoke-tested locally on the real UrbanChain quote
   (3 sites, 2 terms, KVA charge → capacityCharge, kva null). **Verify after merge:**
   POST a real quote + its confirmed mapping to the preview `/api/extract` and check
   the lines match the known-good CSVs. NOTE: the site-reference is an optional
   uploaded **sites.csv** (MPAN = unique key), read via `process_quote.build_site_lookup`.
   Columns configurable in `mapping.db_lookup` (defaults: `mpxn`, `siteName`, `eac`,
   `kva`). Behaviour: RYE's site name always overrides the quote's; **EAC/kVA from
   sites.csv override the supplier quote and are stamped `eac_source:"db"`** (a meter
   absent from sites.csv keeps the quote's EAC as `"quote"`). Incumbent columns in
   sites.csv are NOT read at /extract — they feed the tender `incumbent` block at
   /assemble. Wiring sites.csv to the read-only company Postgres (instead of an
   upload) is still open (see blockers).
3. ~~**`/api/assemble`**~~ **DONE & merged** (PR #7).
   Multipart: `extracts` (JSON array
   of extractResults) + `meta` (JSON; client_name + tender_label required) + optional
   `sites_csv` → `assemble_tender.assemble` → `validate_tender` → versioned row in the
   Retool `tenders` table (payload JSONB + denormalised columns). Incumbent is built
   from sites.csv by a new `assemble_tender.incumbent_from_sites_csv`: reads the rate
   columns + `incumbentSupplier`, keyed on MPAN, scoped to the tender's meters +
   client (`clientName`); a row with no rate data is skipped (site-reference-only), so
   a sites.csv with no incumbent data → no incumbent (schema-valid). Supplier rule:
   one distinct → that name; several → `"Various"`; rates but none named → `"Unknown"`
   (each surfaced as a warning). Versioning: existing `meta.id` bumps to max(version)+1;
   new tender → version 1. `persist=false` assembles + validates WITHOUT a DB write
   (dry run / no-DB dev). Covered by `tests/test_assemble_api.py` (incumbent builder,
   schema drift guard, endpoint with DB mocked). **Finalised sites.csv contract**
   (Retool export): `clientName, siteName, mpxn, eac, supplyStartDate, unitRate,
   dayRate, nightRate, weekendRate, standingCharge, capacityCharge, networkCharge,
   meterCharge, kva, incumbentSupplier`. (`updatedEac`→`eac` so build_site_lookup's
   default matches.) **Verify after merge:** POST extracts + meta + sites.csv to the
   preview `/api/assemble` and confirm a versioned row lands in `tenders` (re-POST
   with the same id → version increments).
4. ~~**`/api/render`**~~ **DONE & merged** (PR #8).
   First cut returns the dashboard HTML **inline** (static publish + UUID link is
   Phase 3). POST JSON body: EITHER `tender_id` (+ optional `version`; fetched from
   the `tenders` table, latest by default) OR an inline `tender` object — exactly
   one. `build_dashboard.render_tender(tender)` bridges the canonical shape to the
   engine's CSV-per-offer config: a new `_write_offer_csv` joins each line to its
   site on MPAN and writes the per-quote (and incumbent) CSVs, then calls
   `build_dashboard.main` UNCHANGED (cost logic stays in one place). No files
   persist (temp dir, removed). Covered by `tests/test_render.py` (adapter + endpoint
   inline/by-id/404/400, DB mocked). **Verify after merge:** POST a stored
   `tender_id` to the preview `/api/render` and eyeball the HTML; spot-check ≥2 site
   costs against the source before any client sees it (the engine prints this
   reminder too). **This completes the headless pipeline: map → extract → assemble →
   render.**

Remaining beyond the backend:
5. **Phase 2 — team UI** (new-tender, upload, mapping review, tender register) calling
   these endpoints. See the build spec's team-facing flow. **STARTED** — vanilla SPA
   in `web/` served at `/app` (see the branch-state note above). UI PR 1 (shell +
   key gate + upload → map review/confirm) is in the working tree; UI PR 2 is the
   extract + assemble screens (per-file /extract with unmatched-MPxN flagging, tender
   meta form — recommended offer, day_split, expiry — then /assemble with version
   feedback); UI PR 3 is the render preview + tender register (needs a small
   `GET /api/tenders` register endpoint over the `tenders_latest` view). Remember to
   set `TEAM_ACCESS_KEY` in Vercel (Prod + Preview) and REDEPLOY before testing a
   preview, or the UI will sit at the unlock screen telling you the key is wrong.
6. **Phase 3 — render & deliver**: static hosting on the custom domain, the UUID link
   lifecycle (noindex, expiry, revoke/rotate), and turning on the learned-mappings
   cache in the flow. This is where `/render` graduates from inline HTML to a
   published per-client URL.
7. **Upgrade to Vercel Pro** before going live / real client data: commercial use
   (Hobby is non-commercial only), team seats, spend controls, EU-region pinning,
   static-IP add-on if needed.

The pipeline core is transport-agnostic: every script is a plain importable
function, so the endpoints stay thin wrappers.

## Open checks / blockers

- **EU data residency** — the locked decision. Retool support emailed 2026-07-16
  (is our org US or EU hosted? can it migrate?), awaiting reply; needed before any
  live client data. Vercel EU-region pinning of functions needs Pro.
- **Company Postgres** may be IP-firewalled — if so, either a Vercel static IP
  (~$100/mo, Pro-only) or sync site-reference data into Retool DB and read from there.
- **Vercel Pro** — required for commercial use / team / spend caps (see step 5).

Resolved this session (no longer open): Vercel Python runtime works; FastAPI is a
supported framework; external DB connection to Retool DB works over SSL; AI Gateway
BYOK is available on all plans.

## Environment / workflow gotchas

- macOS Python is externally-managed: use the venv locally. Claude's sandbox is
  Linux and shares the working folder, but not the macOS venv.
- Run scripts from repo root so same-dir imports resolve.
- `.git/index.lock: File exists` with no git running → `rm -f .git/index.lock`.
  (Claude's file tooling touching the repo can leave one behind.)
- Vercel: "Environments" (custom pre-prod) are Pro-only; "Environment Variables"
  are free. Deployments tab defaults to the Production filter — switch to All to
  see branch previews.
- FastAPI file uploads need `python-multipart` (already in requirements.txt).
- Rory is learning git/Python — explain steps plainly, no assumed jargon, and keep
  commands paste-safe (single lines; `git add -A` beats long path lists).
