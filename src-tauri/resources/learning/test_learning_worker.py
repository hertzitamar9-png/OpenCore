"""Dependency-free policy tests. GPU qualification is deliberately separate."""
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def module(name):
    path = HERE / (name + ".py")
    assert path.is_file(), f"The production {name} module is missing"
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    return __import__(name)


def fixture(root, count=8):
    model = root / "base"
    model.mkdir()
    (model / "config.json").write_text(json.dumps({"model_type": "llama", "hidden_size": 64, "intermediate_size": 128, "num_hidden_layers": 2, "num_attention_heads": 4, "num_key_value_heads": 2, "vocab_size": 256, "max_position_embeddings": 2048, "tie_word_embeddings": True}), encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"policy-fixture-not-a-real-model")
    (model / "tokenizer.json").write_text('{}', encoding="utf-8")
    train = root / "train.jsonl"
    validation = root / "validation.jsonl"
    train.write_text(json.dumps({"sourceId": "train-source", "prompt": "a", "completion": "b"}) + "\n", encoding="utf-8")
    validation.write_text("".join(json.dumps({"sourceId": "heldout-" + str(i), "prompt": str(i), "completion": "answer"}) + "\n" for i in range(count)), encoding="utf-8")
    manifest = root / "dataset.json"
    manifest.write_text(json.dumps({
        "train": {"sha256": hashlib.sha256(train.read_bytes()).hexdigest(), "sourceIds": ["train-source"]},
        "validation": {"sha256": hashlib.sha256(validation.read_bytes()).hexdigest(), "sourceIds": ["heldout-" + str(i) for i in range(count)]},
    }), encoding="utf-8")
    return {"runId": "policy-test", "modelPath": str(model), "datasetManifest": str(manifest), "trainPath": str(train), "validationPath": str(validation), "outputDir": str(root / "run"), "config": {"maxSteps": 100, "checkpointEvery": 25}}


