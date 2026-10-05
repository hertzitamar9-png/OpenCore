import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const PINNED_CODEX_VERSION = '0.160.0';
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const defaultRuntimeRoot = path.resolve(scriptDir, '../src-tauri/resources/codex');

export function expectedPlatformPackageVersion(baseVersion, packageName) {
  const prefix = 'codex-';
  if (typeof packageName !== 'string' || !packageName.startsWith(prefix) || packageName.length === prefix.length) {
    throw new TypeError('Expected a Codex platform package with an OS and architecture suffix');
  }
  return `${baseVersion}-${packageName.slice(prefix.length)}`;
}

export function assertAppServerSchemaMatches(actualHash, expectedHash) {
  const validHash = /^[a-f0-9]{64}$/;
  if (!validHash.test(actualHash ?? '') || !validHash.test(expectedHash ?? '') || actualHash !== expectedHash) {
    throw new Error('Codex app-server schema hash mismatch');
  }
  return true;
}

function readJson(file, label) {
  if (!existsSync(file)) throw new Error(`Missing ${label}: ${file}`);
  try {
    return JSON.parse(readFileSync(file, 'utf8'));
  } catch (error) {
    throw new Error(`Invalid ${label}: ${file}`, { cause: error });
  }
}

function codexTargetTriple(platform, arch) {
  const triples = {
    'win32-x64': 'x86_64-pc-windows-msvc',
    'win32-arm64': 'aarch64-pc-windows-msvc',
    'darwin-x64': 'x86_64-apple-darwin',
    'darwin-arm64': 'aarch64-apple-darwin',
    'linux-x64': 'x86_64-unknown-linux-musl',
    'linux-arm64': 'aarch64-unknown-linux-musl',
  };
  const triple = triples[`${platform}-${arch}`];
  if (!triple) throw new Error(`Unsupported Codex target ${platform}-${arch}`);
  return triple;
}

export function codexRuntimeManifest({ runtimeRoot = defaultRuntimeRoot, platform = process.platform, arch = process.arch } = {}) {
  const runtimePackage = readJson(path.join(runtimeRoot, 'package.json'), 'bundled Codex runtime package');
  if (runtimePackage.dependencies?.['@openai/codex'] !== PINNED_CODEX_VERSION) {
    throw new Error(`Codex CLI must be pinned to ${PINNED_CODEX_VERSION}`);
  }
  const cliPackageRoot = path.join(runtimeRoot, 'node_modules', '@openai', 'codex');
  const cliPackage = readJson(path.join(cliPackageRoot, 'package.json'), 'Codex CLI package');
  if (cliPackage.version !== PINNED_CODEX_VERSION || typeof cliPackage.bin?.codex !== 'string') {
    throw new Error(`Unsupported Codex CLI package version ${cliPackage.version ?? 'unknown'}`);
  }

  const platformPackage = `codex-${platform}-${arch}`;
  const platformPackageVersion = expectedPlatformPackageVersion(PINNED_CODEX_VERSION, platformPackage);
  const platformPackageRoot = path.join(runtimeRoot, 'node_modules', '@openai', platformPackage);
  const installedPlatformPackage = readJson(path.join(platformPackageRoot, 'package.json'), 'Codex native platform package');
  if (installedPlatformPackage.version !== platformPackageVersion) {
    throw new Error(`Unsupported Codex platform package version ${installedPlatformPackage.version ?? 'unknown'}`);
  }

  const targetTriple = codexTargetTriple(platform, arch);
  const appServerExecutable = path.join(
    platformPackageRoot,
    'vendor',
    targetTriple,
    'bin',
    platform === 'win32' ? 'codex.exe' : 'codex',
  );
  const schemaPath = path.join(runtimeRoot, 'protocol', 'app-server.schema.json');
  const runtimeManifestPath = path.join(runtimeRoot, 'protocol', 'runtime-manifest.json');
  const runtimeManifest = readJson(runtimeManifestPath, 'Codex runtime manifest');
  const schemaSha256 = createHash('sha256').update(readFileSync(schemaPath)).digest('hex');
  const platformKey = `${platform}-${arch}`;
  const platformEntry = runtimeManifest.platforms?.[platformKey];
  if (runtimeManifest.cliVersion !== PINNED_CODEX_VERSION) {
    throw new Error(`Codex runtime manifest CLI version does not match ${PINNED_CODEX_VERSION}`);
  }
  if (runtimeManifest.schemaRevision !== 'v2') {
    throw new Error('Codex runtime manifest must pin app-server schema revision v2');
  }
  try {
    assertAppServerSchemaMatches(schemaSha256, runtimeManifest.schemaSha256);
  } catch (error) {
    throw new Error('Codex app-server schema does not match its pinned runtime manifest');
  }
  if (platformEntry?.package !== platformPackage || platformEntry?.version !== platformPackageVersion) {
    throw new Error(`Codex runtime manifest has no matching ${platformKey} executable`);
  }
  if (!existsSync(appServerExecutable)) throw new Error(`Missing bundled Codex app-server executable: ${appServerExecutable}`);

  return {
    runtimeRoot: path.resolve(runtimeRoot),
    cliVersion: runtimeManifest.cliVersion,
    platformPackage,
    platformPackageVersion,
    schemaRevision: runtimeManifest.schemaRevision,
    schemaSha256,
    cliEntry: path.resolve(cliPackageRoot, cliPackage.bin.codex),
    appServerExecutable,
    schemaPath,
    runtimeManifestPath,
  };
}
