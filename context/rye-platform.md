# RYE platform: what the tender tool needs to know

**Drafted 13 Oct 2026** from `~/dev/ryeInfraClone` (api clone at 7 Oct, app clone at 29 Jul) and its
plans. This file covers only the parts of RYE's main platform (`rye-energy/api` + `app.rye.energy`)
that touch tendering. The source of truth is the api repo; if this file and the code disagree, the code
wins and this file needs updating.

---

## 1. How a site moves through the pipeline

Postgres `SiteStatus` enum (on both `Site` and `SupplyPoint`):

`DRAFT → PROSPECT → LOA → AWAITING_QUOTE → QUOTING → PREPARING_CONTRACT → CONTRACTING → PENDING → LIVE`,
plus `BLOCKED` and `CLOSED`.

| Transition | What sets it |
|---|---|
| → `LOA` | Client created in the app procurement flow; LOA sent via DocuSeal |
| `LOA` → `AWAITING_QUOTE` | DocuSeal webhook when the LOA is signed (api `webhooks/docuseal`). Publishes a `site-status-updated` event, which triggers n3rgy trusted consent for every site with an LOA document |
| `AWAITING_QUOTE` → `QUOTING` | Ops, by hand (no code path found) |
| → `PREPARING_CONTRACT` | **The tender app's publish webhook** (section 2) |
| → `CONTRACTING` | Ops, when the contract goes out for signature via DocuSeal (not traced in code) |
| `CONTRACTING` → `PENDING` | DocuSeal webhook when the contract is signed |
| `PENDING` → `LIVE` | Supply start. The switching tracker in `switch-to-online-automation-plan.md` confirms the switch on ECOES/CSS |

If the DocuSeal webhook fails, sites stay in `LOA`. The app's procurement page lists `DRAFT`,
`AWAITING_QUOTE` and `LOA` sites for that reason.

## 2. The publish webhook and the Retool workflow

When a tender is published, the tender app POSTs a slim event to a **Retool workflow called "Tender
Database Update"**. A colleague built and owns it in RYE's Retool workspace; it is not in either repo.

- **Input (frozen contract):** `event: "published"` and a flat top-level `mpxns` array, plus
  `tender_id`, `version`, `client_name`, `dashboard_url`, etc. See `HANDOVER.md` section 3, rule 18.
- **What it does:** matches each meter point to its site in RYE's Postgres and advances its status,
  e.g. `AWAITING_QUOTE` → `PREPARING_CONTRACT`. Its update should be parameterised and must not move
  a meter backwards past `PREPARING_CONTRACT`.
- **Verified** on production 19 Aug on a real 14-meter tender.
- **Not yet confirmed:** whether it updates `Site.status`, `SupplyPoint.status` or both, and which
  field it matches on (`Site.mpanCore` or `SupplyPoint.mpxnSpid`). Export the workflow JS into this
  folder to close that gap.

## 3. Data the tender app reads from the platform

The sites.csv export (contract in `HANDOVER.md` section 6) maps onto these models:

- **`Site`:** `name`, `eac`, `kva`, `mpanCore`, `profileClass`, `contractDuration`, `status`.
- **Incumbent = `Site.prospectContract`** (the contract the client had before RYE).
  `Site.currentContract` is the RYE-brokered deal. The naming is confusing; check it whenever you
  touch the export.
- **`Contract`** holds only `rate`, `dayRate`, `nightRate`, `standingCharge`, `startsAt`, `endsAt`,
  `supplier`, stored in **£**. The export converts to pence. There are no capacity, network, meter or
  weekend fields, which is why those are hand-appended to the CSV.
- **EAC comes from tenders and invoices.** ECOES returns no EAC for RYE's role, so the tender is
  where EAC gets set.

## 4. Tender-stage rules that come from the platform

1. **Data route is decided at tender.** The winning supplier appoints the data collector, and the DC
   decides whether RYE can read consumption:
   - Yu Energy → DC UKDC (IMServ) → n3rgy can reach it.
   - Tem → DC SSIL (Stark) → **n3rgy cannot reach it**. Tem sites have no automated data route until
     Tem offers an API or accepts a nominated DC at contracting.
   - Smart (SMETS) meters work through the DCC whatever the supplier.
2. **Proposed rule: n3rgy reach is a hard criterion for any site RYE monitors.** One MPAN is stuck
   with a supplier that won't sign a data-sharing agreement and can only be fixed at retender. Not yet
   formally adopted; agree it with Rory before building it into the picker.
3. **Objection risk check (parked, not built).** ECOES `supplierEfd` shows when a client has recently
   changed supplier. One client (Richmond) had switched in August, before RYE tendered, and objected to
   our registration. A check at `QUOTING` or `PREPARING_CONTRACT` that flags a recent `supplierEfd`
   would catch this. Limit: a renewal with the same supplier changes nothing on ECOES, so it stays
   invisible. The data is already stored by the weekly ECOES refresh.
4. **Check the incumbent before tendering.** ECOES has shown a different supplier from RYE's ledger
   (e.g. ledger ScottishPower, ECOES EDF). A tender priced against the wrong incumbent produces a
   wrong saving.
5. **Switch lead times seen: 3 to 28 days**, with go-live on the contract `startsAt` or 1 to 4 days
   late, never early.
6. **Every switch needs the physical LOA sent to n3rgy** (AMR/HH meters). That is downstream of the
   tender, but it is why the LOA must name every MPAN in the tender.

## 5. Known platform issues touching the tender flow

- `app/src/app/(auth)/procurement/sign/page.tsx:89` sets `completedRedirectUrl="/tender/success"`,
  which 404s; the real path is `/procurement/success`. Seen in the 29 Jul app clone. **Unverified on
  current `main`.**

## 6. Where to look for more

| Topic | File in `~/dev/ryeInfraClone` |
|---|---|
| Switching tracker, ECOES/CSS, data routes, objection check | `switch-to-online-automation-plan.md` |
| Consumption data and n3rgy consent | `consumption-pipeline-plan.md` |
| Known Sentry errors | `debugging-guide.md` |
| Status enum and models | `api/prisma/schema.prisma` |
| Status transitions in code | `api/src/endpoints/webhooks/docuseal/index.ts`, `api/src/endpoints/internal/pubsub/site-status-updated/index.ts` |
