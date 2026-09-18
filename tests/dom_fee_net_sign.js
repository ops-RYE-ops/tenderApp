// Behavioural check for the single-fuel fee card's net line, in jsdom.
// Reads the fixture rendered by tests/test_fee_net_sign.py, then asserts the bug
// found on a live client dashboard (Public House Group, 17 Sep 2026): a fee that
// turns a small gross result into a net increase rendered as
// "NET SAVING AFTER RYE FEE  £-18,202" — the word "saving" over an increase, and
// the minus sign inside the currency. Also drags the live fee control, because
// that path re-wrote the value and would otherwise leave a stale label behind.
// Run from the repo root: node tests/dom_fee_net_sign.js   (needs npm i jsdom)
const fs = require('fs'), path = require('path');
const FIX = path.join(__dirname, '_work', '_fee_net_sign.html');
if (!fs.existsSync(FIX)) {
  console.error('missing fixture — run: python3 tests/test_fee_net_sign.py'); process.exit(2);
}
const { JSDOM } = require('jsdom');
const errs = [];
const vc = new (require('jsdom').VirtualConsole)().on('jsdomError', e => errs.push(e.message));
const dom = new JSDOM(fs.readFileSync(FIX, 'utf8').replace(/<link[^>]*fonts[^>]*>/g, ''),
  { runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc });
const d = dom.window.document;
let fails = 0;
const ck = (n, c, x) => { console.log((c ? '  PASS  ' : '  FAIL  ') + n + (x ? '  [' + x + ']' : '')); if (!c) fails++; };
const txt = i => (d.getElementById(i) || {}).textContent?.trim() ?? null;
const cls = i => (d.getElementById(i) || {}).className ?? '';

ck('renders without JS errors', errs.length === 0, errs.join(' | '));

console.log('-- as rendered (offer dearer than incumbent, £90/supply point fee) --');
const card = d.querySelector('.card.accent-green');
ck('gross line reads as an increase',
  /annual increase/i.test(card.textContent), (card.textContent.match(/annual \w+/i) || [])[0]);
ck('fee net line says increase, not saving', /net increase after/i.test(txt('net-saving-label')),
  txt('net-saving-label'));
ck('fee net value is amber', /warn/.test(cls('net-saving')), cls('net-saving'));
ck('fee net value carries no minus inside the currency', !/£-/.test(txt('net-saving')), txt('net-saving'));
ck('per-supply figure carries no minus inside the currency', !/£-/.test(txt('net-per-site')), txt('net-per-site'));
ck('nowhere on the page renders "£-"', !/£-/.test(d.body.textContent));

console.log('-- fee dragged down to £10/supply point (still an increase) --');
const input = d.getElementById('fee-input');
input.value = '10';
input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
ck('label still derived after the slider moves', /net increase after/i.test(txt('net-saving-label')),
  txt('net-saving-label'));
ck('value still absolute after the slider moves', !/£-/.test(txt('net-saving')), txt('net-saving'));
ck('per-supply still absolute after the slider moves', !/£-/.test(txt('net-per-site')), txt('net-per-site'));

console.log(fails ? `\n${fails} CHECK(S) FAILED` : '\nALL FEE-NET-SIGN DOM CHECKS PASSED');
process.exit(fails ? 1 : 0);
