"""Setup behavior without downloading packages, models or SDKs."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile

import setup_manager as setup


def runner(*args):
    return setup.Runner(*args, reporter=lambda *args, **kwargs: None)


class SetupBehavior(unittest.TestCase):
    def test_recipe_fingerprint_matches_native_canonical_utf8_fixture(self):
        # The same fixture and digest live in runtime_setup.rs.
        recipe = {"supported": True, "nested": {"z": 2, "a": 1}, "label": "café", "id": "fixture"}
        expected = "82eb70d7b4c560b040337f074e68fc522442eab39a71f0f2d51786aa587fe34a"
        self.assertEqual(setup.recipe_fingerprint(recipe), expected)
        self.assertEqual(setup.recipe_fingerprint({key: value for key, value in recipe.items() if key != "supported"}), expected)

    def test_unknown_architecture_cannot_select_a_generic_recipe(self):
        result = setup.recipe_for("flux-2-klein-4b")
        self.assertFalse(result["supported"])
        self.assertIn("publisher-compatible", result["reason"])

    def test_bad_dependency_version_is_not_verified(self):
        with self.assertRaisesRegex(RuntimeError, "pip.*version"):
            setup.verify_environment(sys.executable, {"pip": "0.0.0"}, {}, runner())

    def test_managed_venv_does_not_modify_existing_python(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "existing-environment"
            original.mkdir()
            (original / "keep.txt").write_text("user environment", encoding="utf-8")
            target = setup.create_environment(root, "fixture-v1", sys.executable, runner())
            self.assertTrue(target.is_file())
            info = json.loads(subprocess.check_output([str(target), "-c", "import json,sys;print(json.dumps({'prefix':sys.prefix,'base':sys.base_prefix}))"], text=True))
            self.assertEqual(Path(info["prefix"]).resolve(), (root / "runtime-setup" / "environments" / "fixture-v1").resolve())
            self.assertNotEqual(info["prefix"], info["base"])
            self.assertEqual((original / "keep.txt").read_text(), "user environment")

    def test_cancel_stops_the_running_child_before_returning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "cancel"
            touched = root / "late-write"
            timer = threading.Timer(0.2, lambda: marker.write_text("cancel"))
            timer.start()
            try:
                with self.assertRaisesRegex(setup.Cancelled, "cancelled"):
                    runner(marker).run([sys.executable, "-c", "import time,pathlib,sys; time.sleep(2); pathlib.Path(sys.argv[1]).write_text('running')", str(touched)])
            finally:
                timer.cancel()
            time.sleep(2.1)
            self.assertFalse(touched.exists(), "Cancelled setup left a live subprocess")

    def test_nonzero_exit_retains_bounded_diagnostics(self):
        with self.assertRaisesRegex(RuntimeError, "fixture failure") as error:
            runner().run([sys.executable, "-c", "import sys;print('x'*30000);print('fixture failure');sys.exit(7)"])
        self.assertLess(len(str(error.exception)), 18000)

    def test_archive_path_escape_is_rejected_without_writing_outside_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../escaped.txt", "no")
            with self.assertRaisesRegex(ValueError, "outside"):
                setup.extract_zip(archive, root / "sdk")
            self.assertFalse((root / "escaped.txt").exists())

    def test_receipt_does_not_survive_recipe_or_interpreter_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipe = {"id": "fixture", "packages": {"pip": "1"}}
            python = root / "python.exe"
            python.write_text("fixture")
            receipt = {"schema": 1, "recipeId": "fixture", "recipeFingerprint": setup.recipe_fingerprint(recipe), "python": str(python), "dependenciesVerified": True, "inferenceVerified": False}
            setup.atomic_json(root / "receipt.json", receipt)
            self.assertTrue(setup.read_receipt(root / "receipt.json", recipe)["dependenciesVerified"])
            self.assertFalse(setup.read_receipt(root / "receipt.json", recipe)["inferenceVerified"])
            recipe["packages"]["pip"] = "2"
            self.assertIsNone(setup.read_receipt(root / "receipt.json", recipe))
            recipe["packages"]["pip"] = "1"
            python.unlink()
            self.assertIsNone(setup.read_receipt(root / "receipt.json", recipe))

    def test_android_provisioning_requires_license_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "license"):
                setup.android_plan(Path(directory), accept_licenses=False)
            plan = setup.android_plan(Path(directory), accept_licenses=True)
            self.assertIn("system-images;android-36;google_apis;x86_64", plan["packages"])
            self.assertEqual(plan["avdName"], "OpenCore_API_36")
            self.assertNotIn("--force", plan["createAvd"])


if __name__ == "__main__":
    unittest.main()
