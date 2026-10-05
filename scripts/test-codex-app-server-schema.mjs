import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { codexRuntimeManifest } from './codex-package-version.mjs';
import { probeCodexAppServer } from './codex-app-server-probe.mjs';

test('bundled Codex app-server accepts a read-only initialize handshake for the pinned schema', async () => {
  const manifest = codexRuntimeManifest();
  const result = await probeCodexAppServer({
    command: manifest.appServerExecutable,
    expectedVersion: manifest.cliVersion,
    probeHomeParent: path.join(manifest.runtimeRoot, 'node_modules'),
  });
  assert.equal(result.cliVersion, manifest.cliVersion);
  assert.equal(result.initialized, true);
  assert.equal(result.codexHome, 'isolated');
});
