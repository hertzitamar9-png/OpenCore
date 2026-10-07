"""Managed setup runner. Import/probe never installs or downloads anything.

The desktop owns durable job admission and cancellation. All package mutations
target an app-owned environment; discovered interpreters are bootstrap sources.
"""
import argparse
from collections import deque
import ctypes
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
from queue import Empty, Queue
import re
import shutil
import signal
import subprocess
import sys
from threading import Thread
import time
import urllib.request
import zipfile

MANIFEST = json.loads(Path(__file__).with_name("recipes.json").read_text(encoding="utf-8"))


class Cancelled(RuntimeError):
    pass


class WindowsJob:
    """A setup command and its descendants stop together, including pip workers."""
    def __init__(self, process):
        from ctypes import wintypes
        class BasicLimits(ctypes.Structure):
            _fields_ = [("processTime", ctypes.c_longlong), ("jobTime", ctypes.c_longlong), ("flags", wintypes.DWORD),
                        ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t), ("processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("readOperations", "writeOperations", "otherOperations", "readBytes", "writeBytes", "otherBytes")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters), ("processMemory", ctypes.c_size_t),
                        ("jobMemory", ctypes.c_size_t), ("peakProcess", ctypes.c_size_t), ("peakJob", ctypes.c_size_t)]
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            error = ctypes.get_last_error()
            self.close()
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError(f"Cannot establish cancellable setup process ownership (Windows error {error})")

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def stop(self):
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)


def emit(stage, **values):
    print(json.dumps({"stage": stage, **values}, ensure_ascii=True), flush=True)


def recipe_for(target):
    recipe = next((item for item in MANIFEST["recipes"] if target in item["modelIds"]), None)
    if recipe is None:
        return {"supported": False, "reason": "Automatic setup has no verified publisher-compatible worker recipe for this architecture. Connect the publisher runtime explicitly."}
    return {"supported": True, **recipe}


def recipe_fingerprint(recipe):
    return hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, indent=2, ensure_ascii=True)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def read_receipt(path, recipe):
    try:
        receipt = json.loads(Path(path).read_text(encoding="utf-8"))
        if receipt.get("schema") != 1 or receipt.get("recipeFingerprint") != recipe_fingerprint(recipe):
            return None
        if not receipt.get("dependenciesVerified"):
            return None
        executable = receipt.get("python") or receipt.get("executable")
        if not executable or not Path(executable).is_file():
            return None
        return receipt
    except (OSError, ValueError, TypeError):
        return None


class Runner:
    def __init__(self, cancel_file=None, reporter=emit):
        self.cancel_file = Path(cancel_file) if cancel_file else None
        self.reporter = reporter

    def check_cancel(self):
        if self.cancel_file and self.cancel_file.exists():
            raise Cancelled("Setup cancelled; completed downloads and the managed environment are retained for retry.")

    def run(self, args, *, env=None, timeout=1800, input_text=None, check=True, allow_cancel=True):
        if allow_cancel:
            self.check_cancel()
        options = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "stdin": subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                   "text": True, "encoding": "utf-8", "errors": "replace", "env": env}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        process = subprocess.Popen([str(value) for value in args], **options)
        job = WindowsJob(process) if os.name == "nt" else None
        lines, queue = deque(maxlen=100), Queue()

        def consume():
            try:
                for line in process.stdout:
                    queue.put(line.rstrip()[-4096:])
            finally:
                queue.put(None)

        reader = Thread(target=consume, daemon=True)
        reader.start()
        if input_text is not None:
            try:
                process.stdin.write(input_text)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        started, finished = time.monotonic(), False
        try:
            while not finished or process.poll() is None:
                if allow_cancel:
                    self.check_cancel()
                if time.monotonic() - started > timeout:
                    raise RuntimeError(f"Setup command exceeded {timeout} seconds")
                try:
                    line = queue.get(timeout=0.1)
                except Empty:
                    continue
                if line is None:
                    finished = True
                else:
                    lines.append(line)
                    self.reporter("diagnostic", detail=line)
            code = process.wait()
        except BaseException:
            if job:
                job.stop()
            self.stop_tree(process)
            reader.join(timeout=2)
            raise
        finally:
            process.stdout.close()
            if job:
                job.close()
        output = "\n".join(lines)[-16000:]
        if code and check:
            raise RuntimeError(f"Setup command exited with {code}: {output}")
        return subprocess.CompletedProcess(args, code, output, "")

    @staticmethod
    def stop_tree(process):
        if process.poll() is not None:
            return
        if os.name == "nt":
            # The owning Job Object has already terminated descendants.
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def python_in(environment):
    environment = Path(environment)
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def create_environment(root, recipe_id, python, runner):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", recipe_id):
        raise ValueError("Invalid managed environment identifier")
    environment = Path(root).resolve() / "runtime-setup" / "environments" / recipe_id
    target = python_in(environment)
    if not target.is_file():
        runner.reporter("creating-environment", detail=str(environment))
        runner.run([python, "-m", "venv", str(environment)])
    # Existing environments must still be isolated; never install into a base Python.
    info = runner.run([target, "-I", "-c", "import json,sys;print(json.dumps({'prefix':sys.prefix,'base':sys.base_prefix}))"])
    value = json.loads(info.stdout.splitlines()[-1])
    if Path(value["prefix"]).resolve() != environment or value["prefix"] == value["base"]:
        raise RuntimeError("The managed environment resolves to an external Python installation")
    return target


