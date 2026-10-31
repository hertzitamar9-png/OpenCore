"""Keep the private agent executable separate from legacy automatic plugin setup."""
from pathlib import Path
import hashlib
import json


RELATIVE = Path('claude/node_modules/@anthropic-ai/claude-agent-sdk-win32-x64')


def prepare(app):
    directory = Path(app).resolve()/RELATIVE
    source, target = directory/'claude.exe', directory/'opencore-agent.exe'
    if not source.is_file() or target.exists(): raise ValueError('Unexpected private agent baseline')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    source.rename(target)
    # Normal updates must replace the old path as well, rather than leaving the
    # previous release's CLI available to its native auto-installer. This is an
    # inert, non-executable tombstone; private SDK use names opencore-agent.exe.
    source.write_bytes(b'OpenCore: external client plugins are never installed automatically.\r\n')
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest: raise ValueError('Private agent changed')
    return {'automaticPluginInstallerDisabled':True,'privateAgentSha256':digest}
