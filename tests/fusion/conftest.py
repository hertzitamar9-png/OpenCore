"""Keep small ephemeral test artifacts independent of host free disk space."""

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def temporary_fusion_outputs_have_test_capacity(monkeypatch, tmp_path):
    """Virtualize disk capacity only below pytest's disposable temp directory."""
    import shutil

    real_disk_usage = shutil.disk_usage
    temp_root = tmp_path.resolve()

    def disk_usage(path):
        usage = real_disk_usage(path)
        resolved = Path(path).resolve()
        if resolved == temp_root or temp_root in resolved.parents:
            return SimpleNamespace(total=usage.total, used=usage.used, free=250_000_000_000)
        return usage

    monkeypatch.setattr(shutil, "disk_usage", disk_usage)
