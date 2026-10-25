import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import hf_download as transfer
except ModuleNotFoundError:
    transfer = None

DATA = b"open-core-weight-data" * 16384
COMMIT = "c" * 40


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        server = self.server
        server.requests.append((self.path, self.headers.get("Authorization"), self.headers.get("Range")))
        if self.path == "/api/whoami-v2":
            if self.headers.get("Authorization") != "Bearer hf_private_test":
                self.send_error(401)
                return
            payload = json.dumps({"name": "test-user", "type": "user"}).encode()
        elif self.path.startswith("/api/models/"):
            if server.private and self.headers.get("Authorization") != "Bearer hf_private_test":
                self.send_error(401)
                return
            payload = json.dumps({"id": "owner/model", "sha": COMMIT, "private": server.private, "siblings": []}).encode()
        else:
            if server.private and self.headers.get("Authorization") != "Bearer hf_private_test":
                self.send_error(403)
                return
            data = DATA if not server.corrupt else b"x" * len(DATA)
            offset, end = 0, len(data) - 1
            requested = self.headers.get("Range")
            if requested and not server.ignore_range:
                first, last = requested.split('=')[1].split('-')
                offset, end = int(first), int(last) if last else len(data) - 1
            ranged = bool(requested and not server.ignore_range)
            self.send_response(206 if ranged else 200)
            self.send_header("Content-Length", str(end - offset + 1))
            if ranged:
                self.send_header("Content-Range", f"bytes {offset}-{end}/{len(data)}")
            self.end_headers()
            try:
                for index in range(offset, end + 1, 8192):
                    self.wfile.write(data[index:min(index + 8192, end + 1)])
                    self.wfile.flush()
                    if server.slow:
                        time.sleep(0.003)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(transfer, "The managed transfer service is not implemented")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        self.server.private = self.server.corrupt = self.server.ignore_range = self.server.slow = False
        self.server.requests = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = transfer.HubClient(lambda: "hf_private_test", endpoint="http://127.0.0.1:" + str(self.server.server_port), allow_test_http=True)

    def entry(self, git_blob=False):
        pin = {"repository": "owner/model", "revision": COMMIT, "filename": "dir/model-F16.gguf", "bytes": len(DATA),
               "sha256": None if git_blob else hashlib.sha256(DATA).hexdigest(),
               "gitBlobSha1": hashlib.sha1(b"blob " + str(len(DATA)).encode() + b"\0" + DATA).hexdigest() if git_blob else None,
               "role": "model", "pinned": True}
        return {"id": "hf-" + "a" * 24, "repository": "owner/model", "revision": COMMIT, "bytes": len(DATA), "files": [pin], "installable": True}

    def test_public_metadata_is_anonymous_even_with_saved_token(self):
        self.assertEqual(self.client.metadata("owner/model")["sha"], COMMIT)
        self.assertIsNone(self.server.requests[0][1])

    def test_private_metadata_retries_with_saved_authorization_only_when_needed(self):
        self.server.private = True
        self.assertTrue(self.client.metadata("owner/model")["private"])
        self.assertEqual([r[1] for r in self.server.requests], [None, "Bearer hf_private_test"])

    def test_resumes_pinned_partial_then_atomically_publishes_receipt(self):
        entry = self.entry()
        downloader = transfer.Downloader(self.root, self.client)
        target = downloader.directory(entry) / "dir/model-F16.gguf"
        target.parent.mkdir(parents=True)
        partial = target.with_name(target.name + ".partial")
        partial.write_bytes(DATA[:65536])
        transfer.atomic_json(partial.with_name(partial.name + ".json"), entry["files"][0])
        progress = []
        receipt = downloader.install(entry, threading.Event(), progress.append)
        self.assertEqual(target.read_bytes(), DATA)
        self.assertFalse(partial.exists())
        self.assertTrue(Path(receipt["receiptPath"]).is_file())
        self.assertEqual(receipt["files"][0]["actualSha256"], hashlib.sha256(DATA).hexdigest())
        self.assertEqual(self.server.requests[0][2], "bytes=65536-")
        self.assertEqual(progress[-1]["bytesDone"], len(DATA))
        self.assertIn(COMMIT, self.server.requests[0][0])

    def test_server_ignoring_range_restarts_without_appending_duplicate_bytes(self):
        entry = self.entry()
        downloader = transfer.Downloader(self.root, self.client)
        target = downloader.directory(entry) / "dir/model-F16.gguf"
        target.parent.mkdir(parents=True)
        partial = target.with_name(target.name + ".partial")
        partial.write_bytes(DATA[:12345])
        transfer.atomic_json(partial.with_name(partial.name + ".json"), entry["files"][0])
        self.server.ignore_range = True
        downloader.install(entry, threading.Event(), lambda _: None)
        self.assertEqual(target.read_bytes(), DATA)

    def test_pause_retains_partial_and_resume_completes(self):
        self.server.slow = True
        downloader = transfer.Downloader(self.root, self.client)
        entry, pause = self.entry(), threading.Event()
        def progress(value):
            if value["bytesDone"] >= 65536:
                pause.set()
        with self.assertRaises(transfer.Paused):
            downloader.install(entry, pause, progress)
        target = downloader.directory(entry) / "dir/model-F16.gguf"
        partial = target.with_name(target.name + ".partial")
        self.assertTrue(partial.is_file())
        self.assertLess(partial.stat().st_size, len(DATA))
        self.assertFalse(target.exists())
        pause.clear()
        downloader.install(entry, pause, lambda _: None)
        self.assertEqual(target.read_bytes(), DATA)
        self.assertTrue(any(x[2] for x in self.server.requests))

    def test_checksum_failure_never_publishes_model_or_receipt(self):
        self.server.corrupt = True
        downloader = transfer.Downloader(self.root, self.client)
        entry = self.entry()
        with self.assertRaisesRegex(ValueError, "checksum"):
            downloader.install(entry, threading.Event(), lambda _: None)
        directory = downloader.directory(entry)
        self.assertFalse((directory / "dir/model-F16.gguf").exists())
        self.assertFalse((directory / "install-receipt.json").exists())

    def parallel_downloader(self):
        downloader = transfer.Downloader(self.root, self.client)
        downloader.parallel_threshold = 0
        downloader.parallel_chunk_bytes = 32768
        downloader.parallel_workers = 3
        return downloader

    def test_parallel_ranges_preserve_order_and_exact_checksum(self):
        downloader = self.parallel_downloader()
        receipt = downloader.install(self.entry(), threading.Event(), lambda _: None)
        self.assertEqual(Path(receipt['files'][0]['localPath']).read_bytes(), DATA)
        self.assertEqual(receipt['files'][0]['actualSha256'], hashlib.sha256(DATA).hexdigest())
        self.assertTrue(all(request[2] and request[2].split('-')[-1] for request in self.server.requests))

    def test_parallel_pause_keeps_a_contiguous_prefix_and_resumes(self):
        self.server.slow = True
        downloader, pause = self.parallel_downloader(), threading.Event()
        def progress(value):
            if value['stage'] == 'downloading' and value['bytesDone'] >= 65536:
                pause.set()
        with self.assertRaises(transfer.Paused):
            downloader.install(self.entry(), pause, progress)
        partial = downloader.directory(self.entry()) / 'dir/model-F16.gguf.partial'
        self.assertEqual(partial.read_bytes(), DATA[:partial.stat().st_size])
        pause.clear()
        receipt = downloader.install(self.entry(), pause, lambda _: None)
        self.assertEqual(Path(receipt['files'][0]['localPath']).read_bytes(), DATA)

    def test_parallel_falls_back_safely_if_server_ignores_ranges(self):
        self.server.ignore_range = True
        receipt = self.parallel_downloader().install(self.entry(), threading.Event(), lambda _: None)
        self.assertEqual(Path(receipt['files'][0]['localPath']).read_bytes(), DATA)

    def test_parallel_corruption_never_publishes_a_receipt(self):
        self.server.corrupt = True
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.parallel_downloader().install(self.entry(), threading.Event(), lambda _: None)
        self.assertFalse((self.parallel_downloader().directory(self.entry()) / 'install-receipt.json').exists())

    def test_disk_preflight_rejects_before_network_activity(self):
        with patch.object(transfer.shutil, 'disk_usage', return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(transfer.HubError, 'free disk'):
                transfer.Downloader(self.root, self.client).install(self.entry(), threading.Event(), lambda _: None)
        self.assertEqual(self.server.requests, [])

    def test_verified_cached_checkpoint_does_not_reserve_another_download_copy(self):
        downloader = transfer.Downloader(self.root, self.client)
        entry = self.entry()
        target = downloader.directory(entry) / 'dir/model-F16.gguf'
        target.parent.mkdir(parents=True)
        target.write_bytes(DATA)
        with patch.object(transfer.shutil, 'disk_usage', return_value=SimpleNamespace(free=1)):
            receipt = downloader.install(entry, threading.Event(), lambda _: None)
        self.assertTrue(Path(receipt['receiptPath']).exists())
        self.assertEqual(self.server.requests, [])

    def test_git_blob_checksum_and_private_weight_authorization_are_verified(self):
        self.server.private = True
        entry = self.entry(git_blob=True)
        receipt = transfer.Downloader(self.root, self.client).install(entry, threading.Event(), lambda _: None)
        self.assertEqual(receipt["files"][0]["actualSha256"], hashlib.sha256(DATA).hexdigest())
        self.assertEqual([r[1] for r in self.server.requests], [None, "Bearer hf_private_test"])

    def test_modified_partial_pin_is_restarted_and_unsafe_path_rejected(self):
        entry = self.entry()
        downloader = transfer.Downloader(self.root, self.client)
        target = downloader.directory(entry) / "dir/model-F16.gguf"
        target.parent.mkdir(parents=True)
        partial = target.with_name(target.name + ".partial")
        partial.write_bytes(b"wrong data")
        transfer.atomic_json(partial.with_name(partial.name + ".json"), {**entry["files"][0], "revision": "b" * 40})
        downloader.install(entry, threading.Event(), lambda _: None)
        self.assertEqual(target.read_bytes(), DATA)
        self.assertIsNone(self.server.requests[0][2])
        entry["files"][0]["filename"] = "../../outside.gguf"
        with self.assertRaises(ValueError):
            downloader.install(entry, threading.Event(), lambda _: None)


if __name__ == "__main__":
    unittest.main()
