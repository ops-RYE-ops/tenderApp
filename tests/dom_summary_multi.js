// Behavioural check for the COMBINED (gas + electricity) Summary tab, in jsdom.
// Reads the fixture rendered by tests/test_summary_multi.py, then asserts the two
// bugs found on the first live combined tender (Public House Group, Sep 2026):
//   1. the Summary rendered NO offer bars at all (buildBars was never called on
//      the MULTI path) — they must be there, electricity visible, gas hidden
//      behind the include-gas tick;
//   2. a NEGATIVE saving rendered as a green "£-6,316" saving — an increase must
//      read as an increase, in amber, as an absolute value;
//   3. commission was netted whole-tender against an electricity-only gross, so
//      the two KPIs could not be reconciled — it must scope to what's on screen.
// Run from the repo root: node tests/dom_summary_multi.js   (needs npm i jsdom)
const fs = require('fs'), path = require('path');
const FIX = path.join(__dirname, '_work', '_summary_multi.html');
if (!fs.existsSync(FIX)) {
  console.error('missing fixture — run: python3 tests/test_summary_multi.py'); process.exit(2);
}
const { JSDOM } = require('jsdom');
const dom = new JSDOM(fs.readFileSync(FIX, 'utf8').replace(/<link[^>]*fonts[^>]*>/g, ''),
  { runScripts: 'dangerously', pretendToBeVisual: true });
const d = dom.window.document;
let fails = 0;
const ck = (n, c, x) => { console.log((c ? '  PASS  ' : '  FAIL  ') + n + (x ? '  [' + x + ']' : '')); if (!c) fails++; };
const txt = i => (d.getElementById(i) || {}).textContent?.trim() ?? null;
const cls = i => (d.getElementById(i) || {}).className ?? null;

// Fixture: electricity is DEARER than incumbent (-6,022), gas saves (+15,036),
// commission 0.6 p/kWh on 800 MWh elec / 1.4 GWh combined.
const sum = d.getElementById('tab-savings');
ck('Summary pane exists', !!sum);
ck('Summary renders offer bars, one block per fuel', sum.querySelectorAll('.bars').length === 2,
  sum.querySelectorAll('.bars').length + ' blocks');
const labels = [...sum.querySelectorAll('.section-label')].map(e => e.textContent.trim());
ck('bars are labelled per fuel',
  labels.some(l => /all offers tendered — electricity/i.test(l)) &&
  labels.some(l => /all offers tendered — gas/i.test(l)));
const gasBars = sum.querySelectorAll('.gas-bars');
ck('gas bars hidden until the tick', gasBars.length === 1 && gasBars[0].hidden);
ck('electricity block lists incumbent + both offers',
  sum.querySelectorAll('.bars')[0].querySelectorAll('.bar-row').length === 3);
ck('offer-filter ids are unique (Summary + Portfolio)',
  new Set([...d.querySelectorAll('.offer-filter')].map(x => x.id)).size ===
  d.querySelectorAll('.offer-filter').length);

console.log('-- electricity only (default) --');
ck('a cost increase reads as an increase', /annual increase/i.test(txt('m-gross-label')), txt('m-gross-label'));
ck('shown as an absolute value, never "£-"', txt('m-gross') === '£6,022', txt('m-gross'));
ck('toned amber, not green', /warn/.test(cls('m-gross')) && !/pos/.test(cls('m-gross')), cls('m-gross'));
ck('net after commission also reads increase', /Net increase/.test(txt('m-net-label')), txt('m-net-label'));
ck('commission scoped to electricity (0.6p x 800 MWh)',
  txt('m-fee-annual') === '£4,800' && txt('m-charge-scope') === 'electricity',
  txt('m-fee-annual') + ' / ' + txt('m-charge-scope'));
ck('net reconciles with the gross above it (6,022 + 4,800)', txt('m-net') === '£10,822', txt('m-net'));
ck('table delta is amber and signed as a cost increase',
  /warn/.test(cls('m-savecell')) && txt('m-savecell') === '+£6,022', cls('m-savecell') + ' ' + txt('m-savecell'));
ck('gas rate rows hidden', [...sum.querySelectorAll('.gas-row')].every(r => r.hidden));

console.log('-- include gas ticked --');
const cb = d.getElementById('inc-gas');
cb.checked = true; cb.dispatchEvent(new dom.window.Event('change'));
ck('gas bars revealed', !gasBars[0].hidden);
ck('gas rate rows revealed', [...sum.querySelectorAll('.gas-row')].every(r => !r.hidden));
ck('headline flips to a saving', /annual saving/i.test(txt('m-gross-label')) && txt('m-gross') === '£9,015',
  txt('m-gross-label') + ' ' + txt('m-gross'));
ck('toned green', /pos/.test(cls('m-gross')) && !/warn/.test(cls('m-gross')));
ck('commission rescoped to the whole tender (0.6p x 1.4 GWh)',
  txt('m-fee-annual') === '£8,400' && txt('m-charge-scope') === 'the tender',
  txt('m-fee-annual') + ' / ' + txt('m-charge-scope'));
ck('net saving matches the engine (9,015 - 8,400)', txt('m-net') === '£615', txt('m-net'));
ck('table delta is green', /best/.test(cls('m-savecell')) && txt('m-savecell') === '−£9,015',
  cls('m-savecell') + ' ' + txt('m-savecell'));

console.log(fails ? '\n' + fails + ' SUMMARY-MULTI CHECK(S) FAILED' : '\nALL SUMMARY-MULTI CHECKS PASSED');
process.exit(fails ? 1 : 0);
