/* RYE Tender Tool — team UI (vanilla JS, no build step).
 *
 * Thin front door over the FastAPI endpoints. All pipeline logic lives in the
 * backend; this file only collects inputs, shows results, and lets a human
 * confirm the column mapping before anything is extracted.
 *
 * Flow: tender basics → upload → map review/confirm → extract → assemble.
 * No app-level auth — access is handled by Vercel deployment protection (Pro).
 * Publish is the next PR.
 */
"use strict";

// The canonical target fields, in display order (mirrors pipeline/rye_quote_core.py
// TARGET_FIELDS — a display hint only; the backend schema stays the source of truth).
const TARGET_FIELDS = [
  "siteName", "mpxn", "updatedEac", "supplyStartDate", "supplyEndDate",
  "unitRate", "dayRate", "nightRate", "weekendRate", "standingCharge",
  "capacityCharge", "networkCharge", "meterCharge", "kva",
];
// Optional per-row fields, not part of the rate CSV: `fuel` is the reliable
// per-meter fuel signal for a combined gas + electricity tender (else inferred
// from the meter-point number), `supplier` lets one sheet carry several suppliers.
const EXTRA_FIELDS = ["fuel", "supplier"];
const NEW_SUPPLIER = "__new__";

const state = {
  meta: { client_name: "", tender_label: "", utility: "electricity", supplier: "", id: null },
  files: [],        // { file, name, status, mapResp, mapping, inspection, extract, extractResp, extractStatus, extractError }
  activeIdx: null,  // index into files for the map screen
  sitesCsv: null,   // shared sites.csv File — feeds both /extract (site-ref) and /assemble (incumbent)
  offers: [],       // /api/cost ranking rows (one per extracted offer)
  featured: new Set(), // offer indices ticked to show the client (max 2)
  saved: null,      // last /api/assemble response (id, slug, url_uuid, version) for preview/publish
  editing: null,    // { version, status, incumbent } when hydrated from the register; null for a new tender
};

const MAX_FEATURED = 2;
const MAX_FEATURED_PER_FUEL = 2;

const $ = (id) => document.getElementById(id);

// --- API helper --------------------------------------------------------------

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* non-JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

async function apiText(path, opts = {}) {
  // Like api(), but for endpoints that return raw text (e.g. /api/render → HTML).
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* non-JSON */ }
    throw new Error(detail);
  }
  return res.text();
}

