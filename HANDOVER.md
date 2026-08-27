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

**The app is COMPLETE, merged, and live.** `main` (= `origin/main`) now includes
Phase 3. The full flow — **map → extract → assemble → render → publish** — plus the
team UI (wizard + register) and the public client link were **verified end-to-end on
the RYE Pro deployment 2026-07-20**, including sharing a live client dashboard link.
Hosted on the **RYE company Vercel Pro** account (project `tender-app`, live at
`tender-app-chi.vercel.app`; custom domain `tender.rye.energy` in DNS setup). See
"Deployment & ops status" below for the live config.

**Latest session (2026-08-27 — branch `feat/live-link-edits`, all suites green, NOT yet
merged or preview-tested):** four things Rory asked for.
(1) **`Current` renamed `Incumbent` across the client dashboard** — the badge on the bars,
breakdown and rate books, the comparison-table column header, the `vs incumbent` delta column
and the Portfolio efficiency header (`incumbent / best`). Done by making `baseLabel` the single
source and adding module-scope `baseLc` (prose/headers) + `baseChip` (short badge); the three
hardcoded `"current"` chips now use `baseChip`. Benchmark wording is untouched on purpose —
a benchmark tender still reads **Market benchmark** / badge `benchmark`, because the two must
never be conflated. Rationale (Rory): "Incumbent" works for both new tenders and re-tenders of
existing clients, so one dashboard covers both without a process change.
(2) **Market snapshot refreshed to 27 Aug 2026.** Power spot 129.37 £/MWh (the top of its
three-year range, +76.95% 1Y, +4.36% 5Y); NBP front-month 161.85 p/therm; every gas contract transcribed from the
ICE board including **Summer 27 at 107.00 and Winter 27 at 104.00 against Winter 26 at 164.80**
— the curve shape that carries the "24-month blends down next winter" argument, and the gas
cards are now a tidy 3×3 (Feb 27 dropped to make room).
**Three of Rory's corrections are baked in — do not re-learn them the hard way:**
(a) **The long-window chart is THREE years, not five.** A five-year view puts the 2022
Russia-Ukraine spikes (~£585) on the same axis as today, which dwarfs a genuinely severe market
and undersells it. Three years starts after the crisis unwound, so £129.37 reads as **the top of
the range** (£56–130) rather than a footnote to 2022. The template no longer hardcodes the
window: it reads `power.rangeLong.years` + `.axisLabels`, so changing it again is a JSON edit
only. The key was renamed `range5y` → `rangeLong` for exactly that reason. Don't widen it back
without a reason.
(b) **The far gas curve is NOT thin.** An earlier draft caveated Summer 27 / Winter 27 as
illiquid and indicative on the strength of their 15-lot volumes. Wrong read: the screenshot was
taken early in the session, and both have held at or above those levels for the preceding
fortnight. They are a settled view of the curve, and caveating them understated the 24-month
argument. Volume alone is not evidence of a stale mark — check whether the level has held.
(c) **Trading Economics' spot index and the N2EX day-ahead view are different measures** — one
daily index vs every HH delivery period — so the commentary quotes the N2EX daily averages and
intraday spread separately rather than implying one series eased off the other's high.
(3) **A published client link now survives an edit** (the actual fix for "we have to send a new
link each time"). Two bugs, both invisible until you tried to edit: `/api/assemble` did not
carry `slug`/`url_uuid`/`dashboard_url` forward on a version bump, so a re-save **re-minted the
uuid and killed the live link**; and `/d/<slug>/<uuid>` required the LATEST version to be
published, so the client's link would have **404'd the instant an edit was saved**. Now:
assemble re-reads the link identity from the stored tender **server-side** (a `url_uuid` sent
from the browser is ignored — a client-facing URL must not be settable from a page, and there is
a test for it), and the public route answers two separate questions — *is this link alive*
(latest version must still carry the uuid, so **Revoke is still a kill switch**) and *what
should it show* (`_get_published_tender_by_uuid`, the latest version actually published under
that uuid). Net effect — **staged publish**, Rory's choice over instant-live: the client keeps
seeing the last published version while you edit, and Publish flips it on the same URL. Saving
an edit to a published tender now returns a warning saying exactly that.
(4) **Edit a saved tender from the register — Tier 1 BUILT** (the design below, now implemented).
New `GET /api/tenders/{id}` (team-gated, optional `?version=`) returns the full stored payload;
a per-row **Edit** action hydrates the wizard and jumps to step 5. New `resetWizard()` is the
build-once primitive the design called for, which also **fixes the dead "New tender" button**.
Hydration rebuilds ONE synthetic extract from the stored `sites`+`quotes` (marked `fromSaved`),
prefills client/label/utility, fee↔commission, expiry, notes and a stored benchmark, and
**restores the previously-featured offers instead of the "two cheapest" default** — otherwise a
re-save silently swaps what the client was shown. Incumbent handling is Rory's "let me choose
per edit": a `keep_incumbent` form field on `/api/assemble` makes the endpoint re-fetch the
stored incumbent **server-side**, with precedence new sites.csv > new benchmark > kept > none.
**Gotcha found and fixed while building:** the synthetic row has no `File` behind it, so stepping
back to the extract step and hitting "Extract confirmed files" would have POSTed a null — saved
rows are now read-only (no map/remove) and skipped by `runExtractAll`.
Also shipped: **confirming a mapping now closes the panel** and returns to the file list (the
notice moved from `map-msg` to `step2-msg`).
Tests: 6 new checks in `test_publish.py` (staged publish serves the published version not the
draft; never-published draft 404s; revoke kills the link through both old and new uuid), 13 in
`test_assemble_api.py` (link identity carried forward, browser-supplied uuid/slug ignored,
unknown id still mints fresh, `keep_incumbent` preserved / beaten by a new sites.csv / nothing
to keep), 6 in `test_ui.py` (`/api/tenders/{id}`), and `dom_smoke` is up to **78 checks** with a
full register → Edit → prefilled assemble → save walk plus the New-tender reset. All 13 Python
suites + dom_smoke green.
**STILL TO VERIFY on a preview deploy** (neither tests nor jsdom can prove it): a real publish →
edit → re-publish round trip against the live DB, confirming the URL does not change and the
link stays up while the edit sits in draft.

**Session (2026-08-20 — client-dashboard polish + contract dates, both merged &
tested e2e on the preview):** two branches shipped.
(1) **Portfolio presentation pass** (`feat/portfolio-efficiency-diff`) — the site-by-site
table now runs Site · EAC · **Efficiency** · Current · Offer · **Δ**. New per-site
**Efficiency** column = each meter's all-in effective p/kWh as a "current / offered" pair
(`27.4p / 24.8p`); the offered side is pinned to the **best/cheapest** quote, and the header
reads "current / best" when >1 offer is shown. New **Δ vs current** column for the
single-offer case (green = saving); for the multi-offer case each offer cell instead carries
an inline `(−£1,262)` muted delta (the finance-report pattern) and the Δ column is dropped.
Plus a hardcoded **"Portfolio cost efficiency: Medium"** final row on the Summary tab's
Standardised comparison table (always "Medium", always client-facing — a deliberate sales
talking-point, not data).
(2) **Contract start/end dates, end-to-end** (`feat/contract-end-dates`) — new
`supplyEndDate` line field captured through the whole spine: `rye_quote_core.TARGET_FIELDS`
(auto-flows to CSV cols + LLM tool schema + map prompt) → `process_quote.row_to_line` →
`map_headers` SYSTEM_PROMPT (contract start/end column guidance) → `tender.schema.json` line
def → `build_dashboard` (`_write_offer_csv` + site cell + new `derive_end_date`) → template →
`web/app.js` TARGET_FIELDS. Rate books now show **Start + End** columns for quoted offers; a
missing end date is **derived from start + term** and flagged with `*` + a footnote, and left
blank (never guessed) for bespoke/coterminous terms. Incumbent rows show no dates (Rory's
call — only the quoted contract dates matter). The standalone **quote-processing skill** was
updated to match (new `supplyEndDate` target column + date-mapping guidance) and reinstalled
by Rory. Also added an **incumbent client-mismatch warning** at `/api/assemble`
(`assemble_tender.diagnose_sites_csv`): a sites.csv whose `clientName` doesn't equal the
tender's client name now returns a message naming the client(s) actually found in the file,
instead of silently dropping the incumbent — this bit us once (client typed "Test End dates"
vs the file's "Treetop Golf", so every incumbent row was filtered out). All suites green:
`make_and_verify`, `test_extract`, `test_cost`, `test_capacity`, `test_weekend`, `dom_smoke`
(bumped to **14** target fields), `test_assemble_api` (line rate-field sync now excludes
`supplyStartDate` + `supplyEndDate`). See the "UX / feature backlog" section below for what
Rory wants next.

**Session (2026-08-18, branch `feat/publish-webhook`):** a **publish → Retool
webhook** — publishing a client dashboard now fires a fire-and-forget event to a Retool
workflow ("Tender Database Update") that advances each meter's status in RYE's Postgres.
First tie-in between the standalone tenderApp and RYE's existing tendering lifecycle;
env-gated (off unless configured). **Merged and working end-to-end on production 2026-08-19**
— the tenderApp side is done (a nasty Cloudflare user-agent 403 and several Vercel env/deploy
traps burned most of the session; all captured below); the colleague is finishing the Retool
workflow's field mapping. Plus a **market snapshot refresh to 18 Aug 2026**. Detailed in
"Publish webhook → RYE tendering workflow" below.

**Session (2026-08-06, on branch `feat/benchmark-baseline`, PR open, tested on
the branch preview against the live DB):** a client-dashboard presentation pass (tabs
renamed **Summary / Portfolio / Market Review**, all three header KPI rows removed, EAC
moved into the title byline, the duplicated commission figure dropped, small print now
matches how RYE is charging), a new **benchmark baseline** so a tender with no incumbent
rates can still show a saving, and a **market snapshot refresh to 6 Aug 2026**. Detailed
in its own section below.

**Previous session (2026-07-20, merged):** the client dashboard was restructured into
three tabs — then named *Savings / Portfolio / Market context*, renamed 2026-08-06 (a
static market snapshot feeds the Market tab), and a **mapping-cache fix** stopped
re-dated re-tenders of the same supplier template from crashing at extract. Both are
detailed in their own sections below.

Merge history:

- PR #1 — Phase 1 spike: `/api/health`, `/api/db-check`, `/api/inspect`.
- PR #2/#3 — header-detection improvement + weekendRate band end-to-end.
- PR #4 — `/api/map` + `/api/map/confirm` (cached/LLM header mapping).
- PR #5 — map prompt fix (capacityCharge vs kva).
- PR #6 — `/api/extract` + sites.csv EAC/kVA override (db provenance).
- PR #7 — `/api/assemble` (incumbent from sites.csv + versioned tender write).
- PR #8 — `/api/render` (canonical tender → dashboard HTML, inline).
- PR #9 — Phase 2 UI PR 1: team UI shell at `/app` (key gate, upload, mapping
  review/confirm), verified live 2026-07-17.
- PR #10 — Phase 2 UI PR 2: extract + assemble steps; price-ranked `/api/cost` with
  up-to-2 featured offers + price-based recommendation; hardcoded consumption splits;
  reworked weekend costing. (PR-2 note further down.)
- PR #11 — Phase 2 UI PR 3: tender register (`GET /api/tenders`), render preview
  overlay, Step 6; + the sites.csv-at-assemble fix and the zero-kVA warning fix.
- PR #12 — reject stacked-table sheets (guidance, not a crash) + mandatory supplier
  choice on step 1.

**Phase 3 — publish + public client link + app gate (MERGED + live, verified
end-to-end 2026-07-20).** The last functional piece — turning a saved tender into a
live, shareable, unguessable client URL. Key decision (with Rory):
**one Vercel project** serves both the private team app and the public client
dashboards, so access control is an **app-level HTTP Basic gate** in `main.py`
(`team_gate`), NOT Vercel Deployment Protection (which can't exempt a public path on
Pro). This REVISITS the PR-2 "auth removed, use Vercel Deployment Protection"
decision — that plan broke once we needed public client pages on the same deployment.
- **Gate**: when `TEAM_ACCESS_KEY` is set, `/api` + `/app` need HTTP Basic auth
  (password == the key; username ignored; browser handles the prompt — no unlock
  screen). Exempt: `/d/*` (public dashboards), `/api/health`, `/`. Unset = open
  (local dev + tests). **Set `TEAM_ACCESS_KEY` in Vercel (Prod + Preview); leave
  Vercel Deployment Protection OFF.**
- **`POST /api/publish`** `{tender_id}` → new version, `status=published`, mint
  slug + `url_uuid` if absent, set `dashboard_url` to `<host>/d/<slug>/<uuid>`.
- **`GET /d/<slug>/<uuid>`** (public, noindex) → serves the dashboard only if the
  LATEST version still carries that uuid AND is published AND not past `expires_at`;
  else an expired / unavailable page. `_get_tender_by_uuid` fetches the latest
  version of the tender that owns the uuid, so a rotated uuid kills old links.
- **`POST /api/revoke`** `{tender_id}` → new version with a fresh `url_uuid` +
  `status=draft` → the old link 404s (leaked-link kill switch). Re-publish mints a
  new link.
- **UI**: Step 6 "Publish client link" (live URL + copy/open); register shows the
  link + a Revoke action for published tenders.
- **`vercel.json`** pins the function region to `lhr1` (London) for UK/EU latency +
  residency intent.
- Tests: `tests/test_publish.py` (publish/revoke/public-route states, DB mocked),
  `test_ui.py` gate test updated, `dom_smoke.js` walks publish. Full suite green.
- **Data residency (still open, not a functional blocker):** Retool Cloud stores the
  DB on AWS in Retool's region (effectively US; NOT EU-configurable on Team — that's
  Enterprise/self-host). "Outbound regions" only affect egress routing, not storage.
  The stored tender data is mostly B2B commercial (company names, site addresses,
  MPANs, EAC, rates) — MPAN isn't PII on its own, but sole-trader clients / residential
  supply addresses can be personal data. Decision pending: accept US under Retool's
  DPA/SCCs, OR point `RETOOL_DATABASE_URL` at an EU/UK Postgres (e.g. Neon `eu-west`;
  ~30 min, DDL in `schema/retool_tables.sql`, app is DB-agnostic). Build on the
  current DB with TEST data until decided.

