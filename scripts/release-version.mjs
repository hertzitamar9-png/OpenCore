import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const APP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export function releaseVersion(value) {
  if (typeof value === 'boolean' || value === null || value === undefined
      || (typeof value === 'string' && value.trim() === '')) {
    throw new TypeError('GitHub Actions run number must be a positive safe integer');
  }
  const runNumber = Number(value);
  if (!Number.isSafeInteger(runNumber) || runNumber < 1) {
    throw new TypeError('GitHub Actions run number must be a positive safe integer');
  }
  return `0.2.${runNumber}`;
}

function updateConfig(runNumber) {
  const configPath = path.join(APP_ROOT, 'src-tauri', 'tauri.conf.json');
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  const version = releaseVersion(runNumber);
  config.version = version;
  fs.writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`);
  process.stdout.write(`Release version: ${version}\n`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    updateConfig(process.argv[2] ?? process.env.GITHUB_RUN_NUMBER);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