function notice(el, text, tone) {
  el.innerHTML = text ? `<div class="notice ${tone || ""}">${escapeHtml(text)}</div>` : "";
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// --- screens -----------------------------------------------------------------

function showScreen(name) {
  $("screen-wizard").classList.toggle("hidden", name !== "wizard");
  $("screen-register").classList.toggle("hidden", name !== "register");
}

// Wizard state reset. Needed in two places — the "New tender" button and entering
// an Edit (reset, then hydrate) — so it lives here once rather than being open-coded
// twice and drifting. Clears the model AND the form fields; a stale value left in an
// input is the same bug as a stale value left in state.
function resetWizard() {
  state.meta = { client_name: "", tender_label: "", utility: "electricity", supplier: "", id: null };
  state.files = [];
  state.activeIdx = null;
  state.sitesCsv = null;
  state.offers = [];
  state.featured = new Set();
  state.saved = null;
  state.editing = null;

  const setVal = (id, v) => { const el = $(id); if (el) el.value = v; };
  const setChk = (id, v) => { const el = $(id); if (el) el.checked = v; };
  setVal("in-client", "");
  setVal("in-label", "");
  setVal("in-utility", "electricity");
  setVal("in-new-supplier", "");
  const sup = $("in-supplier");
  if (sup) sup.selectedIndex = 0;
  setVal("in-charge-model", "fee");
  setVal("in-fee-list", "90");
  setVal("in-fee-discount", "80");
  setVal("in-commission-uplift", "0.30");
  setChk("in-commission-included", false);
  setChk("in-benchmark-on", false);
  setVal("in-benchmark-unit", "");
  setVal("in-benchmark-standing", "");
  setVal("in-benchmark-gas-unit", "");
  setVal("in-benchmark-gas-standing", "");
  setVal("in-expires", "");
  setVal("in-notes", "");
  setChk("in-keep-incumbent", true);

  onSupplierChange();
  onChargeModelChange();
  onBenchmarkToggle();
  renderFiles();
  renderSiteref();
  renderEditBanner();
  renderKeepIncumbent();
  for (const id of ["step1-msg", "map-msg", "extract-msg", "assemble-msg", "preview-msg"]) {
    const el = $(id); if (el) notice(el, "");
  }
  const ar = $("assemble-result"); if (ar) ar.classList.add("hidden");
  const pr = $("publish-result"); if (pr) pr.classList.add("hidden");
}

// "New tender": reset, then back to step 1. Previously this button only switched
// screens, so it looked like a no-op when you were already on the wizard and left
// the previous tender's state in place — a refresh was the only way to start clean.
function newTender() {
  resetWizard();
  showScreen("wizard");
  showStep(1);
}

function showStep(n) {
  for (const s of [1, 2, 3, 4, 5, 6]) $("step-" + s).classList.toggle("hidden", s !== n);
  document.querySelectorAll("#stepper .step[data-step]").forEach((el) => {
    const s = Number(el.dataset.step);
    el.classList.toggle("active", s === n);
    el.classList.toggle("done", s < n);
  });
}

// --- step 1: tender basics -----------------------------------------------------

async function loadSuppliers() {
  const sel = $("in-supplier");
  sel.innerHTML = "";
  // Placeholder first, selected + disabled: force an explicit choice so a real
  // supplier is never silently pre-selected (an "engage brain" moment).
  const placeholder = new Option("Choose supplier…", "");
  placeholder.disabled = true;
  placeholder.selected = true;
  sel.append(placeholder);
  let names = [];
  try {
    const r = await api("/api/suppliers");
    names = r.suppliers || [];
  } catch (e) { /* fall through to free text */ }
  for (const n of names) sel.append(new Option(n, n));
  sel.append(new Option("+ New supplier…", NEW_SUPPLIER));
  sel.value = "";  // default to the placeholder, not the first supplier
  onSupplierChange();
}

function onSupplierChange() {
  $("field-new-supplier").classList.toggle("hidden", $("in-supplier").value !== NEW_SUPPLIER);
}

function onChargeModelChange() {
  const commission = $("in-charge-model").value === "commission";
  $("fee-fields").classList.toggle("hidden", commission);
  $("commission-fields").classList.toggle("hidden", !commission);
}

function onBenchmarkToggle() {
  $("benchmark-fields").classList.toggle("hidden", !$("in-benchmark-on").checked);
}

function currentSupplier() {
  const v = $("in-supplier").value;
  const raw = v === NEW_SUPPLIER ? $("in-new-supplier").value : v;
  return raw.trim().replace(/\s+/g, " ");
}

function toUpload() {
  state.meta.client_name = $("in-client").value.trim();
  state.meta.tender_label = $("in-label").value.trim();
  state.meta.utility = $("in-utility").value;
  state.meta.supplier = currentSupplier();
  if (!state.meta.client_name || !state.meta.tender_label || !state.meta.supplier) {
    notice($("step1-msg"), "Client name, tender label and supplier are all needed.", "error");
    return;
  }
  notice($("step1-msg"), "");
  showStep(2);
}

// --- step 2: upload ------------------------------------------------------------

function addFiles(fileList) {
  for (const f of fileList) {
    const ext = f.name.toLowerCase().split(".").pop();
    if (!["xlsx", "xlsm", "csv"].includes(ext)) continue;
    if (state.files.some((x) => x.name === f.name)) continue;
    state.files.push({ file: f, name: f.name, status: "pending", mapResp: null, mapping: null, inspection: null });
  }
  renderFiles();
}

function statusChip(f) {
  if (f.status === "confirmed") return '<span class="chip success">MAPPING CONFIRMED</span>';
  if (f.status === "review") return '<span class="chip info">NEEDS REVIEW</span>';
  return '<span class="chip">NOT MAPPED</span>';
}

function renderFiles() {
  const el = $("filelist");
  el.innerHTML = "";
  state.files.forEach((f, i) => {
    const div = document.createElement("div");
    div.className = "filecard";
    // A row hydrated from a saved tender has no uploaded file behind it — there is
    // nothing to map or re-extract, and removing it would leave the edit with no
    // offers at all. Show it as read-only rather than offering actions that break.
    div.innerHTML = f.fromSaved
      ? `<span class="name">${escapeHtml(f.name)}</span>
      <div class="right"><span class="chip info">FROM SAVED TENDER</span></div>`
      : `<span class="name">${escapeHtml(f.name)}</span>
      <div class="right">${statusChip(f)}
        <button class="btn-secondary" data-map="${i}">${f.status === "pending" ? "Map columns" : "Review mapping"}</button>
        <button class="btn-ghost" data-del="${i}">Remove</button>
      </div>`;
    el.append(div);
  });
  el.querySelectorAll("[data-map]").forEach((b) =>
    b.addEventListener("click", () => openMap(Number(b.dataset.map))));
  el.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", () => { state.files.splice(Number(b.dataset.del), 1); renderFiles(); }));

  // Extract needs at least one confirmed mapping.
  const btn = $("btn-to-extract");
  if (btn) btn.disabled = !state.files.some((f) => f.status === "confirmed");
}

// --- step 3: mapping review ------------------------------------------------------

async function openMap(idx) {
  state.activeIdx = idx;
  const f = state.files[idx];
  showStep(3);
  $("map-file").textContent = "— " + f.name;
  $("map-result").classList.add("hidden");
  notice($("map-msg"), "");

  if (!f.mapResp) {
    $("map-loading").classList.remove("hidden");
    $("map-loading-text").textContent = "Checking the mappings cache, then asking Claude if it's a new layout…";
    try {
      const fd = new FormData();
      fd.append("file", f.file);
      fd.append("supplier", state.meta.supplier);
      const fdInspect = new FormData();
      fdInspect.append("file", f.file);
      const [mapResp, inspection] = await Promise.all([
        api("/api/map", { method: "POST", body: fd }),
        api("/api/inspect", { method: "POST", body: fdInspect }),
      ]);
      f.mapResp = mapResp;
      f.mapping = JSON.parse(JSON.stringify(mapResp.mapping));
      f.inspection = inspection;
      f.status = "review";
    } catch (e) {
      $("map-loading").classList.add("hidden");
      notice($("map-msg"), "Mapping failed: " + e.message, "error");
      return;
    }
    $("map-loading").classList.add("hidden");
  }
  renderFiles();
  renderMap();
}

function allHeaders(f) {
  const seen = new Set();
  const out = [];
  for (const s of (f.mapResp.sheets || [])) {
    for (const h of (s.headers || [])) {
      const t = String(h).trim();
      if (t && !seen.has(t)) { seen.add(t); out.push(t); }
    }
  }
  return out;
}

function specHeader(spec) {
  if (typeof spec === "string") return spec === "__none__" ? null : spec;
  if (spec && typeof spec === "object") {
    const h = spec.single || spec.split;
    return h === "__none__" ? null : (h || null);
  }
  return null;
}

function withHeader(spec, header) {
  // Preserve the spec's shape ({single:…}/{split:…}/plain string) on override.
  if (header === null) return null;
  if (spec && typeof spec === "object") {
    if ("single" in spec) return { single: header };
    if ("split" in spec) return { split: header };
  }
  return header;
}

function samplesFor(f, header) {
  // Recompute sample values client-side from /inspect's rows — same rule as the
  // backend: exact header match, data starts after the best-guess header row.
  if (!header || !f.inspection) return [];
  for (const s of (f.inspection.sheets || [])) {
    const col = (s.headers || []).findIndex((h) => String(h).trim() === header);
    if (col === -1) continue;
    const rows = (s.first_rows || []).slice(s.header_row_best_guess || 1);
    const out = [];
    for (const r of rows) {
      const v = (r[col] === undefined || r[col] === null) ? "" : String(r[col]).trim();
      if (v) out.push(v);
      if (out.length >= 3) break;
    }
    return out;
  }
  return [];
}

function renderMap() {
  const f = state.files[state.activeIdx];
  const r = f.mapResp;

  const src = r.cache_hit
    ? '<span class="chip success">CACHED — LLM SKIPPED</span>'
    : '<span class="chip info">PROPOSED BY CLAUDE</span>';
  $("map-meta").innerHTML = `
    <span class="kv">${src}</span>
    <span class="kv">supplier <b>${escapeHtml(r.supplier || "—")}</b></span>
    <span class="kv">fingerprint <b>${escapeHtml(r.layout_fingerprint)}</b></span>
    <span class="kv">header row <b>${escapeHtml(String(f.mapping.header_row || "?"))}</b></span>
    ${(r.sheets || []).map((s) => `<span class="kv">sheet <b>${escapeHtml(s.name)}</b></span>`).join("")}
    ${(r.notes || []).map((n) => `<span class="kv">note: ${escapeHtml(n)}</span>`).join("")}`;

  const headers = allHeaders(f);
  const cols = f.mapping.columns || {};
  // Auto-detect a Fuel / Supplier column by name on first render (the operator can
  // override or clear it; a cleared field is null, not undefined, so it stays clear).
  for (const field of EXTRA_FIELDS) {
    if (cols[field] === undefined) {
      const match = headers.find((h) => String(h).trim().toLowerCase() === field);
      if (match) cols[field] = match;
    }
  }
  f.mapping.columns = cols;
  const known = TARGET_FIELDS.concat(EXTRA_FIELDS);
  const fields = known.concat(Object.keys(cols).filter((k) => !known.includes(k)));

  const tbody = $("map-rows");
  tbody.innerHTML = "";
  for (const field of fields) {
    const header = specHeader(cols[field]);
    const tr = document.createElement("tr");
    if (!header) tr.className = "unmapped";
    const opts = ['<option value="">— not mapped —</option>']
      .concat(headers.map((h) =>
        `<option value="${escapeHtml(h)}" ${h === header ? "selected" : ""}>${escapeHtml(h)}</option>`))
      .join("");
    const samples = samplesFor(f, header);
    tr.innerHTML = `
      <td class="fieldname">${escapeHtml(field)}</td>
      <td><select data-field="${escapeHtml(field)}">${opts}</select></td>
      <td class="samples">${samples.length ? samples.map(escapeHtml).join(" · ") : '<span class="none">—</span>'}</td>`;
    tbody.append(tr);
  }
  tbody.querySelectorAll("select").forEach((sel) =>
    sel.addEventListener("change", () => {
      const field = sel.dataset.field;
      const newHeader = sel.value || null;
      cols[field] = withHeader(cols[field], newHeader);
      f.mapping.columns = cols;
      renderMap(); // refresh samples + raw JSON together
    }));

  $("map-json").value = JSON.stringify(f.mapping, null, 2);
  $("map-result").classList.remove("hidden");
}

function applyJson() {
  const f = state.files[state.activeIdx];
  try {
    const obj = JSON.parse($("map-json").value);
    if (!obj || typeof obj !== "object" || !obj.columns) throw new Error("mapping needs a `columns` object");
    f.mapping = obj;
    renderMap();
    notice($("map-msg"), "");
  } catch (e) {
    notice($("map-msg"), "Invalid mapping JSON: " + e.message, "error");
  }
}

async function confirmMap() {
  const f = state.files[state.activeIdx];
  const btn = $("btn-confirm-map");
  btn.disabled = true;
  try {
    await api("/api/map/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        supplier: state.meta.supplier,
        layout_fingerprint: f.mapResp.layout_fingerprint,
        mapping: f.mapping,
        confirmed_by: null,
      }),
    });
    f.status = "confirmed";
    // Confirming is the end of the job on this panel, so close it and go back to the
    // file list rather than leaving the operator to find the Back button themselves.
    showStep(2);
    renderFiles();
    notice($("step2-msg") || $("map-msg"),
      "Mapping saved for " + state.meta.supplier + " — the next quote with this layout skips Claude entirely. "
      + "Confirm any remaining files, then Continue to extract.",
      "success");
  } catch (e) {
    if (e.message !== "unauthorised") notice($("map-msg"), "Save failed: " + e.message, "error");
  } finally {
    btn.disabled = false;
  }
}

