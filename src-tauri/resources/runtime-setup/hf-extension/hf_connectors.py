"""Disable only OpenCore-owned client profiles; preserve source transcripts and logins."""
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import tomllib
import uuid

from hf_download import atomic_json

IDS = {'codex', 'claude-code', 'opencode', 'hermes'}
BASE = 'http://127.0.0.1:8812'
ENV = {'ANTHROPIC_BASE_URL': BASE, 'ANTHROPIC_AUTH_TOKEN': 'opencore-local',
       **{key: 'opencore' for key in ('ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
                                    'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL')}}


def checked(path):
    path = Path(path).absolute()
    for parent in [*reversed(path.parents), path]:
        if parent.exists() or parent.is_symlink():
            info = parent.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('A connector config uses a link or junction; no profile settings were changed')
    if path.exists() and (not path.is_file() or path.stat().st_size > 8 * 1024 * 1024):
        raise ValueError('Connector settings must be a regular file smaller than 8 MB')
    return path


def jsonc(text):
    # Strip comments without interpreting URL/string contents, then trailing commas.
    result, quoted, escaped, index = [], False, False, 0
    while index < len(text):
        char = text[index]
        if quoted:
            result.append(char)
            if escaped: escaped = False
            elif char == '\\': escaped = True
            elif char == '"': quoted = False
        elif char == '"': quoted = True; result.append(char)
        elif text[index:index+2] == '//':
            end = text.find('\n', index)
            if end < 0: break
            index = end - 1
        elif text[index:index+2] == '/*':
            end = text.find('*/', index+2)
            if end < 0: raise ValueError('Invalid connector JSONC comment')
            result.append(' '); index = end + 1
        else: result.append(char)
        index += 1
    clean = ''.join(result).lstrip('\ufeff')
    # Remove commas only outside strings.
    result, quoted, escaped = [], False, False
    for index, char in enumerate(clean):
        if not quoted and char == ',' and clean[index+1:].lstrip().startswith(('}', ']')): continue
        result.append(char)
        if quoted:
            if escaped: escaped = False
            elif char == '\\': escaped = True
            elif char == '"': quoted = False
        elif char == '"': quoted = True
    value = json.loads(''.join(result))
    if not isinstance(value, dict): raise ValueError('Connector config must be an object')
    return value


def without_codex_profile(text):
    value = tomllib.loads(text)
    provider = value.get('model_providers', {}).get('opencore')
    profile = value.get('profiles', {}).get('opencore')
    if provider is None and profile is None: return text
    if provider and (provider.get('base_url') != BASE+'/v1' or provider.get('experimental_bearer_token') != 'opencore-local'):
        raise ValueError('The Codex provider named opencore is not managed by OpenCore')
    if profile and profile.get('model_provider') != 'opencore':
        raise ValueError('The Codex profile named opencore belongs to another provider')
    lines, headers = text.splitlines(keepends=True), []
    for index, line in enumerate(lines):
        if not line.lstrip().startswith('['): continue
        try:
            # A table-like line inside a multiline string is not a TOML header.
            tomllib.loads(''.join(lines[:index]))
            parsed = tomllib.loads(line)
        except tomllib.TOMLDecodeError: continue
        keys, block = [], parsed
        while isinstance(block, dict) and len(block) == 1:
            key, block = next(iter(block.items())); keys.append(key)
        headers.append((index, tuple(keys)))
    remove = set()
    for position, (index, keys) in enumerate(headers):
        if keys[:2] in (('profiles', 'opencore'), ('model_providers', 'opencore')):
            end = headers[position+1][0] if position+1 < len(headers) else len(lines)
            remove.update(range(index, end))
    result = ''.join(line for index, line in enumerate(lines) if index not in remove)
    parsed = tomllib.loads(result)
    if 'opencore' in parsed.get('profiles', {}) or 'opencore' in parsed.get('model_providers', {}):
        raise ValueError('This Codex config uses inline profiles; disconnect could not safely edit it')
    return result


