const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createManualUpdatePolicy } = require('./manual-update-policy.cjs');

test('an installed distribution does not repeatedly offer its native baseline update', () => {
  const policy = createManualUpdatePolicy('0.3.143');
  assert.deepEqual(policy.normalize({ currentVersion: '0.2.305', available: true, version: '0.3.143' }),
    { currentVersion: '0.3.143', available: false, version: null });
});
test('a later main push is available and older releases are not', () => {
  const policy = createManualUpdatePolicy('0.3.143');
  assert.equal(policy.normalize({ available: true, version: '0.3.144' }).available, true);
  assert.equal(policy.normalize({ available: true, version: '0.3.99' }).available, false);
  assert.equal(policy.normalize({ available: false, version: null }).available, false);
});
test('release metadata is validated', () => {
  assert.throws(() => createManualUpdatePolicy('unknown'));
  assert.throws(() => createManualUpdatePolicy('0.3.143').normalize({ available: true, version: 'unknown' }));
});