// --- step 4: extract -----------------------------------------------------------

function confirmedFiles() {
  return state.files.filter((f) => f.status === "confirmed");
}

function renderSiteref() {
  const nameEl = $("siteref-name");
  const clear = $("btn-clear-siteref");
  nameEl.textContent = state.sitesCsv ? state.sitesCsv.name : "";
  clear.classList.toggle("hidden", !state.sitesCsv);
  $("btn-pick-siteref").textContent = state.sitesCsv ? "Replace" : "Choose site reference";
}

function openExtract() {
  showStep(4);
  notice($("extract-msg"), "");
  renderSiteref();
  renderExtractList();
}

function extractChip(f) {
  if (f.extractStatus === "done") return '<span class="chip success">EXTRACTED</span>';
  if (f.extractStatus === "extracting") return '<span class="chip info"><span class="spinner"></span> EXTRACTING</span>';
  if (f.extractStatus === "error") return '<span class="chip danger">FAILED</span>';
  return '<span class="chip">READY</span>';
}

function renderExtractList() {
  const el = $("extract-list");
  el.innerHTML = "";
  const files = confirmedFiles();
  if (!files.length) {
    el.innerHTML = '<div class="notice">No confirmed mappings yet — go back and confirm at least one file\'s columns first.</div>';
    $("btn-to-assemble").disabled = true;
    return;
  }
  for (const f of files) {
    const div = document.createElement("div");
    div.className = "filecard";
    let detail = "";
    if (f.fromSaved && f.extract) {
      const sites = (f.extract.sites || []).length, quotes = (f.extract.quotes || []).length;
      detail = `<div class="sub2">${sites} site(s) · ${quotes} offer(s) · loaded from the saved tender`
        + ", nothing to re-extract</div>";
    } else if (f.extractStatus === "done" && f.extractResp) {
      const c = f.extractResp.counts || {};
      const ref = f.extractResp.site_reference_used ? " · site-ref applied" : "";
      const unmatched = f.extractResp.unmatched_mpxn || [];
      detail = `<div class="sub2">${c.sites || 0} site(s) · ${c.quotes || 0} offer(s) · ${c.lines || 0} line(s)${ref}</div>`;
      if (unmatched.length) {
        detail += `<div class="unmatched-list">⚠ ${unmatched.length} meter point(s) not in the site reference: ${unmatched.map(escapeHtml).join(", ")}</div>`;
      }
    } else if (f.extractStatus === "error") {
      detail = `<div class="unmatched-list">${escapeHtml(f.extractError || "extraction failed")}</div>`;
    }
    div.innerHTML = `<div><span class="name">${escapeHtml(f.name)}</span>${detail}</div>
      <div class="right">${extractChip(f)}</div>`;
    el.append(div);
  }
  $("btn-to-assemble").disabled = !state.files.some((f) => f.extract);
}

async function runExtractAll() {
  // Only files that were actually uploaded can be extracted. A saved-tender row
  // already carries its extract (and has no File object), so it is skipped rather
  // than POSTed as a null file.
  const files = confirmedFiles().filter((f) => !f.fromSaved && f.file);
  if (!files.length) {
    notice($("extract-msg"),
      confirmedFiles().length
        ? "Nothing new to extract — the offers came from the saved tender. Add a quote file to bring in more."
        : "Confirm at least one mapping first.",
      confirmedFiles().length ? "success" : "error");
    return;
  }
  const btn = $("btn-extract-all");
  btn.disabled = true;
  notice($("extract-msg"), "");
  for (const f of files) {
    f.extractStatus = "extracting";
    renderExtractList();
    try {
      const fd = new FormData();
      fd.append("file", f.file);
      fd.append("mapping", JSON.stringify(f.mapping));
      fd.append("supplier", state.meta.supplier);
      if (state.sitesCsv) fd.append("site_reference", state.sitesCsv);
      const r = await api("/api/extract", { method: "POST", body: fd });
      f.extract = r.extract_result;
      f.extractResp = r;
      f.extractStatus = "done";
    } catch (e) {
      f.extractStatus = "error";
      f.extractError = e.message;
      f.extract = null;
      f.extractResp = null;
    }
    renderExtractList();
  }
  btn.disabled = false;
  const anyUnmatched = files.some((f) => (f.extractResp?.unmatched_mpxn || []).length);
  if (files.some((f) => f.extract)) {
    notice($("extract-msg"),
      anyUnmatched
        ? "Extracted — but some meter points aren't in the site reference (flagged above). Resolve them or proceed knowingly."
        : "Extracted. Continue to assemble when ready.",
      anyUnmatched ? "warn" : "success");
  }
}

