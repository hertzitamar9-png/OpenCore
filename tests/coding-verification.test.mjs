import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { CodingVerification, toolFailed } from '../src-tauri/resources/claude/coding-verification.mjs';

const hash = text => createHash('sha256').update(text).digest('hex');
function fixture(t) {
  const cwd = mkdtempSync(join(tmpdir(), 'opencore-check-'));
  t.after(() => rmSync(cwd, { recursive: true }));
  writeFileSync(join(cwd, 'main.py'), 'answer = 1\n');
  return { cwd, guard: new CodingVerification(cwd) };
}

test('plain conversation and read-only work add no verification turn', t => {
  const { guard } = fixture(t);
  guard.recordSdkTool({ tool_name: 'Read', tool_input: { file_path: 'main.py' } });
  assert.deepEqual(guard.beforeStop({}), {});
});

test('failed commands are errors even when the process launched successfully', () => {
  assert.equal(toolFailed({ exitCode: 7, stdout: 'started' }), true);
  assert.equal(toolFailed({ error: 'missing interpreter' }), true);
  assert.equal(toolFailed({ exitCode: 0, stderr: 'warning' }), false);
});

test('code edits trigger one bounded verification request', t => {
  const { guard } = fixture(t);
  guard.recordSdkTool({ tool_name: 'Edit', tool_input: { file_path: 'main.py' } });
  const first = guard.beforeStop({});
  assert.equal(first.decision, 'block');
  assert.match(first.reason, /main\.py/);
  assert.deepEqual(guard.beforeStop({ stop_hook_active: true }), {});
});

test('passing checks authorize only the exact checked source version', t => {
  const { cwd, guard } = fixture(t);
  guard.recordSdkTool({ tool_name: 'Write', tool_input: { file_path: 'main.py' } });
  guard.recordMcp('dev', { action: 'run', verifyPaths: ['main.py'] },
    { exitCode: 0, checked: { 'main.py': hash('answer = 1\n') } });
  assert.deepEqual(guard.beforeStop({}), {});
  writeFileSync(join(cwd, 'main.py'), 'answer = 2\n');
  assert.equal(guard.beforeStop({}).decision, 'block');
});

test('failed or unrelated checks cannot certify changed code', t => {
  const { guard } = fixture(t);
  guard.recordSdkTool({ tool_name: 'Edit', tool_input: { file_path: 'main.py' } });
  guard.recordMcp('dev', { action: 'run', verifyPaths: ['main.py'] },
    { exitCode: 1, checked: { 'main.py': hash('answer = 1\n') }, stderr: 'AssertionError' });
  assert.equal(guard.beforeStop({}).decision, 'block');
});

test('MCP edits and checks use the same verification policy as native edits', t => {
  const { guard } = fixture(t);
  guard.recordMcp('dev', { action: 'patch', path: 'main.py' }, { path: 'main.py', sha256: hash('answer = 1\n') });
  assert.equal(guard.beforeStop({}).decision, 'block');
});

test('documentation edits do not require executable checks', t => {
  const { cwd, guard } = fixture(t);
  writeFileSync(join(cwd, 'README.md'), 'Description');
  guard.recordSdkTool({ tool_name: 'Write', tool_input: { file_path: 'README.md' } });
  assert.deepEqual(guard.beforeStop({}), {});
});

test('paths outside the selected workspace are not read or certified', t => {
  const { guard } = fixture(t);
  guard.recordSdkTool({ tool_name: 'Edit', tool_input: { file_path: '../other.py' } });
  assert.deepEqual(guard.beforeStop({}), {});
});

test('a different passing check cannot erase an unresolved failure', t => {
  const { cwd, guard } = fixture(t);
  guard.recordSdkTool({ tool_name: 'Edit', tool_input: { file_path: 'main.py' } });
  guard.recordMcp('dev', { action: 'run', verifyPaths: ['main.py'] },
    { exitCode: 0, checked: { 'main.py': hash('answer = 1\n') } });
  guard.recordMcp('dev', { action: 'run', verifyPaths: ['main.py'] }, { exitCode: 1 });
  writeFileSync(join(cwd, 'other.py'), 'other = 1\n');
  guard.recordMcp('dev', { action: 'run', verifyPaths: ['other.py'] },
    { exitCode: 0, checked: { 'other.py': hash('other = 1\n') } });
  assert.equal(guard.beforeStop({}).decision, 'block');
});

test('fallback guidance does not demand an unavailable MCP tool', t => {
  const { cwd } = fixture(t);
  const guard = new CodingVerification(cwd, { devAvailable: false });
  guard.recordSdkTool({ tool_name: 'Edit', tool_input: { file_path: 'main.py' } });
  const result = guard.beforeStop({});
  assert.equal(result.decision, 'block');
  assert.doesNotMatch(result.reason, /dev action run/);
  assert.match(result.reason, /Bash/);
});
