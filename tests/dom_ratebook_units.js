// Behavioural check for the RATE-BOOK COLUMN UNITS, in jsdom.
// Reads the fixtures rendered by tests/test_ratebook_units.py and asserts the thing a
// client actually asked for: the non-commodity columns say what unit they are in.
// Also guards the reason it is read from the payload rather than hardcoded — an
// overridden charge_basis must move the header with it, or the page states a falsehood.
// Run from the repo root: node tests/dom_ratebook_units.js   (needs npm i jsdom)
const fs = require('fs'), path = require('path');
const W = path.join(__dirname, '_work');
const need = ['_units_default.html', '_units_override.html', '_units_legacy.html'];
if (need.some(f => !fs.existsSync(path.join(W, f)))) {
  console.error('missing fixtures — run: python3 tests/test_ratebook_units.py'); process.exit(2);
}
const { JSDOM, VirtualConsole } = require('jsdom');
let fails = 0;
const ck = (n, c, x) => { console.log((c ? '  PASS  ' : '  FAIL  ') + n + (x ? '  [' + x + ']' : '')); if (!c) fails++; };

function headers(file) {
  const errs = [];
  const vc = new VirtualConsole().on('jsdomError', e => errs.push(e.message));
  const dom = new JSDOM(fs.readFileSync(path.join(W, file), 'utf8').replace(/<link[^>]*fonts[^>]*>/g, ''),
    { runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc });
  const d = dom.window.document;
  // The rate books are the <details> blocks; take their header rows.
  // The unit is a block-level <span class="unit"> under the label, so textContent
  // runs them together ("Standingp/day"). Normalise to one space before asserting.
  const rows = [...d.querySelectorAll('details table thead tr')].map(r => [...r.children].map(c => {
    const u = c.querySelector('.unit');
    return u ? (c.textContent.replace(u.textContent, '').trim() + ' ' + u.textContent.trim()) : c.textContent.trim();
  }));
  const units = d.querySelectorAll('details table thead th .unit').length;
  return { errs, rows, units, all: rows.flat() };
}

console.log('-- default bases --');
const A = headers('_units_default.html');
ck('renders without JS errors', A.errs.length === 0, A.errs.join(' | '));
ck('at least one rate book rendered', A.rows.length > 0, A.rows.length);
ck('the unit is a SECOND LINE, not a longer header (keeps the table from scrolling)',
   A.units >= 4, A.units);
ck('Standing says p/day', A.all.includes('Standing p/day'), A.all.join(' | '));
ck('Capacity says p/kVA/day', A.all.includes('Capacity p/kVA/day'), A.all.join(' | '));
ck('Network says p/day', A.all.includes('Network p/day'));
ck('Meter says p/day', A.all.includes('Meter p/day'));
ck('NO non-commodity column is left bare', !['Standing', 'Capacity', 'Network', 'Meter'].some(l => A.all.includes(l)),
   A.all.join(' | '));
ck('energy columns are untouched', A.all.includes('Day p/kWh') && A.all.includes('Night p/kWh'));
ck('kVA is cased as an initialism, not p/kva/day', !A.all.some(h => /p\/kva\//.test(h)));

console.log('\n-- overridden bases: the header must follow the basis --');
const B = headers('_units_override.html');
ck('renders without JS errors', B.errs.length === 0, B.errs.join(' | '));
ck('Standing costed as gbp/month reads £/month', B.all.includes('Standing £/month'), B.all.join(' | '));
ck('Network costed as p/kwh reads p/kWh', B.all.includes('Network p/kWh'));
ck('it does NOT still claim p/day', !B.all.includes('Standing p/day') && !B.all.includes('Network p/day'));
ck('Capacity, not overridden, keeps p/kVA/day', B.all.includes('Capacity p/kVA/day'));

console.log('\n-- payload with no chargeBasis: degrade, never guess --');
const C = headers('_units_legacy.html');
ck('renders without JS errors', C.errs.length === 0, C.errs.join(' | '));
ck('falls back to a bare label', C.all.includes('Standing'), C.all.join(' | '));
ck('never prints "undefined" anywhere on the page', !C.all.some(h => /undefined/i.test(h)));
ck('never invents a unit it was not given', !C.all.includes('Standing p/day'));

console.log();
if (fails) { console.log(fails + ' CHECK(S) FAILED'); process.exit(1); }
console.log('ALL RATEBOOK-UNITS DOM CHECKS PASSED');
