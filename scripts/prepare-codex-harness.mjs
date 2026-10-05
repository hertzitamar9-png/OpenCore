import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { assertAppServerSchemaMatches, codexRuntimeManifest, expectedPlatformPackageVersion } from './codex-package-version.mjs';

const PINNED_CODEX_VERSION = '0.160.0';
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const runtime = path.resolve(scriptDir, '../src-tauri/resources/codex');
const runtimePackage = JSON.parse(readFileSync(path.join(runtime, 'package.json'), 'utf8'));
const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const cliManifest = path.join(runtime, 'node_modules', '@openai', 'codex', 'package.json');
const sdkManifest = path.join(runtime, 'node_modules', '@openai', 'codex-sdk', 'package.json');
const nativePackage = `codex-${process.platform}-${process.arch}`;
const nativeManifest = path.join(runtime, 'node_modules', '@openai', nativePackage, 'package.json');
const nativeVersion = expectedPlatformPackageVersion(PINNED_CODEX_VERSION, nativePackage);

function versionOf(file) {
  if (!existsSync(file)) return null;
  try { return JSON.parse(readFileSync(file, 'utf8')).version; } catch { return null; }
}

function requireVersions() {
  if (runtimePackage.dependencies?.['@openai/codex'] !== PINNED_CODEX_VERSION
    || runtimePackage.dependencies?.['@openai/codex-sdk'] !== PINNED_CODEX_VERSION
    || versionOf(cliManifest) !== PINNED_CODEX_VERSION
    || versionOf(sdkManifest) !== PINNED_CODEX_VERSION
    || versionOf(nativeManifest) !== nativeVersion) {
    throw new Error('Codex CLI, SDK compatibility bridge, and native platform package must match pinned version 0.160.0');
  }
}

try {
  requireVersions();
} catch {
  execFileSync(npmCommand, ['ci', '--no-audit', '--no-fund'], {
    cwd: runtime,
    stdio: 'inherit',
    ...(process.platform === 'win32' ? { shell: true } : {}),
  });
  requireVersions();
}

const manifest = codexRuntimeManifest({ runtimeRoot: runtime });
const versionOutput = execFileSync(process.execPath, [manifest.cliEntry, '--version'], { encoding: 'utf8' });
if (!new RegExp(`^codex-cli ${PINNED_CODEX_VERSION}\\s*$`).test(versionOutput.trim())) {
  throw new Error(`Bundled Codex executable does not report pinned version ${PINNED_CODEX_VERSION}`);
}

const schemaProbeDir = mkdtempSync(path.join(os.tmpdir(), 'opencore-codex-schema-'));
try {
  execFileSync(process.execPath, [manifest.cliEntry, 'app-server', 'generate-json-schema', '--out', schemaProbeDir], {
    stdio: 'inherit',
  });
  const generatedSchema = path.join(schemaProbeDir, 'codex_app_server_protocol.v2.schemas.json');
  if (!existsSync(generatedSchema)) throw new Error('Pinned Codex CLI did not emit its v2 app-server schema');
  const generatedHash = createHash('sha256').update(readFileSync(generatedSchema)).digest('hex');
  assertAppServerSchemaMatches(generatedHash, manifest.schemaSha256);
} finally {
  rmSync(schemaProbeDir, { recursive: true, force: true });
}

console.log(`Prepared Codex app-server ${manifest.cliVersion} (${manifest.platformPackageVersion}), protocol ${manifest.schemaRevision}, schema ${manifest.schemaSha256}.`);
