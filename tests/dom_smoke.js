// DOM smoke test for the wizard using jsdom: loads index.html + app.js with a
// stubbed fetch, walks unlock -> tender basics -> upload -> map -> extract ->
// assemble, and fails on any uncaught JS error. Not a visual check — that
// happens on the Vercel preview.
// Run from repo root: npm i jsdom && node tests/dom_smoke.js  (optional; needs Node)
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const WEB = require('path').join(__dirname, '..', 'web');
const html = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8')
  .replace(/<link[^>]*fonts[^>]*>/g, '')            // no network fonts
  .replace('<link rel="stylesheet" href="app.css">', '')
  .replace('<script src="app.js"></script>', '');   // injected manually below

const MAP_RESP = {
  source: 'llm', cache_hit: false, supplier: 'UrbanChain',
  layout_fingerprint: 'ab12cd34ef56ab78', file: 'q.csv',
  mapping: {
    header_row: 1, output_prefix: 'urbanchain',
    columns: {
      siteName: 'Site Name', mpxn: 'MPAN', updatedEac: 'EAC (kWh)',
      unitRate: { single: 'Unit Rate (p/kWh)' }, standingCharge: 'Standing Charge (p/day)',
      capacityCharge: 'KVA Charge (p/kVA/day)', dayRate: null, nightRate: null,
    },
  },
  sample_values: {},
  sheets: [{ name: 'Sheet1', header_row_best_guess: 1,
    headers: ['Site Name', 'MPAN', 'EAC (kWh)', 'Unit Rate (p/kWh)', 'Standing Charge (p/day)', 'KVA Charge (p/kVA/day)', 'Commission'] }],
  notes: [],
};
const INSPECT_RESP = {
  path: 'q.csv',
  sheets: [{
    name: 'Sheet1', header_row_best_guess: 1, headers: MAP_RESP.sheets[0].headers,
    first_rows: [
      MAP_RESP.sheets[0].headers,
      ['Dalston Lane', '1200098765432', '45,210', '24.51', '48.0', '3.10', '0'],
      ['Rye Lane', '1200011122233', '61,900', '23.98', '52.5', '2.95', '0'],
    ],
  }],
};

const TENDER_ID = '11111111-1111-4111-8111-111111111111';
const EXTRACT_RESP = {
  ok: true, file: 'q.csv', supplier: 'UrbanChain',
  extract_result: {
    sites: [{ mpxn: '1200098765432', site_name: 'Dalston Lane', eac: 45210, kva: null, eac_source: 'quote' }],
    quotes: [
      { supplier: 'UrbanChain', term: '24 months', category: 'fixed',
        lines: [{ mpxn: '1200098765432', unitRate: 24.51, standingCharge: 48.0 }] },
      { supplier: 'Octopus', term: '12 months', category: 'fixed',
        lines: [{ mpxn: '1200098765432', unitRate: 22.0, standingCharge: 45.0 }] },
    ],
  },
  counts: { sites: 1, quotes: 2, lines: 2 },
  unmatched_mpxn: [],
  site_reference_used: false,
};
// Ranking: Octopus (index 1) is cheapest; offers arrive cheapest-first.
const COST_RESP = {
  ok: true, site_count: 1, eac_total: 45210, day_split: 0.7, weekend_split: 2 / 7,
  offers: [
    { index: 1, supplier: 'Octopus', term: '12 months', category: 'fixed',
      annual_cost: 10000, effective_pkwh: 22.1, covers_all_sites: true, warnings: [], cheapest: true },
    { index: 0, supplier: 'UrbanChain', term: '24 months', category: 'fixed',
      annual_cost: 11000, effective_pkwh: 24.5, covers_all_sites: true, warnings: [], cheapest: false },
  ],
};
const ASSEMBLE_RESP = {
  ok: true, persisted: true, id: TENDER_ID, version: 1, status: 'draft',
  slug: 'amorino-uk', url_uuid: '22222222-2222-4222-8222-222222222222', dashboard_url: null,
  counts: { sites: 1, quotes: 1, incumbent_lines: 0 },
  incumbent_supplier: null, warnings: [], tender: {},
};

