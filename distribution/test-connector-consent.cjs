const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function runtime() {
  const source = fs.readFileSync(path.join(__dirname, 'connector-research/connector-runtime.js'), 'utf8');
  const context = vm.createContext({document: {createElement: () => ({dataset: {}}), head: {appendChild() {}}}});
  return vm.runInContext(source + '\n({view: row => $connectorView(row, $connectorProfiles), publish: $publishConnectorProfiles})', context);
}

test('detected profiles are off before any saved consent has loaded', () => {
  const api = runtime();
  for (const id of ['claude-code', 'codex', 'hermes', 'opencode']) {
    const row = api.view({id, kind: 'history', status: 'configured', observable: true});
    assert.equal(row.status, 'disconnected', id);
    assert.equal(row.observable, false, id);
  }
  assert.equal(api.view({id: 'custom', status: 'configured'}).status, 'configured');
});

test('loading saved consent enables only the explicitly selected profile', () => {
  const api = runtime();
  api.publish({disconnected: {'claude-code': true, hermes: true, opencode: true}, historyEnabled: {'claude-code': true}});
  assert.equal(api.view({id: 'codex', status: 'configured'}).status, 'configured');
  assert.equal(api.view({id: 'claude-code', status: 'configured'}).status, 'disconnected');
});
