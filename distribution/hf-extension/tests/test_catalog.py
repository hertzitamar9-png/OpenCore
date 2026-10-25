import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import hf_catalog as catalog
except ModuleNotFoundError:
    catalog = None


def file(name, size=6, digest="a" * 64):
    return {"rfilename": name, "size": size, "blobId": "b" * 40,
            "lfs": {"sha256": digest, "size": size}}


def metadata(files, **extra):
    return {"id": "owner/model", "sha": "c" * 40, "siblings": files,
            "config": {"model_type": "llama", "architectures": ["LlamaForCausalLM"]}, **extra}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(catalog, "The catalog planner is not implemented")

    def test_repository_urls_normalize_and_reject_non_hub_inputs(self):
        self.assertEqual(catalog.parse_repository("https://huggingface.co/owner/model/tree/main"), "owner/model")
        self.assertEqual(catalog.parse_repository(" owner/model "), "owner/model")
        for value in ("https://evil.example/owner/model", "https://huggingface.co@evil.example/a/b", "../bad", "owner/../model", "owner/model?token=secret", "https://huggingface.co/datasets/owner/model"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                catalog.parse_repository(value)

    def test_gguf_shards_form_one_quant_card_and_auxiliary_files_are_companions(self):
        data = metadata([file("model-Q4_K_M-00001-of-00002.gguf"), file("model-Q4_K_M-00002-of-00002.gguf"),
                         file("model-Q8_0.gguf"), file("mmproj-model-f16.gguf"), file("imatrix.gguf"),
                         file("draft-Q4_K_M.gguf"), file("optimizer/model.safetensors")])
        plan = catalog.build_plan(data)
        self.assertEqual([x["quantization"] for x in plan["variants"]], ["Q4_K_M", "Q8_0"])
        q4 = plan["variants"][0]
        self.assertEqual(len(q4["files"]), 3)
        self.assertEqual([x["role"] for x in q4["files"]], ["model", "model", "companion"])
        self.assertEqual(q4["bytes"], 18)
        self.assertEqual(q4["files"][0]["revision"], "c" * 40)
        self.assertEqual(q4["files"][0]["sha256"], "a" * 64)
        self.assertFalse(q4["runtime"]["inferenceVerified"])

    def test_incomplete_gguf_shard_group_is_visible_but_not_installable(self):
        plan = catalog.build_plan(metadata([file("model-Q4_K_M-00001-of-00002.gguf")]))
        self.assertFalse(plan["variants"][0]["installable"])
        self.assertIn("missing", plan["variants"][0]["reason"].lower())

    def test_quant_suffix_wins_over_model_version_and_mixed_packages_stay_distinct(self):
        for filename, expected in [('LFM-Q3.8-TBrilliance-Q8_0.gguf', 'Q8_0'),
                                   ('model-Mix-13G.gguf', 'Mix 13G'), ('model-Mix-15.7G.gguf', 'Mix 15.7G'),
                                   ('Sage.MXFP4_MOE.gguf', 'MXFP4_MOE')]:
            with self.subTest(filename=filename):
                entry = catalog.build_plan(metadata([file(filename)]))['variants'][0]
                self.assertEqual(entry['quantization'], expected)

    def test_dflash_is_a_draft_companion_and_not_a_main_quantization(self):
        plan = catalog.build_plan(metadata([file('model-Q4_K_M.gguf'), file('model-DFlash2-Q8_0.gguf')]))
        self.assertEqual([v['quantization'] for v in plan['variants']], ['Q4_K_M'])

    def test_non_text_downloads_do_not_claim_a_text_runtime(self):
        plan = catalog.build_plan(metadata([file('model.safetensors')], pipeline_tag='text-to-image'))
        entry = plan['variants'][0]
        self.assertTrue(entry['installable'])
        self.assertEqual(entry['category'], 'image')
        self.assertIsNone(entry['runtime']['adapter'])

    def test_safetensors_are_one_checkpoint_per_subdirectory_with_safe_support_files(self):
        plan = catalog.build_plan(metadata([file("config.json"), file("tokenizer.json"), file("remote_model.py"),
            file("bf16/model-00001-of-00002.safetensors"), file("bf16/model-00002-of-00002.safetensors"),
            file("bf16/model.safetensors.index.json"), file("int4/model.safetensors"),
            file("training/optimizer.safetensors"), file("draft/model.safetensors")]))
        self.assertEqual(len(plan["variants"]), 2)
        bf16 = next(x for x in plan["variants"] if x["subdirectory"] == "bf16")
        self.assertEqual(len(bf16["files"]), 5)
        self.assertNotIn("remote_model.py", [x["filename"] for x in bf16["files"]])
        self.assertEqual({x["role"] for x in bf16["files"]}, {"model", "support"})

    def test_git_blob_pin_and_stable_card_id_survive_new_commit(self):
        data = metadata([{"rfilename": "model-F16.gguf", "size": 6, "blobId": "d" * 40}])
        first = catalog.build_plan(data)["variants"][0]
        self.assertEqual(first["files"][0]["gitBlobSha1"], "d" * 40)
        data["sha"] = "e" * 40
        second = catalog.build_plan(data)["variants"][0]
        self.assertEqual(first["id"], second["id"])

    def test_unpinned_or_unsafe_files_cannot_be_installed(self):
        for bad in (file("../model-Q4.gguf"), file("C:/model-Q4.gguf"), file("dir\\model-Q4.gguf"), file("CON/model-Q4.gguf")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                catalog.build_plan(metadata([bad]))
        plan = catalog.build_plan(metadata([{"rfilename": "model-Q4.gguf", "size": 6}]))
        self.assertFalse(plan["variants"][0]["installable"])
        with self.assertRaises(ValueError):
            catalog.build_plan(metadata([file("model.gguf")], sha="main"))


if __name__ == "__main__":
    unittest.main()