// A stored tender as GET /api/tenders/{id} returns it — the payload the register's
// Edit action hydrates from. Deliberately awkward in three ways, so the hydration is
// actually proven rather than coincidentally right: it is PUBLISHED (so the link
// warning shows), it is on COMMISSION not the flat fee, and the featured offer is
// UrbanChain — the DEARER one — so restoring it has to beat the "pre-tick the two
// cheapest" default.
const STORED_TENDER = {
  id: TENDER_ID, client_name: 'Amorino UK', tender_label: 'Electricity tender — July 2026',
  utility: 'electricity', status: 'published', version: 3,
  created_at: '2026-07-17T10:00:00Z', created_by: 'x@rye.energy',
  expires_at: '2026-09-30T00:00:00Z',
  slug: 'amorino-uk', url_uuid: '22222222-2222-4222-8222-222222222222',
  dashboard_url: 'http://localhost/d/amorino-uk/22222222-2222-4222-8222-222222222222',
  day_split: 0.7, weekend_split: 2 / 7,
  sites: [{ mpxn: '1200098765432', site_name: 'Dalston Lane', eac: 45210, kva: null, eac_source: 'db' }],
  quotes: [
    { supplier: 'UrbanChain', term: '24 months', category: 'fixed', featured: true,
      lines: [{ mpxn: '1200098765432', unitRate: 24.51, standingCharge: 48.0 }] },
    { supplier: 'Octopus', term: '12 months', category: 'fixed', featured: false,
      lines: [{ mpxn: '1200098765432', unitRate: 22.0, standingCharge: 45.0 }] },
  ],
  incumbent: { supplier: 'British Gas', kind: 'incumbent',
    lines: [{ mpxn: '1200098765432', unitRate: 27.4, standingCharge: 55.0 }] },
  rye_commission: { p_kwh_uplift: 0.45, included: true },
  recommended: { supplier: 'UrbanChain', term: '24 months' },
  notes: ['Offer valid until 10 Aug 2026'],
};

const routes = {
  '/api/suppliers': { suppliers: ['Octopus', 'UrbanChain'] },
  '/api/map': MAP_RESP,
  '/api/inspect': INSPECT_RESP,
  '/api/map/confirm': { ok: true, saved: true, supplier: 'UrbanChain' },
  '/api/extract': EXTRACT_RESP,
  '/api/cost': COST_RESP,
  '/api/assemble': ASSEMBLE_RESP,
  '/api/render': '<!doctype html><html><body><h1>RYE dashboard preview MARKER</h1></body></html>',
  '/api/publish': {
    ok: true, id: TENDER_ID, version: 2, status: 'published',
    url: 'http://localhost/d/amorino-uk/22222222-2222-4222-8222-222222222222',
    url_uuid: '22222222-2222-4222-8222-222222222222', slug: 'amorino-uk', expires_at: null,
  },
  '/api/tenders': {
    ok: true,
    tenders: [{
      id: TENDER_ID, client_name: 'Amorino UK', tender_label: 'Electricity tender — July 2026',
      utility: 'electricity', status: 'draft', version: 1, created_at: '2026-07-17T10:00:00Z',
      created_by: 'x@rye.energy', expires_at: null, slug: 'amorino-uk',
      url_uuid: '22222222-2222-4222-8222-222222222222', dashboard_url: null,
      sites: 1, quotes: 2, recommended_supplier: 'Octopus',
    }],
  },
  ['/api/tenders/' + TENDER_ID]: { ok: true, tender: STORED_TENDER },
};

const failures = [];
const check = (name, cond) => {
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name);
  if (!cond) failures.push(name);
};

