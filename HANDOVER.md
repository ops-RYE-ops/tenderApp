# RYE Tender Tool: handover

**Last revised 13 Oct 2026.** This file is the current state only. The full session history (Jul to Oct
2026, with the reasoning behind every decision) is in `HANDOVER-archive.md`: grep it for the why, do not
read it end to end. Keep this file short: when a session ends, update the sections below in place and
put any long narrative in the archive.

Also read: `README.md` (pipeline layout), `PHASE0-NOTES.md` (schema decisions), and the build spec
(Google Doc "RYE-tender-tool-build-spec.md", id `1jzm2jEgGaur_q4kFm5zVE5n5-stfR_jwVNzyuzhSfcg`, via the
Google Drive connector).

---

## 1. What it is

An internal tool that turns supplier energy quotes (plus incumbent data) into a client-ready tender
comparison dashboard, published as an unguessable per-client link. It productises the `quote-processing`
(extraction) and `quote-to-dashboard` (cost engine + HTML) skills into a hosted team app.

- **Live:** `tender-app-chi.vercel.app` (RYE Vercel Pro, project `tender-app`). Team app at `/app`,
  client dashboards at `/d/<slug>/<uuid>`.
- **Repo:** `~/dev/tenderApp` → `github.com/ops-RYE-ops/tenderApp`. `main` is production.
- **Stack:** Vercel (FastAPI Python functions + static), Claude via Vercel AI Gateway (header mapping
  only), Retool DB (tender store + mapping cache), vanilla JS team UI (no build step).

## 2. Start here: current state (23 Oct 2026)

- **Complete and live.** Full flow map → extract → assemble → cost → render → publish, plus register,
  edit-from-register and staged publish, all verified end to end on production. Real client tenders
  are being published (e.g. Public House Group, X + Why).
