"""Exercise capture integrity against a real local HTTP fixture, never a model."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
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
        self.reply(200, {"status": "ok", "model": "control-model"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        prompt = body["messages"][-1]["content"]
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
        self.identity = self.root / "identity.json"
        self.identity.write_text(json.dumps({"model": "control-model", "evidence_kind": "fixture",
                                             "artifacts": [{"path": str(artifact), "bytes": 17,
                                                            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}]}))
        self.output = self.root / "captures"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.requests, self.server.fail_beta, self.server.malformed = [], False, False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def capture(self, disk_free=300000000000):
        # Test-only child interpreter: CI fixture storage is unrelated to the
        # user's reserve. The production CLI has no flag to bypass that guard.
        runner = ("import runpy,sys; from types import SimpleNamespace; from unittest.mock import patch; "
                  "script=sys.argv.pop(1); free=int(sys.argv.pop(1)); sys.argv[0]=script; "
                  "mock=patch('shutil.disk_usage', return_value=SimpleNamespace(free=free)); "
                  "mock.start(); runpy.run_path(script,run_name='__main__')")
        return subprocess.run([sys.executable, '-c', runner, str(SCRIPT), str(disk_free), "--url", f"http://127.0.0.1:{self.server.server_port}",
                               "--model", "control-model", "--inputs", str(self.inputs),
                               "--identity", str(self.identity), "--output", str(self.output), "--max-tokens", "8"],
                              capture_output=True, text=True, timeout=20)

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
        result = self.capture(disk_free=199999999999)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Storage reserve below 200 GB', result.stderr)
        self.assertEqual(self.server.requests, [])
        self.assertEqual((self.output / 'responses.jsonl').read_bytes(), b'')

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
