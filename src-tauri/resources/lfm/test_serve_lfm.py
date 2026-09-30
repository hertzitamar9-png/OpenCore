from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import serve_lfm
import duocore.runtime as runtime
from dual import DualCoreEngine, LfmBackbone
from duocore.runtime import BackboneReply
from protocol import OutputStream, extract_internal_code_output


class RequestCaptureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        self.server.request_body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"},
                                        "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProfileEngineTests(unittest.TestCase):
    def test_unregistered_tool_syntax_remains_plain_model_output(self):
        decoder = OutputStream()
        text = '<|tool_call_start|>[not valid python<|tool_call_end|>'

        decoder.feed(text, tools=None)
        message = decoder.message(tools=None)

        self.assertEqual(text, message['content'])
        self.assertNotIn('tool_calls', message)

    def test_open_reasoning_prefix_splits_hidden_reasoning_from_the_final_answer(self):
        decoder = OutputStream()
        decoder.feed('<think>')
        decoder.feed('reasoning text</think>return 42')

        message = decoder.message()

        self.assertEqual('reasoning text', message['reasoning_content'])
        self.assertEqual('return 42', message['content'])

    def test_extracts_code_from_minimax_json_action_without_running_it(self):
        raw = ('<|startoftext|><minimax:tool_call><function=state_python><args>'
               '{"code":"def answer():\\n    return 42"}'
               '</arguments></function></tool_call>')
        self.assertEqual(('def answer():\n    return 42', 'minimax_python_code'),
                         extract_internal_code_output(raw))

    def test_extracts_code_when_minimax_json_contains_literal_newlines(self):
        raw = ('<minimax:tool_call><function=state_python><args>'
               '{"code": "def answer():\n    return 42"}'
               '</arguments></function></tool_call>')
        self.assertEqual(('def answer():\n    return 42', 'minimax_python_code'),
                         extract_internal_code_output(raw))

    def test_extracts_code_from_lfm_python_action_without_running_it(self):
        raw = "<|tool_call_start|>[stateful_python_code_exec(code='def answer():\\n    return 42')]<|tool_call_end|>"
        self.assertEqual(('def answer():\n    return 42', 'lfm_python_code_action'),
                         extract_internal_code_output(raw))

    def test_extracts_code_from_lfm_action_with_literal_newlines(self):
        raw = "<|tool_call_start|>[stateful_python_code_exec(code='def answer():\n    return 42')]<|tool_call_end|>"
        self.assertEqual(('def answer():\n    return 42', 'lfm_python_code_action'),
                         extract_internal_code_output(raw))

    def test_extracts_multiline_lfm_wrapper_with_unescaped_inner_quotes(self):
        code = "def answer():\n    return 'it\'s fine'\n"
        raw = "<|tool_call_start|>[stateful_python_code_exec(code='" + code + "')]<|tool_call_end|>"
        self.assertEqual((code, 'lfm_python_code_action'),
                         extract_internal_code_output(raw))

    def test_extracts_alternate_lfm_python_exec_wrapper(self):
        code = "def answer():\n    return 42\n"
        raw = "<|tool_call_start|>[stateful_python_exec(code='" + code + "')]<|tool_call_end|>"
        self.assertEqual((code, 'lfm_python_code_action'), extract_internal_code_output(raw))

    def test_extracts_new_python_file_text_from_lfm_edit_action(self):
        raw = "<|tool_call_start|>[edit(path='/tmp/a.py', old_text='old', new_text='def answer():\\n    return 42')]<|tool_call_end|>"
        self.assertEqual(('def answer():\n    return 42', 'lfm_python_edit_action'),
                         extract_internal_code_output(raw))

    def test_does_not_unwrap_unknown_or_malformed_tool_actions(self):
        self.assertIsNone(extract_internal_code_output(
            '<|tool_call_start|>[delete(path="/tmp/a.py")]<|tool_call_end|>'))

    def test_requested_thinking_budget_requires_positive_integer(self):
        self.assertEqual(serve_lfm.requested_thinking_budget({"thinking_budget_tokens": 96}), 96)
        self.assertIsNone(serve_lfm.requested_thinking_budget({}))
        for invalid in (True, 0, -1, 96.0, "96"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                serve_lfm.requested_thinking_budget({"thinking_budget_tokens": invalid})

    @patch.object(serve_lfm, "launch_llama_server")
    def test_dual_backbone_uses_closed_thinking_template(self, launch):
        serve_lfm.launch_dual_backbone(Path("runtime"), SimpleNamespace(port=18611), 32768)

        args, kwargs = launch.call_args
        self.assertTrue(kwargs["jinja"])
        self.assertEqual(kwargs["chat_template_file"], serve_lfm.ROOT / "chat_template_no_think.jinja")

    @patch.object(serve_lfm, "launch_llama_server")
    def test_dual_backbone_passes_reasoning_budget_to_llama_server(self, launch):
        serve_lfm.launch_dual_backbone(Path("runtime"), SimpleNamespace(port=18611), 32768,
                                       thinking_budget_tokens=512)

        self.assertEqual(launch.call_args.kwargs["reasoning_budget"], 512)

    def test_llama_server_receives_chat_template_file_flag(self):
        backbone = SimpleNamespace(spec=SimpleNamespace(port=18611), model_path=Path("brain.gguf"))
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "template.jinja"
            template.write_text("template", encoding="utf-8")
            with patch.object(runtime.subprocess, "Popen") as popen:
                process = runtime.launch_llama_server(
                    Path("llama-server.exe"), backbone, context=32768,
                    chat_template_file=template)
                try:
                    args = popen.call_args.args[0]
                    self.assertEqual(args[args.index("--chat-template-file") + 1], str(template))
                finally:
                    process._duocore_log_handle.close()
                    process._duocore_log_path.unlink(missing_ok=True)

    def test_llama_server_can_enable_reasoning_budget_control(self):
        backbone = SimpleNamespace(spec=SimpleNamespace(port=18611), model_path=Path("brain.gguf"))
        with patch.object(runtime.subprocess, "Popen") as popen:
            process = runtime.launch_llama_server(Path("llama-server.exe"), backbone, context=32768,
                                                   reasoning="on", reasoning_budget=512)
            try:
                args = popen.call_args.args[0]
                self.assertEqual(args[args.index("--reasoning") + 1], "on")
                self.assertEqual(args[args.index("--reasoning-budget") + 1], "512")
            finally:
                process._duocore_log_handle.close()
                process._duocore_log_path.unlink(missing_ok=True)

    def test_closed_thinking_template_retains_native_lfm_template(self):
        template = (serve_lfm.ROOT / "chat_template_no_think.jinja").read_text(encoding="utf-8")
        self.assertIn(r'<think>\n\n</think>\n\n', template)
        self.assertIn("skip_think | default(false)", template)
        self.assertIn('preserve_thinking | default(false)', template)
        self.assertIn(r'<|im_start|>assistant\n<think>\n', template)
        self.assertIn('<|tool_call_start|>', template)

    def test_lfm_backbone_sends_thinking_disabled_to_llama_server(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), RequestCaptureHandler)
        server.request_body = None
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            backbone = LfmBackbone(SimpleNamespace(name="fixture", port=server.server_port,
                                                    reasoning_format="none"), Path("fixture.gguf"))
            reply = backbone.chat([{"role": "user", "content": "return code"}], max_tokens=8)
            self.assertEqual(reply.content, "ok")
            self.assertEqual(server.request_body["reasoning_format"], "none")
            self.assertEqual(server.request_body["chat_template_kwargs"],
                             {"enable_thinking": False, "skip_think": False,
                              "preserve_thinking": False})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_lfm_backbone_keeps_json_grammar_for_closed_think_template(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), RequestCaptureHandler)
        server.request_body = None
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            backbone = LfmBackbone(SimpleNamespace(name="fixture", port=server.server_port,
                                                    reasoning_format="none"), Path("fixture.gguf"))
            backbone.chat([{"role": "user", "content": "score these answers as JSON"}],
                          max_tokens=32, json_mode=True,
                          json_schema={"type": "object", "properties": {"score": {"type": "number"}}})
            self.assertEqual(server.request_body["response_format"]["type"], "json_schema")
            self.assertEqual(server.request_body["chat_template_kwargs"],
                             {"enable_thinking": False, "skip_think": True,
                              "preserve_thinking": False})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_lfm_backbone_passes_bounded_reasoning_budget(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), RequestCaptureHandler)
        server.request_body = None
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            backbone = LfmBackbone(SimpleNamespace(name="fixture", port=server.server_port,
                                                    reasoning_format="none"), Path("fixture.gguf"))
            backbone.chat([{"role": "user", "content": "solve carefully"}], max_tokens=128,
                          thinking_budget_tokens=96)
            self.assertEqual(server.request_body["thinking_budget_tokens"], 96)
            self.assertEqual(server.request_body["chat_template_kwargs"]["preserve_thinking"], True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_dualcore_candidates_receive_the_reasoning_budget(self):
        class Brain:
            def __init__(self):
                self.budgets = []

            def chat(self, messages, **kwargs):
                self.budgets.append(kwargs.get("thinking_budget_tokens"))
                message = {"role": "assistant", "content": "return 42"}
                return BackboneReply("return 42", message,
                                     {"choices": [{"message": message, "finish_reason": "stop"}],
                                      "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

        engine = DualCoreEngine.__new__(DualCoreEngine)
        engine.native = None
        engine.parameters = None
        engine.brains = [Brain(), Brain()]
        engine.pool = ThreadPoolExecutor(max_workers=2)
        try:
            engine.complete([{"role": "user", "content": "write code"}], None, 128,
                            thinking_budget_tokens=64)
            self.assertEqual([brain.budgets for brain in engine.brains], [[64], [64]])
        finally:
            engine.close()

    def test_dualcore_retries_an_invalid_candidate_review_once(self):
        class Brain:
            def __init__(self, first_review, candidate):
                self.first_review = first_review
                self.candidate = candidate
                self.review_calls = 0
                self.review_prompts = []

            def chat(self, messages, **kwargs):
                if kwargs.get("json_schema"):
                    self.review_calls += 1
                    self.review_prompts.append(messages)
                    content = self.first_review if self.review_calls == 1 else (
                        '{"score_a":80,"score_b":70,"confidence":90,"reason":"More complete."}'
                    )
                else:
                    content = self.candidate
                message = {"role": "assistant", "content": content}
                return BackboneReply(content, message,
                                     {"choices": [{"message": message, "finish_reason": "stop"}],
                                      "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

        brains = [Brain("not JSON", "candidate one"), Brain(
            '{"score_a":70,"score_b":80,"confidence":90,"reason":"Clearer."}', "candidate two")]
        engine = DualCoreEngine.__new__(DualCoreEngine)
        engine.native = None
        engine.parameters = None
        engine.brains = brains
        engine.pool = ThreadPoolExecutor(max_workers=2)
        try:
            message, _, metadata = engine.complete(
                [{"role": "user", "content": "compare"}], None, 128)
            self.assertEqual(message["role"], "assistant")
            self.assertEqual(len(metadata["reviews"]), 2)
            self.assertEqual([brain.review_calls for brain in brains], [2, 1])
            retry_prompt = brains[0].review_prompts[1]
            self.assertTrue(any(
                "Return exactly one JSON object" in str(part.get("content", ""))
                for part in retry_prompt
            ))
        finally:
            engine.close()

    def test_echo_archive_is_separate_from_decoder_cache_mode(self):
        checkpoint = {"sha256": "pinned"}
        for profile in ("dualcore-echo", "fusioncore-echo"):
            with self.subTest(profile=profile):
                evidence = serve_lfm.profile_evidence(profile, checkpoint, parameters=5_394_397_184)
                self.assertTrue(evidence["echo_archive"])
                self.assertEqual(evidence["cache_mode"], "incremental_F16_KV")
                self.assertEqual(evidence["checkpoint"], checkpoint)

    def test_fusioncore_echo_constructs_the_incremental_decoder(self):
        checkpoint = Path("checkpoint.gguf")
        runtime = Path("runtime")
        with patch.object(serve_lfm, "FusionCoreModel") as model:
            engine = serve_lfm.create_engine("fusioncore-echo", checkpoint, runtime, 8192, 18610)
        self.assertIs(engine, model.return_value)
        model.assert_called_once_with(checkpoint, runtime, 8192, recompute=False)

    def test_echo_archive_does_not_select_a_different_dualcore_decoder(self):
        checkpoint = Path("checkpoint.gguf")
        runtime = Path("runtime")
        self.assertFalse(serve_lfm.PROFILES["dualcore-kv"][2])
        self.assertTrue(serve_lfm.PROFILES["dualcore-echo"][2])
        engines = [
            serve_lfm.create_engine("dualcore-kv", checkpoint, runtime, 131072, 18610),
            serve_lfm.create_engine("dualcore-echo", checkpoint, runtime, 32768, 18610),
        ]
        try:
            for engine in engines:
                self.assertIsNone(engine.native)
                self.assertTrue(all(isinstance(brain, LfmBackbone) for brain in engine.brains))
                self.assertEqual([brain.spec.port for brain in engine.brains], [18611, 18612])
                self.assertEqual([brain.spec.reasoning_format for brain in engine.brains], ["none", "none"])
                self.assertEqual([brain.model_path for brain in engine.brains], [checkpoint, checkpoint])
        finally:
            for engine in engines:
                engine.close()


if __name__ == "__main__":
    unittest.main()
