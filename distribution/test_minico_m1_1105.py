"""Regression test for the pinned Minico M1 1105 model and phone profiles."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from register_minico_m1_1105 import QUANTS, REV, REPO, register

spec = importlib.util.spec_from_file_location("phone_catalog", ROOT / "android-remote/phone_catalog.py")
phone_catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phone_catalog)


class MinicoCatalogTests(unittest.TestCase):
    def test_pinned_weights_and_modes_are_selectable_on_desktop_and_phone(self):
        catalog = json.loads((ROOT / "src-tauri/resources/model-catalog.json").read_text(encoding="utf-8"))
        models = {m["id"]: m for m in catalog["models"]}
        artifacts = {a["id"]: a for a in catalog["artifacts"]}
        for key, (precision, filename, size, sha) in QUANTS.items():
            root = f"minico-m1-1105-{key}"
            source = models[root]
            native = models[root + "-native"]
            weight = artifacts[root + "-weight"]
            self.assertEqual((weight["repo"], weight["revision"], weight["filename"], weight["bytes"], weight["sha256"]),
                             (REPO, REV, filename, size, sha))
            self.assertEqual(source["weightArtifacts"], [weight["id"]])
            self.assertEqual(native["weightArtifacts"], [weight["id"]])
            self.assertEqual(source["runtimeModelPath"], weight["path"])
            self.assertEqual(native["runtimeModelPath"], weight["path"])
            self.assertEqual((source["memoryMode"], native["memoryMode"]), ("echo", "native"))
            for model in (source, native):
                self.assertEqual(model["precision"], precision)
                self.assertTrue(model["selectable"] and model["installable"] and model["runtimeReady"])
                self.assertEqual(model["backend"], "gguf")
        before = (len(catalog["models"]), len(catalog["artifacts"]))
        register(catalog)
        self.assertEqual(before, (len(catalog["models"]), len(catalog["artifacts"])),
                         "Catalog registration should be idempotent")
        mobile = phone_catalog.build(catalog, {"entries": []})
        minico = [f for family in mobile["families"] for f in family["files"] if f["repo"] == REPO]
        self.assertEqual(len(minico), len(QUANTS))
        self.assertTrue(all(len(f.get("profiles", [])) == 2 for f in minico))
        self.assertEqual({p["memoryMode"] for f in minico for p in f["profiles"]}, {"echo", "native"})


if __name__ == "__main__":
    unittest.main()
