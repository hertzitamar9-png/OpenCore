"""Remove the exact legacy auto-installed Claude plugin, preserving other settings."""
import json
import os
import shutil
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from hf_connectors import checked
from hf_download import atomic_json

PLUGIN = 'opencore-bridge@opencore-local'
MARKETPLACE = 'opencore-local'

_BRIDGE_FILES={'.claude-plugin/plugin.json','hooks/hooks.json','hooks/register.js',
    'hooks/bridge.mjs','hooks/local-config.mjs','commands/music.md','commands/assets.md','README.md'}
_BRIDGE_DIRS={'.claude-plugin','hooks','commands'}

def retire_legacy_auth(app_data):
    """Revoke only the legacy bridge pairing and status, retaining chat records."""
    root=Path(app_data).absolute()
    database=root/'control-center.sqlite3'
    if not database.exists():return False
    if not database.is_file() or any(not _plain(p) for p in [*reversed(database.parents),database] if p.exists()):
        raise ValueError('The app database is not a regular local file')
    if database.resolve(strict=True).parent != root.resolve(strict=True):
        raise ValueError('The app database leaves the expected app data directory')
    keys=('claude_bridge_token_v1','claude_bridge_registered_v1','claude_bridge_enabled_v1',
          'claude_bridge_setup_error_v1','claude_bridge_last_seen_v1')
    with closing(sqlite3.connect(database.as_uri()+'?mode=rw',uri=True,timeout=2)) as connection:
        with connection:
            cursor=connection.executemany('DELETE FROM settings WHERE key=?',[(key,) for key in keys])
            return cursor.rowcount>0


def _plain(path):
    info=path.lstat()
    return not (stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400)


def _owned_tree(root, target, files, directories, marker):
    if not target.exists():return False
    if not target.is_dir() or any(not _plain(p) for p in [*reversed(target.parents),target] if p.exists()):return False
    if not target.resolve(strict=True).is_relative_to(root.resolve(strict=True)):return False
    try:
        def fail(error):raise error
        for folder,dirs,names in os.walk(target,followlinks=False,onerror=fail):
            for name in dirs+names:
                p=Path(folder)/name
                if not _plain(p):return False
                relative=p.relative_to(target).as_posix()
                if relative not in (directories if name in dirs else files):return False
        manifest=target/marker
        if not manifest.is_file() or manifest.stat().st_size>65536:return False
        if json.loads(manifest.read_text(encoding='utf-8')).get('name')!='opencore-bridge':return False
    except (OSError,ValueError,TypeError,AttributeError):return False
    return True


def retire_legacy_files(app_data):
    """Remove only the verified old OpenCore-owned bridge trees, never chats or other plugins."""
    root=Path(app_data).absolute()
    if not root.is_dir():return False
    direct=root/'claude-bridge';market=root/'claude-marketplace'
    changed=False
    if _owned_tree(root,direct,_BRIDGE_FILES,_BRIDGE_DIRS,'.claude-plugin/plugin.json'):
        shutil.rmtree(direct);changed=True
    files={'.claude-plugin/marketplace.json'}|{'plugins/opencore-bridge/'+p for p in _BRIDGE_FILES}
    dirs={'.claude-plugin','plugins','plugins/opencore-bridge'}|{'plugins/opencore-bridge/'+p for p in _BRIDGE_DIRS}
    if _owned_tree(root,market,files,dirs,'plugins/opencore-bridge/.claude-plugin/plugin.json'):
        try:
            manifest=market/'.claude-plugin/marketplace.json'
            if manifest.stat().st_size>65536:raise ValueError('Marketplace manifest too large')
            metadata=json.loads(manifest.read_text(encoding='utf-8'))
            owned=metadata.get('name')=='opencore-local' and all(p.get('name')=='opencore-bridge' for p in metadata.get('plugins',[]))
        except (OSError,ValueError,AttributeError,TypeError):owned=False
        if owned:shutil.rmtree(market);changed=True
    return changed


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
