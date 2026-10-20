// Behavioural half of tests/test_summary_packages.py: the combined Summary pairs each
// electricity offer with the gas from its OWN package (same packageId), and falls
// back to the recommended gas when it has none. Supplier A and Supplier B both quote
// both fuels on "12 months"; D quotes electricity only; B is recommended.
// Run from the repo root: node tests/dom_summary_packages.js   (needs npm i jsdom)
const fs = require('fs'), path = require('path');
const FIX = path.join(__dirname, '_work', '_summary_packages.html');
if (!fs.existsSync(FIX)) { console.error('missing fixture — run: python3 tests/test_summary_packages.py'); process.exit(2); }
const { JSDOM } = require('jsdom');
const html = fs.readFileSync(FIX, 'utf8').replace(/<link[^>]*fonts[^>]*>/g, '');
const raw = JSON.parse(html.match(/const RAW = (\{.*?\});\n/s)[1]);
const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true });
const d = dom.window.document;
let fails = 0;
const ck = (n, c, x) => { console.log((c ? '  PASS  ' : '  FAIL  ') + n + (x ? '  [' + x + ']' : '')); if (!c) fails++; };
const gbp = v => '£' + Math.round(v).toLocaleString('en-GB');
const fuel = f => raw.fuels.find(x => x.fuel === f);
const E = s => fuel('electricity').offers.find(o => o.supplier === s);
const G = s => fuel('gas').offers.find(o => o.supplier === s);
const cb = d.getElementById('inc-gas');
cb.checked = true; cb.dispatchEvent(new dom.window.Event('change'));
const sum = d.getElementById('tab-savings');
const bar = s => { const r = [...sum.querySelectorAll('.bar-row')].find(r => r.querySelector('.bar-name').textContent.startsWith(s));
  return r ? r.querySelector('.bar-val').childNodes[0].textContent.trim() : null; };

ck('B (recommended) bar = B elec + B gas', bar('Supplier B') === gbp(E('Supplier B').total + G('Supplier B').total), bar('Supplier B'));
ck('A bar = A elec + A gas (not B gas, despite the same term)',
   bar('Supplier A') === gbp(E('Supplier A').total + G('Supplier A').total), bar('Supplier A'));
ck('D bar (no gas in its package) falls back to recommended gas (B)',
   bar('Supplier D') === gbp(E('Supplier D').total + G('Supplier B').total), bar('Supplier D'));
const want = E('Supplier B').total + G('Supplier B').total;
ck('headline spend is B elec + B gas', sum.textContent.includes(gbp(want)), gbp(want));
ck('headline is not A gas under B electricity',
   !sum.textContent.includes(gbp(E('Supplier B').total + G('Supplier A').total)));
console.log(fails ? `${fails} FAILED` : 'ALL SUMMARY-PACKAGE DOM CHECKS PASSED');
process.exit(fails ? 1 : 0);