// --- step 5: assemble ----------------------------------------------------------

function flatQuotes() {
  // Every extracted offer, in the SAME order the backend concatenates them (files
  // with an extract, in order; quotes within each). offer.index lines up with this
  // array, so ticking offer i features flatQuotes()[i].
  const out = [];
  for (const f of state.files) {
    if (!f.extract) continue;
    for (const q of (f.extract.quotes || [])) out.push(q);
  }
  return out;
}

async function openAssemble() {
  showStep(5);
  notice($("assemble-msg"), "");
  $("assemble-result").classList.add("hidden");
  renderEditBanner();
  renderKeepIncumbent();
  await loadOffers();
}

async function loadOffers() {
  const extracts = state.files.filter((f) => f.extract).map((f) => f.extract);
  const list = $("offer-list");
  list.innerHTML = "";
  state.offers = [];
  state.featured = new Set();
  if (!extracts.length) {
    list.innerHTML = '<div class="notice">No extracted offers - go back to the extract step.</div>';
    return;
  }
  $("offer-loading").classList.remove("hidden");
  try {
    const fd = new FormData();
    fd.append("extracts", JSON.stringify(extracts));
    if (state.sitesCsv) fd.append("sites_csv", state.sitesCsv);  // authoritative EAC/kVA for the ranking
    const r = await api("/api/cost", { method: "POST", body: fd });
    state.offers = r.offers || [];
    annotateOffers();   // source, added date, supersede-by-recency, cheapest-current
    // Pre-tick the best (up to MAX_FEATURED_PER_FUEL) of each fuel among CURRENT
    // (non-superseded) offers. Offers arrive per fuel, cheapest-first, so the first
    // current N of a fuel are the ones to feature.
    state.featured = new Set();
    const _seen = {};
    for (const o of state.offers) {
      if (o._superseded) continue;
      const f = o.fuel || "electricity";
      _seen[f] = _seen[f] || 0;
      if (_seen[f] < MAX_FEATURED_PER_FUEL) { state.featured.add(o.index); _seen[f]++; }
    }
    // Editing WITHOUT a new upload = re-saving the same offers: restore exactly what
    // was shown last time so nothing swaps. Editing WITH a new upload is a rate
    // refresh, so keep the recency pre-tick above (the new rates win).
    const hasNewUpload = state.files.some((f) => f.extract && !f.fromSaved);
    if (state.editing && !hasNewUpload) {
      const wasFeatured = flatQuotes()
        .map((q, i) => (q && q.featured ? i : -1))
        .filter((i) => i >= 0);
      if (wasFeatured.length) state.featured = new Set(wasFeatured);
    }
    renderOfferList();
  } catch (e) {
    list.innerHTML = "";
    notice($("assemble-msg"), "Could not cost the offers: " + e.message, "error");
  } finally {
    $("offer-loading").classList.add("hidden");
  }
}

function money(n) {
  return n == null ? "—" : "£" + Number(n).toLocaleString("en-GB", { maximumFractionDigits: 0 });
}

function fuelOf(idx) {
  const o = state.offers.find((x) => x.index === idx);
  return (o && o.fuel) || "electricity";
}
function featuredCountForFuel(f) {
  let n = 0;
  state.featured.forEach((idx) => { if (fuelOf(idx) === f) n++; });
  return n;
}
const FUEL_LABEL = { electricity: "Electricity", gas: "Gas" };

function annotateOffers() {
  // Walk state.files in the SAME order the extracts were sent, so a flat counter
  // lines up with offer.index; record each quote's source file + added date.
  const src = [];
  for (const f of state.files) {
    if (!f.extract) continue;
    const label = f.fromSaved ? "saved" : (f.name || "upload");
    for (const q of (f.extract.quotes || [])) {
      src.push({ fromSaved: !!f.fromSaved, source: label, addedAt: q.added_at || null });
    }
  }
  state.offers.forEach((o) => {
    const s = src[o.index] || {};
    o._fromSaved = !!s.fromSaved;
    o._source = s.source || (o._fromSaved ? "saved" : "upload");
    o.added_at = o.added_at || s.addedAt || null;
  });

  // Supersede: group by supplier|term|fuel; newest added_at is current, older ones
  // superseded (tie-break: a new upload beats the saved set, then later flat index).
  const groups = {};
  state.offers.forEach((o) => {
    const k = (o.supplier || "") + "|" + (o.term || "") + "|" + (o.fuel || "electricity");
    (groups[k] = groups[k] || []).push(o);
  });
  Object.values(groups).forEach((list) => {
    list.forEach((o) => { o._superseded = false; o._supersededBy = null; });
    if (list.length < 2) return;
    const sorted = [...list].sort((a, b) => {
      const ta = Date.parse(a.added_at || "") || 0, tb = Date.parse(b.added_at || "") || 0;
      if (tb !== ta) return tb - ta;                                    // newest first
      if (!!a._fromSaved !== !!b._fromSaved) return a._fromSaved ? 1 : -1; // new upload beats saved
      return b.index - a.index;                                         // later add wins
    });
    sorted.forEach((o, i) => { o._superseded = i > 0; o._supersededBy = i > 0 ? sorted[0] : null; });
  });

  // Cheapest among CURRENT offers, per fuel (drives the badge + the recommendation).
  const best = {};
  state.offers.forEach((o) => {
    if (o._superseded || o.annual_cost == null) return;
    const f = o.fuel || "electricity";
    if (!best[f] || o.annual_cost < best[f].annual_cost) best[f] = o;
  });
  state.offers.forEach((o) => { o._cheapestCurrent = false; });
  Object.values(best).forEach((o) => { o._cheapestCurrent = true; });
}

