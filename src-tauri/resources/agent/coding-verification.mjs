// Execution-backed coding feedback. This never runs a command or grants access;
// checks still use OpenCore's existing permission/tool bridge.
import { createHash } from 'node:crypto';
import { readFileSync, realpathSync, statSync } from 'node:fs';
import { extname, isAbsolute, relative, resolve, sep } from 'node:path';

const CODE_EXTENSIONS = new Set(['.py', '.pyi', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx',
  '.rs', '.c', '.cc', '.cpp', '.h', '.hpp', '.cs', '.go', '.java', '.kt', '.swift',
  '.rb', '.php', '.lua', '.sh', '.ps1', '.html', '.css', '.scss', '.sql', '.vue', '.svelte']);
const MUTATIONS = new Set(['write', 'edit', 'patch', 'apply_patch']);

export const codingBoundaryGuidance =
  'For coding verification, choose checks from the actual contract. Reuse existing tests; add a small regression check when they miss the observed bug. ' +
  'Vary independent lengths or dimensions independently: square-only examples can miss rectangular or ragged-input failures. ' +
  'Use valid arguments matching the function signature, and calculate expected values from the contract, not from the implementation. ' +
  'A malformed test is a test-authoring error; inspect it before changing working code. Keep additional checks focused.';

export function toolFailed(value) {
  return Boolean(value?.error) || (typeof value?.exitCode === 'number' && value.exitCode !== 0);
}

export class CodingVerification {
  constructor(cwd, { devAvailable = true } = {}) {
    this.cwd = realpathSync(cwd);
    this.changed = new Map();
    this.checked = new Map();
    this.stopRequests = 0;
    this.lastFailure = null;
    this.devAvailable = devAvailable;
  }

  file(name) {
    if (typeof name !== 'string' || !name) return null;
    try {
      const target = realpathSync(resolve(this.cwd, name));
      const path = relative(this.cwd, target);
      if (isAbsolute(path) || path === '..' || path.startsWith('..' + sep)) return null;
      const info = statSync(target);
      if (!info.isFile() || info.size > 16 * 1024 * 1024 || !CODE_EXTENSIONS.has(extname(path).toLowerCase())) return null;
      return { path: path.replaceAll('\\', '/'), hash: createHash('sha256').update(readFileSync(target)).digest('hex') };
    } catch { return null; }
  }

  recordSdkTool(data) {
    if (!['Write', 'Edit', 'MultiEdit'].includes(data?.tool_name)) return;
    const file = this.file(data.tool_input?.file_path);
    if (file) this.changed.set(file.path, file.hash);
  }

  recordCodexFileChange(path) {
    const file = this.file(path);
    if (file) this.changed.set(file.path, file.hash);
    else if (typeof path === 'string' && path.trim()) this.changed.set(path.replaceAll('\\', '/'), '');
  }

  recordMcp(name, input, result) {
    if (name !== 'dev') return;
    if (MUTATIONS.has(input?.action) && !toolFailed(result)) {
      const file = this.file(result?.path ?? input.path);
      if (file) this.changed.set(file.path, file.hash);
    }
    if (input?.action !== 'run' || !input.verifyPaths?.length) return;
    if (toolFailed(result)) {
      const paths = input.verifyPaths.map(name => this.file(name)?.path ?? name);
      this.lastFailure = { exitCode: result.exitCode,
        paths: [...new Set([...(this.lastFailure?.paths ?? []), ...paths])] };
      for (const path of paths) this.checked.delete(path);
      return;
    }
    if (result?.exitCode !== 0) return;
    const passedPaths = new Set();
    for (const [name, digest] of Object.entries(result.checked ?? {})) {
      const file = this.file(name);
      if (file && file.hash === digest) { this.checked.set(file.path, file.hash); passedPaths.add(file.path); }
    }
    if (this.lastFailure) {
      this.lastFailure.paths = this.lastFailure.paths.filter(path => !passedPaths.has(path));
      if (!this.lastFailure.paths.length) this.lastFailure = null;
    }
  }

  pending() {
    return [...this.changed.keys()].filter(path => {
      const current = this.file(path);
      return !current || this.checked.get(path) !== current.hash;
    });
  }

  beforeStop(data) {
    const paths = this.pending();
    if ((!paths.length && !this.lastFailure) || data?.stop_hook_active || this.stopRequests) return {};
    this.stopRequests += 1;
    const targets = paths.length ? paths : this.lastFailure.paths;
    const command = this.devAvailable
      ? 'Run the narrow relevant tests or compiler using dev action run with verifyPaths for the changed files. '
      : 'Run the narrow relevant tests or compiler using the available Bash tool. ';
    return { decision: 'block', reason:
      `The latest code has no passing execution check: ${targets.slice(0, 16).join(', ')}. ` +
      command +
      codingBoundaryGuidance + ' ' +
      'Inspect the actual exit code and output. If a check fails, repair the implementation and rerun it. ' +
      'Preserve existing behavior and test assertions. If verification is blocked or still fails, state that clearly; do not claim a verified fix. ' +
      'This is one bounded verification pass, not a request for unrelated refactoring.' };
  }
}
