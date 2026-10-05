import { copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../src-tauri/resources/claude/', import.meta.url));
const lock = readFileSync(root + '/package-lock.json', 'utf8');
const receipt = root + '/node_modules/.opencore-lock';
if (!existsSync(receipt) || readFileSync(receipt, 'utf8') !== lock) {
  const result = spawnSync(process.platform === 'win32' ? 'npm.cmd' : 'npm', ['ci', '--omit=dev', '--no-audit', '--no-fund'], { cwd: root, stdio: 'inherit', shell: process.platform === 'win32' });
  if (result.status !== 0) process.exit(result.status ?? 1);
  writeFileSync(receipt, lock);
}
copyFileSync(process.execPath, root + (process.platform === 'win32' ? '/node.exe' : '/node'));