**OPEN BRANCH (not yet merged): `feat/team-ui-render-register`** — Phase 2 UI PR 3:
the render preview + tender register (see the PR-3 note below). Frontend + one small
read-only endpoint (`GET /api/tenders`); no schema change. Full Python + DOM smoke
suites green. This is everything up to — but not including — the live per-client URL,
which is the Pro-gated publish step (Phase 3).
Once merged this is the new `main`. See the PR-2 note below for the detail.

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

**Phase 2 UI — PR 1 MERGED (PR #9) and verified live.** `TEAM_ACCESS_KEY` is set
in Vercel (Prod + Preview). Next up is UI PR 2 — details in Next steps item 5.
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

**Phase 2 UI — PR 2 (open branch `feat/team-ui-extract-assemble`, built 2026-07-17).**
Frontend-first extension of the wizard, plus one small backend change and the auth
decision below.
- **Step 4 Extract**: one shared `sites.csv` slot feeds BOTH `/extract` (site-ref
  join) and `/assemble` (incumbent). "Extract confirmed files" POSTs each confirmed
  file (`file` + `mapping` JSON string + optional `site_reference`); per-file counts
  shown and `unmatched_mpxn` flagged in red (never silently accepted).
- **Step 5 Assemble** (REVISED per the founder steer): NOT a recommended-offer
  dropdown. Instead a price-ranked **include tick-list** — on entering the step the
  UI POSTs the extracts to `/api/cost` (below), which returns each offer's
  standardised all-in annual cost + effective p/kWh + `covers_all_sites`, sorted
  cheapest-first with the cheapest FULL-COVERAGE offer badged. The team ticks up to 2
  offers to show the client (two cheapest pre-ticked, max 2 enforced); the cheapest
  ticked becomes the recommendation (price-based, never hand-picked on a whim). RYE
  fee (90/80), expiry and notes remain; the day/weekend split inputs were REMOVED
  (splits are hardcoded now — see below). On save the UI flags `featured` on the
  chosen quote objects and POSTs `/api/assemble` (`extracts` + `meta` + shared
  `sites_csv`, `persist=true`); returned version + warnings are the pre-publish gate;
  the tender `id` is stored so a re-save bumps the version.
- **`/api/cost` (new deterministic endpoint)**: assembles a throwaway tender from the
  extracts and runs the EXISTING `build_dashboard.compute_offer` per offer (NEVER a JS
  cost calc — one source of truth), returning `{offers:[{index, supplier, term,
  annual_cost, effective_pkwh, covers_all_sites, cheapest, warnings}], site_count,
  eac_total, day_split, weekend_split}`. Cheapest = min annual_cost among
  full-coverage offers (a partial-cover offer is shown but never badged/ranked
  cheapest). Covered by `tests/test_cost.py`.
- **Featured offers**: the quote schema gains an optional per-quote `featured` bool.
  ALL extracted offers are stored on the tender (full audit record);
  `build_dashboard.render_tender` shows ONLY the featured ones (falls back to all if
  none are flagged), so the client never sees more than the chosen ≤2. `assemble()`
  carries `featured` through untouched. NB: `recommended` must point at a featured
  offer or `build_dashboard` raises — the UI guarantees this (recommended = cheapest
  featured).
- **Splits are now HARDCODED, not per-tender inputs** (founder steer: zero friction).
  `DAY_SPLIT_DEFAULT = 0.7` (Economy-7 17:7 day:night) and `WEEKEND_SPLIT_DEFAULT =
  2/7` live in `rye_quote_core`; `assemble()` and the cost engine default to them and
  the UI no longer collects them. **Weekend costing was reworked** in
  `build_dashboard.compute_offer`: the weekend share now applies ONLY to offers that
  actually quote a weekend rate, split multiplicatively so the fractions always sum to
  1 — a 2-band day/night offer stays 0.7/0.3; a 3-band offer becomes ≈0.50/0.21/0.29
  day/night/weekend — and it's footnoted as a flat-week assumption. (Previously
  `weekend_split` was subtracted tender-wide from `night_frac`, so a nonzero default
  would have zeroed out night for plain day/night offers — that's why this had to be a
  cost-engine change, not just a default. `tests/test_weekend.py` updated to the new
  numbers.)
