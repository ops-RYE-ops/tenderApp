// Offer display names come from the rows' own suppliers, not the builder's
// upload-level supplier. Regression from Public House Group (Oct 2026): one upload
// carried Capture electricity + Yu gas, the builder supplier was "Multiple
// suppliers" (or "Capture Energy"), and every Portfolio label showed that instead
// of the real supplier. Reuses the combined fixture from tests/test_summary_multi.py
// and rewrites its supplier fields before the page script runs.
// Run from the repo root: node tests/dom_offer_supplier_label.js   (needs npm i jsdom)
const fs = require('fs'), path = require('path');
const FIX = path.join(__dirname, '_work', '_summary_multi.html');
if (!fs.existsSync(FIX)) { console.error('missing fixture — run: python3 tests/test_summary_multi.py'); process.exit(2); }
const { JSDOM } = require('jsdom');
let html = fs.readFileSync(FIX, 'utf8').replace(/<link[^>]*fonts[^>]*>/g, '');
const m = html.match(/const RAW = (\{.*?\});\n/s);
if (!m) { console.error('could not find RAW payload in fixture'); process.exit(2); }
const raw = JSON.parse(m[1]);
const REAL = { electricity: 'Capture Energy', gas: 'Yu Energy' };
let incName = null;
for (const f of raw.fuels) {
  if (f.incumbent) incName = f.incumbent.supplier;
  for (const o of f.offers) {
    o.supplier = 'Multiple suppliers';                       // builder-level label
    o.sites.forEach(s => { s.supplier = REAL[f.fuel] || 'Capture Energy'; });
  }
}
html = html.replace(m[0], 'const RAW = ' + JSON.stringify(raw) + ';\n');
const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true });
const d = dom.window.document;
let fails = 0;
const ck = (n, c, x) => { console.log((c ? '  PASS  ' : '  FAIL  ') + n + (x ? '  [' + x + ']' : '')); if (!c) fails++; };
const port = d.getElementById('tab-portfolio') || d.body;
const names = [...d.querySelectorAll('.bar-name')].map(e => e.childNodes[0].textContent.trim());
ck('builder label never shown on a bar', !names.includes('Multiple suppliers'), names.join(' | '));
ck('electricity offers read Capture Energy', names.filter(n => n === 'Capture Energy').length >= 1, names.join(' | '));
const visible = () => { d.querySelectorAll('script').forEach(x => x.remove()); return d.body.textContent; };
const gasBtn = [...d.querySelectorAll('button')].find(b => b.textContent.trim() === 'Gas');
ck('Portfolio has a Gas switch', !!gasBtn);
if (gasBtn) gasBtn.click();
const gasNames = [...d.querySelectorAll('.bar-name')].map(e => e.childNodes[0].textContent.trim());
ck('gas offers read Yu Energy after switching fuel', gasNames.includes('Yu Energy'), gasNames.join(' | '));
ck('no "Multiple suppliers" anywhere in the rendered page', !visible().includes('Multiple suppliers'));
if (incName) ck('incumbent keeps its assembled label', names.includes(incName), incName);
console.log(fails ? `${fails} FAILED` : 'ALL OFFER SUPPLIER LABEL CHECKS PASSED');
process.exit(fails ? 1 : 0);
