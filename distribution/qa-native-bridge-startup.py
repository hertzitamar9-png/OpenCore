"""Start the packaged Windows app on an isolated CI runner and observe its bridge paths."""
import argparse
from contextlib import closing
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time


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
    app_data=Path(os.environ['APPDATA'])/'ai.opencore.control-center'
    bridge_paths=[app_data/'claude-bridge',app_data/'claude-marketplace']
    if any(p.exists() for p in bridge_paths):raise RuntimeError('The test runner is not a fresh OpenCore profile')
    if _windows_processes(exe):raise RuntimeError('The test executable is already running')
    log=Path(os.environ['LOCALAPPDATA'])/'OpenCore/logs/startup.log'
    offset=log.stat().st_size if log.exists() else 0
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    with tempfile.TemporaryDirectory(prefix='opencore-bridge-policy-') as directory:
        config=Path(directory)/'claude';config.mkdir()
        settings=config/'settings.json';baseline={'enabledPlugins':{'unrelated@publisher':True}}
        settings.write_text(json.dumps(baseline),encoding='utf-8')
        env=dict(os.environ,CLAUDE_CONFIG_DIR=str(config));env.pop('OPENCORE_DESKTOP_RELAUNCH',None)
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
            database=app_data/'control-center.sqlite3'
            if not database.is_file():raise RuntimeError('The fresh app database was not created at the expected location')
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5)) as connection:
                count=connection.execute("SELECT count(*) FROM settings WHERE key='claude_bridge_token_v1'").fetchone()[0]
            if count:raise RuntimeError('Startup created a legacy Claude bridge token')
            if json.loads(settings.read_text(encoding='utf-8'))!=baseline:raise RuntimeError('Startup changed the unrelated Claude setting')
            if (config/'plugins').exists():raise RuntimeError('Startup created an external Claude plugin registry')
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
            'bridgeTokenPresent':False,'externalPluginRegistryCreated':False,'unrelatedSettingPreserved':True}
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
