"""Focused API tests for the packaged TwinCore server."""

from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import unittest
import urllib.error
import urllib.request


RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "src-tauri" / "resources" / "doucode"
sys.path.insert(0, str(RESOURCE_ROOT))

import serve_twincore_consensus as twincore_server  # noqa: E402


class TwinCoreServerApiTests(unittest.TestCase):
    def test_props_reports_the_composite_live_context(self):
        handler = getattr(twincore_server, "TwinCoreHandler", None)
        self.assertIsNotNone(handler, "TwinCore HTTP handler must be testable without loading model weights")

        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.config = SimpleNamespace(model_id="doUcode", live_window_tokens=262144)
        server.engine = SimpleNamespace()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/props", timeout=2) as response:
                    status = response.status
                    payload = json.load(response)
            except urllib.error.HTTPError as error:
                status = error.code
                payload = {}

            self.assertEqual(status, 200)
            self.assertEqual(payload["n_ctx"], 262144)
            self.assertEqual(payload["model"], "doUcode")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
