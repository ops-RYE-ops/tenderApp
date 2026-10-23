// Recommendation card reads "fixed" or "rolling" from the offer's own supply end
// dates, never a hardcoded "fixed". Regression from Bondi Bowls (23 Oct 2026): a
// Capture rolling contract (supplyEndDate "Rolling", no term) showed "fixed" and
// a dangling " · " after the supplier name. Covers the single-fuel card
// (buildRecVsInc) and the combined gas + electricity card (buildSummaryMulti).
// Run from the repo root: node tests/dom_contract_basis.js   (needs npm i jsdom + python3;
// the combined fixture comes from python3 tests/test_summary_multi.py)
const { execFileSync } = require('child_process');
const { JSDOM } = require('jsdom');
const base = execFileSync('python3', ['-c',
  'import sys,json;sys.path.insert(0,"pipeline");import build_dashboard as bd;' +
  'print(bd.render_tender(json.load(open("schema/examples/tender.example.json"))))'],
  { encoding: 'utf8', maxBuffer: 64 << 20 }).replace(/<link[^>]*fonts[^>]*>/g, '');
const m = base.match(/const RAW = (\{.*?\});\n/s);
if (!m) { console.error('could not find RAW payload'); process.exit(2); }
let fails = 0;
const ck = (n, c, x) => { console.log((c ? '  PASS  ' : '  FAIL  ') + n + (x ? '  [' + x + ']' : '')); if (!c) fails++; };
function render(mutate) {
  const raw = JSON.parse(m[1]);
  mutate(raw);
  const html = base.replace(m[0], 'const RAW = ' + JSON.stringify(raw) + ';\n');
  const d = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true }).window.document;
  const card = d.querySelector('.card.accent-green .kpi-sub');
  const label = [...d.querySelectorAll('.section-label')].find(e => /Our recommendation/.test(e.textContent));
  return { sub: card ? card.textContent.trim() : null, label: label ? label.textContent.trim() : '' };
}
const each = (raw, fn) => raw.offers.forEach(o => { if (!o.isIncumbent) fn(o); });

let r = render(() => {});
ck('dated end -> "12 months fixed"', r.sub && r.sub.startsWith('12 months fixed ·'), r.sub);

r = render(raw => each(raw, o => { o.term = ''; o.sites.forEach(s => { s.endDate = 'Rolling'; s.endEstimated = false; }); }));
ck('end "Rolling" -> "rolling", never "fixed"', r.sub && r.sub.startsWith('rolling ·') && !/fixed/i.test(r.sub), r.sub);
ck('no dangling " · " after supplier when term is blank', r.label && !/·\s*$/.test(r.label), r.label);

r = render(raw => each(raw, o => { o.term = 'Rolling'; o.sites.forEach(s => { s.endDate = 'Rolling'; }); }));
ck('term "Rolling" is not doubled', r.sub && r.sub.startsWith('Rolling ·') && !/rolling rolling/i.test(r.sub), r.sub);

r = render(raw => each(raw, o => { o.sites[0].endDate = 'Rolling'; }));
ck('mixed ends say "fixed and rolling"', r.sub && r.sub.includes('fixed and rolling'), r.sub);

r = render(raw => each(raw, o => { o.term = ''; o.sites.forEach(s => { s.endDate = ''; }); }));
ck('no end date and no term -> no basis guessed', r.sub && r.sub.startsWith('covers all'), r.sub);

// ---- Combined (multi-fuel) path: buildSummaryMulti ----
const fs = require('fs'), path = require('path');
const MFIX = path.join(__dirname, '_work', '_summary_multi.html');
if (!fs.existsSync(MFIX)) { console.error('missing fixture — run: python3 tests/test_summary_multi.py'); process.exit(2); }
const mbase = fs.readFileSync(MFIX, 'utf8').replace(/<link[^>]*fonts[^>]*>/g, '');
const mm = mbase.match(/const RAW = (\{.*?\});\n/s);
if (!mm) { console.error('could not find RAW payload in combined fixture'); process.exit(2); }
function renderMultiFix(mutate) {
  const raw = JSON.parse(mm[1]);
  ck('combined fixture is multi-fuel', raw.multiFuel === true);
  raw.fuels.filter(f => f.fuel === 'electricity').forEach(f => f.offers.forEach(o => mutate(o)));
  const d = new JSDOM(mbase.replace(mm[0], 'const RAW = ' + JSON.stringify(raw) + ';\n'),
    { runScripts: 'dangerously', pretendToBeVisual: true }).window.document;
  const sub = [...d.querySelectorAll('.kpi-sub')].find(e => /electricity anchor/.test(e.textContent));
  const label = [...d.querySelectorAll('.section-label')].find(e => /Our recommendation/.test(e.textContent));
  return { sub: sub ? sub.textContent.trim() : null, label: label ? label.textContent.trim() : '' };
}
r = renderMultiFix(o => { o.term = ''; o.sites.forEach(s => { s.endDate = 'Rolling'; s.endEstimated = false; }); });
ck('combined: end "Rolling" -> "rolling · electricity anchor"', r.sub && r.sub.startsWith('rolling · electricity anchor'), r.sub);
ck('combined: no dangling " · " after supplier when term is blank', r.label && !/·\s*$/.test(r.label), r.label);
r = renderMultiFix(o => { o.sites.forEach(s => { s.endDate = '2028-09-30'; }); });
ck('combined: coterminous term not suffixed "fixed"', r.sub && r.sub.startsWith('Co-terminous 2029-11-21 · electricity anchor'), r.sub);
r = renderMultiFix(o => { o.term = '36 months'; o.sites.forEach(s => { s.endDate = '2028-09-30'; }); });
ck('combined: dated end -> "36 months fixed"', r.sub && r.sub.startsWith('36 months fixed · electricity anchor'), r.sub);
r = renderMultiFix(o => { o.term = ''; o.sites.forEach(s => { s.endDate = ''; }); });
ck('combined: no end date and no term -> no basis guessed', r.sub && r.sub.startsWith('electricity anchor'), r.sub);

console.log(fails ? `${fails} FAILED` : 'ALL CONTRACT BASIS CHECKS PASSED');
process.exit(fails ? 1 : 0);
