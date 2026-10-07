import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "src-tauri" / "resources" / "configure_unsloth.py"
spec = importlib.util.spec_from_file_location("configure_unsloth", SCRIPT)
configure_unsloth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure_unsloth)


class ConfigureUnslothTests(unittest.TestCase):
    def run_configure(self, existing=False, persist=True):
        providers = [{"id": "opencore-control", "base_url": "http://old.invalid"}] if existing else []
        runtime_paths = []
        database = types.ModuleType("storage.providers_db")
        database.list_providers = lambda: [dict(row) for row in providers]

        def write_provider(provider_id=None, **values):
            if not persist:
                return
            row = {"id": provider_id or values.pop("id"), **values}
            providers[:] = [row]

        database.create_provider = write_provider
        database.update_provider = write_provider
        storage = types.ModuleType("storage")
        storage.providers_db = database
        settings = types.ModuleType("utils.llama_cpp_path_settings")
        settings.set_custom_llama_cpp_path = runtime_paths.append
        modules = {"storage": storage, "storage.providers_db": database, "utils": types.ModuleType("utils"), "utils.llama_cpp_path_settings": settings}
        with tempfile.TemporaryDirectory(prefix="opencore-unsloth-", dir=SCRIPT.parents[2] / "tests") as temp:
            root = Path(temp)
            (root / "Lib" / "site-packages" / "studio" / "backend").mkdir(parents=True)
            runtime = root / "opencore-runtime.exe"
            runtime.write_bytes(b"test-runtime")
            output = io.StringIO()
            with patch.object(sys, "prefix", str(root)), patch.object(sys, "argv", [str(SCRIPT), str(runtime), "http://127.0.0.1:8812/"]), patch.dict(sys.modules, modules), patch.object(sys, "path", list(sys.path)), contextlib.redirect_stdout(output):
                result = configure_unsloth.main()
            self.assertEqual(result, 0)
            self.assertEqual(runtime_paths, [str(runtime.resolve())])
            return json.loads(output.getvalue()), providers

    def test_creates_and_reloads_the_persisted_gateway(self):
        result, providers = self.run_configure()
        self.assertEqual(result["action"], "created")
        self.assertTrue(result["ok"])
        self.assertEqual(providers[0]["base_url"], "http://127.0.0.1:8812")

    def test_updates_an_existing_provider(self):
        result, providers = self.run_configure(existing=True)
        self.assertEqual(result["action"], "updated")
        self.assertEqual(providers[0]["base_url"], "http://127.0.0.1:8812")

    def test_does_not_report_success_when_create_was_not_persisted(self):
        with self.assertRaisesRegex(RuntimeError, "did not persist"):
            self.run_configure(persist=False)

    def test_does_not_report_success_when_update_kept_the_old_gateway(self):
        with self.assertRaisesRegex(RuntimeError, "did not persist"):
            self.run_configure(existing=True, persist=False)


if __name__ == "__main__":
    unittest.main()
