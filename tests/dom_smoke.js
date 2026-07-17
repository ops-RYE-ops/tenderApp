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
    quotes: [{ supplier: 'UrbanChain', term: '24 months', category: 'fixed',
      lines: [{ mpxn: '1200098765432', unitRate: 24.51, standingCharge: 48.0 }] }],
  },
  counts: { sites: 1, quotes: 1, lines: 1 },
  unmatched_mpxn: [],
  site_reference_used: false,
};
const ASSEMBLE_RESP = {
  ok: true, persisted: true, id: TENDER_ID, version: 1, status: 'draft',
  slug: 'amorino-uk', url_uuid: '22222222-2222-4222-8222-222222222222', dashboard_url: null,
  counts: { sites: 1, quotes: 1, incumbent_lines: 0 },
  incumbent_supplier: null, warnings: [], tender: {},
};

const routes = {
  '/api/auth-check': { ok: true, gated: false },
  '/api/suppliers': { suppliers: ['Octopus', 'UrbanChain'] },
  '/api/map': MAP_RESP,
  '/api/inspect': INSPECT_RESP,
  '/api/map/confirm': { ok: true, saved: true, supplier: 'UrbanChain' },
  '/api/extract': EXTRACT_RESP,
  '/api/assemble': ASSEMBLE_RESP,
};

const failures = [];
const check = (name, cond) => {
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name);
  if (!cond) failures.push(name);
};

(async () => {
  const dom = new JSDOM(html, { url: 'http://localhost/app/', runScripts: 'dangerously' });
  const { window } = dom;
  window.fetch = async (url) => {
    const p = new URL(url, 'http://localhost').pathname;
    if (!(p in routes)) throw new Error('unstubbed fetch: ' + p);
    return { ok: true, status: 200, json: async () => routes[p] };
  };
  window.onerror = (msg) => failures.push('uncaught: ' + msg);

  window.eval(fs.readFileSync(path.join(WEB, 'app.js'), 'utf8'));
  window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 50));

  const $ = (id) => window.document.getElementById(id);
  check('auto-unlock (no key configured) shows the wizard', !$('screen-wizard').classList.contains('hidden'));
  check('step 1 visible', !$('step-1').classList.contains('hidden'));
  check('supplier dropdown populated from /api/suppliers',
    [...$('in-supplier').options].some((o) => o.value === 'UrbanChain'));

  $('in-client').value = 'Amorino UK';
  $('in-label').value = 'Electricity tender — July 2026';
  $('in-supplier').value = 'UrbanChain';
  $('btn-to-upload').click();
  check('continue -> step 2', !$('step-2').classList.contains('hidden'));

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
  check('all 13 target fields rendered', rows.length === 13);
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
  check('success notice shown', $('map-msg').textContent.includes('Saved'));
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
  window.__rye_debug.openAssemble();
  await new Promise((r) => setTimeout(r, 20));
  check('assemble screen visible', !$('step-5').classList.contains('hidden'));
  check('recommended dropdown lists the extract combo',
    [...$('in-recommended').options].some((o) => o.textContent.includes('UrbanChain') && o.textContent.includes('24 months')));

  $('in-recommended').value = '0';
  $('in-weekend-split').value = '0.15';
  const meta = window.__rye_debug.assembleMeta();
  check('meta carries weekend_split from the form (backend passthrough)', meta.weekend_split === 0.15);
  check('meta carries recommended_supplier (never computed)', meta.recommended_supplier === 'UrbanChain');
  check('meta carries recommended_term', meta.recommended_term === '24 months');

  await window.__rye_debug.doAssemble();
  await new Promise((r) => setTimeout(r, 50));
  check('assemble result rendered', !$('assemble-result').classList.contains('hidden'));
  check('result shows the saved version', $('assemble-result').textContent.includes('v1'));
  check('tender id stored for re-save versioning', state.meta.id === TENDER_ID);

  if (failures.length) { console.log(`\n${failures.length} CHECK(S) FAILED`); process.exit(1); }
  console.log('\nALL DOM SMOKE CHECKS PASSED');
})();
