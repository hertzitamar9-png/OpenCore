import test from 'node:test';
import assert from 'node:assert/strict';
import { expectedPlatformPackageVersion } from '../scripts/codex-package-version.mjs';

test('matches the exact platform-suffixed Codex CLI package version', () => {
  assert.equal(expectedPlatformPackageVersion('0.160.0', 'codex-win32-x64'), '0.160.0-win32-x64');
  assert.equal(expectedPlatformPackageVersion('0.160.0', 'codex-darwin-arm64'), '0.160.0-darwin-arm64');
});

test('rejects package names without a platform suffix', () => {
  assert.throws(() => expectedPlatformPackageVersion('0.160.0', 'codex'), /platform package/i);
});
