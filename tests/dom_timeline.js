// Behavioural check for the SAVINGS TIMELINE tab, in jsdom.
// Reads the fixture rendered by tests/test_timeline.py (7 supplies switching on four
// different dates across a 24-month term) and asserts the thing the tab exists to
// show: the headline annual saving is a RUN RATE, and less than that lands in the
// first twelve months because supplies join through the year.
// Also guards the chart contract — TWO charts with their own axes, never one with
// two y-scales — and that nothing renders a minus inside a currency.
// Run from the repo root: node tests/dom_timeline.js   (needs npm i jsdom)
const fs = require('fs'), path = require('path');
const FIX = path.join(__dirname, '_work', '_timeline.html');
if (!fs.existsSync(FIX)) {
  console.error('missing fixture — run: python3 tests/test_timeline.py'); process.exit(2);
}
const { JSDOM, VirtualConsole } = require('jsdom');
const errs = [];
const vc = new VirtualConsole().on('jsdomError', e => errs.push(e.message));
const dom = new JSDOM(fs.readFileSync(FIX, 'utf8').replace(/<link[^>]*fonts[^>]*>/g, ''),
  { runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc });
const d = dom.window.document;
let fails = 0;
const ck = (n, c, x) => { console.log((c ? '  PASS  ' : '  FAIL  ') + n + (x ? '  [' + x + ']' : '')); if (!c) fails++; };
const num = s => Number(String(s).replace(/[^0-9.-]/g, ''));

ck('renders without JS errors', errs.length === 0, errs.join(' | '));

const tabs = [...d.querySelectorAll('.tab')].map(t => t.textContent.trim());
ck('Savings timeline is offered as a tab', tabs.includes('Savings timeline'), tabs.join(' | '));
ck('it sits after Portfolio, before Market Review',
  tabs.indexOf('Savings timeline') === tabs.indexOf('Portfolio') + 1, tabs.join(' | '));

const pane = d.getElementById('tab-timeline');
ck('pane exists', !!pane);
ck('pane starts hidden (Summary is the default tab)', pane.hasAttribute('hidden'));

console.log('-- the point of the tab --');
const labels = [...pane.querySelectorAll('.kpi-label')].map(l => l.textContent.trim());
const values = [...pane.querySelectorAll('.kpi-value')].map(v => num(v.textContent));
ck('three figures: run rate, first year, term total', labels.length === 3, labels.join(' | '));
ck('run rate is labelled as a run rate', /run rate/i.test(labels[0]), labels[0]);
ck('FIRST YEAR IS LOWER THAN THE RUN RATE', values[1] < values[0], `${values[1]} vs ${values[0]}`);
ck('term total exceeds one year of it', values[2] > values[0], `${values[2]} vs ${values[0]}`);
ck('an intro line explains why they differ',
  /end on different dates/i.test(pane.querySelector('.subtitle')?.textContent || ''),
  (pane.querySelector('.subtitle')?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 90));
ck('it is plain copy, not an amber note block',
  !/different dates/i.test([...pane.querySelectorAll('.notice')].map(n => n.textContent).join(' ')));
ck('all three read as savings, in green', [...pane.querySelectorAll('.kpi-value')]
  .every(v => /pos/.test(v.className)));

console.log('-- charts --');
const bars = pane.querySelectorAll('#tl-bars rect');
ck('one bar per month of the term', bars.length === 24, bars.length + ' bars');
ck('bars carry hover detail', [...bars].every(b => b.querySelector('title')));
ck('bar heights only ever grow (supplies join, none leave)', (() => {
  const h = [...bars].map(b => parseFloat(b.getAttribute('height')));
  return h.every((v, i) => i === 0 || v >= h[i - 1] - 0.01);
})());
ck('cumulative is a SEPARATE chart with its own axis', !!d.getElementById('tl-cum'));
ck('cumulative draws an area and a line', pane.querySelectorAll('#tl-cum path').length === 2);
// A cumulative total cannot go down. Assert it on the drawn GEOMETRY, not just the
// data — a chart that dips is the fastest way to lose a client's trust in the page.
ck('the cumulative line never descends', (() => {
  const line = [...pane.querySelectorAll('#tl-cum path')][1];
  const pts = (line.getAttribute('d').match(/[\d.]+ [\d.]+/g) || []).map(p => p.split(' ').map(Number));
  return pts.length > 1 && pts.every((p, i) => i === 0 || p[1] <= pts[i - 1][1] + 0.01);
})());
ck('charts scale uniformly (no stretched text)', (() => {
  const a = d.getElementById('tl-bars'), b = d.getElementById('tl-cum');
  return !a.hasAttribute('preserveAspectRatio') && !b.hasAttribute('preserveAspectRatio')
      && a.classList.contains('mchart') && b.classList.contains('mchart');
})());
ck('both charts label their own y-axis',
  pane.querySelectorAll('#tl-bars .axlabel').length > 0 &&
  pane.querySelectorAll('#tl-cum .axlabel').length > 0);

console.log('-- axis --');
// Ticks must land on round numbers. Scaling straight off the data max gives
// "1,881 / 1,410 / 940", which reads like a rounding error rather than a scale.
['tl-bars', 'tl-cum'].forEach(id => {
  const ticks = [...pane.querySelectorAll('#' + id + ' .axlabel')]
    .map(t => t.textContent).filter(t => /^[\d,]+$/.test(t)).map(t => Number(t.replace(/,/g, '')));
  const step = ticks.length > 1 ? ticks[1] - ticks[0] : 0;
  const round = step > 0 && ticks.every((v, i) => Math.abs(v - i * step) < 0.01)
    && /^[125]0*$|^25 *0*$/.test(String(step).replace(/0+$/, m => m));
  ck(id + ' y-axis steps on a round number', step > 0 && ticks.every((v, i) => Math.abs(v - i * step) < 0.01)
     && [1, 2, 2.5, 5].some(b => Math.abs(Math.log10(step / b) % 1) < 1e-9), 'step ' + step.toLocaleString());
});

console.log('-- schedule --');
const rows = pane.querySelectorAll('tbody tr');
ck('one row per supply point plus a total', rows.length === 8, rows.length + ' rows');
ck('the table totals, so the column reconciles with the cards',
  !!pane.querySelector('.total-row'));
ck('each supply row names a switch month',
  [...rows].slice(0, -1).every(r => /\w{3} \d{4}/.test(r.children[1].textContent)),
  [...rows].slice(0, -1).map(r => r.children[1].textContent.trim()).join(', '));
ck('rows are ordered by switch date', (() => {
  const seen = [...rows].slice(0, -1).map(r => r.children[1].textContent.trim());
  return JSON.stringify(seen) === JSON.stringify([...seen].sort(
    (a, b) => new Date('1 ' + a) - new Date('1 ' + b)));
})());

console.log('-- house rules --');
ck('the flat monthly split is disclosed', /spread evenly/i.test(pane.textContent));
ck('no minus inside a currency anywhere on the page', !/£-/.test(d.body.textContent));

console.log(fails ? `\n${fails} CHECK(S) FAILED` : '\nALL TIMELINE DOM CHECKS PASSED');
process.exit(fails ? 1 : 0);