// Format an ISO added-date for the picker: "today", else "2 Sep 2026".
function fmtAdded(iso) {
  if (!iso) return "date unknown";
  const d = new Date(iso);
  if (isNaN(d)) return "date unknown";
  if (d.toDateString() === new Date().toDateString()) return "today";
  const M = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${d.getDate()} ${M[d.getMonth()]} ${d.getFullYear()}`;
}

function renderOfferList() {
  const list = $("offer-list");
  list.innerHTML = "";
  const fuels = [];
  for (const o of state.offers) { const f = o.fuel || "electricity"; if (!fuels.includes(f)) fuels.push(f); }
  const multi = fuels.length > 1;
  for (const f of fuels) {
    if (multi) {
      const head = document.createElement("div");
      head.className = "offer-fuel-head";
      head.style.cssText = "font:600 11px var(--mono,ui-monospace,monospace);letter-spacing:.08em;text-transform:uppercase;color:var(--text-3);margin:16px 0 6px";
      head.textContent = FUEL_LABEL[f] || f;
      list.append(head);
    }
    for (const o of state.offers.filter((x) => (x.fuel || "electricity") === f)) {
      const ticked = state.featured.has(o.index);
      const disabled = !ticked && featuredCountForFuel(f) >= MAX_FEATURED_PER_FUEL;
      const eff = o.effective_pkwh != null ? `${o.effective_pkwh.toFixed(2)}p/kWh` : "—";
      const badges = (o._cheapestCurrent ? '<span class="chip success">CHEAPEST</span>' : "")
        + (o._superseded ? '<span class="chip" style="color:var(--text-3);border-color:rgba(237,237,237,.2)">SUPERSEDED</span>' : "")
        + (o.covers_all_sites ? "" : '<span class="chip danger">PARTIAL COVER</span>');
      // Provenance line: when the rates were added and from where. A superseded row
      // instead shows the newer rate it lost to, with the price move.
      let sub;
      if (o._superseded && o._supersededBy) {
        const nw = o._supersededBy;
        const d = (nw.annual_cost != null && o.annual_cost != null) ? nw.annual_cost - o.annual_cost : null;
        sub = `superseded by the ${fmtAdded(nw.added_at)} rates · ${money(o.annual_cost)} → ${money(nw.annual_cost)}`
          + (d != null ? ` (${d >= 0 ? "+" : "−"}${money(Math.abs(d))})` : "");
      } else {
        sub = `added ${fmtAdded(o.added_at)}${o._source ? " · " + escapeHtml(o._fromSaved ? "saved" : o._source) : ""}`;
      }
      const row = document.createElement("label");
      row.className = "offer" + (ticked ? " on" : "") + (disabled ? " off" : "");
      if (o._superseded) row.style.opacity = "0.62";
      row.innerHTML = `
        <input type="checkbox" data-idx="${o.index}" ${ticked ? "checked" : ""} ${disabled ? "disabled" : ""}>
        <div class="offer-main">
          <div class="offer-name">${escapeHtml(o.supplier || "—")}${o.term ? " · " + escapeHtml(o.term) : ""} ${badges}</div>
          <div class="offer-cost mono">${money(o.annual_cost)}/yr · ${eff}</div>
          <div style="font-size:11px;color:var(--text-3);margin-top:3px">${sub}</div>
        </div>`;
      list.append(row);
    }
  }
  list.querySelectorAll("input[type=checkbox]").forEach((cb) =>
    cb.addEventListener("change", () => {
      const idx = Number(cb.dataset.idx);
      const f = fuelOf(idx);
      if (cb.checked) { if (featuredCountForFuel(f) < MAX_FEATURED_PER_FUEL) state.featured.add(idx); }
      else state.featured.delete(idx);
      renderOfferList();
    }));
  const hint = document.createElement("div");
  hint.className = "offer-hint";
  hint.textContent = `Tick up to ${MAX_FEATURED_PER_FUEL}${multi ? " per fuel" : ""}. Costs use RYE's `
    + "standard splits - day/night 70/30; weekend 2/7 where a weekend rate is quoted.";
  list.append(hint);
}

function assembleMeta() {
  const meta = {
    client_name: state.meta.client_name,
    tender_label: state.meta.tender_label,
    utility: state.meta.utility,
  };
  if (state.meta.id) meta.id = state.meta.id;              // re-assemble -> version bumps
  // created_by is left unset -> the backend stamps a sensible default. day_split /
  // weekend_split are NOT sent -> the backend applies the standing hardcoded splits
  // (day/night 70/30, weekend 2/7 where a weekend rate is quoted).
  if ($("in-charge-model").value === "commission") {
    // Commission (p/kWh uplift) is charged INSTEAD of the flat fee.
    const uplift = parseFloat($("in-commission-uplift").value);
    if (!isNaN(uplift)) meta.commission_p_kwh_uplift = uplift;
    meta.commission_included = $("in-commission-included").checked;
  } else {
    const feeList = parseFloat($("in-fee-list").value);
    if (!isNaN(feeList)) meta.fee_list_price_site_month = feeList;
    const feeDisc = parseFloat($("in-fee-discount").value);
    if (!isNaN(feeDisc)) meta.fee_discount_pct = feeDisc;
  }

  const exp = $("in-expires").value;
  if (exp) meta.expires_at = exp;

  // Recommended = the cheapest of the TICKED offers (price-based; costs come from
  // the backend ranking, never computed here).
  const ticked = state.offers.filter((o) => state.featured.has(o.index) && o.annual_cost != null);
  const fuelsPresent = new Set(state.offers.map((o) => o.fuel || "electricity"));
  // Single fuel: name the cheapest ticked as the recommendation. Combined: each
  // fuel's section recommends its own cheapest featured offer, so we don't pin one
  // cross-fuel supplier here.
  if (fuelsPresent.size <= 1 && ticked.length) {
    const rec = ticked.reduce((x, y) => (y.annual_cost < x.annual_cost ? y : x));
    meta.recommended_supplier = rec.supplier;
    if (rec.term) meta.recommended_term = rec.term;
  }

  const notes = $("in-notes").value.split("\n").map((s) => s.trim()).filter(Boolean);
  if (notes.length) meta.notes = notes;
  return meta;
}

