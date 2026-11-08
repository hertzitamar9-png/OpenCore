"""Build OpenCore's private Windows agent from its checked-in source snapshot."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen


HERE = Path(__file__).resolve().parent / "agent-engine"
REVISION = "a956835d020762cb2b570053af06f643a11c0ecc"
VERSION = "0.160.0"
SOURCE_SHA256 = "08ab34458b438921f7d91e8dae0dc19b03c2f068d540324771000ee6792fb94e"
TOOLCHAIN = "1.95.0"
TARGET = "x86_64-pc-windows-msvc"
V8_VERSION = "150.4.0"
BINARIES = {
    "codex-app-server": "OpenCore.Agent.exe",
    "codex-code-mode-host": "codex-code-mode-host.exe",
    "codex-command-runner": "codex-command-runner.exe",
    "codex-windows-sandbox-setup": "codex-windows-sandbox-setup.exe",
    "codex-windows-sandbox-service": "codex-windows-sandbox-service.exe",
}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url, path, expected_hash):
    if path.is_file() and sha256(path) == expected_hash:
        return
    with urlopen(url, timeout=90) as response, path.open("wb") as output:
        shutil.copyfileobj(response, output)
    if sha256(path) != expected_hash:
        raise ValueError(f"Unexpected build input checksum for {path.name}")


def v8_build_inputs(source, cache):
    profile = "ptrcomp_sandbox_release"
    stem = f"rusty_v8_{profile}_{TARGET}"
    archive = f"{stem}.lib.gz"
    binding = f"src_binding_{profile}_{TARGET}.rs"
    manifest_name = f"{stem}.sha256"
    manifests = source / "third_party/v8" / f"rusty_v8_{V8_VERSION.replace('.', '_')}_release_manifests.sha256"
    checksums = {name: digest for digest, name in map(str.split, manifests.read_text().splitlines())}
    url = f"https://github.com/openai/codex/releases/download/rusty-v8-v{V8_VERSION}/"
    manifest = cache / manifest_name
    download(url + manifest_name, manifest, checksums[manifest_name])
    inputs = {name: digest for digest, name in map(str.split, manifest.read_text().splitlines())}
    if inputs.keys() != {archive, binding}:
        raise ValueError("Unexpected Windows V8 input list")
    for name, digest in inputs.items():
        download(url + name, cache / name, digest)
    return {"RUSTY_V8_ARCHIVE": str(cache / archive), "RUSTY_V8_SRC_BINDING_PATH": str(cache / binding)}


def prepare_source(source):
    archive = HERE / f"upstream-{VERSION}.tar.gz"
    if sha256(archive) != SOURCE_SHA256:
        raise ValueError("The OpenCore agent source archive failed verification")
    with tarfile.open(archive) as snapshot:
        snapshot.extractall(source, filter="data")
    shutil.copy2(HERE / "main.rs", source / "codex-rs/app-server/src/main.rs")
    subprocess.run(["git", "apply", str(HERE / "opencore.patch")], cwd=source, check=True)
    # The upstream release tag bumps the workspace version but leaves the
    # path-only lock entries at 0.0.0. Registry versions and hashes stay pinned.
    lock = source / "codex-rs/Cargo.lock"
    original = lock.read_text(encoding="utf-8")
    fixed, count = re.subn(r'(\[\[package\]\]\nname = "[^"]+"\nversion = )"0\.0\.0"(?=\n(?:dependencies =|\n))',
                           rf'\g<1>"{VERSION}"', original)
    if count != 159:
        raise ValueError(f"Unexpected number of unversioned workspace crates: {count}")
    lock.write_text(fixed, encoding="utf-8", newline="\n")


def build(output, cache, jobs):
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="source-", dir=cache) as scratch:
        source = Path(scratch)
        prepare_source(source)
        environment = dict(os.environ, **v8_build_inputs(source, cache))
        target_dir = cache / "target"
        environment.update(STABLE_GIT_COMMIT=REVISION, CARGO_TARGET_DIR=str(target_dir),
                           LIBSQLITE3_FLAGS="SQLITE_DISABLE_INTRINSIC",
                           CARGO_PROFILE_RELEASE_DEBUG="0", CARGO_PROFILE_RELEASE_LTO="off",
                           CARGO_PROFILE_RELEASE_CODEGEN_UNITS="16")
        options = [option for name in BINARIES for option in ("--bin", name)]
        subprocess.run(["cargo", f"+{TOOLCHAIN}", "build", "--locked", "--release",
                        "--target", TARGET, "--jobs", str(jobs), *options],
                       cwd=source / "codex-rs", env=environment, check=True)
        for name, executable in BINARIES.items():
            shutil.copy2(target_dir / TARGET / "release" / f"{name}.exe", output / executable)
        for license_file in ("LICENSE", "NOTICE"):
            shutil.copy2(source / license_file, output / license_file)
    receipt = {"sourceRevision": REVISION, "version": VERSION,
               "sha256": {name: sha256(output / name) for name in [*BINARIES.values(), "LICENSE", "NOTICE"]}}
    (output / "engine-build.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def stage_windows_agent(app, binaries):
    receipt = json.loads((binaries / "engine-build.json").read_text(encoding="utf-8"))
    expected_files = {*BINARIES.values(), "LICENSE", "NOTICE"}
    if (receipt.get("sourceRevision") != REVISION or receipt.get("version") != VERSION
            or set(receipt.get("sha256", {})) != expected_files):
        raise ValueError("The OpenCore agent build receipt does not match the pinned sources")
    for name, digest in receipt["sha256"].items():
        if sha256(binaries / name) != digest:
            raise ValueError(f"The OpenCore agent file {name} failed build verification")

    # The frozen native host locates the private agent at this legacy path.
    runtime = app / "codex"
    vendor = runtime / "node_modules/@openai/codex-win32-x64/vendor" / TARGET
    entrypoint = vendor / "bin/codex.exe"
    metadata = json.loads((vendor / "codex-package.json").read_text(encoding="utf-8"))
    if metadata.get("version") != VERSION or metadata.get("entrypoint") != "bin/codex.exe":
        raise ValueError("Unexpected private agent layout in the native baseline")

    targets = {"OpenCore.Agent.exe": entrypoint,
               "codex-code-mode-host.exe": vendor / "bin/codex-code-mode-host.exe"}
    targets.update({name: vendor / "codex-resources" / name for name in BINARIES.values() if name not in targets})
    targets.update({"LICENSE": runtime / "OpenCore.Agent.LICENSE.txt", "NOTICE": runtime / "OpenCore.Agent.NOTICE.txt",
                    "engine-build.json": runtime / "engine-build.json"})
    for name, target in targets.items():
        if not target.resolve().is_relative_to(app.resolve()):
            raise ValueError("The private agent staging path leaves the installer root")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binaries / name, target)
    return entrypoint


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    build(args.output.resolve(), args.cache.resolve(), args.jobs)