(async () => {
  const dom = new JSDOM(html, { url: 'http://localhost/app/', runScripts: 'dangerously' });
  const { window } = dom;
  const sent = {};
  window.fetch = async (url, opts) => {
    const p = new URL(url, 'http://localhost').pathname;
    if (!(p in routes)) throw new Error('unstubbed fetch: ' + p);
    sent[p] = (opts && opts.body) || null;
    return {
      ok: true, status: 200,
      json: async () => routes[p],
      text: async () => (typeof routes[p] === 'string' ? routes[p] : JSON.stringify(routes[p])),
    };
  };
  window.onerror = (msg) => failures.push('uncaught: ' + msg);

  window.eval(fs.readFileSync(path.join(WEB, 'app.js'), 'utf8'));
  window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 50));

  const $ = (id) => window.document.getElementById(id);
  check('wizard shown on load (no auth gate)', !$('screen-wizard').classList.contains('hidden'));
  check('step 1 visible', !$('step-1').classList.contains('hidden'));
  check('supplier dropdown populated from /api/suppliers',
    [...$('in-supplier').options].some((o) => o.value === 'UrbanChain'));
  check('supplier defaults to the placeholder, not a real supplier', $('in-supplier').value === '');

  // Mandatory supplier: filling everything but the supplier must NOT advance.
  $('in-client').value = 'Amorino UK';
  $('in-label').value = 'Electricity tender — July 2026';
  $('btn-to-upload').click();
  check('cannot continue without choosing a supplier', $('step-2').classList.contains('hidden'));
  check('supplier-required error shown', $('step1-msg').textContent.toLowerCase().includes('supplier'));

  $('in-supplier').value = 'UrbanChain';
  $('btn-to-upload').click();
  check('continue -> step 2 once a supplier is chosen', !$('step-2').classList.contains('hidden'));

  // Inject a file entry directly (jsdom can't do real file inputs) and open the map.
  const state = window.__rye_debug.state;
  state.files.push({ file: new window.Blob(['x']), name: 'q.csv', status: 'pending', mapResp: null, mapping: null, inspection: null });
  window.__rye_debug.renderFiles();
  check('file card rendered', window.document.querySelector('.filecard .name').textContent === 'q.csv');

  await window.__rye_debug.openMap(0);
  await new Promise((r) => setTimeout(r, 50));
  check('map screen visible', !$('step-3').classList.contains('hidden'));
  check('map result rendered', !$('map-result').classList.contains('hidden'));
  const rows = [...window.document.querySelectorAll('#map-rows tr')];
  check('all 14 target fields rendered', rows.length === 14);
  const unitRow = rows.find((r) => r.querySelector('.fieldname').textContent === 'unitRate');
  check('unitRate select shows the proposed header',
    unitRow.querySelector('select').value === 'Unit Rate (p/kWh)');
  check('unitRate samples recomputed from /inspect rows',
    unitRow.querySelector('.samples').textContent.includes('24.51'));
  check('LLM source chip shown', $('map-meta').textContent.includes('PROPOSED BY CLAUDE'));

  // Override a column and check the spec shape is preserved ({single:…}).
  unitRow.querySelector('select').value = 'Standing Charge (p/day)';
  unitRow.querySelector('select').dispatchEvent(new window.Event('change'));
  await new Promise((r) => setTimeout(r, 20));
  check('override keeps {single:…} shape',
    JSON.parse($('map-json').value).columns.unitRate.single === 'Standing Charge (p/day)');

  $('btn-confirm-map').click();
  await new Promise((r) => setTimeout(r, 50));
  check('confirm marks file confirmed', state.files[0].status === 'confirmed');
  // Confirming closes the mapping panel and returns to the file list, rather than
  // leaving the operator to find Back — so the notice lands on step 2, not step 3.
  check('confirm closes the mapping panel', $('step-3').classList.contains('hidden'));
  check('confirm returns to the file list', !$('step-2').classList.contains('hidden'));
  check('success notice shown on the file list', $('step2-msg').textContent.includes('Mapping saved'));
  check('continue-to-extract enabled once a file is confirmed', !$('btn-to-extract').disabled);

  // --- step 4: extract ---
  $('btn-to-extract').click();
  await new Promise((r) => setTimeout(r, 20));
  check('extract screen visible', !$('step-4').classList.contains('hidden'));
  await window.__rye_debug.runExtractAll();
  await new Promise((r) => setTimeout(r, 50));
  check('file marked extracted', state.files[0].extractStatus === 'done');
  check('extract_result stored on the file', !!(state.files[0].extract && state.files[0].extract.quotes.length));
  check('extract counts rendered in the card', window.document.querySelector('#extract-list .sub2').textContent.includes('offer'));
  check('continue-to-assemble enabled after a successful extract', !$('btn-to-assemble').disabled);

  // --- step 5: assemble ---
  await window.__rye_debug.openAssemble();
  await new Promise((r) => setTimeout(r, 50));
  check('assemble screen visible', !$('step-5').classList.contains('hidden'));
  check('offer tick-list rendered (both offers)',
    window.document.querySelectorAll('#offer-list .offer').length === 2);
  check('cheapest offer is badged', $('offer-list').textContent.includes('CHEAPEST'));
  check('two cheapest pre-ticked', state.featured.size === 2);

  const meta = window.__rye_debug.assembleMeta();
  check('no split fields sent (backend applies standing defaults)',
    meta.day_split === undefined && meta.weekend_split === undefined);
  check('recommended = cheapest ticked offer (price-based)', meta.recommended_supplier === 'Octopus');
  check('recommended term carried', meta.recommended_term === '12 months');

  // Untick the cheapest → recommendation should fall to the next ticked offer.
  const cb = window.document.querySelector('#offer-list input[data-idx="1"]');
  cb.checked = false; cb.dispatchEvent(new window.Event('change'));
  await new Promise((r) => setTimeout(r, 20));
  check('unticking drops it from featured', !state.featured.has(1));
  check('recommendation follows the ticked set', window.__rye_debug.assembleMeta().recommended_supplier === 'UrbanChain');
  // Re-tick for the save.
  cb.checked = true; cb.dispatchEvent(new window.Event('change'));
  await new Promise((r) => setTimeout(r, 20));

  await window.__rye_debug.doAssemble();
  await new Promise((r) => setTimeout(r, 50));
  check('assemble result rendered', !$('assemble-result').classList.contains('hidden'));
  check('result shows the saved version', $('assemble-result').textContent.includes('v1'));
  check('tender id stored for re-save versioning', state.meta.id === TENDER_ID);
  check('featured flag set on the extracted quotes',
    state.files[0].extract.quotes.every((q) => typeof q.featured === 'boolean') &&
    state.files[0].extract.quotes.some((q) => q.featured === true));

  // --- step 6: preview & publish ---
  window.document.getElementById('btn-to-preview').click();
  await new Promise((r) => setTimeout(r, 20));
  check('step 6 (preview) visible', !$('step-6').classList.contains('hidden'));
  check('publish button is enabled', $('btn-publish').disabled === false);

  $('btn-publish').click();
  await new Promise((r) => setTimeout(r, 50));
  check('publish result shows the client link',
    !$('publish-result').classList.contains('hidden') &&
    ($('publish-url').value || '').includes('/d/amorino-uk/'));
  check('copy + open controls rendered',
    !!window.document.getElementById('btn-copy-url') && !!window.document.getElementById('btn-open-url'));

  $('btn-preview').click();
  await new Promise((r) => setTimeout(r, 50));
  check('preview overlay opens', !$('preview-overlay').classList.contains('hidden'));
  check('rendered HTML loaded into the iframe',
    ($('preview-frame').getAttribute('srcdoc') || '').includes('MARKER'));
  $('btn-close-preview').click();
  check('preview overlay closes', $('preview-overlay').classList.contains('hidden'));

  // --- register ---
  await window.__rye_debug.showRegister();
  await new Promise((r) => setTimeout(r, 50));
  check('register screen visible', !$('screen-register').classList.contains('hidden'));
  check('wizard hidden while on register', $('screen-wizard').classList.contains('hidden'));
  check('register lists the saved tender',
    !!window.document.querySelector('#register-list .tender-row') &&
    window.document.querySelector('#register-list .tender-title').textContent.includes('Amorino UK'));
  check('register row has a preview action',
    !!window.document.querySelector('#register-list [data-preview]'));
  check('register row has an edit action',
    !!window.document.querySelector('#register-list [data-edit]'));

  // --- edit a saved tender from the register (no re-upload) ---
  window.document.querySelector('#register-list [data-edit]').click();
  await new Promise((r) => setTimeout(r, 80));
  const st = window.__rye_debug.state;
  check('edit opens the wizard', !$('screen-wizard').classList.contains('hidden'));
  check('edit jumps straight to assemble (step 5)', !$('step-5').classList.contains('hidden'));
  check('edit skips upload/map/extract', $('step-2').classList.contains('hidden') &&
    $('step-3').classList.contains('hidden') && $('step-4').classList.contains('hidden'));
  check('tender id carried so a re-save bumps the version', st.meta.id === TENDER_ID);
  check('client + label prefilled', $('in-client').value === 'Amorino UK' &&
    $('in-label').value === 'Electricity tender — July 2026');
  check('one synthetic extract stands in for the original uploads',
    st.files.length === 1 && st.files[0].fromSaved === true && !!st.files[0].extract);
  check('stored sites + quotes came through the synthetic extract',
    st.files[0].extract.sites.length === 1 && st.files[0].extract.quotes.length === 2);
  check('commission model prefilled (not the flat fee)',
    $('in-charge-model').value === 'commission' &&
    Number($('in-commission-uplift').value) === 0.45 &&
    $('in-commission-included').checked === true);
  check('expiry prefilled as a date-input value', $('in-expires').value === '2026-09-30');
  check('notes prefilled', $('in-notes').value.includes('Offer valid until'));
  check('edit banner names the version and status',
    !$('edit-banner').classList.contains('hidden') &&
    $('edit-banner').textContent.includes('v3') &&
    $('edit-banner').textContent.includes('published'));
  check('published edit warns the live link stays on the published version',
    $('edit-banner').textContent.includes('does not change'));
  check('keep-baseline choice shown, naming the saved incumbent',
    !$('keep-incumbent-field').classList.contains('hidden') &&
    $('keep-incumbent-label').textContent.includes('British Gas') &&
    $('in-keep-incumbent').checked === true);
  // The stored featured offer is the DEARER one, so this fails if the default
  // "two cheapest" pre-tick were left in place.
  check('previously-featured offer restored, not the cheapest default',
    st.featured.size === 1 && st.featured.has(0));
  check('recommendation follows the restored tick',
    window.__rye_debug.assembleMeta().recommended_supplier === 'UrbanChain');

  $('btn-assemble').click();
  await new Promise((r) => setTimeout(r, 80));
  const editBody = sent['/api/assemble'];
  check('re-save sends keep_incumbent so the baseline is preserved server-side',
    !!editBody && editBody.get('keep_incumbent') === 'true');
  check('re-save sends the stored id so the version bumps',
    JSON.parse(editBody.get('meta')).id === TENDER_ID);
  check('re-save never sends incumbent rates from the browser',
    editBody.get('incumbent') === null && editBody.get('benchmark_unit_rate') === null);

  // The saved-tender row has no File behind it: stepping back to extract must not
  // offer to re-map it, and "Extract confirmed files" must not POST a null file.
  window.__rye_debug.openExtract();
  await new Promise((r) => setTimeout(r, 20));
  check('extract step lists the saved row as loaded, not re-extractable',
    ($('extract-list').textContent || '').includes('loaded from the saved tender'));
  window.__rye_debug.renderFiles();
  check('saved row offers no map/remove actions',
    !window.document.querySelector('#filelist [data-map]') &&
    !window.document.querySelector('#filelist [data-del]'));
  await window.__rye_debug.runExtractAll();
  await new Promise((r) => setTimeout(r, 20));
  check('re-extract is a no-op on a saved tender, not a crash',
    st.files.length === 1 && !!st.files[0].extract && failures.length === 0);

  // --- New tender clears it all down (was a no-op before) ---
  window.__rye_debug.newTender();
  await new Promise((r) => setTimeout(r, 20));
  check('New tender returns to step 1', !$('step-1').classList.contains('hidden'));
  check('New tender clears the tender id', st.meta.id === null);
  check('New tender clears the files', st.files.length === 0);
  check('New tender leaves edit mode', st.editing === null &&
    $('edit-banner').classList.contains('hidden'));
  check('New tender clears the form fields',
    $('in-client').value === '' && $('in-label').value === '' &&
    $('in-notes').value === '' && $('in-charge-model').value === 'fee');
  check('New tender hides the keep-baseline choice',
    $('keep-incumbent-field').classList.contains('hidden'));

  if (failures.length) { console.log(`\n${failures.length} CHECK(S) FAILED`); process.exit(1); }
  console.log('\nALL DOM SMOKE CHECKS PASSED');
})();
