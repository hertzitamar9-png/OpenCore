import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const runtime = path.resolve(scriptDir, '../src-tauri/resources/codex');
const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const sdkManifest = path.join(runtime, 'node_modules', '@openai', 'codex-sdk', 'package.json');
const cliManifest = path.join(runtime, 'node_modules', '@openai', 'codex', 'package.json');
const nativePackage = process.platform === 'win32' ? `codex-win32-${process.arch}`
  : process.platform === 'darwin' ? `codex-darwin-${process.arch}` : `codex-linux-${process.arch}`;
const nativeManifest = path.join(runtime, 'node_modules', '@openai', nativePackage, 'package.json');
function versionOf(file) {
  if (!existsSync(file)) return null;
  try { return JSON.parse(readFileSync(file, 'utf8')).version; } catch { return null; }
}
const complete = versionOf(sdkManifest) === '0.160.0'
  && versionOf(cliManifest) === '0.160.0'
  && versionOf(nativeManifest) === '0.160.0';
if (!complete) {
  execFileSync(npmCommand, ['ci', '--no-audit', '--no-fund'], {
    cwd: runtime,
    stdio: 'inherit',
    ...(process.platform === 'win32' ? { shell: true } : {}),
  });
}
if (!existsSync(sdkManifest) || !existsSync(cliManifest)) throw new Error('Codex SDK and CLI must both be installed in the bundled agent runtime');
const sdk = JSON.parse(readFileSync(sdkManifest, 'utf8'));
const cli = JSON.parse(readFileSync(cliManifest, 'utf8'));
if (sdk.version !== '0.160.0' || cli.version !== '0.160.0' || versionOf(nativeManifest) !== '0.160.0') {
  throw new Error('The Codex SDK, CLI, and platform binary must be the matching pinned 0.160.0 runtime');
}
console.log('Prepared pinned OpenAI Codex TypeScript SDK and CLI 0.160.0.');
