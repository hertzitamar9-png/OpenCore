"""Start the packaged Windows app on an isolated CI runner and observe its bridge paths."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time


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
    log=Path(os.environ['LOCALAPPDATA'])/'OpenCore/logs/startup.log'
    offset=log.stat().st_size if log.exists() else 0
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    with tempfile.TemporaryDirectory(prefix='opencore-bridge-policy-') as directory:
        config=Path(directory)/'claude';config.mkdir()
        settings=config/'settings.json';baseline={'enabledPlugins':{'unrelated@publisher':True}}
        settings.write_text(json.dumps(baseline),encoding='utf-8')
        env=dict(os.environ,CLAUDE_CONFIG_DIR=str(config))
        process=subprocess.Popen([str(exe)],cwd=str(exe.parent),env=env,startupinfo=startup,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline=time.monotonic()+60;ready=False
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError(f'The packaged app exited before startup: {process.returncode}')
                if log.exists():
                    with log.open('rb') as f:f.seek(offset);new=f.read().decode('utf-8','replace')
                    if '[setup] Tauri setup completed' in new:ready=True;break
                time.sleep(.25)
            if not ready:raise RuntimeError('The packaged app did not report completed startup')
            time.sleep(6)
            if process.poll() is not None:raise RuntimeError('The packaged app exited during the bridge observation')
            if any(p.exists() for p in bridge_paths):raise RuntimeError('Startup created legacy Claude bridge files')
            database=app_data/'control-center.sqlite3'
            if not database.is_file():raise RuntimeError('The fresh app database was not created at the expected location')
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5)) as connection:
                count=connection.execute("SELECT count(*) FROM settings WHERE key='claude_bridge_token_v1'").fetchone()[0]
            if count:raise RuntimeError('Startup created a legacy Claude bridge token')
            if json.loads(settings.read_text(encoding='utf-8'))!=baseline:raise RuntimeError('Startup changed the unrelated Claude setting')
            if (config/'plugins').exists():raise RuntimeError('Startup created an external Claude plugin registry')
        finally:
            if process.poll() is None:process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
    with exe.open('rb') as source:app_hash=hashlib.file_digest(source,'sha256').hexdigest()
    result={'passed':True,'freshProfile':True,'appStartupObserved':True,'automaticClaudeBridgeCreated':False,'appExeSha256':app_hash,
            'bridgeTokenPresent':False,'externalPluginRegistryCreated':False,'unrelatedSettingPreserved':True}
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
