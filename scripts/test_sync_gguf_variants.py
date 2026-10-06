import copy
import unittest

from scripts.sync_gguf_variants import add_repository_variants, quantization_for_filename, refresh_catalog


def file_entry(filename, checksum):
    return {"rfilename": filename, "size": 1000, "lfs": {"sha256": checksum * 64}}


class GgufVariantRefreshTests(unittest.TestCase):
    def test_parses_quant_and_speculative_modes_but_skips_sidecars(self):
        self.assertEqual(quantization_for_filename("model.Q4_K_M.gguf")[0], "Q4_K_M")
        self.assertEqual(quantization_for_filename("model-IQ2_S-mtp.gguf")[0], "IQ2_S MTP")
        self.assertEqual(quantization_for_filename("model-Q4_K_M-low-mtp.gguf")[0], "Q4_K_M LOW-MTP")
        self.assertEqual(quantization_for_filename("model-MTP-Q4_K_M.gguf")[0], "Q4_K_M MTP")
        self.assertEqual(quantization_for_filename("model-LOW-MTP-Q6_K.gguf")[0], "Q6_K LOW-MTP")
        self.assertEqual(quantization_for_filename("FrogNano-4B-Q6_K_S.gguf")[0], "Q6_K_S")
        self.assertEqual(quantization_for_filename("Ternary-Bonsai-PTQ1_0.gguf")[0], "PTQ1_0")
        self.assertEqual(quantization_for_filename("Ornith-MTP-Q4_K_M.gguf", intrinsic_mtp=True)[0], "Q4_K_M")
        self.assertIsNone(quantization_for_filename("mmproj-F16.gguf"))
        self.assertIsNone(quantization_for_filename("imatrix.gguf"))
        self.assertIsNone(quantization_for_filename("unquantized.gguf"))

    def test_adds_pinned_native_and_echo_profiles_with_shared_projector(self):
        parent = {
            "id": "swift", "label": "Swift", "description": "Swift text model", "precision": "IQ2_S",
            "contextTokens": 16384, "artifacts": ["base", "projector"], "license": "Apache-2.0",
            "experimental": True, "note": "Original note", "selectable": True, "category": "text",
            "backend": "gguf", "sourceUrl": "https://huggingface.co/org/repo/tree/old",
            "setupUrl": "https://huggingface.co/org/repo", "runtimeReady": True, "installable": True,
            "runtimeModelPath": "models/library/swift/base.gguf", "visionProjectorPath": "models/library/swift/mmproj.gguf",
            "memoryMode": "echo",
        }
        native_parent = copy.deepcopy(parent)
        native_parent.update({"id": "swift-native", "memoryMode": "native"})
        catalog = {"artifacts": [
            {"id": "base", "path": parent["runtimeModelPath"], "repo": "org/repo", "filename": "base.gguf"},
            {"id": "projector", "path": parent["visionProjectorPath"], "repo": "org/repo", "filename": "mmproj.gguf"},
        ], "models": [parent, native_parent]}
        repo = {"id": "org/repo", "sha": "a" * 40, "siblings": [file_entry("base-IQ3_S-mtp.gguf", "b")]}
        self.assertEqual(add_repository_variants(catalog, parent, repo), 1)
        echo = next(item for item in catalog["models"] if item.get("variantOf") == "swift")
        native = next(item for item in catalog["models"] if item["id"] == f"{echo['id']}-native")
        artifact = next(item for item in catalog["artifacts"] if item["id"] in echo["artifacts"] and item["filename"].endswith(".gguf") and item["id"] != "projector")
        self.assertEqual(echo["precision"], "IQ3_S MTP")
        self.assertEqual(echo["memoryMode"], "echo")
        self.assertEqual(native["memoryMode"], "native")
        self.assertEqual(echo["artifacts"], native["artifacts"])
        self.assertEqual(echo["visionProjectorPath"], parent["visionProjectorPath"])
        self.assertEqual(artifact["revision"], "a" * 40)
        self.assertEqual(artifact["sha256"], "b" * 64)

    def test_refreshes_image_gguf_variants_without_text_runtime_profiles(self):
        parent = {
            "id": "qwen-image-21-gguf", "label": "Qwen Image GGUF", "precision": "Q6_K",
            "artifacts": ["base"], "selectable": False, "installable": True,
            "category": "image", "backend": "external", "refreshQuantizations": True,
            "variantOf": "qwen-image-21",
        }
        catalog = {"models": [parent], "artifacts": [{
            "id": "base", "path": "models/library/qwen-image/qwen-image-Q6_K.gguf",
            "repo": "org/image", "filename": "qwen-image-Q6_K.gguf",
        }]}
        repo = {"id": "org/image", "sha": "c" * 40, "siblings": [
            file_entry("qwen-image-Q6_K.gguf", "d"), file_entry("qwen-image-Q4_K_M.gguf", "e"),
            file_entry("another-model-Q5_K_M.gguf", "f"), file_entry("mmproj-F16.gguf", "1"),
        ]}
        self.assertEqual(refresh_catalog(catalog, {"org/image": repo}), 1)
        variant = next(item for item in catalog["models"] if item.get("variantOf") == parent["id"])
        self.assertEqual(variant["category"], "image")
        self.assertEqual(variant["backend"], "external")
        self.assertFalse(variant["selectable"])
        self.assertNotIn("memoryMode", variant)
        self.assertIn("text encoder", variant["note"])
        self.assertIn("runtime setup", variant["note"])
        self.assertEqual([item["id"] for item in catalog["models"]], ["qwen-image-21-gguf", variant["id"]])


if __name__ == "__main__":
    unittest.main()
