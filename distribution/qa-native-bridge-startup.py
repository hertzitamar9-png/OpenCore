"""Start the packaged Windows app on an isolated CI runner and observe its bridge paths."""
import argparse
from contextlib import closing
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time


HISTORY_MARKER = 'OpenCore startup consent fixture'


def isolated_windows_environment(home):
    """Keep Windows known folders and environment paths in one valid profile."""
    home=Path(home).resolve()
    for relative in ('AppData/Roaming','AppData/Local','Desktop','Documents','Downloads','Pictures','Music','Videos'):
        (home/relative).mkdir(parents=True,exist_ok=True)
    drive,tail=os.path.splitdrive(str(home))
    environment=dict(os.environ,USERPROFILE=str(home),HOMEDRIVE=drive,HOMEPATH=tail,
                     APPDATA=str(home/'AppData/Roaming'),LOCALAPPDATA=str(home/'AppData/Local'),
                     CLAUDE_CONFIG_DIR=str(home/'.claude'),CODEX_HOME=str(home/'.codex'))
    environment.pop('OPENCORE_DESKTOP_RELAUNCH',None)
    return environment


def source_inventory(home):
    home = Path(home)
    return {path.relative_to(home).as_posix(): path.read_bytes()
            for name in ('.claude', '.codex') for path in (home/name).rglob('*') if path.is_file()}


def seed_external_sources(home):
    """Create valid synthetic client transcripts in a disposable test home."""
    home = Path(home)
    project = home/'consent-project'
    project.mkdir(parents=True)
    claude = [
        {'type': 'user', 'timestamp': '2026-10-01T00:00:00Z', 'cwd': str(project),
         'message': {'role': 'user', 'content': HISTORY_MARKER+' Claude user'}},
        {'type': 'assistant', 'timestamp': '2026-10-01T00:00:01Z',
         'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': HISTORY_MARKER+' Claude reply'}]}}]
    codex = [
        {'type': 'session_meta', 'payload': {'id': 'codex-consent', 'cwd': str(project)}},
        {'timestamp': '2026-10-01T00:00:00Z', 'type': 'response_item',
         'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': HISTORY_MARKER+' Codex user'}]}},
        {'timestamp': '2026-10-01T00:00:01Z', 'type': 'response_item',
         'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': HISTORY_MARKER+' Codex reply'}]}}]
    files = {
        '.claude/projects/consent-fixture/claude-consent.jsonl': ''.join(json.dumps(row)+'\n' for row in claude),
        '.codex/sessions/2026/10/01/codex-consent.jsonl': ''.join(json.dumps(row)+'\n' for row in codex),
        '.claude/settings.json': json.dumps({'enabledPlugins': {'unrelated@publisher': True}}),
        '.claude/plugins/installed_plugins.json': json.dumps({'version': 2, 'plugins': {'unrelated@publisher': [
            {'scope': 'user', 'installPath': str(home/'.claude/plugins/cache/publisher/unrelated/1.0.0'), 'version': '1.0.0'}]}}),
        '.codex/config.toml': 'model="cloud-model"\n',
        '.codex/auth.json': '{"syntheticCredential":"preserve-fixture"}',
        '.claude/plugins/cache/publisher/unrelated/1.0.0/.claude-plugin/plugin.json':
            json.dumps({'name': 'unrelated', 'version': '1.0.0'}),
        '.claude/plugins/cache/publisher/unrelated/1.0.0/skills/consent-fixture/SKILL.md':
            '---\nname: consent-fixture\ndescription: Optional plugin must not be imported automatically.\n---\n'+HISTORY_MARKER,
    }
    for relative, content in files.items():
        path = home/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    return source_inventory(home)


def assert_sources_preserved(home, before):
    if source_inventory(home) != before:
        raise RuntimeError('Startup changed external client files or created a client profile')


def assert_no_automatic_history(database):
    with closing(sqlite3.connect(Path(database).as_uri()+'?mode=ro', uri=True, timeout=5)) as connection:
        imports = connection.execute("SELECT count(*) FROM conversations WHERE lower(client) LIKE '%claude%' OR lower(client) LIKE '%codex%' OR id LIKE 'claude:%' OR id LIKE 'codex:%'").fetchone()[0]
        copied_content = connection.execute('SELECT count(*) FROM timeline WHERE instr(content,?)>0', (HISTORY_MARKER,)).fetchone()[0]
        syncs = connection.execute("SELECT count(*) FROM operations WHERE kind='history_sync'").fetchone()[0]
    if imports or copied_content:
        raise RuntimeError('Startup imported external history without an explicit import action')
    if syncs:
        raise RuntimeError('Startup began a history sync without an explicit import action')


def wait_for_relaunched_app(process, matching, read_log, timeout=60):
    """The initial process exits successfully after launching a shell-owned child."""
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        code=process.poll()
        if code is not None and code!=0:
            raise RuntimeError(f'The packaged app launcher failed: {code}')
        children=set(matching())-{process.pid}
        if children and '[setup] Tauri setup completed' in read_log():
            return children
        time.sleep(.25)
    raise RuntimeError('The relaunched packaged app did not report completed startup')


