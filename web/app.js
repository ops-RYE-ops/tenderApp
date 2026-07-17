/* RYE Tender Tool — team UI (vanilla JS, no build step).
 *
 * Thin front door over the FastAPI endpoints. All pipeline logic lives in the
 * backend; this file only collects inputs, shows results, and lets a human
 * confirm the column mapping before anything is extracted.
 *
 * PR 1 scope: unlock → tender basics → upload → map review/confirm.
 * Extract / assemble / publish are the next PRs.
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
  key: localStorage.getItem("rye_team_key") || "",
  email: localStorage.getItem("rye_user_email") || "",
  meta: { client_name: "", tender_label: "", utility: "electricity", supplier: "", id: null },
  files: [],        // { file, name, status, mapResp, mapping, inspection, extract, extractResp, extractStatus, extractError }
  activeIdx: null,  // index into files for the map screen
  sitesCsv: null,   // shared sites.csv File — feeds both /extract (site-ref) and /assemble (incumbent)
  recCombos: [],    // supplier+term combos found across the extracts (recommended-offer options)
};

const $ = (id) => document.getElementById(id);

// --- API helper --------------------------------------------------------------

async function api(path, opts = {}) {
  opts.headers = Object.assign({}, opts.headers, state.key ? { "X-RYE-Key": state.key } : {});
  const res = await fetch(path, opts);
  if (res.status === 401) {
    showUnlock("Key rejected — check it and try again.");
    throw new Error("unauthorised");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* non-JSON */ }
    throw new Error(detail);
  }
  return res.json();
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

function show(screen) {
  $("screen-unlock").classList.toggle("hidden", screen !== "unlock");
  $("screen-wizard").classList.toggle("hidden", screen === "unlock");
  $("btn-lock").classList.toggle("hidden", screen === "unlock");
  $("nav-user").classList.toggle("hidden", screen === "unlock" || !state.email);
  $("nav-user").textContent = state.email;
}

function showUnlock(msg) {
  show("unlock");
  $("in-key").value = state.key;
  $("in-email").value = state.email;
  notice($("unlock-msg"), msg || "", msg ? "error" : "");
}

function showStep(n) {
  for (const s of [1, 2, 3, 4, 5]) $("step-" + s).classList.toggle("hidden", s !== n);
  document.querySelectorAll("#stepper .step[data-step]").forEach((el) => {
    const s = Number(el.dataset.step);
    el.classList.toggle("active", s === n);
    el.classList.toggle("done", s < n);
  });
}

// --- unlock ------------------------------------------------------------------

async function unlock() {
  state.key = $("in-key").value.trim();
  state.email = $("in-email").value.trim();
  localStorage.setItem("rye_team_key", state.key);
  localStorage.setItem("rye_user_email", state.email);
  notice($("unlock-msg"), "");
  try {
    await api("/api/auth-check");
  } catch (e) {
    if (e.message !== "unauthorised") showUnlock("Could not reach the API: " + e.message);
    return;
  }
  show("wizard");
  showStep(1);
  loadSuppliers();
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
        confirmed_by: state.email || null,
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

function collectCombos() {
  // Distinct supplier+term offers across the extracts — the recommended-offer options.
  const seen = new Set();
  const combos = [];
  for (const f of state.files) {
    for (const q of ((f.extract && f.extract.quotes) || [])) {
      const key = (q.supplier || "") + " " + (q.term || "");
      if (q.supplier && !seen.has(key)) {
        seen.add(key);
        combos.push({ supplier: q.supplier, term: q.term || "" });
      }
    }
  }
  return combos;
}

function openAssemble() {
  showStep(5);
  notice($("assemble-msg"), "");
  state.recCombos = collectCombos();
  const sel = $("in-recommended");
  sel.innerHTML = '<option value="">— none / decide later —</option>';
  state.recCombos.forEach((c, i) =>
    sel.append(new Option(c.term ? `${c.supplier} — ${c.term}` : c.supplier, String(i))));
}

function assembleMeta() {
  const meta = {
    client_name: state.meta.client_name,
    tender_label: state.meta.tender_label,
    utility: state.meta.utility,
  };
  if (state.meta.id) meta.id = state.meta.id;              // re-assemble → version bumps
  if (state.email) meta.created_by = state.email;

  const ds = parseFloat($("in-day-split").value);
  meta.day_split = isNaN(ds) ? 0.7 : ds;
  const ws = parseFloat($("in-weekend-split").value);
  meta.weekend_split = isNaN(ws) ? 0 : ws;

  const feeList = parseFloat($("in-fee-list").value);
  if (!isNaN(feeList)) meta.fee_list_price_site_month = feeList;
  const feeDisc = parseFloat($("in-fee-discount").value);
  if (!isNaN(feeDisc)) meta.fee_discount_pct = feeDisc;

  const exp = $("in-expires").value;
  if (exp) meta.expires_at = exp;

  const recIdx = $("in-recommended").value;
  if (recIdx !== "") {
    const c = state.recCombos[Number(recIdx)];
    if (c) { meta.recommended_supplier = c.supplier; if (c.term) meta.recommended_term = c.term; }
  }

  const notes = $("in-notes").value.split("\n").map((s) => s.trim()).filter(Boolean);
  if (notes.length) meta.notes = notes;
  return meta;
}

async function doAssemble() {
  const extracts = state.files.filter((f) => f.extract).map((f) => f.extract);
  if (!extracts.length) {
    notice($("assemble-msg"), "No extracted files yet — go back to the extract step.", "error");
    return;
  }
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
    <div class="notice">Saved as a draft version. Publishing to a client link is the next step (coming in a later update). Re-saving from here bumps the version, never overwrites.</div>`;
  $("assemble-result").classList.remove("hidden");
}

// --- wiring ------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  $("btn-unlock").addEventListener("click", unlock);
  $("in-key").addEventListener("keydown", (e) => { if (e.key === "Enter") unlock(); });
  $("btn-lock").addEventListener("click", () => {
    state.key = "";
    localStorage.removeItem("rye_team_key");
    showUnlock();
  });

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

  // Auto-unlock if a stored key still works (or no key is configured).
  api("/api/auth-check")
    .then(() => { show("wizard"); showStep(1); loadSuppliers(); })
    .catch(() => showUnlock());
});

// Exposed for the headless DOM smoke test (jsdom) — not used by the UI itself.
window.__rye_debug = {
  state, addFiles, openMap, renderFiles,
  openExtract, runExtractAll, renderExtractList,
  openAssemble, doAssemble, collectCombos, assembleMeta,
};
