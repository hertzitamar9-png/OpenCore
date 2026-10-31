"""Verify an isolated /TEST extraction against the release's allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    failures = []
    expected = set()
    checked_bytes = 0
    for entry in manifest["files"]:
        prefix, relative = entry["path"].split("/", 1)
        if prefix == "app":
            target = root / relative
        elif prefix == "python":
            target = root / "test-python" / relative
        else:
            raise ValueError("Unknown manifest section: " + prefix)
        target = target.resolve()
        if not target.is_relative_to(root):
            raise ValueError("Manifest path leaves extraction root")
        expected.add(target)
        if not target.is_file():
            failures.append({"path": entry["path"], "error": "missing"})
            continue
        actual_size = target.stat().st_size
        if actual_size != entry["bytes"] or sha256(target) != entry["sha256"]:
            failures.append({"path": entry["path"], "error": "content mismatch"})
        checked_bytes += actual_size

    repair_path = root / "build-info.json"
    repair = json.loads(repair_path.read_text(encoding="utf-8"))
    expected.add(repair_path)
    if repair["build"] != manifest["build"] or repair["appExeSha256"] != manifest["appExeSha256"]:
        failures.append({"path": "build-repair.json", "error": "build mismatch"})
    receipt_path = root / 'installed-file-manifest.json'
    expected.add(receipt_path)
    try:
        receipt = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
        if (receipt['schema'], receipt['version'], receipt['commit'], receipt['files']) != (1, manifest['build'], manifest['commit'], manifest['files']):
            failures.append({'path': 'installed-file-manifest.json', 'error': 'install record mismatch'})
    except (OSError, ValueError, KeyError):
        failures.append({'path': 'installed-file-manifest.json', 'error': 'missing or invalid install record'})
    extras = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and p.resolve() not in expected)
    if extras:
        failures.append({"error": "unexpected extracted files", "files": extras})

    python = root / "test-python" / "python.exe"
    smoke = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import ssl,sqlite3,venv,ensurepip,ctypes,http.server,json,sys; "
         "print(json.dumps({'version':sys.version.split()[0],'pip':ensurepip.version(),'ssl':ssl.OPENSSL_VERSION}))"],
        check=False, capture_output=True, text=True, timeout=30,
    )
    if smoke.returncode:
        failures.append({"error": "packaged Python smoke failed", "stderr": smoke.stderr})
    result = {
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "build": manifest["build"],
        "isolatedExtraction": str(root),
        "matchedFiles": len(manifest["files"]) - len(failures),
        "manifestFiles": len(manifest["files"]),
        "checkedBytes": checked_bytes,
        "exactAppSha256": sha256(root / "OpenCore.exe"),
        "pythonSmoke": json.loads(smoke.stdout) if smoke.returncode == 0 else None,
        "personalStateIncluded": manifest["personalStateIncluded"],
        "modelWeightsIncluded": manifest["modelWeightsIncluded"],
        "failures": failures,
        "passed": not failures,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
