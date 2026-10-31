"""Remove the exact legacy auto-installed Claude plugin, preserving other settings."""
import json
import os
from pathlib import Path
import subprocess
from hf_connectors import checked
from hf_download import atomic_json

PLUGIN = 'opencore-bridge@opencore-local'
MARKETPLACE = 'opencore-local'


def _read(path):
    path=checked(path)
    if not path.is_file(): return {},None
    raw=path.read_bytes()
    value=json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value,dict): raise ValueError('Invalid Claude plugin settings')
    return value,raw


def retire_automatic_plugin(home=None, resources=None):
    directory=(Path(home)/'.claude' if home is not None else
        Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path(os.environ.get('USERPROFILE') or Path.home())/'.claude'))
    paths={name:directory/name for name in ('settings.json','plugins/installed_plugins.json','plugins/known_marketplaces.json')}
    records={name:_read(path) for name,path in paths.items()}
    settings=records['settings.json'][0]
    enabled=settings.get('enabledPlugins',{})
    plugins=records['plugins/installed_plugins.json'][0].get('plugins',{})
    markets=records['plugins/known_marketplaces.json'][0]
    extras=settings.get('extraKnownMarketplaces',{})
    if not all(isinstance(value,dict) for value in (enabled,plugins,extras)):
        raise ValueError('Invalid Claude plugin inventory')
    registered=any(row.get('scope')=='user' for row in plugins.get(PLUGIN,[]) if isinstance(row,dict))
    marketplace=MARKETPLACE in markets or MARKETPLACE in extras
    if PLUGIN not in enabled and not registered and not marketplace: return False
    # Fresh installations do not invoke Claude or create its external profile.
    # Back up existing configuration before the supported scoped uninstall.
    for name,(value,raw) in records.items():
        if raw is None: continue
        backup=checked(paths[name].with_name(paths[name].name+'.before-opencore-removal.bak'))
        if not backup.exists():
            with backup.open('xb') as output: output.write(raw)
    removed_marketplace=False
    if registered or marketplace:
        if resources is None or not Path(resources).is_absolute(): raise ValueError('An installed resource root is required for plugin removal')
        cli=Path(resources)/'claude/node_modules/@anthropic-ai/claude-agent-sdk-win32-x64/opencore-agent.exe'
        if not cli.is_file(): raise ValueError('The managed agent is unavailable for legacy plugin removal')
        with cli.open('rb') as source:
            if source.read(2)!=b'MZ': raise ValueError('The managed agent is not executable')
        environment=dict(os.environ,CLAUDE_CONFIG_DIR=str(directory),CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
        def run(*args):
            result=subprocess.run([str(cli),*args],stdin=subprocess.DEVNULL,capture_output=True,
                text=True,encoding='utf-8',errors='replace',env=environment,timeout=25,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode: raise RuntimeError('Claude could not remove the legacy OpenCore plugin')
            return result.stdout
        before=json.loads(run('plugin','list','--json'))
        if not isinstance(before,list): raise ValueError('Invalid Claude plugin inventory')
        if any(row.get('id')==PLUGIN and row.get('scope')=='user' for row in before):
            run('plugin','uninstall',PLUGIN,'--scope','user')
        others=[row for row in before if row.get('id','').endswith('@'+MARKETPLACE) and row.get('id')!=PLUGIN]
        if marketplace and not others:
            run('plugin','marketplace','remove',MARKETPLACE,'--scope','user')
            removed_marketplace=True
        after=json.loads(run('plugin','list','--json'))
        if any(row.get('id')==PLUGIN and row.get('scope')=='user' for row in after):
            raise RuntimeError('Claude still lists the legacy OpenCore plugin')
        if not {(row.get('id'),row.get('scope')) for row in before if row.get('id')!=PLUGIN} <= {(row.get('id'),row.get('scope')) for row in after}:
            raise RuntimeError('Claude plugin inventory changed during removal')
    # Also retire old disabled entries when the plugin was already uninstalled.
    value,raw=_read(paths['settings.json'])
    changed=False
    for key,entry in (('enabledPlugins',PLUGIN),('extraKnownMarketplaces',MARKETPLACE)):
        items=value.get(key,{})
        if isinstance(items,dict) and entry in items and (key=='enabledPlugins' or removed_marketplace):
            items.pop(entry);changed=True
    if changed:
        if paths['settings.json'].read_bytes()!=raw: raise ValueError('Claude settings changed during removal')
        atomic_json(paths['settings.json'],value)
    return True
