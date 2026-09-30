"""Exercise capture integrity against a real local HTTP fixture, never a model."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import benchmark_capture

SCRIPT = Path(__file__).with_name("benchmark_capture.py")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body):
        body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/echo/stats"):
            self.server.echo_route_requests += 1
            if self.server.echo_proxy:
                self.reply(200, {"history_mode": "persistent_echo", "archive_capacity": "limited by disk and SQLite"})
            else:
                self.reply(404, {"error": "not found"})
            return
        self.reply(200, {"status": "ok", "model": "control-model"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/tokenize":
            self.server.tokenize_requests.append(body)
            self.reply(200, {"tokens": body.get("content", "").split()})
            return
        prompt = body["messages"][-1]["content"]
        if prompt.startswith("SPEED_QUALIFICATION:"):
            self.server.speed_requests.append(body)
            time.sleep(self.server.preflight_delay)
            content = " ".join(f"token{i}" for i in range(self.server.preflight_token_count))
            self.reply(200, {"id": "speed-fixture", "object": "chat.completion", "model": "control-model",
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                                          "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 1, "completion_tokens": self.server.preflight_token_count,
                                       "total_tokens": self.server.preflight_token_count + 1}})
            return
        self.server.requests.append(body)
        if prompt == "beta" and self.server.fail_beta:
            self.server.fail_beta = False
            self.reply(502, {"error": {"message": "controlled interruption"}})
            return
        if self.server.malformed:
            self.reply(200, {"choices": []})
            return
        self.reply(200, {"id": "fixture", "object": "chat.completion", "model": "control-model",
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": prompt.upper()},
                                      "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs.json"
        self.rows = [{"id": str(i), "prompt": text,
                      "prompt_sha256": hashlib.sha256(text.encode()).hexdigest()}
                     for i, text in enumerate(("alpha", "beta"))]
        self.inputs.write_text(json.dumps({"schema": 1, "benchmark": "control", "rows": self.rows}))
        artifact = self.root / "fixture-weights.bin"
        artifact.write_bytes(b"fixture-not-model")
        runtime = self.root / "selection.py"
        runtime.write_bytes(b"old runtime")
        self.identity = self.root / "identity.json"
        self.identity.write_text(json.dumps({"model": "control-model", "evidence_kind": "fixture",
                                             "artifacts": [{"path": str(artifact), "bytes": 17,
                                                            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}],
                                             "runtime_files": [{"path": str(runtime), "bytes": runtime.stat().st_size,
                                                                "sha256": hashlib.sha256(runtime.read_bytes()).hexdigest()}]}))
        self.output = self.root / "captures"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.requests, self.server.speed_requests, self.server.tokenize_requests = [], [], []
        self.server.fail_beta, self.server.malformed = False, False
        self.server.preflight_delay, self.server.preflight_token_count = 0, 80
        self.server.echo_route_requests = 0
        self.server.echo_proxy = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def capture(self, disk_free=300000000000, *, identity=None, output=None, resume_from=None,
                thinking_budget_tokens=None):
        # Test-only child interpreter: CI fixture storage is unrelated to the
        # user's reserve. The production CLI has no flag to bypass that guard.
        runner = ("import runpy,sys; from types import SimpleNamespace; from unittest.mock import patch; "
                  "script=sys.argv.pop(1); free=int(sys.argv.pop(1)); sys.argv[0]=script; "
                  "mock=patch('shutil.disk_usage', return_value=SimpleNamespace(free=free)); "
                  "mock.start(); runpy.run_path(script,run_name='__main__')")
        identity = identity or self.identity
        output = output or self.output
        args = [sys.executable, '-c', runner, str(SCRIPT), str(disk_free), "--url", f"http://127.0.0.1:{self.server.server_port}",
                "--model", "control-model", "--inputs", str(self.inputs), "--identity", str(identity),
                "--output", str(output), "--max-tokens", "8"]
        if resume_from:
            args.extend(["--resume-from", str(resume_from)])
        if thinking_budget_tokens is not None:
            args.extend(["--thinking-budget-tokens", str(thinking_budget_tokens)])
        return subprocess.run(args, capture_output=True, text=True, timeout=20)

    def test_resume_keeps_verified_rows_with_hash_bound_runtime_lineage(self):
        self.server.fail_beta = True
        self.assertNotEqual(self.capture().returncode, 0)
        original_rows = [json.loads(line) for line in (self.output / "responses.jsonl").read_bytes().splitlines()]
        self.assertEqual(len(original_rows), 1)

        changed_runtime = self.root / "selection.py"
        changed_runtime.write_bytes(b"fixed runtime")
        new_identity = json.loads(self.identity.read_text())
        new_identity["runtime_files"] = [{"path": str(changed_runtime), "bytes": changed_runtime.stat().st_size,
                                           "sha256": hashlib.sha256(changed_runtime.read_bytes()).hexdigest()}]
        new_identity_path = self.root / "identity-new.json"
        new_identity_path.write_text(json.dumps(new_identity))
        new_output = self.root / "resumed-capture"
        result = self.capture(identity=new_identity_path, output=new_output, resume_from=self.output)
        self.assertEqual(result.returncode, 0, result.stderr)

        rows = [json.loads(line) for line in (new_output / "responses.jsonl").read_bytes().splitlines()]
        self.assertEqual([row["id"] for row in rows], ["0", "1"])
        self.assertEqual(rows[0]["response"], original_rows[0]["response"])
        old_hash = hashlib.sha256((self.output / "identity.json").read_bytes()).hexdigest()
        new_hash = hashlib.sha256(new_identity_path.read_bytes()).hexdigest()
        self.assertEqual(rows[0]["runtime_identity_sha256"], old_hash)
        self.assertEqual(rows[1]["runtime_identity_sha256"], new_hash)
        benchmark_capture.load_grading_capture(self.inputs, new_output)

    def test_resume_allows_only_the_reviewed_peg_native_schema_fallback(self):
        old = {
            "model": "DualCore ECHO",
            "evidence_kind": "real_model",
            "profile": "dualcore-echo",
            "checkpoint": {"sha256": "fixed-checkpoint"},
            "complete_towers": 2,
            "candidate_budget": 2,
            "sampling": {"temperature": 0},
            "coupling_trained": False,
            "request_isolation": "fresh_conversation_per_sample",
            "artifacts": [{"path": "weights.gguf", "bytes": 4, "sha256": "fixed-weights"}],
            "runtime_files": [{
                "path": "runtime.py",
                "bytes": 28368,
                "sha256": "a674f0368709687b1cdcafe88c182957c80bd35b9e60e8b0ab53444f992305a0",
            }],
        }
        new = json.loads(json.dumps(old))
        new["runtime_files"] = [{
            "path": "runtime.py",
            "bytes": 29906,
            "sha256": "19ba307825d656aaecc4919b6f979151fb2cc2cde7c3febcea979f71401491e3",
        }]

        benchmark_capture._validate_runtime_transition(old, new)

        changed_again = json.loads(json.dumps(new))
        changed_again["runtime_files"][0]["sha256"] = "unreviewed-change"
        with self.assertRaisesRegex(ValueError, "outside the reviewed parser"):
            benchmark_capture._validate_runtime_transition(old, changed_again)

    def test_resume_allows_the_reviewed_single_brain_failure_fallback(self):
        old = {
            "model": "DualCore ECHO",
            "evidence_kind": "real_model",
            "profile": "dualcore-echo",
            "checkpoint": {"sha256": "fixed-checkpoint"},
            "complete_towers": 2,
            "candidate_budget": 2,
            "sampling": {"temperature": 0},
            "coupling_trained": False,
            "request_isolation": "fresh_conversation_per_sample",
            "artifacts": [{"path": "weights.gguf", "bytes": 4, "sha256": "fixed-weights"}],
            "runtime_files": [{
                "path": "dual.py",
                "bytes": 10616,
                "sha256": "fd5e190bcf6d8097020b4864791e5cf1775654c23c2758e2cd5aceae41091060",
            }],
        }
        new = json.loads(json.dumps(old))
        new["runtime_files"] = [{
            "path": "dual.py",
            "bytes": 11522,
            "sha256": "8ddeeb34da432c4269c47f7ff5e60d8b8fbee4661233ace50930fca510a726d2",
        }]

        benchmark_capture._validate_runtime_transition(old, new)

        changed_again = json.loads(json.dumps(new))
        changed_again["runtime_files"][0]["sha256"] = "unreviewed-change"
        with self.assertRaisesRegex(ValueError, "outside the reviewed parser"):
            benchmark_capture._validate_runtime_transition(old, changed_again)

    def test_resume_allows_reviewed_dualcore_text_parser_fallback(self):
        old = {
            "model": "DualCore ECHO", "evidence_kind": "real_model", "profile": "dualcore-echo",
            "checkpoint": {"sha256": "fixed-checkpoint"}, "complete_towers": 2,
            "candidate_budget": 2, "sampling": {"temperature": 0},
            "coupling_trained": False, "request_isolation": "fresh_conversation_per_sample",
            "artifacts": [{"path": "weights.gguf", "bytes": 4, "sha256": "fixed-weights"}],
            "runtime_files": [
                {"path": "runtime.py", "bytes": 29906,
                 "sha256": "19ba307825d656aaecc4919b6f979151fb2cc2cde7c3febcea979f71401491e3"},
                {"path": "dual.py", "bytes": 11522,
                 "sha256": "8ddeeb34da432c4269c47f7ff5e60d8b8fbee4661233ace50930fca510a726d2"},
                {"path": "llama-common.dll", "bytes": 8198656,
                 "sha256": "7270f14eb41b55d5d596b214dd9cae66a202897b8e0bb60bf2e8e7bbf4434308"},
            ],
        }
        new = json.loads(json.dumps(old))
        new["runtime_files"][0].update(bytes=29902,
            sha256="0f6e8dae4faaba2f4fafc94616582aa6b797a354b04b69125b497bd01bacc252")
        new["runtime_files"][1].update(bytes=14472,
            sha256="b2eb4b3f64df494f31959d6516d35d00cbbd6f9e6577e0a0137531f6d5883907")
        new["runtime_files"].append({"path": "llama-common.dll", "bytes": 8199680,
            "sha256": "210fa3e2fc550eb87774b18102a2d9e1488983c54f5d864a5624291d366af5fb"})
        benchmark_capture._validate_runtime_transition(old, new)

        changed_again = json.loads(json.dumps(new))
        changed_again["runtime_files"][0]["sha256"] = "unreviewed-change"
        with self.assertRaisesRegex(ValueError, "outside the reviewed parser"):
            benchmark_capture._validate_runtime_transition(old, changed_again)

    def test_resume_binds_reviewed_echo_harness_additions(self):
        old = {
            "model": "DualCore ECHO", "evidence_kind": "real_model", "profile": "dualcore-echo",
            "checkpoint": {"sha256": "fixed-checkpoint"}, "complete_towers": 2,
            "candidate_budget": 2, "sampling": {"temperature": 0},
            "coupling_trained": False, "request_isolation": "fresh_conversation_per_sample",
            "artifacts": [{"path": "weights.gguf", "bytes": 4, "sha256": "fixed-weights"}],
            "runtime_files": [{"path": "selection.py", "bytes": 4, "sha256": "same-runtime"}],
        }
        new = json.loads(json.dumps(old))
        new["harness_files"] = [
            {"path": "echo_server.py", "bytes": 116359,
             "sha256": "1e09fcb6afb04b4ef2845694dd060e0edc76d3bc8a0cbdc95116d09e9d1688dd"},
            {"path": "echo_context.py", "bytes": 22043,
             "sha256": "06fafc0e4eed4cff2a13b9a0c244b65f0b319e6fd83896e97ecb80cba61dbd51"},
        ]
        benchmark_capture._validate_runtime_transition(old, new)

        changed_again = json.loads(json.dumps(new))
        changed_again["harness_files"][0]["sha256"] = "unreviewed-change"
        with self.assertRaisesRegex(ValueError, "unreviewed ECHO harness"):
            benchmark_capture._validate_runtime_transition(old, changed_again)

    def test_complete_capture_preserves_answers_and_resume_makes_no_new_requests(self):
        first = self.capture()
        self.assertEqual(first.returncode, 0, first.stderr)
        records = (self.output / "responses.jsonl").read_bytes()
        rows = [json.loads(line) for line in records.splitlines()]
        self.assertEqual([row["response"]["choices"][0]["message"]["content"] for row in rows], ["ALPHA", "BETA"])
        report = json.loads((self.output / "capture-manifest.json").read_text())
        self.assertEqual(report["status"], "complete")
        self.assertFalse(report["model_quality_measured"])
        self.assertEqual(self.capture().returncode, 0)
        self.assertEqual((self.output / "responses.jsonl").read_bytes(), records)
        self.assertEqual(len(self.server.requests), 2)

    def test_interrupted_capture_resumes_without_overwriting_first_answer(self):
        self.server.fail_beta = True
        self.assertNotEqual(self.capture().returncode, 0)
        first = (self.output / "responses.jsonl").read_bytes()
        self.assertEqual(len(first.splitlines()), 1)
        self.assertEqual(json.loads((self.output / "capture-manifest.json").read_text())["status"], "partial")
        self.assertEqual(self.capture().returncode, 0)
        final = (self.output / "responses.jsonl").read_bytes()
        self.assertTrue(final.startswith(first))
        self.assertEqual([json.loads(line)["id"] for line in final.splitlines()], ["0", "1"])

    def test_capture_records_exact_visible_answer_speed_qualification(self):
        result = self.capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        qualification = json.loads((self.output / "speed-qualification.json").read_text())
        self.assertEqual(qualification["status"], "passed")
        self.assertGreaterEqual(qualification["visible_answer_tokens"], 50)
        self.assertGreaterEqual(qualification["visible_tokens_per_second"], 20)
        self.assertEqual(qualification["model"], "control-model")
        manifest = json.loads((self.output / "capture-manifest.json").read_text())
        self.assertEqual(manifest["schema"], 2)
        self.assertEqual(manifest["speed_gate_schema"], 1)
        self.assertEqual(manifest["speed_qualification_sha256"],
                         benchmark_capture.file_hash(self.output / "speed-qualification.json"))
        benchmark_capture.load_grading_capture(self.inputs, self.output)

    def test_reasoning_budget_is_bound_to_preflight_and_every_sample(self):
        identity = json.loads(self.identity.read_text())
        identity['request_isolation'] = 'fresh_conversation_per_sample'
        self.identity.write_text(json.dumps(identity))
        result = self.capture(thinking_budget_tokens=3072)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.server.speed_requests[0]['thinking_budget_tokens'], 3072)
        self.assertTrue(all(row['thinking_budget_tokens'] == 3072 for row in self.server.requests))
        manifest = json.loads((self.output / 'capture-manifest.json').read_text())
        self.assertEqual(manifest['binding']['thinking_budget_tokens'], 3072)
        qualification = json.loads((self.output / 'speed-qualification.json').read_text())
        self.assertEqual(qualification['thinking_budget_tokens'], 3072)
        records = [json.loads(line) for line in (self.output / 'responses.jsonl').read_text().splitlines()]
        self.assertTrue(all(row['request']['thinking_budget_tokens'] == 3072 for row in records))
        benchmark_capture.load_grading_capture(self.inputs, self.output)

    def test_resume_rejects_a_different_reasoning_budget(self):
        self.server.fail_beta = True
        self.assertNotEqual(self.capture(thinking_budget_tokens=3072).returncode, 0)
        first_response = (self.output / 'responses.jsonl').read_bytes()
        resumed = self.root / 'different-budget-resume'
        result = self.capture(output=resumed, resume_from=self.output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('generation settings', result.stderr)
        self.assertFalse(resumed.exists())
        self.assertEqual((self.output / 'responses.jsonl').read_bytes(), first_response)

    def test_grading_rejects_tampered_speed_qualification(self):
        self.assertEqual(self.capture().returncode, 0)
        path = self.output / "speed-qualification.json"
        qualification = json.loads(path.read_text())
        qualification["visible_tokens_per_second"] = 9999
        path.write_text(json.dumps(qualification))
        with self.assertRaisesRegex(ValueError, "(?i)speed qualification hash mismatch"):
            benchmark_capture.load_grading_capture(self.inputs, self.output)

    def test_slow_profile_is_rejected_before_any_benchmark_sample(self):
        self.server.preflight_delay = 3.1
        self.server.preflight_token_count = 55
        result = self.capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("20 tokens/s", result.stderr)
        qualification = json.loads((self.output / "speed-qualification.json").read_text())
        self.assertEqual(qualification["status"], "rejected")
        self.assertLess(qualification["visible_tokens_per_second"], 20)
        self.assertEqual(self.server.requests, [])
        self.assertEqual(len(self.server.speed_requests), 1)
        self.assertFalse((self.output / "capture-manifest.json").exists())

    def test_echo_profile_rejects_raw_model_endpoint_before_generation(self):
        identity = json.loads(self.identity.read_text())
        identity["profile"] = "dualcore-echo"
        self.identity.write_text(json.dumps(identity))
        result = self.capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ECHO proxy", result.stderr)
        self.assertEqual(self.server.echo_route_requests, 1)
        self.assertEqual(self.server.speed_requests, [])
        self.assertEqual(self.server.requests, [])

    def test_echo_capture_records_verified_archive_route(self):
        self.server.echo_proxy = True
        identity = json.loads(self.identity.read_text())
        identity["profile"] = "dualcore-echo"
        identity["request_isolation"] = "fresh_conversation_per_sample"
        self.identity.write_text(json.dumps(identity))
        result = self.capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output / "capture-manifest.json").read_text())
        self.assertEqual(manifest["echo_route_verification"]["status"], "passed")
        self.assertEqual(manifest["echo_route_verification"]["history_mode"], "persistent_echo")
        self.assertEqual(manifest["binding"]["echo_max_total_tokens"], 8)
        rows = [json.loads(line) for line in (self.output / "responses.jsonl").read_text().splitlines()]
        self.assertTrue(all(row["request"]["echo_max_total_tokens"] == 8 for row in rows))
        self.assertEqual(self.server.echo_route_requests, 1)
        benchmark_capture.load_grading_capture(self.inputs, self.output)

    def test_grading_rejects_echo_capture_without_verified_archive_route(self):
        self.server.echo_proxy = True
        identity = json.loads(self.identity.read_text())
        identity["profile"] = "dualcore-echo"
        self.identity.write_text(json.dumps(identity))
        self.assertEqual(self.capture().returncode, 0)
        path = self.output / "capture-manifest.json"
        manifest = json.loads(path.read_text())
        manifest.pop("echo_route_verification")
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "(?i)ECHO route"):
            benchmark_capture.load_grading_capture(self.inputs, self.output)

    def test_failed_request_retains_server_error_body_without_retrying(self):
        self.server.fail_beta = True
        result = self.capture()
        self.assertNotEqual(result.returncode, 0)
        errors = [json.loads(line) for line in (self.output / 'errors.jsonl').read_bytes().splitlines()]
        self.assertEqual(len(errors), 1)
        self.assertIn('controlled interruption', errors[0]['error'])
        self.assertEqual(len(self.server.requests), 2)

    def test_changed_response_rejects_resume(self):
        self.assertEqual(self.capture().returncode, 0)
        path = self.output / "responses.jsonl"
        path.write_bytes(path.read_bytes().replace(b"ALPHA", b"ALTER"))
        result = self.capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hash mismatch", result.stderr)

    def test_duplicate_capture_rejects_resume(self):
        self.assertEqual(self.capture().returncode, 0)
        path = self.output / "responses.jsonl"
        with path.open("ab") as handle:
            handle.write(path.read_bytes().splitlines()[0] + b"\n")
        result = self.capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", result.stderr.lower())

    def test_changed_input_rejects_generation(self):
        data = json.loads(self.inputs.read_text())
        data["rows"][0]["prompt"] = "modified"
        self.inputs.write_text(json.dumps(data))
        result = self.capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hash mismatch", result.stderr)
        self.assertEqual(len(self.server.requests), 0)

    def test_low_storage_stops_before_any_generation_request(self):
        result = self.capture(disk_free=99999999999)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Storage reserve below 100 GB', result.stderr)
        self.assertEqual(self.server.requests, [])
        self.assertEqual(self.server.speed_requests, [])
        self.assertFalse((self.output / 'responses.jsonl').exists())

    def test_malformed_api_response_remains_partial(self):
        self.server.malformed = True
        result = self.capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads((self.output / "capture-manifest.json").read_text())["status"], "partial")
        self.assertEqual((self.output / "responses.jsonl").read_bytes(), b"")

    def test_grading_rejects_partial_capture(self):
        self.server.fail_beta = True
        self.assertNotEqual(self.capture().returncode, 0)
        loader = getattr(benchmark_capture, "load_grading_capture", None)
        self.assertTrue(callable(loader), "Grading integrity loader is not implemented")
        with self.assertRaisesRegex(ValueError, "complete"):
            loader(self.inputs, self.output)

    def test_grading_rejects_changed_embedded_identity_kind(self):
        self.assertEqual(self.capture().returncode, 0)
        path = self.output / 'capture-manifest.json'
        manifest = json.loads(path.read_text())
        manifest['identity']['evidence_kind'] = 'real_model'
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'identity'):
            benchmark_capture.load_grading_capture(self.inputs, self.output)

    def test_grading_rejects_embedded_model_that_differs_from_binding(self):
        self.assertEqual(self.capture().returncode, 0)
        path = self.output / 'capture-manifest.json'
        manifest = json.loads(path.read_text())
        manifest['identity']['model'] = 'another-model'
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'identity'):
            benchmark_capture.load_grading_capture(self.inputs, self.output)

    def test_grading_rejects_changed_identity_snapshot_bytes(self):
        self.assertEqual(self.capture().returncode, 0)
        path = self.output / 'identity.json'
        self.assertTrue(path.is_file(), 'New capture must retain exact identity bytes')
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'identity'):
            benchmark_capture.load_grading_capture(self.inputs, self.output)

    def test_grading_rejects_missing_identity_snapshot_for_new_capture(self):
        self.assertEqual(self.capture().returncode, 0)
        path = self.output / 'capture-manifest.json'
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest['schema'], 2, 'New capture must declare its stronger identity schema')
        manifest.pop('identity_snapshot')
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'identity'):
            benchmark_capture.load_grading_capture(self.inputs, self.output)

    def test_grading_rejects_missing_answer_even_if_manifest_says_complete(self):
        self.assertEqual(self.capture().returncode, 0)
        path = self.output / "responses.jsonl"
        path.write_bytes(path.read_bytes().splitlines()[0] + b"\n")
        manifest_path = self.output / "capture-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["responses_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        loader = getattr(benchmark_capture, "load_grading_capture", None)
        self.assertTrue(callable(loader), "Grading integrity loader is not implemented")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            loader(self.inputs, self.output)

    def test_echo_requests_have_unique_fresh_conversations_and_retained_request_hashes(self):
        identity = json.loads(self.identity.read_text())
        identity['request_isolation'] = 'fresh_conversation_per_sample'
        self.identity.write_text(json.dumps(identity))
        result = self.capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        conversations = [request.get('conversation_id') for request in self.server.requests]
        self.assertTrue(all(conversations), 'ECHO requests did not declare isolated conversations')
        self.assertEqual(len(set(conversations)), 2)
        rows = [json.loads(line) for line in (self.output / 'responses.jsonl').read_bytes().splitlines()]
        for row, request in zip(rows, self.server.requests):
            self.assertEqual(row['request'], request)
            self.assertEqual(row['request_sha256'], hashlib.sha256(benchmark_capture.json_bytes(request)).hexdigest())
        benchmark_capture.load_grading_capture(self.inputs, self.output)

    def test_grading_rejects_reused_echo_conversation_even_with_valid_record_hashes(self):
        identity = json.loads(self.identity.read_text())
        identity['request_isolation'] = 'fresh_conversation_per_sample'
        self.identity.write_text(json.dumps(identity))
        self.assertEqual(self.capture().returncode, 0)
        path = self.output / 'responses.jsonl'
        rows = [json.loads(line) for line in path.read_bytes().splitlines()]
        self.assertIn('request', rows[0], 'Request provenance was not retained')
        rows[1]['request']['conversation_id'] = rows[0]['request']['conversation_id']
        rows[1]['request_sha256'] = hashlib.sha256(benchmark_capture.json_bytes(rows[1]['request'])).hexdigest()
        path.write_bytes(b''.join(benchmark_capture.json_bytes(row) + b'\n' for row in rows))
        manifest_path = self.output / 'capture-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['responses_sha256'] = benchmark_capture.file_hash(path)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'conversation'):
            benchmark_capture.load_grading_capture(self.inputs, self.output)


if __name__ == "__main__":
    unittest.main()
