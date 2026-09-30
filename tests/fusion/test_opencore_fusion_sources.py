import json
import hashlib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
RESOURCES = ROOT / "src-tauri" / "resources" / "fusion"


class OpenCoreFusionSourceTests(unittest.TestCase):
    def test_source_pair_is_pinned_and_not_misreported_as_ready(self):
        spec = json.loads((RESOURCES / "opencore_fusion_sources.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["schema"], 1)
        self.assertEqual(spec["model_id"], "opencore-fusion")
        self.assertEqual(
            spec["status"],
            "sources_pinned_reference_only_not_trained_or_app_integrated",
        )
        self.assertEqual(spec["generation"]["architecture"], "coupled_full_weight_single_output_stream")
        self.assertIs(spec["generation"]["separate_candidate_answers"], False)
        self.assertEqual(
            spec["generation"]["hidden_state_coupling"],
            "bidirectional_low_rank_final_hidden_projection_reference",
        )
        self.assertEqual(
            spec["generation"]["tokenizer_alignment"],
            "exact_decoded_surface_pairs_sparse_coverage_measured",
        )
        alignment = spec["generation"]["alignment_measurement"]
        self.assertEqual(alignment["method"], "single_token_exact_decoded_surface_v1")
        self.assertEqual(alignment["shared_token_strings"], 128_887)
        self.assertEqual(alignment["exact_surface_pairs"], 128_094)
        self.assertEqual(alignment["qwen"]["tokenizer_vocab_entries"], 248_070)
        self.assertEqual(alignment["qwen"]["model_logit_rows"], 248_320)
        self.assertEqual(alignment["qwen"]["mapped_vocab_coverage_percent"], 51.59)
        self.assertEqual(alignment["qwen"]["tokenizer_json_sha256"],
                         "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42")
        self.assertEqual(alignment["k2"]["tokenizer_vocab_entries"], 250_624)
        self.assertEqual(alignment["k2"]["model_logit_rows"], 250_624)
        self.assertEqual(alignment["k2"]["mapped_vocab_coverage_percent"], 51.11)
        self.assertEqual(alignment["k2"]["tokenizer_json_sha256"],
                         "838d767b7c9925ff257feb20eaa4299a8e3cc35bb3d805589c373f51d2cc3cb6")
        self.assertIs(alignment["coverage_is_token_frequency"], False)
        self.assertIs(spec["generation"]["coupling_trained"], False)
        self.assertEqual(spec["generation"]["reference_path"], "fusion.CoupledFusion")
        self.assertEqual(spec["runtime"]["status"], "not_app_integrated")

        qwen = spec["checkpoints"]["qwen"]
        self.assertEqual(qwen["repo_id"], "Qwen/Qwen3.5-9B")
        self.assertEqual(qwen["revision"], "c202236235762e1c871ad0ccb60c8ee5ba337b9a")
        self.assertEqual(qwen["format"], "safetensors")
        self.assertEqual(qwen["dtype"], "bfloat16")
        self.assertEqual(qwen["weight_bytes"], 19_306_310_880)
        self.assertEqual(qwen["source_config"], {
            "path": "config.json",
            "sha256": "d0883072e01861ed0b2d47be3c16c36a8e81c224c7ffaa310c6558fb3f932b05",
            "architecture": "Qwen3_5ForConditionalGeneration",
            "text_model_type": "qwen3_5_text",
            "hidden_size": 4096,
            "vocab_size": 248320,
            "native_context_tokens": 262144,
        })
        self.assertEqual(qwen["source_tokenizer_config"], {
            "path": "tokenizer_config.json",
            "sha256": "316230d6a809701f4db5ea8f8fc862bc3a6f3229c937c174e674ff3ca0a64ac8",
            "tokenizer_class": "Qwen2Tokenizer",
            "model_max_length": 262144,
            "eos_token": "<|im_end|>",
        })
        self.assertEqual(
            qwen["weight_files"],
            [
                {
                    "filename": "model.safetensors-00001-of-00004.safetensors",
                    "bytes": 5_276_436_216,
                    "sha256": "db6f444b43d318c92f360a13a25561a6a65b10c0631b8ed305a426dbaa6c380e",
                },
                {
                    "filename": "model.safetensors-00002-of-00004.safetensors",
                    "bytes": 5_335_161_512,
                    "sha256": "31c7d7e2dd5d207840b31cc59083c8f4c4718959149e0358c0364052bb9a0330",
                },
                {
                    "filename": "model.safetensors-00003-of-00004.safetensors",
                    "bytes": 5_368_717_440,
                    "sha256": "7ec36ba3a4176a44c3c0876ad80c56a2f70c84bf008d82e9501df642f17dadec",
                },
                {
                    "filename": "model.safetensors-00004-of-00004.safetensors",
                    "bytes": 3_325_995_712,
                    "sha256": "b62b0c4cd7e44edee103ee8f4fe225f246d5e768e07bfd5f25b63a8aa1fdd0c6",
                },
            ],
        )

        k2 = spec["checkpoints"]["k2"]
        manifest_path = RESOURCES / "checkpoints.sha256.json"
        self.assertEqual(
            k2["checkpoint_manifest"],
            {
                "path": "checkpoints.sha256.json",
                "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            },
        )
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        pinned_k2 = existing["models"]["k2-horizon-3.7b"]
        self.assertEqual(k2["repo_id"], pinned_k2["repo_id"])
        self.assertEqual(k2["repo_id"], "IFM/K2-Horizon-3.7B")
        self.assertEqual(k2["revision"], "85f683bc15947495341baa91ae1246dcecc47407")
        self.assertEqual(k2["revision"], pinned_k2["revision"])
        self.assertEqual(k2["format"], "safetensors")
        self.assertEqual(k2["dtype"], "bfloat16")
        self.assertEqual(k2["weight_file_count"], 36)
        self.assertEqual(k2["weight_bytes"], sum(
            info["bytes"] for name, info in pinned_k2["files"].items() if name.endswith(".safetensors")
        ))
        self.assertEqual(k2["source_config"], {
            "path": "config.json",
            "sha256": "a98a4dc771aadcbe03a390d825723a42eaee2682758b29b7d44341d4f33d8ab4",
            "architecture": "K2HorizonForCausalLM",
            "text_model_type": "k2_horizon",
            "hidden_size": 2560,
            "vocab_size": 250624,
            "native_context_tokens": 524288,
        })
        self.assertEqual(k2["source_tokenizer_config"], {
            "path": "tokenizer_config.json",
            "sha256": "068cfdcf2bcef44fd77f935a9fb41b4d45af547fd41b95079817bd40b24fe518",
            "tokenizer_class": "TokenizersBackend",
            "model_max_length": 1000000000000000019884624838656,
            "bos_token": "<|ifm|begin_of_text|>",
            "eos_token": "<|ifm|endoftext|>",
        })
        self.assertEqual(spec["compatibility"], {
            "hidden_size_mismatch": True,
            "vocabulary_size_mismatch": True,
            "tokenizer_mismatch": True,
            "shared_native_context_tokens": 262144,
            "direct_logit_averaging": "invalid_without_token_alignment",
        })

        self.assertEqual(spec["profiles"]["native_1m"]["status"], "not_implemented")
        self.assertEqual(spec["profiles"]["echo"]["status"], "not_implemented")
        self.assertEqual(spec["training"]["status"], "not_started")


if __name__ == "__main__":
    unittest.main()
