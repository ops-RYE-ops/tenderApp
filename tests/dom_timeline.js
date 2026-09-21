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
ck('a notice explains why they differ',
  /different dates/i.test(pane.querySelector('.notice')?.textContent || ''));
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
ck('both charts label their own y-axis',
  pane.querySelectorAll('#tl-bars .axlabel').length > 0 &&
  pane.querySelectorAll('#tl-cum .axlabel').length > 0);

console.log('-- schedule --');
const rows = pane.querySelectorAll('tbody tr');
ck('one row per supply point', rows.length === 7, rows.length + ' rows');
ck('each row names a switch month',
  [...rows].every(r => /\w{3} \d{4}/.test(r.children[1].textContent)),
  [...rows].map(r => r.children[1].textContent.trim()).join(', '));
ck('rows are ordered by switch date', (() => {
  const seen = [...rows].map(r => r.children[1].textContent.trim());
  return JSON.stringify(seen) === JSON.stringify([...seen].sort(
    (a, b) => new Date('1 ' + a) - new Date('1 ' + b)));
})());

console.log('-- house rules --');
ck('the flat monthly split is disclosed', /spread evenly/i.test(pane.textContent));
ck('no minus inside a currency anywhere on the page', !/£-/.test(d.body.textContent));

console.log(fails ? `\n${fails} CHECK(S) FAILED` : '\nALL TIMELINE DOM CHECKS PASSED');
process.exit(fails ? 1 : 0);