class PreflightRecommendationTests(unittest.TestCase):
    def test_qlora_recommendation_retains_explicit_precision_and_bf16_requirement(self):
        with tempfile.TemporaryDirectory() as temp:
            request = fixture(Path(temp))
            request["config"]["precision"] = "qlora-4bit"
            hardware = {"cudaAvailable": True, "bf16Supported": False, "devices": [{"index": 0, "totalBytes": 8 * 1024**3, "freeBytes": 7 * 1024**3}]}
            result = module("worker").recommended_configuration(request, hardware)
            self.assertEqual(result["config"]["precision"], "qlora-4bit")
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(result["canStart"])

    def test_recommend_cli_explains_unknown_hardware_without_loading_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            request = fixture(Path(temp))
            request_path, output = Path(temp) / "request.json", Path(temp) / "recommendation.json"
            request_path.write_text(json.dumps(request))
            result = subprocess.run([sys.executable, "-S", str(HERE / "worker.py"), "recommend", "--request", str(request_path), "--output", str(output)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stderr)
            receipt = json.loads(output.read_text())
            self.assertEqual(receipt["status"], "blocked")
            self.assertEqual(receipt["config"]["precision"], "bf16-lora")
            self.assertIsNone(receipt["estimates"]["predictedDurationSeconds"])

    def test_plan_returns_all_missing_input_errors_without_loading_gpu_or_creating_run(self):
        result = module("worker").plan({"config": {"maxSteps": -1}})
        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], "invalid")
        fields = {error["field"] for error in result["errors"]}
        self.assertTrue({"runId", "modelPath", "datasetManifest", "trainPath", "validationPath", "outputDir", "config"} <= fields)
        self.assertNotIn("torch", sys.modules)

    def test_plan_rejects_missing_indexed_shard_and_insufficient_holdout(self):
        with tempfile.TemporaryDirectory() as temp:
            request = fixture(Path(temp), count=2)
            model = Path(request["modelPath"])
            (model / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"model.layers.0.weight": "missing-00001.safetensors"}}))
            result = module("worker").plan(request)
            self.assertFalse(result["valid"])
            self.assertIn("missing-00001", str(result["errors"]))
            (model / "model.safetensors.index.json").unlink()
            result = module("worker").plan(request)
            self.assertFalse(result["valid"])
            self.assertTrue(any(error["code"] == "insufficient-holdout" for error in result["errors"]))
            self.assertFalse(Path(request["outputDir"]).exists())

    def test_invalid_plan_cli_writes_structured_receipt_before_environment_setup(self):
        with tempfile.TemporaryDirectory() as temp:
            request_path, output = Path(temp) / "request.json", Path(temp) / "plan.json"
            request_path.write_text('{"config":{}}')
            result = subprocess.run([sys.executable, "-S", str(HERE / "worker.py"), "plan", "--request", str(request_path), "--output", str(output)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(json.loads(output.read_text())["valid"])
            self.assertIn("modelPath", output.read_text())

    def test_recommendation_preserves_precision_and_user_budgets_with_explicit_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            request = fixture(Path(temp))
            request["goal"] = "quick"
            request["config"].update({"precision": "bf16-lora", "maxMinutes": 3.0, "maxDiskBytes": 1024**3, "maxSteps": 70, "batchSize": 8})
            hardware = {"cudaAvailable": True, "bf16Supported": True, "devices": [{"index": 0, "totalBytes": 8 * 1024**3, "freeBytes": 7 * 1024**3}]}
            result = module("worker").recommended_configuration(request, hardware)
            self.assertEqual(result["status"], "recommended")
            self.assertTrue(result["canStart"])
            for key in ("precision", "maxMinutes", "maxDiskBytes"):
                self.assertEqual(result["config"][key], request["config"][key])
            self.assertLessEqual(result["config"]["maxSteps"], request["config"]["maxSteps"])
            self.assertEqual(result["config"]["batchSize"], 1)
            self.assertTrue(any(item["field"] == "batchSize" for item in result["changes"]))
            self.assertGreater(result["estimates"]["modelWeightBytes"], 0)
            self.assertTrue(result["requiresProbe"])
            self.assertFalse(Path(request["outputDir"]).exists())
            self.assertNotIn("torch", sys.modules)

    def test_recommendation_blocks_known_insufficient_memory_without_quantizing(self):
        with tempfile.TemporaryDirectory() as temp:
            request = fixture(Path(temp))
            (Path(request["modelPath"]) / "config.json").write_text(json.dumps({"model_type": "llama", "hidden_size": 8192, "intermediate_size": 28672, "num_hidden_layers": 80, "num_attention_heads": 64, "num_key_value_heads": 8, "vocab_size": 128256}))
            hardware = {"cudaAvailable": True, "bf16Supported": True, "devices": [{"index": 0, "totalBytes": 12 * 1024**3, "freeBytes": 10 * 1024**3}]}
            result = module("worker").recommended_configuration(request, hardware)
            self.assertFalse(result["canStart"])
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["config"]["precision"], "bf16-lora")
            self.assertTrue(any("memory" in item.lower() for item in result["warnings"]))

    def test_recommendation_unknown_hardware_and_invalid_budget_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            request = fixture(Path(temp))
            result = module("worker").recommended_configuration(request)
            self.assertFalse(result["canStart"])
            self.assertTrue(any("unknown" in item.lower() for item in result["warnings"]))
            request["config"]["maxMinutes"] = math.inf
            result = module("worker").recommended_configuration(request)
            self.assertEqual(result["status"], "invalid")
            self.assertFalse(result["canStart"])


class ConfigurationTests(unittest.TestCase):
    def test_precision_is_explicit_and_unknown_values_are_rejected(self):
        config = module("config")
        self.assertEqual(config.validate_config({})["precision"], "bf16-lora")
        self.assertEqual(config.validate_config({"precision": "qlora-4bit"})["precision"], "qlora-4bit")
        with self.assertRaisesRegex(ValueError, "precision"):
            config.validate_config({"precision": "auto"})

    def test_nonfinite_and_boolean_numeric_configuration_is_rejected(self):
        config = module("config")
        for key, value in [("learningRate", math.nan), ("minimumImprovement", math.inf), ("maxSteps", True), ("loraRank", 0), ("maxMinutes", -1), ("maxDiskBytes", 100), ("gradientAccumulation", 0), ("epochs", 0)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                config.validate_config({key: value})

    def test_unknown_keys_do_not_silently_hide_misspelled_hyperparameters(self):
        with self.assertRaisesRegex(ValueError, "unknown"):
            module("config").validate_config({"learningRte": 0.1})

    def test_regression_thresholds_are_validated(self):
        config = module("config")
        valid = {"regressionGates": [{"metric": "chosen_nll", "direction": "lower", "maximumRegression": 0.1}]}
        self.assertEqual(config.validate_config(valid)["regressionGates"][0]["maximumRegression"], 0.1)
        with self.assertRaises(ValueError):
            config.validate_config({"regressionGates": [{"metric": "x", "direction": "whatever", "maximumRegression": 0}]})


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = fixture(self.root)

    def test_prepare_request_hashes_inputs_without_importing_gpu_libraries(self):
        result = module("worker").prepare_request(self.request)
        self.assertEqual(result["counts"], {"train": 1, "validation": 8})
        self.assertEqual(result["model"]["files"][1]["path"], "model.safetensors")
        self.assertEqual(len(result["identitySha256"]), 64)
        self.assertEqual(set(result["worker"]["files"]), {"worker.py", "config.py", "requirements.json"})
        self.assertEqual(result["worker"]["files"]["worker.py"], hashlib.sha256((HERE / "worker.py").read_bytes()).hexdigest())
        self.assertEqual(result["plannedMaximumSteps"], 1)
        self.assertNotIn("torch", sys.modules)

    def test_data_hash_drift_is_rejected(self):
        with Path(self.request["trainPath"]).open("a", encoding="utf-8") as handle:
            handle.write("{}\n")
        with self.assertRaisesRegex(ValueError, "hash"):
            module("worker").prepare_request(self.request)

    def test_source_group_overlap_is_rejected_even_if_text_is_different(self):
        manifest_path = Path(self.request["datasetManifest"])
        manifest = json.loads(manifest_path.read_text())
        manifest["validation"]["sourceIds"].append("train-source")
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "source.*overlap"):
            module("worker").prepare_request(self.request)

    def test_identical_examples_are_rejected_across_holdout(self):
        validation = Path(self.request["validationPath"])
        row = {"sourceId": "heldout-0", "prompt": "a", "completion": "b"}
        validation.write_text(json.dumps(row) + "\n")
        manifest_path = Path(self.request["datasetManifest"])
        manifest = json.loads(manifest_path.read_text())
        manifest["validation"]["sha256"] = hashlib.sha256(validation.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "content.*overlap"):
            module("worker").prepare_request(self.request)

    def test_missing_source_provenance_cannot_claim_disjoint_holdout(self):
        row = {"prompt": "a", "completion": "b"}
        train = Path(self.request["trainPath"])
        train.write_text(json.dumps(row) + "\n")
        manifest_path = Path(self.request["datasetManifest"])
        manifest = json.loads(manifest_path.read_text())
        manifest["train"] = {"sha256": hashlib.sha256(train.read_bytes()).hexdigest()}
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "source"):
            module("worker").prepare_request(self.request)

    def test_gguf_catalog_entry_and_quantized_bf16_sources_fail_precisely(self):
        worker = module("worker")
        (Path(self.request["modelPath"]) / "config.json").write_text('{"quantization_config":{"quant_method":"bitsandbytes"}}')
        with self.assertRaisesRegex(ValueError, "quantiz"):
            worker.prepare_request(self.request)
        self.request["modelPath"] = str(self.root / "model.gguf")
        Path(self.request["modelPath"]).write_bytes(b"GGUF")
        with self.assertRaisesRegex(ValueError, "Transformers"):
            worker.prepare_request(self.request)

    def test_resume_identity_freezes_config_and_source_content(self):
        worker = module("worker")
        frozen = worker.prepare_request(self.request)
        changed = copy.deepcopy(self.request)
        changed["config"]["learningRate"] = 0.01
        with self.assertRaisesRegex(ValueError, "identity"):
            worker.verify_resume_identity(frozen, worker.prepare_request(changed))
        (Path(self.request["modelPath"]) / "model.safetensors").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "identity"):
            worker.verify_resume_identity(frozen, worker.prepare_request(self.request))

    def test_output_cannot_be_inside_or_contain_the_base_model(self):
        worker = module("worker")
        for output in [self.request["modelPath"], str(Path(self.request["modelPath"]) / "candidate"), str(self.root)]:
            self.request["outputDir"] = output
            with self.assertRaisesRegex(ValueError, "overlap"):
                worker.prepare_request(self.request)

    def test_dpo_requires_a_real_preference_pair(self):
        worker = module("worker")
        with self.assertRaisesRegex(ValueError, "chosen.*rejected"):
            worker.normalize_record({"prompt": "p", "chosen": "same", "rejected": "same"}, "dpo", 1)
        row = worker.normalize_record({"prompt": "p", "chosen": "yes", "rejected": "no", "sourceId": "s"}, "dpo", 1)
        self.assertEqual(row["payload"], {"prompt": "p", "chosen": "yes", "rejected": "no"})