async function doAssemble() {
  const flat = flatQuotes();
  if (!flat.length) {
    notice($("assemble-msg"), "No extracted offers yet - go back to the extract step.", "error");
    return;
  }
  if (!state.featured.size) {
    notice($("assemble-msg"), "Tick at least one offer to show the client.", "error");
    return;
  }
  {
    const present = [];
    for (const o of state.offers) { const f = o.fuel || "electricity"; if (!present.includes(f)) present.push(f); }
    for (const f of present) {
      if (!featuredCountForFuel(f)) {
        notice($("assemble-msg"), `Tick at least one ${(FUEL_LABEL[f] || f).toLowerCase()} offer to show the client.`, "error");
        return;
      }
    }
  }
  if ($("in-benchmark-on").checked
      && !($("in-benchmark-unit").value || "").trim()
      && !($("in-benchmark-gas-unit").value || "").trim()) {
    notice($("assemble-msg"), "Enter a benchmark unit rate for electricity or gas, or untick the benchmark baseline.", "error");
    return;
  }
  // Flag the featured offers on the quote objects - this rides through /assemble
  // into the tender, and /render shows only the featured ones.
  flat.forEach((q, i) => { q.featured = state.featured.has(i); });

  const extracts = state.files.filter((f) => f.extract).map((f) => f.extract);
  const btn = $("btn-assemble");
  btn.disabled = true;
  $("assemble-result").classList.add("hidden");
  $("assemble-loading").classList.remove("hidden");
  notice($("assemble-msg"), "");
  try {
    const fd = new FormData();
    fd.append("extracts", JSON.stringify(extracts));
    fd.append("meta", JSON.stringify(assembleMeta()));
    fd.append("persist", "true");
    if (state.sitesCsv) fd.append("sites_csv", state.sitesCsv);
    // Benchmark baseline - only sent when the operator ticked it AND typed a rate.
    // The backend ignores it if the site reference yields a real incumbent.
    if ($("in-benchmark-on").checked) {
      const unit = ($("in-benchmark-unit").value || "").trim();
      if (unit) {
        fd.append("benchmark_unit_rate", unit);
        const standing = ($("in-benchmark-standing").value || "").trim();
        if (standing) fd.append("benchmark_standing_charge", standing);
      }
      const gasUnit = ($("in-benchmark-gas-unit").value || "").trim();
      if (gasUnit) {
        fd.append("gas_benchmark_unit_rate", gasUnit);
        const gasStanding = ($("in-benchmark-gas-standing").value || "").trim();
        if (gasStanding) fd.append("gas_benchmark_standing_charge", gasStanding);
      }
    }
    // Editing: keep the baseline the saved tender already had. The backend re-reads
    // it from the DB by id — we only send the intent, never the rates, so a client
    // -facing baseline can't be set from the browser. A new sites.csv or benchmark
    // above still takes precedence server-side.
    if (state.editing && state.editing.incumbent && $("in-keep-incumbent").checked) {
      fd.append("keep_incumbent", "true");
    }
    const r = await api("/api/assemble", { method: "POST", body: fd });
    state.meta.id = r.id;   // subsequent saves bump the version instead of duplicating
    state.saved = r;        // slug / url_uuid / version for the preview + publish step
    renderAssembleResult(r);
  } catch (e) {
    if (e.message !== "unauthorised") notice($("assemble-msg"), "Assemble failed: " + e.message, "error");
  } finally {
    $("assemble-loading").classList.add("hidden");
    btn.disabled = false;
  }
}

function renderAssembleResult(r) {
  const c = r.counts || {};
  const savedChip = r.persisted
    ? '<span class="chip success">SAVED TO REGISTER</span>'
    : '<span class="chip info">DRY RUN — NOT SAVED</span>';
  const warns = (r.warnings || []).map((w) =>
    `<div class="notice warn">⚠ ${escapeHtml(w)}</div>`).join("");
  $("assemble-result").innerHTML = `
    <div class="result-grid">
      <span class="kv">${savedChip}</span>
      <span class="kv">version <b>v${escapeHtml(String(r.version))}</b></span>
      <span class="kv">status <b>${escapeHtml(r.status || "—")}</b></span>
      <span class="kv">sites <b>${escapeHtml(String(c.sites ?? "—"))}</b></span>
      <span class="kv">offers <b>${escapeHtml(String(c.quotes ?? "—"))}</b></span>
      <span class="kv">incumbent <b>${escapeHtml(r.incumbent_supplier || "none")}</b> (${escapeHtml(String(c.incumbent_lines ?? 0))} line(s))</span>
      <span class="kv">tender id <b class="mono">${escapeHtml(r.id || "—")}</b></span>
    </div>
    ${warns || '<div class="notice success">No warnings — cost assumptions look clean. Review before publishing.</div>'}
    <div class="notice">Saved as a draft version. Re-saving from here bumps the version, never overwrites.</div>
    <div class="actions"><div class="spacer"></div><button class="btn-primary" id="btn-to-preview">Continue to preview &rarr;</button></div>`;
  $("assemble-result").classList.remove("hidden");
  $("btn-to-preview").addEventListener("click", openPublishStep);
}

// --- step 6: preview & publish -------------------------------------------------

function wouldBeUrl(saved) {
  const slug = (saved && saved.slug) || "client";
  const uuid = (saved && saved.url_uuid) || "…";
  return `rye.energy/${slug}/${uuid}`;
}

function openPublishStep() {
  showStep(6);
  notice($("preview-msg"), "");
  $("publish-result").classList.add("hidden");
  const s = state.saved || {};
  $("publish-meta").innerHTML = `
    <span class="kv">client <b>${escapeHtml(state.meta.client_name || "—")}</b></span>
    <span class="kv">tender <b>${escapeHtml(state.meta.tender_label || "—")}</b></span>
    <span class="kv">version <b>v${escapeHtml(String(s.version ?? "—"))}</b></span>
    <span class="kv">status <b>${escapeHtml(s.status || "draft")}</b></span>`;
}

async function doPublish() {
  if (!state.meta.id) { notice($("preview-msg"), "Save the tender first (assemble step).", "error"); return; }
  const btn = $("btn-publish");
  btn.disabled = true;
  $("publish-result").classList.add("hidden");
  $("publish-loading").classList.remove("hidden");
  notice($("preview-msg"), "");
  try {
    const r = await api("/api/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tender_id: state.meta.id }),
    });
    if (state.saved) { state.saved.status = "published"; state.saved.version = r.version; state.saved.url_uuid = r.url_uuid; }
    renderPublishResult(r);
  } catch (e) {
    notice($("preview-msg"), "Publish failed: " + e.message, "error");
  } finally {
    $("publish-loading").classList.add("hidden");
    btn.disabled = false;
  }
}

function copyToClipboard(text, btn) {
  const done = () => { const t = btn.textContent; btn.textContent = "Copied"; setTimeout(() => { btn.textContent = t; }, 1500); };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, () => {});
}

function renderPublishResult(r) {
  const url = r.url || "";
  const exp = r.expires_at ? ` · expires ${escapeHtml(fmtDate(r.expires_at))}` : "";
  const el = $("publish-result");
  el.innerHTML = `
    <div class="notice success">Published — v${escapeHtml(String(r.version))}${exp}. Paste this client link into Front / WhatsApp:</div>
    <div class="linkbar">
      <input type="text" id="publish-url" class="mono" readonly value="${escapeHtml(url)}">
      <button class="btn-secondary" id="btn-copy-url">Copy</button>
      <a class="btn-secondary" id="btn-open-url" href="${escapeHtml(url)}" target="_blank" rel="noopener">Open</a>
    </div>
    <div class="notice">Anyone with this link can view the dashboard (no login). Revoke it any time from the Register — that rotates the link and takes it offline.</div>`;
  el.classList.remove("hidden");
  $("btn-copy-url").addEventListener("click", () => copyToClipboard(url, $("btn-copy-url")));
}

