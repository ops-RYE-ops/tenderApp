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
  meta: { client_name: "", tender_label: "", utility: "electricity", supplier: "" },
  files: [],        // { file, name, status, mapResp, mapping, inspection }
  activeIdx: null,  // index into files for the map screen
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
  for (const s of [1, 2, 3]) $("step-" + s).classList.toggle("hidden", s !== n);
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
      "Extract is the next step (coming in the next update); your confirmed mapping is kept on this file.",
      "success");
  } catch (e) {
    if (e.message !== "unauthorised") notice($("map-msg"), "Save failed: " + e.message, "error");
  } finally {
    btn.disabled = false;
  }
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
window.__rye_debug = { state, addFiles, openMap, renderFiles };
