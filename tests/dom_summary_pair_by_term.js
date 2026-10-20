// Combined Summary pairs each electricity offer with the gas offer quoted on the SAME
// term label (one upload sheet = one package), falling back to the recommended gas.
// Regression from Public House Group (Oct 2026): "rolling elec + 12m gas" and
// "24m fixed" were tendered as packages, but every Summary bar carried the
// recommended 24m gas, so the rolling bar matched no package the client was offered.
// Reuses the combined fixture from tests/test_summary_multi.py and rewrites terms.
// Run from the repo root: node tests/dom_summary_pair_by_term.js   (needs npm i jsdom)
const fs = require('fs'), path = require('path');
const FIX = path.join(__dirname, '_work', '_summary_multi.html');
if (!fs.existsSync(FIX)) { console.error('missing fixture — run: python3 tests/test_summary_multi.py'); process.exit(2); }
const { JSDOM } = require('jsdom');
const base = fs.readFileSync(FIX, 'utf8').replace(/<link[^>]*fonts[^>]*>/g, '');
const m = base.match(/const RAW = (\{.*?\});\n/s);
if (!m) { console.error('could not find RAW payload in fixture'); process.exit(2); }

let fails = 0;
const ck = (n, c, x) => { console.log((c ? '  PASS  ' : '  FAIL  ') + n + (x ? '  [' + x + ']' : '')); if (!c) fails++; };
const gbp = v => '£' + Math.round(v).toLocaleString('en-GB');

function render(mutate) {
  const raw = JSON.parse(m[1]);
  mutate(raw);
  const dom = new JSDOM(base.replace(m[0], 'const RAW = ' + JSON.stringify(raw) + ';\n'),
    { runScripts: 'dangerously', pretendToBeVisual: true });
  const d = dom.window.document;
  const cb = d.getElementById('inc-gas');
  if (cb) { cb.checked = true; cb.dispatchEvent(new dom.window.Event('change')); }
  const sum = d.getElementById('tab-savings');
  const bars = {};
  sum.querySelectorAll('.bar-row').forEach(r => {
    const term = (r.querySelector('.term') || {}).textContent || 'incumbent';
    bars[term.trim()] = r.querySelector('.bar-val').childNodes[0].textContent.trim();
  });
  return { raw, bars, d };
}

const E = r => r.fuels.find(f => f.fuel === 'electricity');
const G = r => r.fuels.find(f => f.fuel === 'gas');

console.log('-- packages: each electricity term has a same-term gas offer --');
{
  const { raw, bars } = render(r => {
    const e = E(r), g = G(r);
    e.offers[0].term = '24 months';                       // recommended electricity
    e.offers[1].term = 'Rolling elec + 12m gas';
    const g12 = JSON.parse(JSON.stringify(g.offers[0]));
    g12.id = 'tem-rolling'; g12.term = 'Rolling elec + 12m gas'; g12.total = g.offers[0].total + 5000;
    g.offers.push(g12);                                    // recommended gas stays the 24m one
  });
  const e = E(raw), g = G(raw);
  const g24 = g.offers[0].total, g12 = g.offers[1].total;
  ck('24m bar = 24m elec + 24m gas', bars['24 months'] === gbp(e.offers[0].total + g24), bars['24 months']);
  ck('rolling bar = rolling elec + ITS OWN 12m gas, not the recommended gas',
     bars['Rolling elec + 12m gas'] === gbp(e.offers[1].total + g12), bars['Rolling elec + 12m gas']);
  const incRow = Object.entries(bars).find(([k]) => !['24 months', 'Rolling elec + 12m gas'].includes(k));
  ck('incumbent bar still carries the current gas',
     incRow && incRow[1] === gbp(e.incumbent.total + g.incumbent.total), incRow && incRow.join('='));
}

console.log('-- no shared terms: unchanged behaviour (recommended gas on every bar) --');
{
  const { raw, bars } = render(() => {});
  const e = E(raw), g = G(raw), gr = g.offers.find(o => o.id === (g.recommendedId || g.bestId)).total;
  ck('each electricity bar carries the recommended gas',
     e.offers.every(o => bars[o.term] === gbp(o.total + gr)),
     e.offers.map(o => o.term + '=' + bars[o.term]).join(' | '));
}

console.log('-- headline uses the recommended electricity offer\'s package gas --');
{
  const { raw, d } = render(r => {
    const e = E(r), g = G(r);
    e.offers[0].term = 'Package A';                       // recommended electricity
    const ga = JSON.parse(JSON.stringify(g.offers[0]));
    ga.id = 'tem-a'; ga.term = 'Package A'; ga.total = g.offers[0].total + 1234;
    g.offers.push(ga);                                     // NOT the recommended gas
  });
  const e = E(raw), g = G(raw);
  const want = e.offers[0].total + g.offers[1].total;
  const txt = d.getElementById('tab-savings').textContent;
  ck('combined recommended spend includes the package gas', txt.includes(gbp(want)), gbp(want));
}

console.log(fails ? `${fails} FAILED` : 'ALL SUMMARY PAIR-BY-TERM CHECKS PASSED');
process.exit(fails ? 1 : 0);