async function openPreview(opts) {
  // Render the client dashboard HTML (by tender_id or inline tender) into the
  // sandboxed iframe overlay. /api/render returns HTML, so use apiText, not api.
  const overlay = $("preview-overlay");
  overlay.classList.remove("hidden");
  $("preview-title").textContent = opts.title || "Client dashboard preview";
  $("preview-url").textContent = opts.url || "";
  $("preview-frame").srcdoc = "";
  $("preview-loading").classList.remove("hidden");
  try {
    const body = opts.tender_id ? { tender_id: opts.tender_id } : { tender: opts.tender };
    const html = await apiText("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("preview-frame").srcdoc = html;
  } catch (e) {
    $("preview-frame").srcdoc =
      `<pre style="padding:16px;font-family:monospace;color:#b00;white-space:pre-wrap">Render failed: ${escapeHtml(e.message)}</pre>`;
  } finally {
    $("preview-loading").classList.add("hidden");
  }
}

function closePreview() {
  $("preview-overlay").classList.add("hidden");
  $("preview-frame").srcdoc = "";
}

// --- register ------------------------------------------------------------------

function fmtDate(s) {
  if (!s) return "—";
  const d = new Date(s);
  return isNaN(d.getTime()) ? String(s) : d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

// --- editing a saved tender (Tier 1: no re-upload) ----------------------------
//
// The raw quote files are NOT stored — only the extracted result (sites + quotes
// with their rates) lives in the saved payload. That is exactly what the assemble
// step consumes, so an edit can skip upload/map/extract entirely: we rebuild ONE
// synthetic extract from the stored payload, drop it in as a confirmed file, and
// jump to step 5. Re-mapping or adding a supplier still needs the file, which is
// a separate (bigger) job.

function renderEditBanner() {
  const el = $("edit-banner");
  if (!el) return;
  const e = state.editing;
  if (!e) { el.classList.add("hidden"); el.innerHTML = ""; return; }
  const live = e.status === "published"
    ? " The client's existing link stays on the published version until you publish again — the link itself does not change."
    : "";
  el.innerHTML = `<div class="notice">✎ Editing <b>${escapeHtml(state.meta.client_name || "—")}
    — ${escapeHtml(state.meta.tender_label || "")}</b> (v${escapeHtml(String(e.version))},
    ${escapeHtml(e.status || "draft")}). Saving writes a new version.${live}</div>`;
  el.classList.remove("hidden");
}

function renderKeepIncumbent() {
  const wrap = $("keep-incumbent-field");
  if (!wrap) return;
  const inc = state.editing && state.editing.incumbent;
  if (!inc) { wrap.classList.add("hidden"); return; }
  const kind = inc.kind === "benchmark" ? "market benchmark" : "actual contract";
  const lines = (inc.lines || []).length;
  const lbl = $("keep-incumbent-label");
  if (lbl) {
    lbl.textContent = `Keep the saved baseline — ${inc.supplier || "unknown"} `
      + `(${kind}, ${lines} line${lines === 1 ? "" : "s"})`;
  }
  wrap.classList.remove("hidden");
}

function dateInputValue(s) {
  // <input type="date"> only accepts YYYY-MM-DD; stored expires_at may be a full
  // timestamp. Anything unparseable is left blank rather than guessed.
  if (!s) return "";
  const m = String(s).match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : "";
}

function hydrateFromTender(p) {
  state.meta.id = p.id || null;                    // re-save bumps the version
  state.meta.client_name = p.client_name || "";
  state.meta.tender_label = p.tender_label || "";
  state.meta.utility = p.utility || "electricity";
  // Supplier is only used as the mapping-cache key on upload; an edit re-uploads
  // nothing, so take it from the stored quotes purely so the screen reads right.
  state.meta.supplier = ((p.quotes || [])[0] || {}).supplier || "";

  $("in-client").value = state.meta.client_name;
  $("in-label").value = state.meta.tender_label;
  $("in-utility").value = state.meta.utility;

  // ONE synthetic extract standing in for the original uploads. Marked confirmed so
  // the existing openAssemble() -> /api/cost path re-ranks from stored data; the
  // stored sites already carry authoritative EAC (eac_source "db"), so the ranking
  // matches what /render will show without re-uploading a sites.csv.
  state.files = [{
    file: null,
    name: `saved tender v${p.version} (${(p.sites || []).length} site(s), ${(p.quotes || []).length} offer(s))`,
    status: "confirmed",
    fromSaved: true,
    mapResp: null,
    mapping: null,
    inspection: null,
    extract: { sites: p.sites || [], quotes: (p.quotes || []).map((q) => ({ ...q, added_at: q.added_at || p.created_at || p.generated || null })) },
    extractResp: null,
    extractStatus: "done",
    extractError: null,
  }];

  // How RYE is paid.
  if (p.rye_commission) {
    $("in-charge-model").value = "commission";
    if (p.rye_commission.p_kwh_uplift != null) $("in-commission-uplift").value = p.rye_commission.p_kwh_uplift;
    $("in-commission-included").checked = !!p.rye_commission.included;
  } else {
    $("in-charge-model").value = "fee";
    const f = p.rye_fee || {};
    if (f.list_price_site_month != null) $("in-fee-list").value = f.list_price_site_month;
    if (f.discount_pct != null) $("in-fee-discount").value = f.discount_pct;
  }
  onChargeModelChange();

  $("in-expires").value = dateInputValue(p.expires_at);
  $("in-notes").value = (p.notes || []).join("\n");

  // Baseline. A stored benchmark prefills the benchmark fields so the operator can
  // adjust the rate; a real incumbent came from a sites.csv we no longer hold, so it
  // can only be kept as-is or replaced.
  const inc = p.incumbent || null;
  if (inc && inc.kind === "benchmark") {
    const first = (inc.lines || [])[0] || {};
    $("in-benchmark-on").checked = true;
    if (first.unitRate != null) $("in-benchmark-unit").value = first.unitRate;
    if (first.standingCharge != null) $("in-benchmark-standing").value = first.standingCharge;
  }
  onBenchmarkToggle();

  state.editing = { version: p.version, status: p.status || "draft", incumbent: inc };
  state.saved = {
    id: p.id, version: p.version, status: p.status,
    slug: p.slug, url_uuid: p.url_uuid, dashboard_url: p.dashboard_url,
  };
  $("in-keep-incumbent").checked = !!inc;
}

async function editTender(tenderId) {
  notice($("register-msg"), "");
  try {
    const r = await api(`/api/tenders/${encodeURIComponent(tenderId)}`);
    resetWizard();
    hydrateFromTender(r.tender || {});
    renderEditBanner();
    renderKeepIncumbent();
    showScreen("wizard");
    renderFiles();
    // Straight to assemble/review: everything the previous save produced is already
    // in state, so there is nothing to re-upload or re-map.
    await openAssemble();
  } catch (e) {
    notice($("register-msg"), "Could not open that tender for editing: " + e.message, "error");
  }
}

async function showRegister() {
  showScreen("register");
  await loadRegister();
}

async function loadRegister() {
  const list = $("register-list");
  list.innerHTML = "";
  notice($("register-msg"), "");
  $("register-loading").classList.remove("hidden");
  try {
    const r = await api("/api/tenders");
    renderRegister(r.tenders || [], r.note);
  } catch (e) {
    notice($("register-msg"), "Could not load the register: " + e.message, "error");
  } finally {
    $("register-loading").classList.add("hidden");
  }
}

function renderRegister(rows, note) {
  const list = $("register-list");
  list.innerHTML = "";
  if (!rows.length) {
    list.innerHTML = `<div class="notice">${escapeHtml(note || "No tenders saved yet — start one from New tender.")}</div>`;
    return;
  }
  for (const t of rows) {
    const row = document.createElement("div");
    row.className = "tender-row";
    const status = (t.status || "draft");
    const rec = t.recommended_supplier ? " · rec: " + escapeHtml(t.recommended_supplier) : "";
    row.innerHTML = `
      <div class="tender-main">
        <div class="tender-title">${escapeHtml(t.client_name || "—")} — ${escapeHtml(t.tender_label || "")}</div>
        <div class="tender-sub">v${escapeHtml(String(t.version))} · ${escapeHtml(String(t.quotes ?? 0))} offer(s) · ${escapeHtml(String(t.sites ?? 0))} site(s) · saved ${escapeHtml(fmtDate(t.created_at))}${rec}</div>
      </div>
      <div class="right">
        <span class="chip status-${escapeHtml(status)}">${escapeHtml(status.toUpperCase())}</span>
        <button class="btn-secondary" data-preview="${escapeHtml(t.id)}" data-title="${escapeHtml(t.client_name || "")}">Preview</button>
        <button class="btn-secondary" data-edit="${escapeHtml(t.id)}">Edit</button>
        ${status === "published" && t.dashboard_url
          ? `<a class="btn-secondary" href="${escapeHtml(t.dashboard_url)}" target="_blank" rel="noopener">Open link</a>
             <button class="btn-ghost" data-revoke="${escapeHtml(t.id)}">Revoke</button>`
          : ""}
      </div>`;
    list.append(row);
  }
  list.querySelectorAll("[data-preview]").forEach((b) =>
    b.addEventListener("click", () => openPreview({
      tender_id: b.dataset.preview,
      title: (b.dataset.title || "Client") + " — dashboard preview",
    })));
  list.querySelectorAll("[data-edit]").forEach((b) =>
    b.addEventListener("click", () => editTender(b.dataset.edit)));
  list.querySelectorAll("[data-revoke]").forEach((b) =>
    b.addEventListener("click", () => revokeTender(b.dataset.revoke)));
}

async function revokeTender(tenderId) {
  if (typeof confirm === "function" && !confirm("Revoke this client link? The current link stops working immediately.")) return;
  notice($("register-msg"), "");
  try {
    await api("/api/revoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tender_id: tenderId }),
    });
    notice($("register-msg"), "Link revoked — the tender is back to draft. Re-publish to mint a fresh link.", "success");
    await loadRegister();
  } catch (e) {
    notice($("register-msg"), "Revoke failed: " + e.message, "error");
  }
}

// --- wiring ------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  $("in-supplier").addEventListener("change", onSupplierChange);
  $("in-charge-model").addEventListener("change", onChargeModelChange);
  $("in-benchmark-on").addEventListener("change", onBenchmarkToggle);
  onChargeModelChange();
  $("btn-to-upload").addEventListener("click", toUpload);
  $("btn-back-1").addEventListener("click", () => showStep(1));
  $("btn-back-2").addEventListener("click", () => { showStep(2); renderFiles(); });
  $("btn-to-extract").addEventListener("click", openExtract);

  // step 4: extract
  $("btn-back-to-upload").addEventListener("click", () => { showStep(2); renderFiles(); });
  $("btn-extract-all").addEventListener("click", runExtractAll);
  $("btn-to-assemble").addEventListener("click", openAssemble);
  $("btn-pick-siteref").addEventListener("click", () => $("in-siteref").click());
  $("in-siteref").addEventListener("change", (e) => {
    state.sitesCsv = e.target.files[0] || null;
    e.target.value = "";
    renderSiteref();
  });
  $("btn-clear-siteref").addEventListener("click", () => { state.sitesCsv = null; renderSiteref(); });

  // step 5: assemble
  $("btn-back-to-extract").addEventListener("click", () => { showStep(4); renderExtractList(); });
  $("btn-assemble").addEventListener("click", doAssemble);

  // step 6: preview & publish
  $("btn-back-to-assemble").addEventListener("click", () => showStep(5));
  $("btn-publish").addEventListener("click", doPublish);
  $("btn-preview").addEventListener("click", () => openPreview({
    tender_id: state.meta.id,
    title: (state.meta.client_name || "Client") + " — dashboard preview",
    url: wouldBeUrl(state.saved),
  }));
  $("btn-close-preview").addEventListener("click", closePreview);

  // nav + register
  $("nav-new").addEventListener("click", newTender);
  $("nav-register").addEventListener("click", showRegister);
  $("btn-register-new").addEventListener("click", newTender);
  $("btn-refresh-register").addEventListener("click", loadRegister);

  const dz = $("dropzone");
  dz.addEventListener("click", () => $("in-files").click());
  $("in-files").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("drag");
    addFiles(e.dataTransfer.files);
  });

  $("btn-apply-json").addEventListener("click", applyJson);
  $("btn-confirm-map").addEventListener("click", confirmMap);

  // No auth gate — open the wizard straight away.
  showScreen("wizard");
  showStep(1);
  loadSuppliers();
});

// Exposed for the headless DOM smoke test (jsdom) — not used by the UI itself.
window.__rye_debug = {
  state, addFiles, openMap, renderFiles,
  openExtract, runExtractAll, renderExtractList,
  openAssemble, loadOffers, renderOfferList, annotateOffers, flatQuotes, doAssemble, assembleMeta,
  openPublishStep, openPreview, closePreview, showRegister, loadRegister, renderRegister, showScreen,
  resetWizard, newTender, editTender, hydrateFromTender, renderEditBanner, renderKeepIncumbent,
  showStep,
};