- **Last code change:** `fix/contract-basis-label` (PR #39, merged 23 Oct). Recommendation card
  label comes from the offer's supply end dates: dated = fixed, "Rolling" = rolling, mixed = "fixed
  and rolling", none = nothing; coterminous/dated terms never get "fixed" appended; no dangling " · "
  when the term is blank. Both `buildRecVsInc` and `buildSummaryMulti`. Guarded by
  `tests/dom_contract_basis.js` (needs the `test_summary_multi.py` fixture, so Python suites first).
  Found on Bondi Bowls (Capture rolling).
- **Custom domain (16 Oct):** client links publish as `https://tender.rye.energy/d/<slug>/<uuid>`.
  `_public_base()` in `main.py` reads `PUBLIC_BASE_URL` (Production only, plain config), else the
  request host. Old `vercel.app` links keep working. Team builder: `https://tender.rye.energy/app/`.
- **Last market snapshot:** 26 Oct 2026 (11th edition, 12:45 ICE board). Straight to `main`. See section 7.
- **Git state 23 Oct:** `origin/main` = 4d1bb7b (PR #39 merge). Nothing in flight.
- Merged branches still to delete: local `fix/rate-book-units`, `feat/custom-domain`,
  `fix/contract-basis-label`; remote `fix/contract-basis-label`,
  `feat/savings-timeline`, `fix/fee-net-sign`, `fix/rate-book-units`, `feat/custom-domain`.
- **Tests:** 20 Python suites + 8 jsdom suites, all green (re-run 23 Oct; section 10).

## 3. Rules (do not break, do not relitigate without reason)

**Working rules**
1. **Claude never runs a git command that writes.** `add`, `commit`, `branch`, `checkout`, `merge`,
   `stash`, `reset`, `clean`, `rm`, `push` are Rory's. Read-only `status`/`log`/`diff`/`show` are fine.
   Claude edits files and hands over paste-safe single-line commands. Reason: the bridge cannot unlink
   files, so a failed git write leaves `.lock` files, and a half-finished `stash`/`checkout` can lose
   the only copy of uncommitted work (near miss 22 Sep). This rule was broken once because it was
   buried; it stays at the top.
2. **Branch and preview for everything except two files.** `assets/market_snapshot.json` and
   `HANDOVER.md` go straight to `main` (standing authorisation). Anything touching the template, cost
   engine, `main.py`, wizard or schema goes on a branch, gets eyeballed on the Vercel preview, then
   PR. Do not merge a PR until the branch code is final.
3. **Published dashboards render live.** `/d/...` re-renders from the current template and market
   snapshot on every request. A deploy changes every live client link instantly. Treat
   `dashboard_template.html` and `market_snapshot.json` as production.
4. Rory is building git and Python confidence: explain steps plainly, keep commands paste-safe.

**Numbers rules**
5. **AI maps, code moves numbers.** The LLM only names which column feeds which field. It never sees
   or emits a rate, EAC or meter point. `map_headers.py` is the only LLM touchpoint.
6. **The LLM never decides units.** Charge basis comes from `DEFAULT_BASIS` (network `p/day`, capacity
   `p/kVA/day`, etc.), overridden only by a hand-authored `mapping.json`. `charge_basis` is absent from
   the LLM tool schema and stripped from the mapping cache on read and write. (A guessed `p/kWh`
   network basis once produced a ~£1.4M mis-cost.)
7. **We cost capacity. Do not reconcile to Tem's portal headline**, which excludes it. On a real meter
   capacity was 18% of site cost, and excluding it flatters suppliers who quote it separately.
8. **Effective rate is cost-first:** annualise every charge by its basis, sum across sites, divide by
   total kWh. Day/night split hardcoded at 0.7; weekend share 2/7 applied only to offers that quote a
   weekend rate, split multiplicatively so bands sum to 1. Assumptions are footnoted, never silent.
9. **EAC and kVA live on `sites[]` once** (meter facts, one consumption basis across offers).
   sites.csv EAC/kVA override the quote and are stamped `eac_source: "db"`.
10. **A real incumbent always beats a benchmark.** Precedence on assemble: new sites.csv > benchmark >
    kept stored incumbent > none.
11. **Client-facing link identity is server-side only.** A `url_uuid`/slug sent from the browser is
    ignored. Incumbent "keep" is re-fetched server-side, never accepted from the page.

**Client-facing presentation rules**
12. **Sign and wording:** a saving reads `£x` in green, an increase `+£x` in amber with the word
    "increase" (never "dearer"). `gbp()` puts the minus outside the currency; a test asserts no literal
    `£-` on any page. `saveWord()`/`saveTone()` drive labels.
13. **Show the losers.** Meters that come out dearer stay in every table and are named in the intro.
    Never cherry-pick.
14. **"Supply points"/"supplies", not "sites"** on the client page (one site = one MPAN in the data
    model). Internal payload keys keep `site` names.
15. **Fee on a subset of supplies shows the discount, never the count** ("29% portfolio discount ·
    equivalent to £64.29 per supply"). The count goes in the covering email.
16. **Never two y-axes.** Monthly and cumulative charts stay separate. SVG charts use plain `viewBox`
    + `.mchart`; never `preserveAspectRatio="none"` (stretches text; a test guards it).
17. **Palette:** the live dashboard runs the older emerald/amber palette. The `rye-design-system`
    skill specifies newer green `#186d18`/blue `#416ff8`. Migrate deliberately in one pass or not at all.

**Integration rules**
18. **Publish webhook contract is frozen:** top-level `event: "published"` and a flat top-level
    `mpxns` string array. Changing either breaks the Retool workflow silently.
19. **A webhook failure never breaks a publish.** All fallible work sits in one try/except; the result
    is echoed in the publish response's `webhook` field.
20. **Every outbound HTTP call sends a real User-Agent** (`RYE-tenderApp/1.0`). Cloudflare in front
    of `api.retool.com` 403s Python's default urllib UA before auth.

## 4. Architecture and code map

**Pipeline (`pipeline/`)** - every script is an importable function; endpoints are thin wrappers.

| File | Role |
|---|---|
| `rye_quote_core.py` | Shared `parse_num`, `TARGET_FIELDS`, split defaults. One definition so extractor and engine cannot drift |
| `map_headers.py` | Header detection (scans 40 rows), layout fingerprint, LLM mapping proposal, `resync_sheets`, stacked-table detection |
| `process_quote.py` | Deterministic extractor → canonical `extractResult` `{sites, quotes}` (+ CSVs on CLI) |
| `assemble_tender.py` | Merge extracts + incumbent + meta → versioned canonical tender. Incumbent from sites.csv or benchmark. Moves no values |
| `build_dashboard.py` | Cost engine (`compute_offer`, `annualise`), render payload (single or multi-fuel), `render_tender` → HTML |

**Endpoints (`main.py`)**

| Endpoint | Does |
|---|---|
| `/api/inspect` | Header-row candidates + first rows per sheet |
| `/api/map`, `/api/map/confirm` | Cache lookup on (supplier, fingerprint), else LLM; confirm upserts the cache |
| `/api/extract` | File + confirmed mapping (+ sites.csv) → extractResult; flags `unmatched_mpxn` |
| `/api/cost` | Throwaway tender → `compute_offer` per offer, ranked per fuel; cheapest = full-coverage only |
| `/api/assemble` | Extracts + meta (+ sites.csv / benchmark / `keep_incumbent`) → versioned row in `tenders` |
| `/api/render` | Tender (by id or inline) → dashboard HTML |
| `/api/publish`, `/api/revoke` | Publish mints/keeps slug + uuid, fires webhook. Revoke rotates uuid (kill switch) |
| `/api/tenders`, `/api/tenders/{id}` | Register list; full payload for edit |
| `/api/suppliers` | Controlled supplier list (cache-key hygiene) |
| `/api/webhook-check?test=true` | Webhook diagnostics + harmless test fire |
| `/api/health`, `/api/db-check` | Diagnostics |
| `GET /d/<slug>/<uuid>` | Public dashboard. Alive if the latest version carries the uuid and is not expired; shows the latest version actually published under it |

**Data**
- Retool DB tables `tenders` (whole tender in a `payload` jsonb column + denormalised columns),
  `supplier_mappings`, view `tenders_latest`. DDL in `schema/retool_tables.sql`. New tender fields
  ride inside the jsonb: **no DB migrations**; only `schema/tender.schema.json` changes.
- Every save is a new version (audit trail). Field naming is deliberately mixed: tender/meta
  snake_case, line fields the fixed CSV camelCase.
- Raw uploaded quote files are **not stored**, only the extracted result.
- **Reference exports** of `tenders`, `tenders_latest` and `supplier_mappings` (13 Oct) are in `db sample/`.
  Real client data: gitignored, never commit, never paste into chat. Re-export rather than trust them for long.

**UI**
- Team app: `web/index.html`, `web/app.css` (tokens at top), `web/app.js`. Wizard: basics → upload →
  map review → extract → assemble (price-ranked tick-list, ≤2 featured per fuel) → preview & publish.
- Client dashboard: `assets/dashboard_template.html`, data injected as `__TENDER_DATA__`, market as
  `payload.market`. Tabs Summary / Portfolio / Market Review / optional Savings timeline. Pane ids are
  still `tab-savings` etc.; `savings` means Summary.
- Gotcha: `_compute_payload` receives the **cfg** from `_build_cfg`, not the tender. A new
  tender-level key must be added to that function's `keys` tuple or it never arrives.

## 5. Features as built (where to look)

| Feature | Notes |
|---|---|
| Mapping cache | Key = exact (supplier, fingerprint). Fingerprint ignores sheet names; `resync_sheets` re-points cached sheets positionally on a re-dated file |
| Stacked tables | A sheet with two rate tables is refused with guidance, by design. One rate table per sheet |
| Featured offers | All offers stored; only `featured` (≤2 per fuel) rendered. `recommended` must be a featured offer |
| Edit from register (Tier 1) | Hydrates the wizard from the stored tender, restores featured offers, `keep_incumbent` |
| Staged publish | Edits are invisible to the client until Publish; the URL never changes; Revoke kills it |
| RYE fee | `rye_fee`: explicit annual > per_site_month > list less `discount_pct`. `chargeable_sites` charges N of M supplies. One implementation: `_fee_block` |
| Commission | `rye_commission.p_kwh_uplift`, mutually exclusive with fee. "Already included in rate" toggle avoids double counting |
| Benchmark baseline | Unit rate (+ optional standing charge) per fuel when there are no incumbent rates (`benchmark_unit_rate`, `gas_benchmark_unit_rate` etc. on `/api/assemble`; `incumbent_from_benchmark`). Labelled "Market benchmark" / "Indicative saving". Use what businesses currently pay (DESNZ/Ofgem averages), not best new-contract rates |
| Combined gas + electricity | Fuel detected per meter row (fuel column → site fuel → `infer_fuel_from_mpxn`: MPAN 13 digits / MPRN 6-10). Each fuel costed independently, one tender-level fee/commission (`_tender_level_charge`). Wizard features ≤2 per fuel (`MAX_FEATURED_PER_FUEL`). Summary is electricity-anchored with an "include gas" tick; fuel switch lives in Portfolio |
| Rate provenance | `added_at` on each quote; newest wins per supplier+term+fuel, older shown SUPERSEDED |
| Contract dates | `supplyStartDate`/`supplyEndDate`; missing end derived from start + term and starred; never guessed for bespoke terms |
| Savings timeline | Operator tickbox `show_timeline`; needs a baseline; shows run rate, first 12 months and cumulative. Saving spread flat (annual ÷ 12), footnoted |
| Client-name check | sites.csv whose `clientName` does not match warns with the names found |

## 6. sites.csv contract (Retool export)

Columns: `clientName, siteName, mpxn, eac, supplyStartDate, unitRate, dayRate, nightRate, weekendRate,
standingCharge, capacityCharge, networkCharge, meterCharge, kva, incumbentSupplier`. Any filename ending
`.csv` works.
- Incumbent rates come from the **prospect** contract (`Site.prospectContractId`; `currentContractId`
  is the RYE-brokered deal).
- **Export converts £ → p.** The app never converts units, so a mismatch is a 100× error.
- `supplyStartDate` = incumbent contract end + 1 day. Capacity/network/meter charges are hand-appended
  in pence when held.
- `clientName` must equal the wizard's client name.

## 7. Market snapshot refresh (procedure)

`assets/market_snapshot.json`, hand-edited from ICE / Trading Economics / N2EX screenshots Rory supplies,
straight to `main`. Updates every live dashboard on deploy.

**Mechanics**
- `power.series` holds **39 points against 13 labels**. Replace the latest bucket's print rather than
  append when the count would break; rebuild the window when the year rolls.
- Re-tune `power.series[0]` (a float) every refresh so the "trend vs start" KPI reconciles with TE's
  1Y % (use the chart header, not the Yearly tile; they differ). 26 Oct value: `76.96` for +134.52%.
- `rangeLong` is a **3-year** window on purpose (5 years lets 2022 dwarf today). `max` must be a
  verbatim print (180.49 at 26 Oct).
- `gas.series` appends. Gas cards: at most nine (3×3).
- Commentary renders via `textContent`: `\n\n` does nothing. Write one tight block, ~230-260 words.

**Content discipline**
- Read every price verbatim off the board. **A contract with no print is dropped, never carried.**
- A first-appearance contract is shown but not leaned on until it holds a second edition.
- Do not invent a driver for a daily move. Label Rory-supplied drivers as such; if none, say the
  existing driver persists.
- First-party intelligence (e.g. supplier spread across RYE's live tenders) is worded as RYE's
  observation, never as a market statistic.
- Negative day-ahead prices are not a "wait" signal; use them to show the exposure a fix removes.
- TE spot index and N2EX day-ahead are different measures; quote separately.
- Check what kind of move it is: a risk-premium rally lifts the whole strip; a physical outage (and its
  relief) concentrates in the prompt. That decides the term argument.
- **Blend method** for p/kWh equivalents: winter-weighted 55/45 gas blend over the contract's
  delivery seasons, through a ~50% CCGT. Say when a before/after leg is not same-board.

**Carry-forward checks for the next edition**
- Ask Rory for the full scrolled board every time. 26 Oct board had no Summer 27, Q2 27, Q3 27, May-Sep 27
  or Winter 29 rows, so Summer 27 was dropped and Winter 28 carded instead. Check whether S27 is back.
- Term discount measure: Q1 27 vs Winter 27 (28.0% on 26 Oct, 26.8% on 22 Oct, 28.5% on 16 Oct).
- Q1 27 printed with 0.000% day change for a second edition (233.50, exactly +10.00 on 223.50), 1.49 above
  the Jan-Mar singles average. Still not leaned on.
- Blends need Summer 27. 26 Oct copy used the winter leg only (12m +0.37 p/kWh, "on the winter months
  alone"); no full 12m/24m figure or 24m-vs-12m gap was stated. Restore same-board blends when S27 prints.
- Winter 27 / Summer 28 / Winter 28 were thin prints (vol 5-25, 0.000% day). Treat moves on them cautiously.
- "Waiting has cost money in all five updates since late September": bump or drop the count next edition.

**26 Oct edition at a glance (12:45 board):** power spot 180.49 £/MWh (+134.5% 1Y), new 3-year high
(was 173.79 on 13 Oct). NBP Nov 26 233.99 (new run high, 12.6% above Sep peak), Q1 27 233.50, Winter 27
168.20, Summer 28 106.00, Winter 28 110.00. Front-led this time (winter singles +4.2-4.6%, Winter 27
+2.8%), so the term discount widened; signal FIX. N2EX 21-27 Oct 4.86-150.47, weekend 24-25 Oct averaged
24-35 £/MWh, no negatives. Drivers (Rory-supplied Montel, 23 Oct): NESO Winter Outlook margin 5.9 GW /
9.5% ("credible"), weather the main risk, mild wet windy Q4 but colder February risk; storage and Hormuz
carried from 21 Oct as last week's reporting.

## 8. Deployment and ops

| Item | Value |
|---|---|
| Env vars (Production + Preview) | `TEAM_ACCESS_KEY`, `RETOOL_DATABASE_URL`, `ANTHROPIC_API_KEY`, optional `ANTHROPIC_BASE_URL` |
| Webhook env vars | `PUBLISH_WEBHOOK_URL` (full Retool URL), `PUBLISH_WEBHOOK_SECRET` (`retool_wk_…`, sent as `X-Workflow-Api-Key`). Unset = webhook off. Keys are per Retool environment. Prod key rotated after the 19 Aug leak (confirmed 13 Oct). Never paste keys into chat |
| Client link domain | `PUBLIC_BASE_URL=https://tender.rye.energy`, **Production scope only** (a Preview copy would point preview publishes at prod). Must start `https://` or it is ignored. Set only once the domain shows Valid Configuration in Vercel |
| Data residency | Retool Cloud DB is US-hosted. **Accepted decision** (Rory, 13 Oct), under Retool's DPA/SCCs. If it ever changes, moving is a `RETOOL_DATABASE_URL` swap to a UK/EU Postgres (DDL in `schema/retool_tables.sql`), no code change |
| Access | App-level HTTP Basic gate (`team_gate`) on `/api` and `/app`; `/d/*`, `/api/health`, `/` exempt. **Vercel Deployment Protection stays OFF** (cannot exempt `/d/*`) |
| Region | `lhr1` via `vercel.json` |
| Spend | No hard cap (account-global); notifications on |
| Downstream | Retool workflow "Tender Database Update" advances meter status (`AWAITING_QUOTE` → `PREPARING_CONTRACT`) on publish. Verified on production 19 Aug |

**Vercel traps**
- Env vars bake in at build: **redeploy after any change**. Vars are per scope; a Production-only var
  is invisible to previews.
- `tender-app-git-main-…` is the **production** deploy; branches are `tender-app-git-feat-…`.
- Deployments tab defaults to the Production filter; switch to All for previews.
- Vercel's "outgoing requests" panel does not trace stdlib `urllib`. Use `/api/webhook-check?test=true`:
  200/400 = healthy, 403 = UA or wrong-environment key, `skipped` = no URL in that build.

## 9. Supplier layouts seen

- **"Client Quote" template** (Avant, Blank St, Chance, Rosslyn, UrbanChain): header row 9 under a
  metadata block; single rate; has a Commission column (leave unmapped).
- **YU broker portal:** camelCase headers row 1; day/night/weekend. Ignore "Incumbent data" sheets.
  YU "stacked tables" exports are refused (split onto separate sheets).
- **Octopus multisite:** summary block, real table at row 21; mixed single/two-rate, elec + gas.
- **Salad Kitchen (Salesforce) / Octopus CSV:** header row 1, single rate.
- **Tem** (PDF/portal) and **Capture Energy** (PowerImport xlsx) are converted outside the app by the
  `tem-quote-to-csv` and `capture-quote-to-csv` skills, which normalise to per-day bases.
- Keep each supplier's electricity and gas on separate tabs, or they become one blended offer in the
  picker.

## 10. Run and verify

```bash
cd ~/dev/tenderApp
source .venv/bin/activate
for t in tests/make_and_verify.py tests/test_*.py; do python3 "$t" || echo "FAILED: $t"; done
for t in tests/dom_*.js; do node "$t" || echo "FAILED: $t"; done
```
Each Python suite prints an "ALL … PASSED" line. No network needed (LLM and DB mocked). jsdom is an
ad-hoc local dependency (`npm i jsdom`; `node_modules/` and `package*.json` are gitignored).

**From Claude's side (device bridge Linux VM):** the macOS `.venv` does not work. Install with
`pip3 install --break-system-packages fastapi openpyxl jsonschema psycopg2-binary python-multipart httpx`,
then **force** `pip3 install --break-system-packages --upgrade --ignore-installed jsonschema` (the
system copy is too old for `Draft202012Validator`). Run from the repo root.

**After any UI change:** run the jsdom suites, render `schema/examples/tender.example.json` headless and
eyeball it, then check the Vercel preview. Spot-check two site costs against the source quote before a
client sees a new tender.

## 11. Open items (priority order)

**Now: custom domain follow-ups (rollout done 16 Oct: DNS, merge, `PUBLIC_BASE_URL`, live test)**
1. Find hardcoded `tender-app-chi.vercel.app` links in Front templates, WhatsApp saved replies,
   signatures and the Retool "Tender Database Update" workflow. Optional: `replace()` the host in stored
   `dashboard_url` (column and `payload->dashboard_url`) so the register shows the new link; old links
   work either way.

**Housekeeping**
2. Disconnect or delete the old hobby Vercel project so a push cannot deploy twice.
3. Delete merged branches (section 2).

**Backlog**
4. Register clean-up: hard delete for drafts/test rows, soft archive for published. Plus a test mode
   that keeps dry runs out of the register. At 13 Oct: 115 tenders (72 published, 43 draft), 25 test-named,
   **and at least 7 webhook-test tenders are still published with live public links** (revoke these now).
   Client names have drifted (`X + Why` / `X & Why` / `x+why`, `Pizzarova` / `PIzzarova`): normalise or pick
   from a list. `TEST SUPPLIER` rows sit in the mapping cache.
5. `created_by` is `unknown@rye.energy` on every tender: nothing records who built or published what.
6. Wizard: free back/forward before first save; Tier 2 edit (re-upload one quote inside an edit).
7. Savings timeline for combined gas + elec tenders (single-fuel only today).
8. Combined Summary: hero byline and counts read from electricity only (undercount); efficiency and
   incl./excl. commission rows absent.
9. Portfolio matrix delta signs contradict each other (`(−£1,262)` vs `+£1,262`). Left on Rory's
    instruction; tidy if wanted.
10. Dedicated unit tests for the commission and benchmark paths (covered only indirectly).
11. Margin analysis: `added_at` on every quote makes "rate move minus wholesale-equivalent move"
    computable across RYE's book. Would turn the supplier-competition argument into a number.
12. Wire the market tab to RYE's weekly market API (`rye-energy/api`) via a thin adapter, instead of
    hand-edited JSON.
13. Sync site reference from company Postgres into the tender DB (kills the sites.csv upload).
14. Decide whether internal "excluded" cost warnings should show client-side.
15. Longer term "Path B": run the whole tender flow inside Retool. Path A (webhook) is live.
16. Optional: per-person SSO via a two-project split.
17. Shorter client links (Rory 16 Oct: wanted, not urgent): `tender.rye.energy/<slug>/<12-char token>`
    instead of `/d/<slug>/<uuid>`. Token lowercase base32 (no 0/o/1/l), never shorter than 12. Needs a new
    `url_token text` column (url_uuid is typed uuid), lookup by token OR uuid so old links live on,
    regenerate rotates both, reserve slugs `app`/`api`. Never drop the random part: `/d/*` has no auth.

**Known limitations, accepted**
- Benchmark baselines are not dated or attributed on the client page (removed as clutter).
- `eacHeadline()` shows 999,999 kWh as "1,000 MWh".

## 12. Environment gotchas

- Lock files: `find .git -name "*.lock" -delete` (Rory runs it). Stale remote-ref locks block
  `git fetch --prune`.
- A stuck local branch ref can be removed as a file once it is confirmed reachable from `main`.
- `git branch -d` does not delete on GitHub: `git push origin --delete <branch>`.
- FastAPI uploads need `python-multipart` (in requirements.txt).
- Vercel "Environments" are Pro-only; "Environment Variables" are on all plans.
