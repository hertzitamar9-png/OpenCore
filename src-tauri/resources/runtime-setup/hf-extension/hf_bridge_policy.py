"""Remove the exact legacy auto-installed Claude plugin, preserving other settings."""
import json
import os
from pathlib import Path
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
    # Remove only OpenCore's legacy registration. Never launch an external
    # client or its plugins while starting OpenCore.
    shared=any(isinstance(key,str) and key!=PLUGIN and key.endswith('@'+MARKETPLACE)
               for key in (*plugins, *enabled))
    changes=[]
    if PLUGIN in enabled:
        enabled.pop(PLUGIN);changes.append('settings.json')
    if PLUGIN in plugins:
        plugins.pop(PLUGIN);changes.append('plugins/installed_plugins.json')
    if not shared:
        if MARKETPLACE in markets:
            markets.pop(MARKETPLACE);changes.append('plugins/known_marketplaces.json')
        if MARKETPLACE in extras:
            extras.pop(MARKETPLACE);changes.append('settings.json')
    changes=list(dict.fromkeys(changes))
    if not changes:return False
    for name in changes:
        value,raw=records[name]
        if raw is None or paths[name].read_bytes()!=raw:
            raise ValueError('Plugin settings changed during removal')
        backup=checked(paths[name].with_name(paths[name].name+'.before-opencore-removal.bak'))
        if not backup.exists():
            with backup.open('xb') as output:output.write(raw)
        if paths[name].read_bytes()!=raw:
            raise ValueError('Plugin settings changed during removal')
        atomic_json(paths[name],value)
    return True