def _windows_processes(exe, terminate=False):
    """Find or terminate only processes whose opened executable matches this test."""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    class Entry(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('usage',wintypes.DWORD),('pid',wintypes.DWORD),
                  ('heap',ctypes.c_size_t),('module',wintypes.DWORD),('threads',wintypes.DWORD),
                  ('parent',wintypes.DWORD),('priority',wintypes.LONG),('flags',wintypes.DWORD),
                  ('name',wintypes.WCHAR*260)]
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
    if snapshot==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    expected=os.path.normcase(os.path.abspath(exe));found=set()
    try:
        entry=Entry();entry.size=ctypes.sizeof(Entry)
        ok=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while ok:
            if entry.name.casefold()==exe.name.casefold():
                process=kernel.OpenProcess(0x1000 | (1 if terminate else 0),False,entry.pid)
                if process:
                    try:
                        size=wintypes.DWORD(32768);path=ctypes.create_unicode_buffer(size.value)
                        if (kernel.QueryFullProcessImageNameW(process,0,path,ctypes.byref(size)) and
                                os.path.normcase(os.path.abspath(path.value))==expected):
                            found.add(entry.pid)
                            if terminate and not kernel.TerminateProcess(process,0):raise ctypes.WinError(ctypes.get_last_error())
                    finally:kernel.CloseHandle(process)
            ok=kernel.Process32NextW(snapshot,ctypes.byref(entry))
    finally:kernel.CloseHandle(snapshot)
    return found


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if os.name!='nt' or os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('This fresh-profile launch check runs only on an isolated Windows GitHub runner')
    exe=(args.root/'OpenCore.exe').resolve(strict=True)
    if _windows_processes(exe):raise RuntimeError('The test executable is already running')
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    with tempfile.TemporaryDirectory(prefix='opencore-bridge-policy-',ignore_cleanup_errors=True) as directory:
        home=Path(directory)/'home'
        if not home.resolve().is_relative_to(Path(directory).resolve()):raise RuntimeError('Invalid isolated profile')
        env=isolated_windows_environment(home)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))
            debug_port=listener.getsockname()[1]
        env.update(OPENCORE_HOME=str(home/'OpenCore'),
                   WEBVIEW2_USER_DATA_FOLDER=str(Path(directory)/'webview'),
                   WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f'--remote-debugging-port={debug_port}')
        app_data=Path(env['APPDATA'])/'ai.opencore.control-center'
        database=app_data/'control-center.sqlite3'
        bridge_paths=[app_data/'claude-bridge',app_data/'claude-marketplace']
        if database.exists() or any(p.exists() for p in bridge_paths):raise RuntimeError('The test profile is not fresh')
        log=Path(env['LOCALAPPDATA'])/'OpenCore/logs/startup.log'
        offset=log.stat().st_size if log.exists() else 0
        source_files=seed_external_sources(home)
        config=home/'.claude'
        settings=config/'settings.json';baseline=json.loads(settings.read_text(encoding='utf-8'))
        process=subprocess.Popen([str(exe)],cwd=str(exe.parent),env=env,startupinfo=startup,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            def new_log():
                if log.exists():
                    with log.open('rb') as f:f.seek(offset);new=f.read().decode('utf-8','replace')
                    return new
                return ''
            children=wait_for_relaunched_app(process,lambda:_windows_processes(exe),new_log)
            time.sleep(6)
            if not children.intersection(_windows_processes(exe)):
                raise RuntimeError('The relaunched app exited during the bridge observation')
            if any(p.exists() for p in bridge_paths):raise RuntimeError('Startup created legacy Claude bridge files')
            if not database.is_file():raise RuntimeError('The fresh app database was not created at the expected location')
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5)) as connection:
                count=connection.execute("SELECT count(*) FROM settings WHERE key='claude_bridge_token_v1'").fetchone()[0]
            if count:raise RuntimeError('Startup created a legacy Claude bridge token')
            if json.loads(settings.read_text(encoding='utf-8'))!=baseline:raise RuntimeError('Startup changed the unrelated Claude setting')
            assert_no_automatic_history(database)
            assert_sources_preserved(home,source_files)
            removal_check=Path(__file__).with_name('qa-native-model-removal.cjs').resolve()
            removal_result=json.loads(subprocess.check_output(
                ['node',str(removal_check),str(debug_port),str(home)],text=True,timeout=80))
        except Exception:
            print(json.dumps({'launcherExit':process.poll(),'matchingProcesses':sorted(_windows_processes(exe)),
                              'startupLog':new_log()[-4000:]}),flush=True)
            raise
        finally:
            if process.poll() is None:process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
            _windows_processes(exe,terminate=True)
    with exe.open('rb') as source:app_hash=hashlib.file_digest(source,'sha256').hexdigest()
    result={'passed':True,'freshProfile':True,'appStartupObserved':True,'automaticClaudeBridgeCreated':False,'appExeSha256':app_hash,
            'bridgeTokenPresent':False,'externalPluginRegistryCreated':False,'unrelatedSettingPreserved':True,
            'syntheticClaudeAndCodexHistoriesPresent':True,'externalHistoryImported':False,
            'automaticHistorySyncStarted':False,'externalProfilesAndCredentialsPreserved':True,
            'modelRemoval':removal_result}
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
