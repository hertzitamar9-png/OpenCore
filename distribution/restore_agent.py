"""Restore the exact source-built agent from a prior signed OpenCore release.

Only use this recovery path when the complete builder and source tree match
the known release. The downloaded installer must pass the existing updater
signature check. Normal stage_windows_agent receipt checks still run.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from agent_engine import BINARIES, HERE, REVISION, VERSION, sha256

ROOT = HERE.parent.parent
TAG = "app-v0.3.366"
PINNED_BUILDER = "13431a00a7c23ce04ac1705436f9f4de5fd1bacc"
PINNED_SOURCE = "883ee2acaf340e523d5cf3335b93fe46b7bd49fe"

def source_matches(root=ROOT):
    def obj(path):
        return subprocess.check_output(["git","rev-parse","HEAD:"+path],cwd=root,text=True).strip()
    return (obj("distribution/agent_engine.py"),obj("distribution/agent-engine")) == (PINNED_BUILDER,PINNED_SOURCE)

def copy_verified_agent(app,output):
    runtime=app/"codex"
    vendor=runtime/"node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc"
    receipt_path=runtime/"engine-build.json"
    receipt=json.loads(receipt_path.read_text(encoding="utf-8"))
    expected={name:sha256(HERE/name) for name in ("main.rs","opencore.patch","legacy-tools.rs")}
    paths={"OpenCore.Agent.exe":vendor/"bin/codex.exe",
           "codex-code-mode-host.exe":vendor/"bin/codex-code-mode-host.exe",
           "LICENSE":runtime/"OpenCore.Agent.LICENSE.txt",
           "NOTICE":runtime/"OpenCore.Agent.NOTICE.txt"}
    paths.update({name:vendor/"codex-resources"/name for name in BINARIES.values() if name not in paths})
    if (receipt.get("sourceRevision"),receipt.get("version"),receipt.get("openCoreSources")) != (REVISION,VERSION,expected):
        raise ValueError("Released agent source receipt does not match")
    if set(receipt.get("sha256",{})) != set(paths):
        raise ValueError("Released agent receipt has the wrong files")
    for name,path in paths.items():
        if not path.is_file() or sha256(path)!=receipt["sha256"][name]:
            raise ValueError("Released agent failed checksum verification: "+name)
    output.mkdir(parents=True,exist_ok=True)
    for name,path in paths.items():shutil.copy2(path,output/name)
    shutil.copy2(receipt_path,output/"engine-build.json")

def restore(output):
    if not source_matches():
        raise ValueError("Agent source changed; compilation is required")
    with tempfile.TemporaryDirectory(prefix="opencore-signed-agent-") as tmp:
        folder=Path(tmp)
        subprocess.run(["gh","release","download",TAG,"--repo","hertzitamar9-png/OpenCore",
            "--dir",str(folder),"--pattern","OpenCore.Package.exe","--pattern","OpenCore.Package.exe.sig"],check=True)
        exe=folder/"OpenCore.Package.exe"
        subprocess.run(["node",str(ROOT/"distribution/verify-signature.cjs"),str(exe),
            str(folder/"OpenCore.Package.exe.sig"),str(ROOT/"src-tauri/tauri.conf.json")],check=True)
        app=folder/"app"
        subprocess.run([str(exe),"/S","/TEST","/D="+str(app)],check=True,timeout=600,
                       creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        copy_verified_agent(app,output)
    print("Reused the exact signed source-built OpenCore agent",flush=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    restore(args.output.resolve())
