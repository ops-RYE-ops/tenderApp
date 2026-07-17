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
  "siteName", "mpxn", "updatedEac", "supplyStartDate",
  "unitRate", "dayRate", "nightRate", "weekendRate", "standingCharge",
  "capacityCharge", "networkCharge", "meterCharge", "kva",
];
const NEW_SUPPLIER = "__new__";

const state = {
  meta: { client_name: "", tender_label: "", utility: "electricity", supplier: "", id: null },
  files: [],        // { file, name, status, mapResp, mapping, inspection, extract, extractResp, extractStatus, extractError }
  activeIdx: null,  // index into files for the map screen
  sitesCsv: null,   // shared sites.csv File — feeds both /extract (site-ref) and /assemble (incumbent)
  offers: [],       // /api/cost ranking rows (one per extracted offer)
  featured: new Set(), // offer indices ticked to show the client (max 2)
  saved: null,      // last /api/assemble response (id, slug, url_uuid, version) for preview/publish
};

const MAX_FEATURED = 2;

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
  let names = [];
  try {
    const r = await api("/api/suppliers");
    names = r.suppliers || [];
  } catch (e) { /* fall through to free text */ }
  for (const n of names) sel.append(new Option(n, n));
  sel.append(new Option("+ New supplier…", NEW_SUPPLIER));
  if (!names.length) sel.value = NEW_SUPPLIER;
  onSupplierChange();
}

function onSupplierChange() {
  $("field-new-supplier").classList.toggle("hidden", $("in-supplier").value !== NEW_SUPPLIER);
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
    div.innerHTML = `<span class="name">${escapeHtml(f.name)}</span>
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
  const fields = TARGET_FIELDS.concat(Object.keys(cols).filter((k) => !TARGET_FIELDS.includes(k)));

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
    renderFiles();
    notice($("map-msg"),
      "Saved — the next " + state.meta.supplier + " quote with this layout skips Claude entirely. " +
      "Head back to files, then Continue to extract.",
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
  $("btn-pick-siteref").textContent = state.sitesCsv ? "Replace" : "Choose sites.csv";
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
    if (f.extractStatus === "done" && f.extractResp) {
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
  const files = confirmedFiles();
  if (!files.length) { notice($("extract-msg"), "Confirm at least one mapping first.", "error"); return; }
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
    const r = await api("/api/cost", { method: "POST", body: fd });
    state.offers = r.offers || [];
    // Pre-tick the two cheapest (offers arrive full-coverage-first, cheapest-first).
    state.featured = new Set(state.offers.slice(0, MAX_FEATURED).map((o) => o.index));
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

function renderOfferList() {
  const list = $("offer-list");
  list.innerHTML = "";
  for (const o of state.offers) {
    const ticked = state.featured.has(o.index);
    const disabled = !ticked && state.featured.size >= MAX_FEATURED;
    const eff = o.effective_pkwh != null ? `${o.effective_pkwh.toFixed(2)}p/kWh` : "—";
    const badges = (o.cheapest ? '<span class="chip success">CHEAPEST</span>' : "")
      + (o.covers_all_sites ? "" : '<span class="chip danger">PARTIAL COVER</span>');
    const row = document.createElement("label");
    row.className = "offer" + (ticked ? " on" : "") + (disabled ? " off" : "");
    row.innerHTML = `
      <input type="checkbox" data-idx="${o.index}" ${ticked ? "checked" : ""} ${disabled ? "disabled" : ""}>
      <div class="offer-main">
        <div class="offer-name">${escapeHtml(o.supplier || "—")}${o.term ? " · " + escapeHtml(o.term) : ""} ${badges}</div>
        <div class="offer-cost mono">${money(o.annual_cost)}/yr · ${eff}</div>
      </div>`;
    list.append(row);
  }
  list.querySelectorAll("input[type=checkbox]").forEach((cb) =>
    cb.addEventListener("change", () => {
      const idx = Number(cb.dataset.idx);
      if (cb.checked) { if (state.featured.size < MAX_FEATURED) state.featured.add(idx); }
      else state.featured.delete(idx);
      renderOfferList();
    }));
  const hint = document.createElement("div");
  hint.className = "offer-hint";
  hint.textContent = `Showing ${state.featured.size} of max ${MAX_FEATURED}. Costs use RYE's `
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
  const feeList = parseFloat($("in-fee-list").value);
  if (!isNaN(feeList)) meta.fee_list_price_site_month = feeList;
  const feeDisc = parseFloat($("in-fee-discount").value);
  if (!isNaN(feeDisc)) meta.fee_discount_pct = feeDisc;

  const exp = $("in-expires").value;
  if (exp) meta.expires_at = exp;

  // Recommended = the cheapest of the TICKED offers (price-based; costs come from
  // the backend ranking, never computed here).
  const ticked = state.offers.filter((o) => state.featured.has(o.index) && o.annual_cost != null);
  if (ticked.length) {
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
  const s = state.saved || {};
  $("publish-meta").innerHTML = `
    <span class="kv">client <b>${escapeHtml(state.meta.client_name || "—")}</b></span>
    <span class="kv">tender <b>${escapeHtml(state.meta.tender_label || "—")}</b></span>
    <span class="kv">version <b>v${escapeHtml(String(s.version ?? "—"))}</b></span>
    <span class="kv">status <b>${escapeHtml(s.status || "draft")}</b></span>
    <span class="kv">link (when published) <b class="mono">${escapeHtml(wouldBeUrl(s))}</b></span>`;
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
      </div>`;
    list.append(row);
  }
  list.querySelectorAll("[data-preview]").forEach((b) =>
    b.addEventListener("click", () => openPreview({
      tender_id: b.dataset.preview,
      title: (b.dataset.title || "Client") + " — dashboard preview",
    })));
}

// --- wiring ------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  $("in-supplier").addEventListener("change", onSupplierChange);
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
  $("btn-preview").addEventListener("click", () => openPreview({
    tender_id: state.meta.id,
    title: (state.meta.client_name || "Client") + " — dashboard preview",
    url: wouldBeUrl(state.saved),
  }));
  $("btn-close-preview").addEventListener("click", closePreview);

  // nav + register
  $("nav-new").addEventListener("click", () => showScreen("wizard"));
  $("nav-register").addEventListener("click", showRegister);
  $("btn-register-new").addEventListener("click", () => showScreen("wizard"));
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
  openAssemble, loadOffers, renderOfferList, flatQuotes, doAssemble, assembleMeta,
  openPublishStep, openPreview, closePreview, showRegister, loadRegister, renderRegister, showScreen,
};