def verify_environment(python, packages, imports, runner, require_cuda=False):
    code = """
import importlib,importlib.metadata,json,sys
packages,imports,require_cuda=json.loads(sys.argv[1])
versions={}
for name,want in packages.items():
    actual=importlib.metadata.version(name)
    if actual.split('+')[0]!=want:
        raise RuntimeError(f'{name} version {actual} does not match pinned version {want}')
    versions[name]=actual
for module,names in imports.items():
    loaded=importlib.import_module(module)
    for name in names:
        getattr(loaded,name)
hardware={}
if 'torch' in packages:
    import torch
    hardware={'torch':torch.__version__,'cudaBuild':torch.version.cuda,'cudaAvailable':torch.cuda.is_available()}
    if require_cuda and not torch.cuda.is_available():
        raise RuntimeError('A working NVIDIA CUDA driver/GPU is required by this worker; no model inference was verified')
    if torch.cuda.is_available():
        tensor=torch.ones((8,8),device='cuda',dtype=torch.float16)
        value=(tensor@tensor).sum().item()
        torch.cuda.synchronize()
        if value!=512.0: raise RuntimeError('CUDA kernel verification failed')
        hardware.update(device=torch.cuda.get_device_name(),bf16Supported=torch.cuda.is_bf16_supported(),kernelVerified=True)
        del tensor
        torch.cuda.empty_cache()
print(json.dumps({'packages':versions,'hardware':hardware,'dependenciesVerified':True,'inferenceVerified':False}))
"""
    result = runner.run([python, "-I", "-c", code, json.dumps([packages, imports, require_cuda])], timeout=120)
    return json.loads(result.stdout.splitlines()[-1])


def extract_zip(archive, target):
    target = Path(target).resolve()
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if sum(entry.file_size for entry in entries) > 2_000_000_000:
            raise ValueError("SDK archive expands beyond its allowed size")
        for entry in entries:
            destination = (target / entry.filename).resolve()
            if not destination.is_relative_to(target) or "\\" in entry.filename or ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("Archive entry points outside the managed directory")
        source.extractall(target)