- **AUTH (this bullet is now SUPERSEDED — see the Phase 3 note above).** PR-2 removed
  the PR-1 gate and planned to use Vercel Deployment Protection instead. That plan was
  reversed in Phase 3: once the public client dashboards had to live on the same
  deployment as the private team app, Deployment Protection couldn't exempt them on
  Pro, so an **app-level HTTP Basic gate came back** (`team_gate` in main.py, keyed on
  `TEAM_ACCESS_KEY`, exempting `/d/*`). Net: `TEAM_ACCESS_KEY` is live and required
  again; leave Vercel Deployment Protection OFF.
- Tests: `test_ui.py` now asserts open access + `/api/auth-check` is 404;
  `dom_smoke.js` walks unlock-free load → … → extract → assemble (28 checks). Full
  Python suite + DOM smoke all green. (jsdom is an ad-hoc local dep: `npm i jsdom`;
  `node_modules/`, `package.json`, `package-lock.json` are gitignored.)
- Step 6 (Publish) is unlocked as a preview step in PR 3 (below); the actual
  publish-to-live-URL remains Phase 3 / Pro.

**Phase 2 UI — PR 3 (open branch `feat/team-ui-render-register`, built 2026-07-17).**
The render preview + tender register — everything up to, but not including, the live
per-client URL (that's the Pro-gated publish step, Phase 3). Frontend-led plus one
small read-only endpoint.
- **`GET /api/tenders` (new)**: the team register. Read-only over the
  `tenders_latest` view — scalar columns + `jsonb_array_length` site/offer counts +
  `payload->recommended->>supplier`, newest first. Degrades to `{tenders:[], note}`
  with no DB (like `/api/suppliers`). This is NOT a layer over the quote-to-dashboard
  skill — it's just a DB read. Covered by `tests/test_ui.py`.
- **Register screen** (`#screen-register`, top-nav toggle "New tender" / "Register"):
  lists every tender (latest per id) with client, label, status chip, version,
  counts, saved date, recommended supplier, and a **Preview** action per row.
- **Render preview overlay**: a full-screen sandboxed `<iframe srcdoc>` showing the
  real client dashboard HTML from `/api/render` (fetched via a new `apiText` helper —
  render returns HTML, not JSON). Reused by the register (by `tender_id`) and by the
  wizard's Step 6.
- **Wizard Step 6 "Preview & publish"** (unlocked): after assemble, shows the tender
  meta + the **would-be** client URL (`rye.energy/<slug>/<url_uuid>`, from the stamped
  slug/url_uuid), a **Preview client dashboard** button, and a **disabled Publish
  button** noting it needs Vercel Pro. No new backend — publish is the only piece held
  for Phase 3.
- **Backend surface is now complete** up to publish: the endpoints over the
  quote-to-dashboard cost engine (`build_dashboard`) are `/api/cost` (ranking numbers)
  and `/api/render` (dashboard HTML); everything else is extraction/mapping
  (`/api/inspect|map|extract`), assembly (`/api/assemble`), or plumbing
  (`/api/suppliers|tenders|health|db-check`).
- Tests: `test_ui.py` gains the register test; `dom_smoke.js` walks Step 6 preview
  (iframe loads the rendered HTML; publish gated) + the register list. Full suite green.
- **Bugfix (same branch): site names showed as MPANs on the dashboard.** Root cause:
  RYE's site names/EAC were only overlaid at `/extract`, so if the sites.csv wasn't
  present when a quote was extracted (e.g. added later), the tender kept MPAN-only
  names. Fix: the sites.csv is now authoritative wherever a tender is built from
  extracts — a new `assemble_tender.apply_site_reference()` (reuses
  `process_quote.build_site_lookup`, so column contract + MPAN keying don't drift)
  overlays site name + authoritative EAC/kVA (provenance `db`), and it's applied at
  `/api/assemble` AND `/api/cost` (the UI now sends the shared sites.csv to `/api/cost`
  too, so the ranking EAC matches the render). Idempotent with `/extract`. Covered by
  `test_assemble_api.test_site_reference_override`.
- **Bugfix (same branch): spurious "no kVA figure — excluded" notes on the client
  dashboard.** A capacity charge of `0` quoted per kVA, on a site with no kVA, was
  firing the "excluded from that site's total" warning even though excluding £0 is a
  no-op. Fix: `build_dashboard.annualise` now treats a `0`/None charge as nothing to
  cost (returns 0, no warning); a genuine NON-ZERO per-kVA charge with no kVA still
  warns (real undercosting). Covered by `tests/test_capacity.py`. (Open design
  question for later: whether internal "excluded" warnings should show client-side at
  all, or only in the team pre-publish review — left as-is for now.)

## Deployment & ops status (as of 2026-07-20)

- **Hosting:** RYE company **Vercel Pro** account, project `tender-app`, live at
  `tender-app-chi.vercel.app`. (Migrated off Rory's personal hobby account — the app
  is Git-backed + DB-backed, so the move was just a re-import.) **TODO:** disconnect
  the Git integration on (or delete) the OLD hobby project so a push doesn't deploy
  to both.
- **Env vars (Production + Preview):** `TEAM_ACCESS_KEY` (team Basic-auth password),
  `RETOOL_DATABASE_URL`, `ANTHROPIC_API_KEY` (freshly minted during the move — old
  key revoked), optional `ANTHROPIC_BASE_URL`. Vercel never shows these again; re-add
  from source if the project moves.
- **Access:** app-level Basic gate (see Phase 3). **Vercel Deployment Protection is
  OFF and must stay off** (it can't exempt the public `/d/*` client route on Pro).
- **Region:** `lhr1` (London) via `vercel.json`.
- **Spend:** no hard cap — Spend Management is account-global at RYE and a cap could
  pause the whole company product; **notifications enabled** instead. (This tool's
  spend is tiny — cache-suppressed Claude mapping calls.)
- **Custom domain:** `tender.rye.energy` added in Vercel, pending GoDaddy DNS (CNAME
  `tender` → the vercel-dns value + a `_vercel` TXT to release it from the old hobby
  account). Optional/cosmetic — the app works on the vercel.app URL. Domains are at
  **GoDaddy**; main-site hosting is elsewhere, so only this one subdomain points at
  Vercel (root site + email + `runonrye.com` untouched).

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
through, never computed *by assemble* — the price ranking is done by `/api/cost`
(the cost engine) and the UI passes the cheapest featured offer as `recommended`
(see PR-2). Completes the headless "quotes in → tender JSON out" pipeline.

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
- **YU "stacked tables" export** (e.g. Rosslyn Coffee): a sheet holds TWO rate
  tables under separate header rows (single-rate block, then a day/night block lower
  down), and layout differs per sheet. **This is REFUSED by design, not parsed.** A
  single header row can't describe two tables, so `map_headers.stacked_tables_in_sheet`
  detects the repeated header (a data row never reproduces header text — low false
  positive) and `process_quote.run` raises a clear message telling the user to split
  each rate table onto its own sheet and re-upload. Flagged early as a `/api/map` note;
  `/api/cost` also guards a degenerate (no-priced-rows) offer with a clear 422. Decision
  (2026-07-17, with Rory): one rate table per sheet is the expected hygiene — crash +
  guide beats silently mis-reading. Covered by `tests/test_stacked.py`.

## How to run / verify

```bash
cd ~/dev/tenderApp
source .venv/bin/activate          # macOS venv; needed for this project's Python
python3 tests/make_and_verify.py   # extraction→schema→assemble→dashboard, no drift
python3 tests/test_assemble.py     # multi-extract merge / dedupe / versioning
python3 tests/test_weekend.py      # weekend band: capture + warn-vs-cost
python3 tests/test_map.py          # /api/map: fingerprint, cache-vs-LLM, confirm, resync_sheets (mocked)
python3 tests/test_extract.py      # /api/extract: value pass-through, site-ref join, 400s
python3 tests/test_assemble_api.py # /api/assemble: incumbent-from-sites.csv + endpoint (DB mocked)
python3 tests/test_render.py       # /api/render: canonical->HTML adapter + featured filter + endpoint
python3 tests/test_cost.py         # /api/cost: price ranking, cheapest full-coverage, degenerate-offer guard
python3 tests/test_capacity.py     # annualise(): zero per-kVA charge is silent; real one still warns
python3 tests/test_stacked.py      # refuse a sheet with two stacked rate tables (guidance, not a crash)
python3 tests/test_publish.py      # publish / public /d/<uuid> route / revoke (DB mocked)
python3 tests/test_ui.py           # team UI: Basic-auth gate (public /d/* exempt), static /app, /suppliers, /tenders
node tests/dom_smoke.js            # optional: jsdom walk of the whole wizard incl. preview + register (npm i jsdom first)
```
All Python tests should print their "ALL … PASSED" line. No network needed (the LLM and DB
are mocked in test_map / test_assemble_api / test_render / test_ui).
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

**Phases 0–3 are all DONE and merged** (backend pipeline, team UI PR 1–3, and Phase 3
publish/link/gate above). The build is functionally complete and live on Vercel Pro.

## What's left (housekeeping + decisions, none blocking the app from running)

1. **Data residency decision** (before REAL client data — test data is fine now).
   Retool Cloud stores the DB on AWS in Retool's region (effectively US; not
   EU-configurable on Team — that's Enterprise/self-host). The stored data is mostly
   B2B commercial (company names, site addresses, MPANs, EAC, rates); MPAN alone isn't
   PII, but sole-trader clients / residential supply addresses can be. Options: accept
   US under Retool's DPA/SCCs, OR point `RETOOL_DATABASE_URL` at an EU/UK Postgres
   (e.g. Neon `eu-west`; ~30 min, DDL in `schema/retool_tables.sql`, app is
   DB-agnostic). No code change needed either way beyond the connection string.
2. **Disconnect / delete the OLD hobby Vercel project** so a push doesn't deploy to
   both accounts (Settings → Git → Disconnect).
3. **Finish the custom domain** `tender.rye.energy` (GoDaddy CNAME + `_vercel` TXT;
   see Deployment & ops status). Optional/cosmetic; publish a tender *after* it's live
   so the client links carry the custom host.
4. **Future / nice-to-have** (not required): swap the shared-key gate for real **SSO**
   via a two-project split (private team app + separate public client-pages project)
   if per-person auth is wanted; a light audit/version-history view; turning "excluded"
   cost warnings into team-only (not client-facing) notes. **Visual design polish is
   the next session** — see below.
5. **Register cleanup — remove expired/test tenders** (requested 2026-07-20, not
   urgent). The register (`GET /api/tenders` over the `tenders_latest` view) is
   read-only; there's no way to remove clutter. Add a delete/archive action. Prefer a
   **soft delete** (bump a new version with `status = 'archived'`, filtered out of the
   register) over a hard row delete, to keep the versioned audit trail — and it reuses
   the existing versioning + the `_get_tender_by_uuid` "latest version wins" logic, so
   archiving also kills any live client link (like revoke). A hard purge of test rows
   is a separate admin/SQL job. New surface: a small `POST /api/tenders/archive`
   `{tender_id}` + a per-row action in `web/app.js`; cover with a test like
   `test_publish.py`.

6. ~~**Commission option instead of the RYE fee**~~ **DONE 2026-07-31** (see
   Recently done). Commission = a **unit uplift (p/kWh)** on total consumption. Final
   client-facing treatment: headline tiles lead with the energy deal (consumption /
   blended rate excl. commission / total spend) and commission is a **line item** in
   the breakdown, not a headline figure. Includes an **"already included in the supplier
   rate" toggle** so a baked-in commission is shown as included (not added on top, and
   the saving isn't reduced by it) — prevents double-counting.
7. **Multiple suppliers in one tender** (gap — becomes pressing as tenders go
   multi-supplier). The data model already supports it (`tender.quotes[]`, each quote
   carries its own `supplier`), but the **wizard collects ONE supplier at step 1 and
   stamps it on every uploaded file**: `/extract` passes `state.meta.supplier` into
   `process_quote.run`, which sets `quote.supplier = supplier or mapping.get("supplier")`
   for every offer. So today a tender is effectively single-supplier (multiple *terms*
   of one supplier vs the incumbent). Fix: move supplier selection to **per-file** (at
   upload / map-review) so each quote carries its own supplier — which also makes the
   mapping cache key `(supplier, fingerprint)` correct per file. Phase-2-UI enhancement;
   no schema change.
8. **Navigate/edit an existing tender in the wizard** (requested 2026-07-31). Today the
   wizard is forward-only and there's no way to edit a saved tender — you start again.
   Wanted: move freely back/forward between steps, and load a saved tender from the
   register to edit (re-map, re-tick offers, change fee/commission) rather than rebuild.
   Bigger Phase-2 UI/state piece (wizard state persistence + hydrate from a stored
   tender). Pairs naturally with item 5 (register actions — which the founder
   reconfirmed 2026-07-31, specifically deleting UNPUBLISHED tenders to cut clutter).

**Recently done (2026-07-31):**
- **Commission option (p/kWh uplift), instead of the flat fee — built end-to-end.**
  Model (with founder): commission = a **unit uplift (p/kWh)** on total consumption,
  shown to the client as a single £/yr figure plus the unit rate **with and without**
  the uplift. Pieces: schema `rye_commission {p_kwh_uplift}` (mutually exclusive with
  `rye_fee`); `assemble_tender._build_rye_commission` reads a flattened
  `commission_p_kwh_uplift` from meta, stamps `rye_commission` and omits `rye_fee`;
  `build_dashboard` emits a `commission` payload block (annual = uplift × total kWh,
  `baseEffective`, `withUpliftEffective`, net saving after commission) and sets
  `fee=None` when it's present; `render_tender` now carries `rye_commission` through;
  the dashboard renders `buildCommission()` (no slider) in the Summary tab (with
  incumbent) or the Portfolio tab (without). Wizard **step 5 has a "How RYE is paid"
  toggle** (fee | commission) that swaps the fee inputs for a p/kWh-uplift input
  (`web/index.html`, `web/app.js`). Verified schema-valid + assemble + jsdom render
  (both tabs, rates with/without, no fee slider) + full suite + dom_smoke. **Still to
  do:** a dedicated commission unit test (currently covered indirectly by
  render/assemble suites).
- **Total consumption (EAC) card** added to the client dashboard — first KPI on both
  the Savings and Portfolio rows (`buildSavingsKpis` / `buildPortfolioKpis` in
  `assets/dashboard_template.html`), in kWh/yr (or GWh for large portfolios).
  **Superseded 2026-08-06:** all three KPI rows were removed and the EAC now lives in
  the title byline. Those three builder functions no longer exist.
- **sites.csv filename friction removed.** The app never required the name `sites.csv`
  — `_save_upload` checks the extension only, and the picker is `accept=".csv"`, so a
  dated Retool export (`sites_2026-07-31_122851.csv`) already works. Updated the wizard
  copy + button (`web/index.html`, `web/app.js`) so no one hunts through Downloads to
  rename it.

## UX / feature backlog (from Rory, 2026-08-20)

Current priorities gathered after the dashboard + contract-date work landed. Some expand
earlier "What's left" items (cross-referenced); none are blocking the live app.

- **Edit steps mid-journey.** *Half done 2026-08-27:* editing a SAVED tender from the register
  is built (Tier 1 — see the design section below, now implemented), so re-ticking offers and
  changing fee/commission no longer means rebuilding. What remains is free back/forward movement
  WITHIN a fresh wizard run before the first save, and Tier 2 (re-uploading/re-mapping one quote
  inside an edit session).
- ~~**"New tender" button does nothing.**~~ **DONE 2026-08-27** — `resetWizard()` + `newTender()`
  in `web/app.js`, wired to both `nav-new` and `btn-register-new`. The same reset primitive is
  reused when entering Edit.
- ~~**Confirm-mapping / save-to-cache should close the mapping panel.**~~ **DONE 2026-08-27** —
  `confirmMap()` now calls `showStep(2)` and puts its success notice on `step2-msg`.
- **Delete tenders from the register (incl. the DB row).** Testing leaves lots of draft
  tenders cluttering the register with no way to remove them. Rory specifically wants a real
  delete (hard purge of test/draft rows), not only a soft-archive. *Expands "What's left"
  item 5, which proposed a soft delete/archive to preserve the audit trail — reconcile the
  two: probably soft-archive for published tenders, hard-delete for drafts/test rows.*
- **Test mode.** A sandbox/dry-run mode so testing doesn't create real register clutter or
  demand exact client-name matches — e.g. a "test" flag that excludes a tender from the
  register, and/or a way to skip/relax incumbent `clientName` scoping for dry runs. *New;
  surfaced directly by the 2026-08-20 "Test End dates" incumbent-mismatch episode. Pairs with
  the register-delete item.*
- **Custom domain in Vercel.** Finish `tender.rye.energy` (GoDaddy CNAME + `_vercel` TXT).
  *Still open — see "What's left" item 3 and "Deployment & ops status".*
- **Retool consolidation ("Path B").** Explore bringing the whole tender flow into Retool to
  link directly to RYE's Postgres (select sites → CSV → Front email → status transitions, all
  in one place, no handover). The longer-term direction beyond the current publish-webhook
  tie-in ("Path A"). *See "Publish webhook → RYE tendering workflow".*

## Design: edit a tender from the register (scoped 2026-08-20, Tier 1 BUILT 2026-08-27)

**Tier 1 is now implemented on `feat/live-link-edits`** — the section below is the design as
scoped, kept because Tier 2 is still open and the reasoning still applies. Deltas from the plan
as built: the register row's Edit action sits beside Preview as designed; `keep_incumbent` is a
plain bool form field and the kept block is re-fetched server-side as specified; hydration also
**restores the previously-featured offers** (not in the original scope — without it a re-save
silently reverts to the two cheapest); and saved rows are marked `fromSaved` so the extract step
cannot try to re-extract a file that was never uploaded.

The top UX ask (expands "UX / feature backlog" item 1). Goal: stop rebuilding a tender from
scratch to change small things — load a saved tender from the register and edit it.

**Hard constraint that shapes everything:** the raw uploaded quote files are NOT stored — only
the *extracted result* (sites + quotes with rates) lives in the saved tender payload. So there
are two tiers, and Tier 1 is the one to build first.

**Tier 1 — edit WITHOUT re-uploading (build first; ~one focused session).**
Everything the assemble step produced is already in the stored tender, so this needs no
re-extraction:
- **New endpoint `GET /api/tenders/{id}`** (optional `?version=`) → the full stored payload as
  JSON, team-gated, degrades to 404 / `{tenders:[]}`-style with no DB. Reuses the existing
  `_get_tender()` (today only `/api/render` consumes it, as HTML). Cover with a test like
  `test_ui`/`test_render`.
- **Register "Edit" action** (per row, beside Preview/Revoke) in `web/app.js` + `index.html`.
  On click: fetch the payload, hydrate wizard state, jump straight to the assemble/review step
  (step 5).
- **Hydration (payload → `state`):**
  - `state.meta.id = payload.id` → re-save bumps to the next version via the existing path.
  - `state.meta.client_name / tender_label / utility` from payload.
  - Build ONE synthetic extract `{sites: payload.sites, quotes: payload.quotes}` and drop it in
    as a single confirmed `state.files` entry with `.extract` set, so the existing
    `openAssemble()` path (`POST /api/cost` with `extracts`) re-ranks from stored data. Stored
    `sites[]` already carry authoritative EAC (`eac_source:"db"`), so ranking matches the render
    WITHOUT re-uploading sites.csv.
  - Pre-tick `state.featured` from `payload.quotes[].featured` (fall back to 2 cheapest, as now);
    prefill fee↔commission toggle + values from `payload.rye_fee` / `payload.rye_commission`;
    recommendation from `payload.recommended`; expiry from `payload.expires_at`; benchmark fields
    from `payload.incumbent` when `kind==="benchmark"`.
- **Incumbent on edit — Rory's choice: "let me choose per edit".** Default = PRESERVE the stored
  incumbent; allow replacing it. Precedence on save: new sites.csv > new benchmark > preserved
  stored incumbent > none. Implementation: add a `keep_incumbent: bool` form field to
  `/api/assemble`; when true AND no new sites_csv AND no benchmark, the endpoint **re-fetches the
  stored tender by `meta.id` server-side and copies its `incumbent` block into the new version**
  — do NOT accept a client-sent incumbent block (keeps "code owns the numbers"; a client-facing
  doc's baseline rates must never be injectable from the browser). UI on the review screen:
  "Current: <supplier> (kept from saved tender)" with Keep / Replace with sites.csv / Use
  benchmark.
- **Save:** `doAssemble()` essentially unchanged — flags featured, POSTs `extracts + meta`
  (+ `keep_incumbent` or a new sites_csv/benchmark). Version bumps; re-publish as normal (the
  live `/d/` link renders the latest published version, so it updates on re-publish).
- **Tests:** `GET /api/tenders/{id}`; the `keep_incumbent` assemble path (`test_assemble_api`);
  a `dom_smoke` walk register → Edit → prefilled assemble → save.

**Tier 2 — replace / re-map a single quote (later, bigger).** To fix a mis-mapped rate or add a
supplier, that one file must be re-uploaded (we don't keep it) → map → extract → join the
existing extracts, other quotes untouched. Reintroduces the upload→map→extract path inside an
edit session; defer until Tier 1 is in use.

**Build-once primitive:** a proper wizard **state-reset** helper is needed by BOTH the broken
"New tender" button (backlog item 2) and entering Edit (reset, then hydrate). Write it once and
reuse. Editing always creates a new version, so the audit trail is preserved.

## Client dashboard — tabs + Market Review (done 2026-07-20, renamed + trimmed 2026-08-06)

The client-facing dashboard (`assets/dashboard_template.html`, served at
`/d/<slug>/<uuid>`) is a **three-tab** layout, built client-side from the
injected `__TENDER_DATA__`. Tab labels are **Summary / Portfolio / Market Review**; the
pane ids are still `tab-savings` / `tab-portfolio` / `tab-market`, so don't be thrown by
`savings` in the code — it means Summary.
- **Summary** — the benchmark callout (benchmark baselines only), the
  recommendation-vs-baseline panel, and the offer comparison bars. Shown and defaulted
  to **only when the tender has an `incumbent`** (real or benchmark).
- **Portfolio** — the per-site detail, unchanged (offer breakdown, site-by-site
  matrix, rate books). Default tab when there's no baseline (two-tab fallback).
- **Market Review** — chart-led (power spot + 1y series, 5-year range, gas curve
  + cards, written takeaway). Hidden when there's no market data. **This is the only
  tab that still has a KPI card row** — the three tender KPI rows were removed
  2026-08-06 because every figure they carried is in the comparison table.

**Market data = a static snapshot, `assets/market_snapshot.json`.** `build_dashboard`
loads it (next to the template) and injects it into the render payload as `market`;
absent/invalid → `null` → the Market tab hides itself. Refresh by hand-editing the
JSON and redeploying; the file's `_note` explains the shape. Current contents are a
real snapshot taken **2026-08-06** from ICE / Trading Economics screenshots (power spot +
gas curve read from source; the 1y/5y power *series* are shape-traced, not
tick-exact).

**Published dashboards are rendered LIVE, not stored.** `GET /d/{slug}/{url_uuid}`
calls `build_dashboard.render_tender(tender)` on every request, reading
`assets/dashboard_template.html` and `assets/market_snapshot.json` from the deployment at
that moment. So refreshing the market JSON and redeploying updates **every already-published
client link** — there is no need to re-render or re-publish anything. The flip side: a
template or market-data change is instantly visible to every client holding a live link,
so treat both files as production. (Verified 2026-08-06 while chasing what turned out to
be a stale browser tab.)

The palette is kept deliberately on the **live dashboard's older palette**
(emerald/amber) so the new tabs match the existing page — NOT the design-system
skill's newer green/blue. A full palette migration is a separate, deliberate pass.

**Market data — the open decision (deferred).** Live-at-render was rejected (paid
ICE/Tradingeconomics APIs cost thousands; free-feed wiring wasn't worth it this
session). RYE *does* have a weekly-updated market API, but it lives in the separate
private **`rye-energy/api`** repo (business logic; `app.rye.energy` is the Next.js
front end) and we didn't have the endpoint/shape to hand. To wire it later: grab one
real JSON response from that repo's market route, write a thin adapter (their fields →
the `market_snapshot.json` shape), and either stamp it at publish or fetch
server-side at render with a daily cache. (Claude's sandbox can't push, so a
repo-file refresh always comes back as paste-safe git commands, not a silent write.)

**Founder's visual feedback — closed 2026-08-06.** The outstanding tweaks (tab names, KPI
rows, EAC placement, duplicated commission figure) were specced from annotated screenshots
and built; see the next section.

## Dashboard presentation pass + benchmark baseline (done 2026-08-06)

Branch `feat/benchmark-baseline`, two commits, PR open. Driven by founder feedback on a
live commission tender. Both suites green (`tests/test_render.py`,
`tests/dom_smoke.js`) plus six render variants exercised in headless Chromium with no JS
errors: real incumbent × (fee | commission-included | commission-on-top), benchmark ×
(fee | commission), and no baseline at all.

**1. Presentation changes (`assets/dashboard_template.html`)**

- **Tabs renamed** Savings → **Summary**, Market context → **Market Review**. Pane ids
  unchanged.
- **All three header KPI rows deleted** — `buildSavingsKpis` (5-card, fee variant),
  `buildCommissionKpis` (3-card, commission variant) and `buildPortfolioKpis`. Every
  figure they held is in the standardised comparison table; the rows also wrapped badly
  at 3–4 cards. There is a comment in the file saying don't reinstate them. Market Review
  keeps its own cards.
- **EAC moved into the title byline**: "Prepared for {client} by RYE. 1.16 GWh tender."
  via `eacHeadline()` — GWh ≥ 1 GWh, MWh ≥ 1 MWh, else kWh. Styled as the smaller grey
  mono, so only "Prepared for X by RYE." carries the heavier weight. Known cosmetic edge:
  999,999 kWh renders "1,000 MWh" rather than rolling to 1.00 GWh.
- **Commission de-duplicated.** When `commission.included` is true the net-saving-after-
  commission figure is arithmetically identical to the gross saving printed directly above
  it, so it now renders as a single disclosure line ("RYE commission 0.6 p/kWh · £1,020/yr
  · already in the rate above"). When commission is added **on top** the full block stays,
  because the number genuinely differs.
- **Small print is charging-model aware** (`buildFooter`). The old footer claimed
  "independent, flat-fee energy brokerage — we take no supplier commission" on *every*
  tender, which directly contradicted the Basis-of-comparison line above it on commission
  tenders. Now: flat-fee wording when `fee` is set, otherwise "independent energy
  brokerage · we work for you, not the supplier".

**2. Benchmark baseline — the client's rates are unknown, but they still see a saving**

Previously a tender with no incumbent rates rendered with **no Summary tab at all**. Now
the operator can type a market average and it flows through the existing incumbent code
path untouched — the cost engine, comparison table, bars and site matrix needed no
changes.

- `schema/tender.schema.json` — `$defs.incumbent` gains **`kind`** (`incumbent` |
  `benchmark`; absent = incumbent, so every pre-existing tender keeps its old wording),
  plus `as_at` and `source`. **`as_at`/`source` are accepted but no longer collected or
  displayed** — see "removed as clutter" below. They stay in the schema so tenders
  assembled during the first commit remain valid.
- `pipeline/assemble_tender.py` — new **`incumbent_from_benchmark(mpxns, unit_rate,
  standing_charge=None, ...)`** applies one rate card to every meter in the tender and
  returns the same shape as `incumbent_from_sites_csv`. Returns `None` on an unparseable
  or missing rate, or no meters. Deliberately **two numbers, not a full rate card**:
  inventing an average capacity or network charge per meter would dress a guess up as
  precision, and non-commodity costs largely cancel out of the saving.
- `main.py` `/api/assemble` — new form fields `benchmark_unit_rate`,
  `benchmark_standing_charge` (`benchmark_as_at`, `benchmark_source` still accepted but
  unused). **A real incumbent always wins**: the benchmark is only used when sites.csv
  yields nothing, and if both are present the actual contract is used and a warning says
  the benchmark was ignored.
- `pipeline/build_dashboard.py` — carries `kind`/`as_at`/`source` into the render config
  and emits a **`baseline`** payload block (`{kind, asAt, source}`), plus a Basis-of-
  comparison line stating the baseline is a benchmark and the saving is indicative.
- Dashboard — when `baseline.kind === "benchmark"`: an amber **"Benchmarked, not billed"**
  callout above the recommendation (disclosure doubling as the ask for their real rates),
  the baseline column headed **Market benchmark** not *Current*, the saving relabelled
  **Indicative annual saving**, and a `benchmark` chip instead of `current` on the bar,
  matrix and site detail.
- Wizard step 5 — tickbox **"No incumbent rates — compare against a market benchmark"**
  reveals the two fields. Ticking it without a unit rate blocks assemble with a message
  rather than silently producing a baseline-less dashboard.

**Removed as clutter (same session, second commit):** the "Rates as at" and "Source"
fields were built, then removed at the founder's request from the wizard, the callout and
the small print. Trade-off accepted knowingly: the benchmark is no longer dated or
attributed on the client-facing page. If a client ever challenges a benchmark saving,
that's the gap.

**Operator guidance that matters more than the code.** The wording says "UK market
average costs", so the number typed must be an average of **what businesses currently
pay**, not the sharpest available new-contract rate — benchmark against today's best
offers and the saving collapses to near zero, because RYE's offers *are* new-contract
rates. DESNZ/Ofgem publish quarterly non-domestic averages; that's the defensible source.
Also note the "optional" standing charge does real work: on a 518 MWh test portfolio,
750 p/day accounted for roughly £3.2k of a £30.4k headline saving (~10%). Leave it blank
unless there's a basis for it.

**Still to do:** a dedicated unit test for the benchmark path (currently covered
indirectly by the render/assemble suites and the manual variant sweep).

## Mapping-cache stale-sheet fix (done 2026-07-20)

Symptom: a re-tender of the same supplier template failed at extract with
`KeyError: 'Worksheet <old date> does not exist'`. Cause: the layout fingerprint
deliberately ignores sheet names (they carry dates), so a re-dated file hits the
**cached** mapping — but that mapping still carried the *previous* file's sheet
names, and `process_quote.load_rows` opens sheets by exact name. Fix:
`map_headers.resync_sheets(mapping, inspection)` re-points the cached `sheets`
positionally onto the uploaded file's actual sheet names (safe because an identical
fingerprint guarantees the same sheet count + order) and re-keys `term_labels`; a
count mismatch (a mapping that excluded sheets) is left untouched and flagged, never
guessed. Called on every cache hit in `/api/map`, which also adds a review-screen
note if any referenced sheet still isn't in the file. Covered by
`tests/test_map.py::test_resync_sheets`. Matters because re-tendering the same client
repeatedly is common.

## Publish webhook → RYE tendering workflow (2026-08-18, branch `feat/publish-webhook`)

**What it does.** On `POST /api/publish`, tenderApp fires a fire-and-forget webhook to a
downstream **Retool workflow** ("Tender Database Update"), which keys on the meter points
to advance each site's status in RYE's Postgres (e.g. `AWAITING_QUOTE` →
`PREPARING_CONTRACT`). Deliberately **event-only**: tenderApp knows nothing of RYE's
schema, it just emits the event and the Retool workflow owns the DB write. This is
"**Path A**" (a quick lifecycle tie-in) chosen ahead of the larger "Path B" (embedding
the whole tender flow inside Retool — select sites → CSV → Front email → status
transitions, all in one place, no handover). Path B is the longer-term direction.

**Where it lives.** `main.py`: `_fire_publish_webhook(tender)` + `_tender_mpxns(tender)`,
called at the end of `publish_endpoint` after `_write_tender`. Stdlib `urllib` only — no
new runtime dependency. Isolated test: `tests/test_publish_webhook.py` (5 cases; needs
`httpx2` for the TestClient — see requirements.txt).

**Config — two Vercel env vars, both unset by default (webhook off ⇒ publishing unchanged):**
- `PUBLISH_WEBHOOK_URL` — the full Retool endpoint, e.g.
  `https://api.retool.com/v1/workflows/<id>/startTrigger?environment=staging`. The VALUE
  must be the URL (starts with `https://`). Unset ⇒ webhook skipped.
- `PUBLISH_WEBHOOK_SECRET` — the Retool workflow **API key** (`retool_wk_…`), sent as the
  **`X-Workflow-Api-Key`** header (Retool's scheme — NOT `Authorization: Bearer`). Retool
  keys are **per-environment**: staging URL needs the staging key, prod URL the prod key;
  a prod key against the staging URL returns **403**.

**Payload** (POST JSON): `event:"published"`, `tender_id`, `version`, `client_name`,
`tender_label`, `utility`, `mpxns` (array of every site meter point — the join key the
workflow matches on), `dashboard_url`, `url_uuid`, `expires_at`, `created_by`,
`created_at`. The workflow's JS should do a **parameterised** update (bind mpxn + target
status, never string-built SQL) and **guard the transition** (don't regress a meter
already past `PREPARING_CONTRACT`).

**Payload contract — agreed 2026-08-19 (do not drift).** The webhook is a **slim event**,
not the full tender: the "it's live" signal is **`event: "published"`** (top level), and
the meters are a **flat top-level `mpxns` array of strings**. The Retool workflow was
originally built against the *full stored tender JSON* (which signals via a top-level
`status` field and nests meters as `sites[].mpxn`), so its first version rejected our
payload with a 400 — it read `status`/`sites[].mpxn`, which the slim event doesn't have.
Decision (Option A): keep the webhook slim (a big multi-site tender's full JSON would be a
heavy payload on every publish; the workflow has DB access to fetch detail from
`tender_id` if it needs it), and the **workflow reads `event` + top-level `mpxns`**. If you
ever change these two keys in `_fire_publish_webhook`, the workflow breaks silently — treat
`event` + `mpxns` as the frozen contract.

**Hard rule — a webhook failure must NEVER break a publish.** All fallible work — including
the `urllib.request.Request()` constructor, which raises `ValueError` on a malformed URL —
sits inside one try/except that returns a status dict; `publish_endpoint` echoes it as the
response's `webhook` field (`{fired:true,status}` or `{fired:false,error}`). Watch that
field (or the network tab) to see each fire's outcome. Regression test covers: malformed
URL, a 500 endpoint, an unreachable endpoint, the unset-env skip, and the User-Agent
(7 cases total in `tests/test_publish_webhook.py`).

**⚠️ CRITICAL — outbound calls MUST send a real User-Agent (Cloudflare, 2026-08-19).**
`api.retool.com` sits behind **Cloudflare**, which **403s the default `Python-urllib/x.y`
user-agent as a bot** — *before* the request reaches Retool's auth. This cost most of a
session: a correct URL + correct key still returned 403, purely because of the UA. Proof:
same key+URL, `curl -A "Python-urllib/3.12"` → 403, `curl -A "curl/8.4"` → 400 (reached the
workflow). The webhook now sends `User-Agent: RYE-tenderApp/1.0` (`_WEBHOOK_UA` in
`main.py`) — **do not remove it**, and set a real UA on ANY future outbound HTTP from this
app. (Symptom trap: Vercel's "External APIs / No outgoing requests" panel does **not** trace
stdlib `urllib`, so it showed "no request" whether the call was skipped OR firing — don't
trust it; read the `webhook` field instead.)

**Debug endpoint — `GET /api/webhook-check` (team-gated).** Open
`https://tender-app-chi.vercel.app/api/webhook-check?test=true` in the browser (you're
authed via `/app`) to see, for *that* deployment: `url_set`, `url_host`, `secret_set`,
`vercel_env`, and a live `test_fire` (a harmless `event:"webhook_test"`, empty `mpxns`, so
the workflow no-ops). Statuses: `fired:true/200` or a `400` = healthy (past Cloudflare +
authenticated); `403` = UA/Cloudflare or wrong-env key; `skipped` = the build has no
`PUBLISH_WEBHOOK_URL`. This replaces the publish-and-squint loop — one URL tells you exactly
where the pipeline stands, no tender published, no DB write.

**Staging safely (Vercel Preview scoping).** Set both env vars **Preview-scoped** only,
pointing at the Retool **staging** URL/key; leave Production unset so `main`/prod stays a
no-op. Push the branch → Vercel builds a Preview deploy → publish a test tender on the
preview URL. Env-var AND code changes only take effect on a fresh build, so **redeploy
after any change**. Promote by adding the same vars **Production-scoped** with the prod
URL/key. Fast isolation test of a Retool endpoint: `curl -i` the URL with an
`X-Workflow-Api-Key` header (or Retool's "Copy as cURL") — 200 = URL+key good, 401 = no
key sent, 403 = wrong-env key, 400 "resources not configured" = the workflow's DB
resources aren't wired for that environment.

**Status (2026-08-19).** End-to-end on **production**: a real publish now fires, clears
Cloudflare, authenticates, and the workflow runs (verified on a real 14-meter tender). The
**tenderApp side is done**. Remaining is workflow-side: the colleague is updating the
Retool workflow to read `event` (was `status`) and top-level `mpxns` (was `sites[].mpxn`)
per the agreed contract above; once that lands, the meters' status advances in RYE's
Postgres. (Staging was abandoned as a path — its `rye-app-db` resources were never wired
for the staging environment, `400 resources not configured`; we tested on prod against
throwaway tenders instead.)

**OUTSTANDING — rotate the leaked keys.** Two live Retool keys went through chat/files this
session: staging `retool_wk_c18c2c…` and **prod `retool_wk_bb769f…`** (in the colleague's
`RYE API Connection.R`). Rotate the prod key in Retool, update `PUBLISH_WEBHOOK_SECRET` in
Vercel + the R script, redeploy. Not yet done as of 2026-08-19.

**Gotchas this cost us (don't repeat).** (1) The webhook PR (#21) was merged with the
*pre-amend* commit before the code was final; we then amended + force-pushed, so `main`
and the branch diverged and conflicted on `main.py`/the test — resolved by merging
`origin/main` into the branch and taking the branch's version (`git checkout --ours`).
**Don't merge a PR until the branch code is final.** (2) A Retool workflow API key was
pasted into chat and had to be **rotated** — keep keys out of chat; the curl status line
is all that's needed. (3) `PUBLISH_WEBHOOK_URL` was accidentally set to the literal value
`PUBLISH_WEBHOOK_SECRET` in Vercel (URL/key crossed) — that's what surfaced the
unguarded-`Request()` 500. (4) **Vercel env vars bake in at build time** — editing/adding a
var does nothing to a running deployment; you must **redeploy** for it to take effect. Hours
were lost publishing against builds that predated the vars. (5) **`tender-app-git-main-…` is
the PRODUCTION deploy**, not a "branch" one (the `git-main` in the hostname is misleading);
your feature branch is `tender-app-git-feat-…`. Vars are also **per-scope** (Production vs
Preview) — a Production-only var is invisible to a preview deploy. (6) See the CRITICAL
User-Agent note above — the single nastiest one: a perfectly correct key + URL still 403s
from Python's default urllib UA.

## sites.csv / Retool export (clarified 2026-07-20)

The `sites.csv` uploaded at extract/assemble is produced by a Retool export
transformer (not in this repo). Clarifications locked this session:
- **Incumbent rate columns come from the *prospect* contract** — the `Contract` row
  whose id sits in `Site.prospectContractId` (their DB confusingly calls the incumbent
  the "prospect" contract; `Site.currentContractId` is the RYE-brokered deal). The
  export query self-joins `Contract` twice; incumbent = the `ProspectContract`/`old*`
  alias.
- **Units: the export converts £ → p** (×100) so incumbent rates match the supplier
  quotes' p/kWh and p/day — the app does NOT convert units, so a mismatch throws the
  saving off by 100×.
- `supplyStartDate` = the incumbent contract's end date **+ 1 day**
  (`preRyeContractEndDate`).
- `capacityCharge` / `networkCharge` / `meterCharge` aren't stored in `Contract` —
  they're **hand-appended** to the CSV (in pence) when held.
- `clientName` must equal the wizard's client name so the incumbent rows match.

## Where the visual/UI code lives (for future edits)

- **Team app UI:** `web/index.html`, `web/app.css` (RYE design tokens at the top of
  app.css), `web/app.js` (behaviour). No build step — edit and redeploy.
- **Client dashboard:** `assets/dashboard_template.html` + `assets/market_snapshot.json`
  (cost data injected as `__TENDER_DATA__`, market data as `payload.market`).
- **Brand tokens:** the `rye-design-system` skill is the source of truth. NB the live
  dashboard still runs the older emerald/amber palette; the skill specifies the newer
  green (`#186d18`)/blue (`#416ff8`) and drops amber — reconcile deliberately, don't
  half-migrate.
- After any UI edit, run `node tests/dom_smoke.js` and render a sample dashboard to
  eyeball (`build_dashboard.render_tender` on `schema/examples/tender.example.json`),
  plus a preview deploy.

The pipeline core is transport-agnostic: every script is a plain importable
function, so the endpoints stay thin wrappers.

## Resolved / no longer open

- Vercel Python runtime + FastAPI work; external Postgres over SSL works; AI Gateway
  BYOK is on all plans; **moved to Vercel Pro** (RYE company account); spend handled
  via notifications (no hard cap — global-account constraint).
- **Company Postgres / site-reference:** currently an uploaded `sites.csv` at
  extract/assemble (authoritative site names + EAC/kVA). Syncing it from the read-only
  company Postgres into the tender DB (killing the upload + the static-IP question) is
  still an optional future improvement, not a blocker.

## Environment / workflow gotchas

- macOS Python is externally-managed: use the venv locally. Claude's sandbox is
  Linux and shares the working folder, but not the macOS venv.
- **Running the suites from Claude's side (2026-08-27):** the whole suite runs natively in the
  Linux VM the device bridge shells into — `pip3 install --break-system-packages fastapi
  openpyxl jsonschema psycopg2-binary python-multipart httpx`, and `node tests/dom_smoke.js`
  works from the repo root because `node_modules/` is in the mounted folder. **Trap:** the VM
  ships an ancient system `jsonschema` in `/usr/lib/python3/dist-packages` that pip reports as
  "already satisfied", so `Draft202012Validator` fails to import and half the suites die at the
  import line. Force it: `pip3 install --break-system-packages --upgrade --ignore-installed
  jsonschema`. None of this touches the macOS `.venv`.
- Run scripts from repo root so same-dir imports resolve.
- `.git/index.lock: File exists` with no git running → `rm -f .git/index.lock`.
  (Claude's file tooling touching the repo can leave one behind.)
- **Claude must not run git against the repo through the device bridge.** The bridge is
  denied unlink permission on the mounted folder, so git creates `.git/index.lock`, fails
  to remove it, and every subsequent git command in the repo blocks. It happened
  2026-08-06 from a single read-only `git status`. Claude reads and writes files; Rory runs
  git. (Claude *can* move a stale lock out of the way — `mv` is permitted, `rm` isn't.)
- **Stale ref locks silently block pruning.** `.git/refs/remotes/origin/**/*.lock` files
  left by a crashed git make `git fetch --prune` error per-ref and leave phantom remote
  branches in `git branch -a` forever — five of them dated 17 July were found on
  2026-08-06. Clear with `find .git/refs -name "*.lock" -delete`, then `git fetch --prune`.
- Deleting a local branch (`git branch -d x`) does **not** delete it on GitHub; that needs
  `git push origin --delete x`. `git branch` alone shows only local branches — `git branch -a`
  shows GitHub's too, and `git branch -r --merged origin/main` shows which remote branches
  are safe to delete.
- The repo's `.venv` symlinks to a macOS Python, so it is **broken inside Claude's Linux
  sandbox** — Claude reconstructs deps in its own container instead. Rory needs
  `source .venv/bin/activate` before running the test suites locally.
- Vercel: "Environments" (custom pre-prod) are Pro-only; "Environment Variables"
  are free. Deployments tab defaults to the Production filter — switch to All to
  see branch previews.
- FastAPI file uploads need `python-multipart` (already in requirements.txt).
- Rory is learning git/Python — explain steps plainly, no assumed jargon, and keep
  commands paste-safe (single lines; `git add -A` beats long path lists).
