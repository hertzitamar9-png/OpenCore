import json
import re
import unittest
from pathlib import Path


CATALOG = Path(__file__).resolve().parents[1] / "model-catalog.json"


class GameDevImageCatalog(unittest.TestCase):
    def test_qwen_image_uses_the_builtin_diffusers_worker(self):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        model = next(item for item in catalog["models"] if item["id"] == "qwen-image-21")
        self.assertEqual(model["backend"], "diffusers")
        self.assertTrue(model["installable"])

    def test_small_local_image_models_have_complete_pinned_downloads(self):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        artifacts = {item["id"]: item for item in catalog["artifacts"]}
        models = {item["id"]: item for item in catalog["models"]}
        for model_id in ("sana-16", "hunyuan-dit-v12-distilled"):
            with self.subTest(model_id=model_id):
                model = models[model_id]
                self.assertEqual(model["category"], "image")
                self.assertTrue(model["installable"])
                self.assertGreater(len(model["artifacts"]), 1)
                self.assertRegex(model["label"], r"(?i)(sana|hunyuan).*(1\.6b|v1\.2|distilled)")
                for artifact_id in model["artifacts"]:
                    artifact = artifacts[artifact_id]
                    self.assertEqual(artifact["path"].split("/")[2], model_id)
                    self.assertRegex(artifact["revision"], r"^[0-9a-f]{40}$")
                    self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")
                    self.assertGreater(artifact["bytes"], 0)

    def test_mage_flow_is_visible_but_not_misrepresented_as_downloadable(self):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        model = next(item for item in catalog["models"] if item["id"] == "mage-flow-turbo")
        self.assertEqual(model["category"], "image")
        self.assertFalse(model["installable"])
        self.assertFalse(model["artifacts"])
        self.assertEqual(model["setupUrl"], "https://huggingface.co/spaces/microsoft/mage-flow")


if __name__ == "__main__":
    unittest.main()
