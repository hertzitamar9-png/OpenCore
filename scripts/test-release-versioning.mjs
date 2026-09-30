import assert from 'node:assert/strict';
import { test } from 'node:test';
import { releaseVersion } from './release-version.mjs';

function compareVersions(left, right) {
  const a = left.split('.').map(Number);
  const b = right.split('.').map(Number);
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    const difference = (a[index] ?? 0) - (b[index] ?? 0);
    if (difference !== 0) return Math.sign(difference);
  }
  return 0;
}

test('each release is newer than the installed 0.2.0 app', () => {
  const next = releaseVersion(39);
  assert.equal(next, '0.2.39');
  assert.equal(compareVersions(next, '0.2.0'), 1);
  assert.equal(releaseVersion(38), '0.2.38');
  assert.equal(releaseVersion('39'), '0.2.39');
});

test('release run number must be a positive safe integer', () => {
  for (const value of [0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, 'abc', null]) {
    assert.throws(() => releaseVersion(value));
  }
});