class ConnectorProfiles:
    def __init__(self, root, *, home=None, environ=None):
        self.root = Path(root)
        self.environ = dict(os.environ if environ is None else environ)
        self.home = Path(home or self.environ.get('USERPROFILE') or Path.home()).absolute()
        self.path = self.root/'connectors'/'disconnected.json'
        self.lock = threading.RLock()

    def status(self):
        with self.lock:
            try:
                checked(self.path)
                value = json.loads(self.path.read_text(encoding='utf-8'))
                return {'disconnected': {key: row for key, row in value.get('disconnected', {}).items() if key in IDS}}
            except FileNotFoundError: return {'disconnected': {}}

    def _hermes_root(self):
        env = self.environ
        home = Path(env.get('HERMES_HOME') or Path(env.get('LOCALAPPDATA', self.home/'AppData/Local'))/('hermes'+env.get('HERMES_DATA_DIR_SUFFIX', '')))
        # Native folder selection is trusted state, never a caller-supplied write path.
        appdata = env.get('APPDATA')
        if appdata:
            database = Path(appdata)/'ai.opencore.control-center/control-center.sqlite3'
            if database.is_file():
                try:
                    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=1) as connection:
                        row = connection.execute('SELECT value FROM settings WHERE key=?', ('agent_connector_source_folder_hermes',)).fetchone()
                    if row and row[0]: home = Path(row[0])
                except sqlite3.Error: pass
        return home.parent.parent if home.parent.name == 'profiles' else home

    def _changes(self, identity):
        changes = []
        def edit(path, transform):
            path = checked(path)
            if not path.exists(): return
            raw = path.read_bytes()
            value = transform(raw.decode('utf-8-sig'))
            encoded = value.encode('utf-8')
            if encoded != raw: changes.append((path, raw, encoded))
        def encode(value): return json.dumps(value, indent=2, ensure_ascii=False)+'\n'
        if identity == 'codex': edit(self.home/'.codex/config.toml', without_codex_profile)
        elif identity == 'claude-code':
            def claude(text):
                value = jsonc(text); env = value.get('env', {})
                if not isinstance(env, dict): raise ValueError('Claude settings env must be an object')
                changed = False
                for key, expected in ENV.items():
                    if env.get(key) == expected: env.pop(key); changed = True
                return encode(value) if changed else text
            for name in ('opencore-settings.json', 'settings.json'): edit(self.home/'.claude'/name, claude)
        elif identity == 'opencode':
            folder = Path(self.environ.get('XDG_CONFIG_HOME', self.home/'.config'))/'opencode'
            path = Path(self.environ.get('OPENCODE_CONFIG') or (folder/'opencode.jsonc' if (folder/'opencode.jsonc').is_file() else folder/'opencode.json'))
            def opencode(text):
                value = jsonc(text); provider = value.get('provider', {})
                if not isinstance(provider, dict): raise ValueError('OpenCode provider settings must be an object')
                owned = provider.get('opencore')
                if owned is not None:
                    if not isinstance(owned, dict) or owned.get('options', {}).get('baseURL') != BASE+'/v1':
                        raise ValueError('The OpenCode provider named opencore is not managed by OpenCore')
                    provider.pop('opencore')
                for key in ('enabled_providers', 'disabled_providers'):
                    if key in value:
                        if not isinstance(value[key], list): raise ValueError('Invalid OpenCode provider list')
                        value[key] = [row for row in value[key] if row != 'opencore']
                for key in ('model', 'small_model'):
                    if str(value.get(key, '')).startswith('opencore/'): value.pop(key)
                return encode(value)
            edit(path, opencode)
        elif identity == 'hermes':
            path = checked(self._hermes_root()/'profiles/opencore/config.yaml')
            if path.is_file():
                raw = path.read_bytes(); text = raw.decode('utf-8-sig')
                if not re.search(r'^\s+base_url:\s*[\'"]?http://127\.0\.0\.1:8812/v1[\'"]?\s*$', text, re.M):
                    raise ValueError('The Hermes profile named opencore is not managed by OpenCore')
                # Retire only the named opt-in config; its profile/history directory stays.
                changes.append((path, raw, None))
        return changes

    def disconnect(self, identity):
        if identity not in IDS: raise ValueError('Unknown agent connector')
        with self.lock:
            state = self.status()
            if identity in state['disconnected']: return {**state, 'message': 'Already disconnected'}
            changes, applied = self._changes(identity), []
            try:
                for path, raw, replacement in changes:
                    checked(path)
                    if path.read_bytes() != raw: raise ValueError('Connector settings changed during disconnect; retry')
                    backup = path.with_name(path.name+'.opencore-disconnected-'+uuid.uuid4().hex+'.bak')
                    if replacement is None: os.replace(path, backup)
                    else:
                        with backup.open('xb') as output: output.write(raw)
                        temporary = path.with_name(path.name+'.opencore-'+uuid.uuid4().hex+'.tmp')
                        try:
                            temporary.write_bytes(replacement); os.replace(temporary, path)
                        finally: temporary.unlink(missing_ok=True)
                    applied.append((path, raw))
                state['disconnected'][identity] = True
                checked(self.path); atomic_json(self.path, state)
            except Exception:
                for path, raw in reversed(applied): path.write_bytes(raw)
                raise
            return {**state, 'message': 'Disconnected. Imported conversations can be deleted separately.'}

    def reconnect(self, identity):
        if identity not in IDS: raise ValueError('Unknown agent connector')
        with self.lock:
            state = self.status(); state['disconnected'].pop(identity, None)
            checked(self.path); atomic_json(self.path, state)
            return state
