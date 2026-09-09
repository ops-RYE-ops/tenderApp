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
ck('Summary renders ONE set of offer bars', sum.querySelectorAll('.bars').length === 1,
  sum.querySelectorAll('.bars').length + ' blocks');
ck('labelled electricity while gas is off',
  /all offers tendered — electricity ·/i.test([...sum.querySelectorAll('.section-label')]
    .map(e => e.textContent.trim()).find(t => /All offers/.test(t)) || ''));
ck('bars list incumbent + both electricity offers',
  sum.querySelectorAll('.bar-row').length === 3);
ck('no gas segment before the tick', sum.querySelectorAll('.bar-gas').length === 0);
const elecWidths = [...sum.querySelectorAll('.bar-row')].map(r =>
  parseFloat(r.querySelector('.bar-fill').style.width));
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
ck('still ONE set of bars', sum.querySelectorAll('.bars').length === 1);
ck('every bar gains a gas segment', sum.querySelectorAll('.bar-gas').length === 3,
  sum.querySelectorAll('.bar-gas').length);
ck('title now says electricity + gas',
  /electricity \+ gas/i.test([...sum.querySelectorAll('.section-label')]
    .map(e => e.textContent.trim()).find(t => /All offers/.test(t)) || ''));
const rowsNow = [...sum.querySelectorAll('.bar-row')];
ck('bar values are the COMBINED totals',
  rowsNow.map(r => r.querySelector('.bar-val').textContent.trim().split(' ')[0])
    .includes('£227,184'),
  rowsNow.map(r => r.querySelector('.bar-val').textContent.trim().split(' ')[0]).join(' '));
ck('segments abut (gas starts where electricity ends)',
  rowsNow.every(r => {
    const e = r.querySelector('.bar-fill'), g = r.querySelector('.bar-gas');
    return Math.abs(parseFloat(g.style.left) - parseFloat(e.style.width)) < 0.15;
  }));
ck('longest bar still fills the track (rescaled, not overflowing)',
  Math.max(...rowsNow.map(r => parseFloat(r.querySelector('.bar-fill').style.width)
    + parseFloat(r.querySelector('.bar-gas').style.width))) > 99.5);
ck('a bar is LONGER than it was on electricity alone',
  Math.max(...rowsNow.map(r => parseFloat(r.querySelector('.bar-fill').style.width)
    + parseFloat(r.querySelector('.bar-gas').style.width))) > Math.max(...elecWidths) - 0.01);
ck('legend names both fuels', /electricity/.test(sum.querySelector('.bar-legend').textContent)
  && /gas/.test(sum.querySelector('.bar-legend').textContent));
ck('gas segment animates AFTER its electricity segment, not alongside it',
  rowsNow.every(r => parseFloat(r.querySelector('.bar-gas').style.animationDelay)
    > parseFloat(r.querySelector('.bar-fill').style.animationDelay)),
  rowsNow.map(r => r.querySelector('.bar-fill').style.animationDelay + '->'
    + r.querySelector('.bar-gas').style.animationDelay).join(' '));
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

// Rate-row tone must be DERIVED from the two numbers in the row, never hardcoded —
// the gas row shipped with a literal "best", so a dearer gas rate rendered green.
console.log('-- rate row tones follow the numbers --');
[...sum.querySelectorAll('tbody tr')].forEach(tr => {
  const cells = [...tr.querySelectorAll('td')];
  if (cells.length !== 3 || !/Mean effective rate/.test(cells[0].textContent)) return;
  const name = cells[0].textContent.trim();
  const inc = parseFloat(cells[1].textContent), rec = parseFloat(cells[2].textContent);
  if (!isFinite(inc) || !isFinite(rec)) return;
  const want = rec <= inc ? 'best' : 'warn';
  ck(`${name}: ${rec} vs ${inc} -> ${want}`, cells[2].className.includes(want),
    cells[2].className || '(none)');
});

console.log(fails ? '\n' + fails + ' SUMMARY-MULTI CHECK(S) FAILED' : '\nALL SUMMARY-MULTI CHECKS PASSED');
process.exit(fails ? 1 : 0);