class GateAndChunkTests(unittest.TestCase):
    def test_qlora_loaded_modules_must_use_requested_bf16_compute(self):
        from types import SimpleNamespace
        worker = module("worker")
        torch_types = SimpleNamespace(bfloat16="bf16", float16="fp16", float32="fp32")
        linear = type("Linear4bit", (), {})()
        linear.compute_dtype = "fp32"
        linear.weight = SimpleNamespace(quant_state=SimpleNamespace(quant_type="nf4"))
        model = SimpleNamespace(is_loaded_in_4bit=True, named_modules=lambda: [("q_proj", linear)], named_parameters=lambda: [])
        with self.assertRaisesRegex(RuntimeError, "BF16 compute"):
            worker.verify_loaded_precision(model, torch_types, "qlora-4bit")
        linear.compute_dtype = "bf16"
        result = worker.verify_loaded_precision(model, torch_types, "qlora-4bit")
        self.assertEqual(result[0]["quantizationType"], "nf4")
        with self.assertRaisesRegex(RuntimeError, "quantized"):
            worker.verify_loaded_precision(model, torch_types, "bf16-lora")

    def test_insufficient_raw_holdout_rejects_before_runtime_probe_or_model_load(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = fixture(root, count=2)
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request))
            result = subprocess.run([sys.executable, "-S", str(HERE / "worker.py"), "train", "--request", str(request_path)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads((root / "run" / "receipt.json").read_text())
            self.assertEqual(receipt["status"], "rejected")
            self.assertEqual(receipt["lastStep"], 0)
            self.assertEqual(receipt["gates"]["comparisons"][0]["observed"], 2)
            self.assertNotIn("environment", receipt)
            self.assertFalse(list((root / "run").glob("probe-*.json")))

    def test_dpo_matches_trl_unconditional_eos_and_rejects_trainer_token_drift(self):
        class Tokenizer:
            eos_token_id = 0
            def __call__(self, text, **kwargs):
                return {"input_ids": [ord(char) for char in text]}
        worker = module("worker")
        rows = [worker.normalize_record({"prompt": "p", "chosen": "yes\u0000", "rejected": "no", "sourceId": "s"}, "dpo", 1)]
        usable, exclusions = worker.tokenize_records(rows, Tokenizer(), 32, "dpo")
        self.assertEqual(usable[0]["chosen_input_ids"][-2:], [0, 0])
        actual = [{key: usable[0][key] for key in ("prompt_input_ids", "chosen_input_ids", "rejected_input_ids")}]
        worker.verify_trainer_dataset(actual, usable, "dpo")
        actual[0]["chosen_input_ids"] = actual[0]["chosen_input_ids"][:-1]
        with self.assertRaisesRegex(ValueError, "token.*identity"):
            worker.verify_trainer_dataset(actual, usable, "dpo")
        self.assertEqual(exclusions, [])

    def test_sft_masks_exact_full_text_tokens_and_refuses_cross_boundary_merge(self):
        class MergingTokenizer:
            eos_token_id = 0
            def __call__(self, text, **kwargs):
                return {"input_ids": [99] if text == "ab" else [ord(char) for char in text]}
        worker = module("worker")
        rows = [worker.normalize_record({"prompt": "a", "completion": "b", "sourceId": "s"}, "sft", 1)]
        with self.assertRaisesRegex(ValueError, "prompt.*token.*prefix"):
            worker.tokenize_records(rows, MergingTokenizer(), 32, "sft")

    def test_sft_trainer_cannot_unmask_prompt_or_change_frozen_tokens(self):
        worker = module("worker")
        frozen = [{"input_ids": [1, 2, 3], "attention_mask": [1, 1, 1], "labels": [-100, 2, 3]}]
        actual = copy.deepcopy(frozen)
        worker.verify_trainer_dataset(actual, frozen, "sft")
        actual[0]["labels"][0] = 1
        with self.assertRaisesRegex(ValueError, "token.*identity"):
            worker.verify_trainer_dataset(actual, frozen, "sft")

    def test_dpo_preference_metric_uses_summed_completion_log_probabilities(self):
        metrics = module("worker").preference_observation(1.0, 10, 2.0, 2)
        self.assertEqual(metrics["preference_margin"], -6.0)
        self.assertEqual(metrics["chosen_logp"], -10.0)
        self.assertEqual(metrics["rejected_logp"], -4.0)
        self.assertEqual(metrics["chosen_nll"], 1.0)

    def test_heldout_uses_completion_cross_entropy_instead_of_auxiliary_model_loss(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        import io
        worker = module("worker")
        # CUDA is the external boundary. Its CE result is ln(2), while the
        # model's composite loss deliberately includes a large auxiliary term.
        ce = MagicMock()
        ce.detach.return_value.float.return_value.cpu.return_value.item.return_value = math.log(2)
        composite = MagicMock()
        composite.detach.return_value.float.return_value.cpu.return_value.item.return_value = 100.0
        logits, labels = MagicMock(), MagicMock()
        batch = {"input_ids": MagicMock(), "attention_mask": MagicMock(), "labels": labels}
        for value in batch.values():
            value.to.return_value = value
        cross_entropy = MagicMock(return_value=ce)
        torch = SimpleNamespace(no_grad=nullcontext, autocast=lambda *args, **kwargs: nullcontext(), bfloat16="bf16", nn=SimpleNamespace(functional=SimpleNamespace(cross_entropy=cross_entropy)))
        model = MagicMock()
        model.parameters.return_value = iter([SimpleNamespace(device="cuda:0")])
        model.return_value = SimpleNamespace(loss=composite, logits=logits)
        row = worker.normalize_record({"text": "answer", "sourceId": "heldout"}, "sft", 1)
        records = [{"input_ids": [1, 2, 3], "labels": [-100, -100, 3], "row": row}]
        with tempfile.TemporaryDirectory() as temp, patch.object(worker, "collate_causal", return_value=batch):
            output = Path(temp)
            result = worker.evaluate_model(model, None, records, "sft", SimpleNamespace(check=lambda: None), worker.EventSink(output, "r", io.StringIO()), output, "candidate", torch, "i")
        self.assertAlmostEqual(result["eval_loss"], math.log(2))
        self.assertEqual(result["tokens"], 1)
        self.assertEqual(cross_entropy.call_args.kwargs, {"ignore_index": -100, "reduction": "mean"})
        composite.detach.assert_not_called()

    def test_tokenization_masks_prompt_and_excludes_long_samples_with_raw_identity(self):
        class Tokenizer:
            eos_token_id = 0
            def __call__(self, text, **kwargs):
                return {"input_ids": [ord(char) for char in text]}
        worker = module("worker")
        rows = [worker.normalize_record({"prompt": "hi", "completion": "yes", "sourceId": "s"}, "sft", 1), worker.normalize_record({"text": "x" * 50, "sourceId": "s"}, "sft", 2)]
        usable, exclusions = worker.tokenize_records(rows, Tokenizer(), 32, "sft")
        self.assertEqual(usable[0]["labels"], [-100, -100, 121, 101, 115, 0])
        self.assertEqual(len(usable), 1)
        self.assertEqual(exclusions[0]["reason"], "sequence-too-long")
        self.assertEqual(exclusions[0]["line"], 2)
        self.assertEqual(exclusions[0]["sourceIds"], ["s"])

    def test_resume_checkpoint_seal_detects_optimizer_mutation(self):
        worker = module("worker")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "trainer_state.json").write_text('{"global_step":1}')
            for name in ["adapter_model.safetensors", "optimizer.pt", "scheduler.pt", "rng_state.pth"]:
                (path / name).write_bytes(b"state")
            seal = worker.seal_checkpoint(path, 1, "identity")
            self.assertEqual(worker.verify_checkpoint_seal(path, "identity")["step"], 1)
            (path / "optimizer.pt").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "checkpoint.*hash"):
                worker.verify_checkpoint_seal(path, "identity")

    def test_budget_counts_previous_chunks_and_cancel_file(self):
        worker = module("worker")
        config = module("config").validate_config({"maxMinutes": 1})
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            budget = worker.RunBudget(output, config, previous_active=61)
            with self.assertRaisesRegex(worker.BudgetExceeded, "time"):
                budget.check()
            (output / "cancel.requested").write_text("cancel")
            with self.assertRaises(worker.RunCancelled):
                worker.RunBudget(output, config).check()
            for invalid in (-1, math.nan, math.inf, True):
                with self.subTest(previous=invalid), self.assertRaisesRegex(ValueError, "activeSeconds"):
                    worker.RunBudget(output, config, previous_active=invalid)

    def test_owned_run_lock_prevents_concurrent_mutation(self):
        worker = module("worker")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with worker.run_lock(output, "r"):
                with self.assertRaisesRegex(ValueError, "already running"):
                    with worker.run_lock(output, "r"):
                        self.fail("second process acquired owned run")
            self.assertFalse((output / ".worker.lock").exists())

    def test_setup_spec_pins_library_in_isolated_environment_without_executing(self):
        worker = module("worker")
        with tempfile.TemporaryDirectory() as temp:
            spec = worker.setup_spec(Path(temp))
            self.assertTrue(Path(spec["python"]).is_relative_to(Path(temp)))
            self.assertIn("unsloth==2026.9.4", spec["packages"])
            self.assertIn("torchvision==0.26.0+cu130", spec["torch"]["companions"])
            self.assertTrue(any("triton-windows==3.6.0.post26" in item for item in spec["packages"]))
            self.assertTrue(any("torchao==0.17.0" in item for item in spec["packages"]))
            self.assertEqual(spec["license"], "Apache-2.0 core library")
            self.assertFalse((Path(temp) / "venv").exists())

    def test_probe_cli_persists_exact_missing_runtime_blockers(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "probe.json"
            proc = subprocess.run([sys.executable, "-S", str(HERE / "worker.py"), "probe", "--output", str(output)], capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            result = json.loads(output.read_text())
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(result["blockers"])
            self.assertEqual(result["python"], sys.executable)

    def test_probe_rejects_wrong_direct_versions_and_cuda_builds(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        worker = module("worker")
        versions = {name: "installed" for name in worker.DEPENDENCIES}
        versions.update({"torch": "2.11.0+cpu", "torchvision": "0.26.0+cpu", "xformers": "0.0.34", "bitsandbytes": "0.50.1"})
        fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False), version=SimpleNamespace(cuda=None))
        with patch.object(worker, "package_versions", return_value=versions), patch.dict(sys.modules, {"torch": fake_torch}):
            result = worker.probe()
        self.assertEqual(result["status"], "blocked")
        for name in ("torch", "torchvision", "xformers", "unsloth", "trl"):
            self.assertTrue(any("package version mismatch: " + name + " " in item for item in result["blockers"]), name)
        self.assertEqual(result["coreImport"]["status"], "not-run")

    def test_dependency_pins_filter_platform_specific_packages(self):
        worker = module("worker")
        windows = worker.required_package_versions("win32")
        linux = worker.required_package_versions("linux")
        self.assertIn("triton-windows", windows)
        self.assertNotIn("triton", windows)
        self.assertIn("triton", linux)
        self.assertNotIn("triton-windows", linux)
        for pins in (windows, linux):
            self.assertEqual(pins["torch"], "2.11.0+cu130")
            self.assertEqual(pins["torchvision"], "0.26.0+cu130")
            self.assertEqual(pins["xformers"], "0.0.35")
            self.assertEqual(worker.package_pin_blockers(pins, pins), [])

    def test_setup_reinstalls_an_import_ready_environment_with_wrong_pins(self):
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from unittest.mock import patch
        import io
        worker = module("worker")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = worker.setup_spec(root)
            python = Path(spec["python"])
            python.parent.mkdir(parents=True)
            python.touch()
            worker.atomic_json(root / "environment-owner.json", {"owner": "opencore-learning"})
            requirements = worker.read_json(HERE / "requirements.json")
            pins = {}
            for entry in [requirements["torch"]["requirement"], *requirements["torch"]["companions"], *requirements["packages"]]:
                requirement, _, marker = entry.partition(";")
                if marker and sys.platform not in marker:
                    continue
                name, version = requirement.strip().split("==")
                pins[name] = version
            installed = False

            def child(command, **kwargs):
                nonlocal installed
                if "probe" in command:
                    packages = dict(pins)
                    if not installed:
                        packages["trl"] = "0.24.0"
                    worker.atomic_json(root / "probe.json", {"ready": True, "trainingReady": True, "packages": packages})
                elif "install" in command:
                    installed = True
                return SimpleNamespace(returncode=0)

            output = root / "setup-receipt.json"
            with patch.object(worker.subprocess, "run", side_effect=child), redirect_stdout(io.StringIO()):
                result = worker.setup(root, output)
            receipt = worker.read_json(output)
            self.assertEqual(result, 0)
            self.assertEqual(receipt["status"], "ready")
            self.assertFalse(receipt["reused"])
            self.assertEqual(receipt["probe"]["packages"]["trl"], "0.23.1")

    def test_blocked_train_persists_receipt_raw_error_and_final_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = fixture(root)
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request))
            proc = subprocess.run([sys.executable, "-S", str(HERE / "worker.py"), "train", "--request", str(request_path)], capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 1)
            receipt = json.loads((root / "run" / "receipt.json").read_text())
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["lastStep"], 0)
            self.assertEqual(receipt["previousActiveSeconds"], 0)
            self.assertEqual(receipt["timeBudgetSeconds"], 1800)
            self.assertRegex(receipt["invocationStartedAt"], r"T\d\d:\d\d:\d\d\.\d{6}Z$")
            self.assertGreater(receipt["activeSeconds"], 0)
            self.assertIn("missing required package", receipt["error"]["message"])
            self.assertIn("RuntimeError", (root / "run" / "stderr.log").read_text())
            self.assertEqual(json.loads(proc.stdout.splitlines()[-1])["status"], "failed")
            self.assertFalse((root / "run" / "candidate").exists())

    def test_terminal_receipt_is_preserved_when_a_duplicate_start_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = fixture(root)
            output = root / "run"
            output.mkdir()
            original = {"runId": request["runId"], "status": "accepted", "lastStep": 25, "gates": {"comparisons": [{"passed": True}]}}
            (output / "receipt.json").write_text(json.dumps(original))
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request))
            proc = subprocess.run([sys.executable, "-S", str(HERE / "worker.py"), "train", "--request", str(request_path)], capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(json.loads((output / "receipt.json").read_text()), original)

    def test_unchanged_worse_nonfinite_and_small_evaluation_are_rejected(self):
        worker = module("worker")
        config = module("config").validate_config({})
        baseline = {"eval_loss": 2.0, "samples": 8}
        for candidate in [{"eval_loss": 2.0, "samples": 8}, {"eval_loss": 2.1, "samples": 8}, {"eval_loss": math.nan, "samples": 8}, {"eval_loss": 1.0, "samples": 7}]:
            gate = worker.evaluate_gates(baseline, candidate, config)
            self.assertEqual(gate["status"], "rejected")
            self.assertTrue(any(not item["passed"] for item in gate["comparisons"]))

    def test_objective_threshold_and_regression_observations_are_retained(self):
        worker = module("worker")
        config = module("config").validate_config({"minimumImprovement": 0.1, "regressionGates": [{"metric": "accuracy", "direction": "higher", "maximumRegression": 0.05}]})
        result = worker.evaluate_gates({"eval_loss": 2.0, "samples": 8, "accuracy": 0.8}, {"eval_loss": 1.8, "samples": 8, "accuracy": 0.7}, config)
        self.assertEqual(result["status"], "rejected")
        regression = next(item for item in result["comparisons"] if item["gate"] == "regression:accuracy")
        self.assertAlmostEqual(regression["observed"], 0.1)
        self.assertEqual(regression["threshold"], 0.05)
        self.assertEqual(worker.evaluate_gates({"eval_loss": 2.0, "samples": 8}, {"eval_loss": 1.8, "samples": 8}, module("config").validate_config({}))["status"], "accepted")

    def test_improvement_at_threshold_is_not_rejected_due_to_float_subtraction(self):
        worker = module("worker")
        config = module("config").validate_config({"minimumImprovement": 0.1})
        gate = worker.evaluate_gates({"eval_loss": 1.0, "samples": 8}, {"eval_loss": 0.9, "samples": 8}, config)
        self.assertEqual(gate["status"], "accepted")
        self.assertEqual(worker.evaluate_gates({"eval_loss": 1.0, "samples": 8}, {"eval_loss": 1.0, "samples": 8}, config)["status"], "rejected")

    def test_dpo_uses_improving_heldout_preference_margin(self):
        config = module("config").validate_config({"method": "dpo"})
        result = module("worker").evaluate_gates({"preference_margin": 0.15, "samples": 8}, {"preference_margin": 0.14, "samples": 8}, config)
        self.assertEqual(result["status"], "rejected")

    def test_chunk_target_never_resets_total_schedule_or_duplicates_steps(self):
        worker = module("worker")
        self.assertEqual(worker.next_chunk(0, 100, 25), {"startStep": 0, "endStep": 25, "totalSteps": 100})
        self.assertEqual(worker.next_chunk(25, 100, 25), {"startStep": 25, "endStep": 50, "totalSteps": 100})
        self.assertEqual(worker.next_chunk(90, 100, 25), {"startStep": 90, "endStep": 100, "totalSteps": 100})
        with self.assertRaises(ValueError):
            worker.next_chunk(100, 100, 25)

    def test_completed_optimizer_checkpoint_resumes_only_final_evaluation(self):
        worker = module("worker")
        self.assertEqual(worker.execution_phase(100, 100, 25), {"phase": "evaluation", "startStep": 100, "endStep": 100, "totalSteps": 100})
        self.assertEqual(worker.execution_phase(25, 100, 25)["phase"], "training")
        with self.assertRaisesRegex(ValueError, "step"):
            worker.execution_phase(101, 100, 25)

    def test_checkpoint_callback_stops_only_after_the_target_optimizer_step(self):
        from types import SimpleNamespace
        worker = module("worker")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            config = module("config").validate_config({})
            import io
            sink = worker.EventSink(output, "r", io.StringIO())
            budget = worker.RunBudget(output, config)
            callback = worker.checkpoint_callback(object, {"startStep": 25, "endStep": 50, "totalSteps": 100}, {"outputDir": str(output)}, budget, sink, {}, "invocation", 0)
            control = SimpleNamespace(should_save=False, should_training_stop=False)
            callback.on_step_end(None, SimpleNamespace(global_step=49), control)
            self.assertFalse(control.should_training_stop)
            callback.on_step_end(None, SimpleNamespace(global_step=50), control)
            self.assertTrue(control.should_training_stop)
            self.assertTrue(control.should_save)

    def test_checkpoint_persists_cumulative_invalid_metrics_before_trainer_returns(self):
        from types import SimpleNamespace
        import io
        worker = module("worker")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            checkpoint = output / "checkpoint-1"
            checkpoint.mkdir()
            (checkpoint / "trainer_state.json").write_text('{"global_step":1}')
            for name in ("optimizer.pt", "scheduler.pt", "rng_state.pth", "adapter_model.safetensors"):
                (checkpoint / name).write_bytes(b"state")
            receipt = {}
            prepared = {"outputDir": str(output), "identitySha256": "identity"}
            budget = worker.RunBudget(output, module("config").validate_config({}))
            sink = worker.EventSink(output, "r", io.StringIO())
            callback = worker.checkpoint_callback(object, {"startStep": 0, "endStep": 1, "totalSteps": 2}, prepared, budget, sink, receipt, "i", 0)
            control = SimpleNamespace(should_save=False, should_training_stop=False)
            callback.on_log(None, SimpleNamespace(global_step=1), control, {"grad_norm": math.inf})
            callback.on_save(SimpleNamespace(output_dir=str(output)), SimpleNamespace(global_step=1), control)
            saved = worker.read_json(output / "receipt.json")
            self.assertTrue(saved.get("invalidTrainingMetrics"))
            self.assertTrue(worker.verify_checkpoint_seal(checkpoint, "identity").get("invalidTrainingMetrics"))
            resumed = worker.checkpoint_callback(object, {"startStep": 1, "endStep": 2, "totalSteps": 2}, prepared, budget, sink, saved, "j", 0)
            resumed.on_log(None, SimpleNamespace(global_step=1), control, {"loss": 1.0})
            self.assertTrue(resumed.invalid_training)

    def test_resume_recovers_invalid_metrics_from_seal_if_receipt_write_was_interrupted(self):
        from unittest.mock import patch
        worker = module("worker")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            checkpoint = output / "checkpoint-1"
            checkpoint.mkdir()
            (checkpoint / "trainer_state.json").write_text('{"global_step":1}')
            for name in ("optimizer.pt", "scheduler.pt", "rng_state.pth", "adapter_model.safetensors"):
                (checkpoint / name).write_bytes(b"state")
            worker.seal_checkpoint(checkpoint, 1, "identity", invalid_training=True)
            prepared = {"outputDir": str(output), "identitySha256": "identity", "config": module("config").validate_config({})}
            receipt = {"lastStep": 1, "invalidTrainingMetrics": False}
            with patch.object(worker, "load_training_model", side_effect=RuntimeError("stop-before-model-load")), self.assertRaisesRegex(RuntimeError, "stop-before-model-load"):
                worker.execute_training(prepared, {"resumeCheckpoint": str(checkpoint)}, {}, None, receipt, None, "i")
            self.assertTrue(receipt["invalidTrainingMetrics"])

    def test_frozen_manifest_edits_are_detected_even_with_a_copied_identity_hash(self):
        worker = module("worker")
        with tempfile.TemporaryDirectory() as temp:
            prepared = worker.prepare_request(fixture(Path(temp)))
            frozen = worker.frozen_manifest(prepared)
            frozen["config"] = {**frozen["config"], "learningRate": 0.1}
            with self.assertRaisesRegex(ValueError, "manifest identity"):
                worker.verify_resume_identity(frozen, prepared)

    def test_resume_rejects_modified_baseline_measurements(self):
        worker = module("worker")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "baseline.json"
            path.write_text(json.dumps({"identitySha256": "source", "tokenizedSha256": "tokens", "metrics": {"eval_loss": 2, "samples": 8}}))
            expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(worker.load_baseline(path, "source", "tokens", expected_hash)["eval_loss"], 2)
            record = json.loads(path.read_text())
            record["metrics"]["eval_loss"] = 100
            path.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "baseline.*hash"):
                worker.load_baseline(path, "source", "tokens", expected_hash)

    def test_epoch_and_step_caps_both_bound_total_updates(self):
        worker = module("worker")
        config = module("config").validate_config({"epochs": 2, "batchSize": 2, "gradientAccumulation": 2, "maxSteps": 100})
        self.assertEqual(worker.total_training_steps(9, config), 6)
        config["maxSteps"] = 4
        self.assertEqual(worker.total_training_steps(9, config), 4)

    def test_optimizer_checkpoint_is_required_and_checked_against_global_step(self):
        worker = module("worker")
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "checkpoint-25"
            checkpoint.mkdir()
            (checkpoint / "adapter_model.safetensors").write_bytes(b"adapter")
            (checkpoint / "trainer_state.json").write_text('{"global_step":25}')
            with self.assertRaisesRegex(ValueError, "optimizer"):
                worker.validate_checkpoint(checkpoint, 25)
            for name in ["optimizer.pt", "scheduler.pt", "rng_state.pth"]:
                (checkpoint / name).write_bytes(b"state")
            self.assertEqual(worker.validate_checkpoint(checkpoint, 25)["step"], 25)
            with self.assertRaisesRegex(ValueError, "step"):
                worker.validate_checkpoint(checkpoint, 24)

    def test_raw_event_append_preserves_microsecond_utc_and_nonfinite_observation(self):
        worker = module("worker")
        with tempfile.TemporaryDirectory() as temp:
            import io
            stdout = io.StringIO()
            sink = worker.EventSink(Path(temp), "run-1", stdout)
            sink.emit("log", 25, {"loss": math.inf})
            sink.emit("checkpoint", 25, checkpoint="checkpoint-25", status="checkpoint-ready")
            events = [json.loads(line) for line in (Path(temp) / "events.jsonl").read_text().splitlines()]
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["metrics"]["loss"], "Infinity")
            self.assertRegex(events[0]["timestamp"], r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z")
            self.assertEqual(events[1]["step"], 25)
            self.assertEqual(stdout.getvalue().splitlines(), (Path(temp) / "events.jsonl").read_text().splitlines())

    def test_plan_cli_validates_real_files_without_training(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = fixture(root)
            request_path = root / "request.json"
            output_path = root / "plan.json"
            request_path.write_text(json.dumps(request))
            proc = subprocess.run([sys.executable, str(HERE / "worker.py"), "plan", "--request", str(request_path), "--output", str(output_path)], capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(output_path.read_text())
            self.assertEqual(result["status"], "planned")
            self.assertEqual(result["counts"]["validation"], 8)
            self.assertFalse((root / "run" / "candidate").exists())


if __name__ == "__main__":
    unittest.main()
