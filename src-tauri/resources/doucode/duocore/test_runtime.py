from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from duocore import runtime
from duocore.runtime import LlamaBackbone, launch_llama_server
from duocore.spec import BackboneSpec


class RuntimeProcessTests(unittest.TestCase):
    def test_model_backend_process_does_not_open_a_console_window(self) -> None:
        spec = BackboneSpec(
            name="fixture",
            repo="fixture",
            gguf_repo="fixture",
            gguf_file="fixture.gguf",
            hidden_size=8,
            num_hidden_layers=1,
            num_attention_heads=1,
            num_key_value_heads=1,
            head_dim=8,
            vocab_size=16,
            native_context=32,
            port=8841,
        )
        process = type("Process", (), {})()
        directory = Path(tempfile.mkdtemp())
        log_path: Path | None = None
        try:
            with patch.object(runtime.subprocess, "Popen", return_value=process) as popen:
                launched = launch_llama_server(directory / "llama-server.exe", LlamaBackbone(spec, directory / "fixture.gguf"), context=32)
                launched._duocore_log_handle.close()
                log_path = launched._duocore_log_path
                options = popen.call_args.kwargs
            if os.name == "nt":
                self.assertEqual(options.get("creationflags"), subprocess.CREATE_NO_WINDOW)
            else:
                self.assertNotIn("creationflags", options)
        finally:
            if log_path is not None:
                log_path.unlink(missing_ok=True)
            directory.rmdir()


if __name__ == "__main__":
    unittest.main()
