import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { assertAppServerSchemaMatches, expectedPlatformPackageVersion } from '../scripts/codex-package-version.mjs';

test('matches the exact platform-suffixed Codex CLI package version', () => {
  assert.equal(expectedPlatformPackageVersion('0.160.0', 'codex-win32-x64'), '0.160.0-win32-x64');
  assert.equal(expectedPlatformPackageVersion('0.160.0', 'codex-darwin-arm64'), '0.160.0-darwin-arm64');
});

test('rejects package names without a platform suffix', () => {
  assert.throws(() => expectedPlatformPackageVersion('0.160.0', 'codex'), /platform package/i);
});

test('rejects a regenerated app-server schema with a different pinned hash', async () => {
  assert.throws(() => assertAppServerSchemaMatches('a'.repeat(64), 'b'.repeat(64)), /schema hash mismatch/i);
});

test('app-server executable and checked-in schema must match one pinned release', async () => {
  const { codexRuntimeManifest } = await import('../scripts/codex-package-version.mjs');
  const runtime = JSON.parse(readFileSync(new URL('../src-tauri/resources/codex/package.json', import.meta.url), 'utf8'));
  assert.equal(runtime.dependencies?.['@openai/codex-sdk'], undefined, 'the retired SDK runner must not remain a runtime dependency');
  const manifest = codexRuntimeManifest();
  assert.equal(manifest.cliVersion, '0.160.0');
  assert.equal(manifest.platformPackage, `codex-${process.platform}-${process.arch}`);
  assert.equal(manifest.platformPackageVersion, expectedPlatformPackageVersion('0.160.0', manifest.platformPackage));
  assert.equal(manifest.schemaRevision, 'v2');
  assert.equal(createHash('sha256').update(readFileSync(manifest.schemaPath)).digest('hex'), manifest.schemaSha256);
  const versionOutput = execFileSync(process.execPath, [manifest.cliEntry, '--version'], { encoding: 'utf8' });
  assert.match(versionOutput, /^codex-cli 0\.160\.0\s*$/);
});

test('unsupported app-server version fails the read-only startup probe', async () => {
  const { probeCodexAppServer } = await import('../scripts/codex-app-server-probe.mjs');
  const directory = await mkdtemp(path.join(os.tmpdir(), 'codex-server-version-'));
  const fakeServer = path.join(directory, 'fake-codex.mjs');
  const trace = path.join(directory, 'calls.txt');
  await writeFile(fakeServer, `
    import { appendFileSync } from 'node:fs';
    import readline from 'node:readline';
    const trace = process.argv[2];
    if (process.argv.includes('--version')) {
      appendFileSync(trace, 'version\\n');
      process.stdout.write('codex-cli 99.0.0\\n');
      process.exit(0);
    }
    appendFileSync(trace, 'app-server\\n');
    for await (const line of readline.createInterface({ input: process.stdin })) {
      const request = JSON.parse(line);
      if (request.method === 'initialize') {
        process.stdout.write(JSON.stringify({ id: request.id, result: { serverInfo: { name: 'codex', version: '99.0.0' } } }) + '\\n');
      }
    }
  `);
  try {
    await assert.rejects(
      probeCodexAppServer({
        command: process.execPath,
        args: [fakeServer, trace],
        expectedVersion: '0.160.0',
      }),
      /Unsupported Codex app-server version 99\.0\.0/,
    );
    assert.equal(readFileSync(trace, 'utf8'), 'version\n');
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