def download(url, target, sha256, runner, max_bytes=500_000_000):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and file_hash(target) == sha256:
        return target
    temporary = target.with_name(target.name + ".partial")
    runner.reporter("downloading-tool", detail=url)
    try:
        with urllib.request.urlopen(url, timeout=30) as source, temporary.open("wb") as output:
            total = int(source.headers.get("Content-Length", 0))
            if total > max_bytes:
                raise RuntimeError("Tool download exceeds the pinned recipe size limit")
            received, digest = 0, hashlib.sha256()
            while True:
                runner.check_cancel()
                block = source.read(1024 * 1024)
                if not block:
                    break
                received += len(block)
                if received > max_bytes:
                    raise RuntimeError("Tool download exceeds the pinned recipe size limit")
                digest.update(block)
                output.write(block)
                runner.reporter("downloading-tool", downloadedBytes=received, totalBytes=total)
            if digest.hexdigest() != sha256:
                raise RuntimeError("Tool download failed SHA-256 verification")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def memory_total():
    if os.name == "nt":
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), *[(name, ctypes.c_ulonglong) for name in ("total", "available", "page", "pageFree", "virtual", "virtualFree", "extended")]]
        value = Memory()
        value.length = ctypes.sizeof(value)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            return value.total
        return None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def probe(root):
    root = Path(root).resolve()
    candidates = [sys.executable, os.environ.get("OPENCORE_PYTHON"), os.environ.get("OPENCORE_SPEECH_TORCH_PYTHON")]
    for variable in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        if os.environ.get(variable):
            prefix = Path(os.environ[variable])
            candidates.extend([str(python_in(prefix)), str(prefix / "python.exe")])
    candidates.extend(shutil.which(name) for name in ("python", "python3"))
    programs = Path(os.environ.get("LOCALAPPDATA", str(root))) / "Programs" / "Python"
    candidates.extend(str(path) for path in programs.glob("Python*/python.exe"))
    candidates.extend(str(path) for path in (root / "runtime-setup" / "environments").glob("*/Scripts/python.exe"))
    candidates.extend(str(path) for path in (root / "speech").glob("*-venv/Scripts/python.exe"))
    quiet = Runner(reporter=lambda *args, **kwargs: None)
    if shutil.which("py"):
        try:
            listing = quiet.run(["py", "-0p"], timeout=8).stdout
            candidates.extend(match.group(1) for line in listing.splitlines() if (match := re.search(r"([A-Za-z]:\\.*python\.exe)\s*$", line)))
        except (OSError, RuntimeError):
            pass
    interpreters, seen = [], set()
    for candidate in candidates:
        if not candidate or str(candidate).casefold() in seen:
            continue
        seen.add(str(candidate).casefold())
        try:
            result = quiet.run([candidate, "-I", "-c", "import ctypes,json,sys,struct,tempfile,venv;print(json.dumps({'path':sys.executable,'version':list(sys.version_info[:3]),'bits':struct.calcsize('P')*8,'prefix':sys.prefix,'base':sys.base_prefix}))"], timeout=6)
            value = json.loads(result.stdout.splitlines()[-1])
            value["compatible"] = value["bits"] == 64 and (3, 10) <= tuple(value["version"][:2]) <= (3, 12)
            value["managed"] = Path(value["path"]).resolve().is_relative_to(root)
            if not any(item["path"].casefold() == value["path"].casefold() for item in interpreters):
                interpreters.append(value)
        except (OSError, RuntimeError, ValueError, IndexError):
            pass
    gpus = []
    if shutil.which("nvidia-smi"):
        try:
            lines = quiet.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"], timeout=10).stdout
            for line in lines.splitlines():
                name, total, free, driver = [part.strip() for part in line.split(",", 3)]
                gpus.append({"name": name, "totalBytes": int(total) * 1024**2, "freeBytes": int(free) * 1024**2, "driver": driver})
        except (OSError, RuntimeError, ValueError):
            pass
    from provision_testing import discover_tools
    return {"platform": sys.platform, "architecture": platform.machine(), "ramBytes": memory_total(),
            "diskFreeBytes": shutil.disk_usage(root).free, "python": interpreters, "gpus": gpus,
            "tools": discover_tools(root, quiet)}


def android_plan(root, accept_licenses):
    if not accept_licenses:
        raise ValueError("Review and accept the Android SDK license before provisioning")
    home = Path(root).resolve() / "runtime-setup" / "android"
    return {"sdkRoot": str(home / "sdk"), "avdHome": str(home / "avd"), "avdName": "OpenCore_API_36",
            "packages": ["platform-tools", "emulator", "platforms;android-36", "system-images;android-36;google_apis;x86_64"],
            "createAvd": ["create", "avd", "--name", "OpenCore_API_36", "--package", "system-images;android-36;google_apis;x86_64", "--path", str(home / "avd" / "OpenCore_API_36.avd")]}


