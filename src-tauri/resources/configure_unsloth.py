"""Install or refresh the local OpenCore gateway provider in Unsloth Studio.

Runs with Unsloth's own Python so schema and storage-path decisions remain
owned by the installed Unsloth version. No credential is stored or required:
the gateway listens on localhost only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: configure_unsloth.py OPENCORE_RUNTIME GATEWAY_URL")
    runtime = Path(sys.argv[1]).resolve(strict=True)
    gateway = sys.argv[2].rstrip("/")
    backend = Path(sys.prefix) / "Lib" / "site-packages" / "studio" / "backend"
    if not backend.is_dir():
        raise RuntimeError(f"Unsloth backend not found under {sys.prefix}")
    sys.path.insert(0, str(backend))

    from storage import providers_db
    from utils.llama_cpp_path_settings import set_custom_llama_cpp_path

    set_custom_llama_cpp_path(str(runtime))
    existing = next(
        (row for row in providers_db.list_providers() if row["id"] == "opencore-control"),
        None,
    )
    values = dict(
        display_name="OpenCore ECHO",
        base_url=gateway,
        is_enabled=True,
        models=["OpenCore-Code-Single-File.gguf"],
        available_models=["OpenCore-Code-Single-File.gguf"],
        max_output_tokens=None,
    )
    if existing:
        providers_db.update_provider("opencore-control", **values)
        action = "updated"
    else:
        values.pop("is_enabled")
        providers_db.create_provider(
            id="opencore-control",
            provider_type="custom",
            **values,
        )
        action = "created"
    print(json.dumps({"ok": True, "action": action, "gateway": gateway, "runtime": str(runtime)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
