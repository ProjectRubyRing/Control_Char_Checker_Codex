// Exercise the generated report's DOM behavior without browser dependencies.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.listeners = {};
    this.textContent = ''; this.hidden = false; this.open = false; this.value = '';
  }
  append(...children) { this.children.push(...children); }
  addEventListener(event, listener) { this.listeners[event] = listener; }
  dispatch(event) { this.listeners[event](); }
}

const html = fs.readFileSync(process.argv[2], 'utf8');
const payload = html.split('<script id="report-data" type="application/json">')[1].split('</script>')[0];
const script = html.split('  <script>\n')[1].split('</script>')[0];
const report = JSON.parse(payload);
const nodes = {};
for (const name of ['report-data', 'metadata', 'completion', 'cards', 'search', 'status', 'files', 'visible-count']) {
  nodes[name] = new Element('div');
}
nodes['report-data'].textContent = payload;
nodes.status.value = 'all';
const document = { getElementById: id => nodes[id], createElement: tag => new Element(tag) };
vm.runInNewContext(script, { document }, { timeout: 5000 });
assert.equal(nodes.files.children.length, report.files.length * 2);
assert.equal(nodes.cards.children.length, 6);
assert.equal(nodes['visible-count'].textContent, report.files.length + ' / ' + report.files.length + '項目');

const index = report.files.findIndex(file => file.analyses.some(analysis => analysis.findings.length));
assert.ok(index >= 0, 'Fixture must contain an invisible character.');
const row = nodes.files.children[index * 2], detailRow = nodes.files.children[index * 2 + 1];
const details = row.children[0].children[0];
assert.equal(detailRow.hidden, true);
details.open = true; details.dispatch('toggle');
assert.equal(detailRow.hidden, false);
assert.equal(detailRow.children[0].colSpan, 6);
assert.equal(detailRow.children[0].children.length, 1);
const collectText = node => node.textContent + node.children.map(collectText).join(' ');
for (const analysis of report.files[index].analyses) {
  for (const finding of analysis.findings) {
    assert.ok(collectText(detailRow).includes(finding.codepoint));
    assert.ok(collectText(detailRow).includes(finding.positions[finding.positions.length - 1].join(':')));
  }
}
details.open = false; details.dispatch('toggle');
details.open = true; details.dispatch('toggle');
assert.equal(detailRow.children[0].children.length, 1, 'Reopening must not duplicate details.');

nodes.search.value = 'no-such-file'; nodes.search.dispatch('input');
assert.equal(row.hidden, true); assert.equal(detailRow.hidden, true);
nodes.search.value = report.files[index].path; nodes.search.dispatch('input');
assert.equal(row.hidden, false); assert.equal(detailRow.hidden, false);
nodes.status.value = 'ok'; nodes.status.dispatch('change');
assert.equal(row.hidden, true); assert.equal(detailRow.hidden, true);
nodes.status.value = 'warning'; nodes.status.dispatch('change');
assert.equal(row.hidden, false); assert.equal(detailRow.hidden, false);
console.log('Report UI rendering, positions, lazy details, and filters passed.');