def install(root, resources, target, options, runner):
    root, resources = Path(root).resolve(), Path(resources).resolve()
    recipe = recipe_for(target)
    if not recipe["supported"]:
        raise ValueError(recipe["reason"])
    ram = memory_total()
    if ram and ram < recipe["minimumRamBytes"]:
        raise RuntimeError(f"This recipe needs at least {recipe['minimumRamBytes'] / 1e9:g} GB host RAM; {ram / 1e9:g} GB was detected")
    if shutil.disk_usage(root).free < recipe["minimumDiskBytes"]:
        raise RuntimeError(f"Not enough disk space: dependency/tool setup needs {recipe['minimumDiskBytes'] / 1e9:g} GB free, plus the selected checkpoint")
    if recipe.get("requiresLicenseAcceptance") and not options.get("acceptLicenses"):
        raise ValueError("Review and accept the publisher license before installing this testing environment")
    runner.reporter("checking-hardware", detail="Checking host memory, disk and supported worker requirements")
    kind = recipe["kind"]
    if kind == "studio":
        inventory = probe(root)
        if recipe.get("requiresCuda") and not inventory["gpus"]:
            raise RuntimeError("No NVIDIA GPU/driver was detected. This built-in image worker requires CUDA; setup did not download packages.")
        python = create_environment(root, recipe["id"], sys.executable, runner)
        environment = dict(os.environ, PIP_CONFIG_FILE=os.devnull, PIP_INDEX_URL="https://pypi.org/simple", PIP_EXTRA_INDEX_URL="", PYTHONIOENCODING="utf-8")
        runner.reporter("installing-dependencies", detail="Installing pinned CUDA runtime in the managed environment")
        runner.run([python, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--index-url", recipe["torchIndex"], *[f"{name}=={version}" for name, version in recipe["torchPackages"].items()]], env=environment)
        runner.reporter("installing-dependencies", detail="Installing pinned model worker packages")
        runner.run([python, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", *[f"{name}=={version}" for name, version in recipe["packages"].items()]], env=environment)
        runner.reporter("verifying-runtime", detail="Checking exact versions, worker pipeline imports and CUDA execution")
        verification = verify_environment(python, {**recipe["torchPackages"], **recipe["packages"]}, recipe["imports"], runner, require_cuda=True)
        result = {"python": str(python), "runner": str(resources / "studio" / "asset_worker.py"), **verification}
    elif kind == "speech":
        name = "prepare_phonon_runtime.py" if target == "phonon-2" else "prepare_runtime.py"
        environment = dict(os.environ, PYTHONIOENCODING="utf-8", PIP_CONFIG_FILE=os.devnull, PIP_INDEX_URL="https://pypi.org/simple", PIP_EXTRA_INDEX_URL="")
        if runner.cancel_file:
            environment["OPENCORE_SETUP_CANCEL_FILE"] = str(runner.cancel_file)
        runner.reporter("installing-dependencies", detail="Preparing the isolated speech environment")
        runner.run([sys.executable, resources / "speech" / name, "--root", root / "speech"], env=environment)
        python = python_in(root / "speech" / ("phonon-venv" if target == "phonon-2" else "whisper-venv"))
        imports = {"transformers": ["ParakeetForTDT", "ParakeetTDTConfig", "AutoProcessor"]} if target == "phonon-2" else {"transformers": ["WhisperForConditionalGeneration"]}
        runner.reporter("verifying-runtime", detail="Checking speech worker imports and pinned dependency versions")
        verification = verify_environment(python, recipe["packages"], imports, runner)
        result = {"python": str(python), **verification}
    else:
        from provision_testing import provision_android, provision_virtualbox, provision_vm
        action = {"android": provision_android, "virtualbox": provision_virtualbox, "virtualbox-guest": provision_vm}[kind]
        result = action(root, options, runner)
    runner.check_cancel()
    receipt = {"schema": 1, "targetId": target, "recipeId": recipe["id"], "recipeFingerprint": recipe_fingerprint(recipe),
               "verifiedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sourceUrls": recipe["sourceUrls"],
               "dependenciesVerified": True, "inferenceVerified": False, **result}
    atomic_json(root / "runtime-setup" / "receipts" / f"{target}.json", receipt)
    runner.reporter("dependencies-verified", receipt=receipt, detail="Dependencies verified. A completed model job is required to verify inference.")
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--resources", type=Path)
    parser.add_argument("--target")
    parser.add_argument("--cancel-file", type=Path)
    parser.add_argument("--options-file", type=Path)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    try:
        if args.probe:
            emit("inventory", inventory=probe(args.root))
        else:
            if not args.target or not args.resources:
                parser.error("--target and --resources are required for setup")
            options = json.loads(args.options_file.read_text(encoding="utf-8")) if args.options_file else {}
            install(args.root, args.resources, args.target, options, Runner(args.cancel_file))
        return 0
    except Cancelled as error:
        emit("cancelled", error=str(error))
        return 2
    except Exception as error:
        emit("failed", error=str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
