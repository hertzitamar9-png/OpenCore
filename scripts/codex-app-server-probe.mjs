import { execFileSync, spawn } from 'node:child_process';
import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import os from 'node:os';
import path from 'node:path';

function runtimeEnvironment(codexHome) {
  const safeKeys = ['PATH', 'Path', 'PATHEXT', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'ComSpec'];
  const env = {};
  for (const key of safeKeys) {
    if (process.env[key] !== undefined) env[key] = process.env[key];
  }
  env.CODEX_HOME = codexHome;
  return env;
}

function readCliVersion(command, args, expectedVersion, env) {
  const output = execFileSync(command, [...args, '--version'], {
    encoding: 'utf8',
    timeout: 10_000,
    windowsHide: true,
    env,
  });
  const version = output.match(/codex-cli\s+(\d+\.\d+\.\d+(?:-[\w.-]+)?)/i)?.[1];
  if (!version) throw new Error('Codex executable returned an unrecognized version string');
  if (version !== expectedVersion) {
    throw new Error(`Unsupported Codex app-server version ${version}; expected ${expectedVersion}`);
  }
  return version;
}

function initialize(child, params, timeoutMs) {
  return new Promise((resolve, reject) => {
    const lines = createInterface({ input: child.stdout });
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      lines.close();
      if (error) reject(error);
      else resolve(value);
    };
    const timer = setTimeout(() => finish(new Error(`Codex app-server initialize timed out after ${timeoutMs} ms`)), timeoutMs);
    lines.on('line', (line) => {
      if (!line.trim()) return;
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        finish(new Error('Codex app-server wrote malformed JSON during initialize'));
        return;
      }
      if (message.id !== 1) {
        if (message.method && message.id !== undefined) {
          finish(new Error(`Codex app-server requested unsupported startup method ${message.method}`));
        }
        return;
      }
      if (message.error) {
        finish(new Error(`Codex app-server initialize failed: ${message.error.message ?? 'unknown error'}`));
        return;
      }
      const result = message.result;
      if (!result || typeof result !== 'object'
        || typeof result.codexHome !== 'string'
        || typeof result.platformFamily !== 'string'
        || typeof result.platformOs !== 'string'
        || typeof result.userAgent !== 'string') {
        finish(new Error('Codex app-server returned an invalid initialize response'));
        return;
      }
      finish(null, result);
    });
    child.once('error', (error) => finish(new Error(`Codex app-server failed to start: ${error.message}`)));
    child.once('exit', (code, signal) => {
      finish(new Error(`Codex app-server exited before initialize completed (code ${code ?? 'null'}, signal ${signal ?? 'none'})`));
    });
    child.stdin.write(`${JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method: 'initialize',
      params,
    })}\n`);
  });
}

export async function probeCodexAppServer({ command, args = [], expectedVersion, timeoutMs = 15_000, probeHomeParent = os.tmpdir() }) {
  if (typeof command !== 'string' || !command || typeof expectedVersion !== 'string' || !expectedVersion) {
    throw new TypeError('A Codex executable and pinned expected version are required');
  }

  const probeHome = await mkdtemp(path.join(probeHomeParent, 'opencore-codex-app-server-probe-'));
  const env = runtimeEnvironment(probeHome);
  let child;
  let exitPromise;
  try {
    const cliVersion = readCliVersion(command, args, expectedVersion, env);
    child = spawn(command, [...args, 'app-server', '--listen', 'stdio://'], {
      cwd: probeHome,
      env,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'ignore'],
    });
    exitPromise = new Promise((resolve) => child.once('exit', resolve));
    const result = await initialize(child, {
      clientInfo: { name: 'opencore-runtime-probe', version: '1.0.0' },
      capabilities: {},
    }, timeoutMs);
    const [actualCodexHome, expectedCodexHome] = await Promise.all([
      realpath(result.codexHome),
      realpath(probeHome),
    ]);
    const normalizedActualHome = process.platform === 'win32' ? actualCodexHome.toLowerCase() : actualCodexHome;
    const normalizedExpectedHome = process.platform === 'win32' ? expectedCodexHome.toLowerCase() : expectedCodexHome;
    if (normalizedActualHome !== normalizedExpectedHome) {
      throw new Error(`Codex app-server ignored the isolated CODEX_HOME startup probe (expected ${expectedCodexHome}, got ${actualCodexHome})`);
    }
    child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', method: 'initialized' })}\n`);
    child.stdin.end();
    const exited = await Promise.race([
      exitPromise.then(() => true),
      new Promise((resolve) => setTimeout(() => resolve(false), 1_500)),
    ]);
    if (!exited) child.kill();
    return { cliVersion, initialized: true, codexHome: 'isolated' };
  } finally {
    if (child && child.exitCode === null && child.signalCode === null) {
      child.kill();
      if (exitPromise) await Promise.race([exitPromise, new Promise((resolve) => setTimeout(resolve, 1_500))]);
    }
    await rm(probeHome, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}
