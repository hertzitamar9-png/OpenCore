import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src-tauri/resources/runtime-setup'))
import managed_model_sources
import setup_manager as setup


class FixtureRunner:
    """Replace package installation; receipt publication stays real."""

    def __init__(self):
        self.events = []

    def check_cancel(self):
        pass

    def reporter(self, stage, **fields):
        self.events.append((stage, fields))

    def run(self, args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="")


class ReceiptRepairTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="edgerock-receipt-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.recipe = copy.deepcopy(setup.recipe_for("edgerock-4b"))
        self.python = setup.python_in(self.root / "runtime-setup/environments/edgerock-portable-v1")
        self.result = {
            "packages": {"torch": "2.11.0+cpu", **self.recipe["packages"]},
            "hardware": {"torch": "2.11.0+cpu", "cudaAvailable": False, "cudaBuild": None},
            "dependenciesVerified": True,
            "inferenceVerified": False,
        }
        self.weights = self.root / "models/edgerock-4b/weights.safetensors"
        self.weights.parent.mkdir(parents=True)
        self.weights.write_bytes(b"verified model weights")
        identity = [["models/edgerock-4b/weights.safetensors", "rockmatrix-ai/EdgeRock", "a" * 40,
                     "weights.safetensors", hashlib.sha256(self.weights.read_bytes()).hexdigest(),
                     self.weights.stat().st_size]]
        self.previous = {
            "schema": 1, "targetId": "edgerock-4b", "recipeId": self.recipe["id"],
            "recipeFingerprint": setup.recipe_fingerprint(self.recipe),
            "verifiedAt": "2026-10-23T18:30:00Z", "sourceUrls": self.recipe["sourceUrls"],
            "python": str(self.python), "runner": str(self.root / "resources/text-runtime/serve_text.py"),
            **copy.deepcopy(self.result),
            "inferenceVerified": True,
            "inferenceProof": {
                "executionVerified": True, "generatedTokens": 8, "jobId": "completed-install",
                "checkedAt": "2026-10-23T18:34:00Z", "device": "cpu",
                "modelPin": {"packageIdentity": json.dumps(identity), "artifacts": ["weights"],
                             "precision": "BF16", "sourceUrl": "https://huggingface.co/rockmatrix-ai/EdgeRock"},
            },
        }
        self.receipt_path = self.root / "runtime-setup/receipts/edgerock-4b.json"
        self.receipt_path.parent.mkdir(parents=True)

    def run_preparation(self, runner=None):
        self.receipt_path.write_text(json.dumps(self.previous), encoding="utf-8")
        runner = runner or FixtureRunner()
        with patch.object(setup, "recipe_for", return_value=self.recipe), \
             patch.object(setup, "create_environment", return_value=self.python), \
             patch.object(setup, "verify_environment", return_value=self.result), \
             patch.object(setup, "download", return_value=self.root / "pinned-transformers.whl"), \
             patch.object(managed_model_sources, "shared_torch_source", return_value=None):
            value = setup.install(self.root, self.root / "resources", "edgerock-4b", {}, runner)
        saved = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(value, saved)
        self.assertEqual(runner.events[-1][0], "dependencies-verified")
        return saved

    def test_repeat_preparation_keeps_verified_inference_for_unchanged_files(self):
        saved = self.run_preparation()
        self.assertIs(saved["inferenceVerified"], True)
        self.assertEqual(saved["inferenceProof"], self.previous["inferenceProof"])

    def test_cancel_during_proof_hashing_does_not_publish_a_new_receipt(self):
        class CancellingRunner(FixtureRunner):
            cancelled = False

            def check_cancel(self):
                if self.cancelled:
                    raise setup.Cancelled("Cancelled during model hash validation")

        runner = CancellingRunner()
        real_hash = setup.file_hash

        def cancel_in_hash(path, *args, **kwargs):
            runner.cancelled = True
            return real_hash(path, *args, **kwargs)

        with patch.object(setup, "file_hash", side_effect=cancel_in_hash):
            with self.assertRaises(setup.Cancelled):
                self.run_preparation(runner)
        saved = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(saved, self.previous)

    def test_same_size_changed_weights_require_new_generation_check(self):
        self.weights.write_bytes(b"x" * self.weights.stat().st_size)
        self.assertIs(self.run_preparation()["inferenceVerified"], False)

    def test_missing_weights_require_new_generation_check(self):
        self.weights.unlink()
        self.assertIs(self.run_preparation()["inferenceVerified"], False)

    def test_changed_dependency_versions_require_new_generation_check(self):
        self.previous["packages"]["torch"] = "2.10.0+cpu"
        self.assertIs(self.run_preparation()["inferenceVerified"], False)

    def test_changed_recipe_requires_new_generation_check(self):
        self.previous["recipeFingerprint"] = "old-recipe"
        self.assertIs(self.run_preparation()["inferenceVerified"], False)

    def test_wrong_target_receipt_cannot_be_reused(self):
        self.previous["targetId"] = "another-model"
        self.assertIs(self.run_preparation()["inferenceVerified"], False)

    def test_verified_flag_without_execution_proof_cannot_be_reused(self):
        del self.previous["inferenceProof"]
        self.assertIs(self.run_preparation()["inferenceVerified"], False)

    def test_file_outside_model_root_cannot_prove_readiness(self):
        identity = json.loads(self.previous["inferenceProof"]["modelPin"]["packageIdentity"])
        identity[0][0] = "../outside.safetensors"
        self.previous["inferenceProof"]["modelPin"]["packageIdentity"] = json.dumps(identity)
        self.assertIs(self.run_preparation()["inferenceVerified"], False)

    def test_missing_hash_cannot_prove_readiness(self):
        identity = json.loads(self.previous["inferenceProof"]["modelPin"]["packageIdentity"])
        identity[0][4] = ""
        self.previous["inferenceProof"]["modelPin"]["packageIdentity"] = json.dumps(identity)
        self.assertIs(self.run_preparation()["inferenceVerified"], False)


class EnvironmentRepairTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows extended path regression")
    def test_fresh_environment_bootstraps_pip_with_windows_extended_path(self):
        with tempfile.TemporaryDirectory(prefix="edgerock-extended-test-") as directory:
            root = Path("\\\\?\\" + directory)
            runner = setup.Runner(None, reporter=lambda *args, **kwargs: None)
            returned = setup.create_environment(root, "edgerock-portable-v1", sys.executable, runner)
            result = subprocess.run([str(returned), "-I", "-m", "pip", "--version"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_retry_repairs_existing_isolated_environment_without_pip(self):
        with tempfile.TemporaryDirectory(prefix="edgerock-pip-test-") as directory:
            root = Path(directory)
            environment = root / "runtime-setup/environments/edgerock-portable-v1"
            subprocess.run([sys.executable, "-I", "-m", "venv", "--without-pip", str(environment)],
                           check=True, capture_output=True, text=True)
            python = setup.python_in(environment)
            absent = subprocess.run([str(python), "-I", "-m", "pip", "--version"], capture_output=True)
            self.assertNotEqual(absent.returncode, 0)
            runner = setup.Runner(None, reporter=lambda *args, **kwargs: None)
            returned = setup.create_environment(root, "edgerock-portable-v1", sys.executable, runner)
            working = subprocess.run([str(returned), "-I", "-m", "pip", "--version"],
                                     capture_output=True, text=True)
            self.assertEqual(working.returncode, 0, working.stderr)
            self.assertIn("pip ", working.stdout)


if __name__ == "__main__":
    unittest.main()
