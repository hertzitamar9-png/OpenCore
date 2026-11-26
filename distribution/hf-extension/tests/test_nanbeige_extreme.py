"""Regression tests for the pinned Nanbeige Extreme packages."""
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from hf_download import Downloader, validate_entry
from hf_continuum import catalog_entry as continuum_catalog_entry
import hf_nanbeige_extreme as extreme
from managed_engines import definition, engine_for
from hf_runtime import runtime_plan
from hf_bridge import _engine_demand

class ExtremeTests(unittest.TestCase):
    def test_catalog_and_source_pins(self):
        entry=validate_entry(extreme.catalog_entry())
        self.assertEqual(entry["id"],extreme.IDENTITY)
        self.assertEqual(entry["files"][0]["sha256"],extreme.TARGET_SHA)
        self.assertEqual(entry["files"][1]["sha256"],extreme.DRAFT_SHA)
        for source in extreme.sources():validate_entry(source)
        self.assertEqual(engine_for(entry),extreme.ENGINE)
        self.assertEqual(_engine_demand(entry,[],None),
                         {"engine:"+extreme.ENGINE,"engine:"+extreme.LIBS})
        self.assertIn("/b12732/",definition(extreme.ENGINE)["url"])
        self.assertIn("/b12503/",definition(extreme.LIBS)["url"])
        self.assertEqual(definition(extreme.ENGINE)["sha256"],
                         "2eb1f3cc20a0e4e493bfad32c7f733c6e8ec769cdfbaf28d8586da9c76dcb3eb")
        bad=dict(entry,revision="0"*40)
        with self.assertRaises(ValueError):extreme.check_entry(bad)
    def test_plan_keeps_precision_and_verified_speculation(self):
        with tempfile.TemporaryDirectory() as t,patch("managed_engines.cuda_driver_version",return_value=13040):
            root=Path(t)
            entry=extreme.catalog_entry()
            entry["localDirectory"]=str(root/"models/hub"/extreme.IDENTITY)
            for mode in ("native","echo"):
                entry["operatingMode"]=mode
                plan=runtime_plan(entry,root,root)
                cmd=plan["command"]
                self.assertEqual(plan["context"],8192)
                self.assertEqual(plan["publisherEngine"],extreme.ENGINE)
                self.assertEqual(plan["cudaLibraryId"],extreme.LIBS)
                self.assertEqual(Path(cmd[cmd.index("-m")+1]).resolve(),(Path(entry["localDirectory"])/extreme.TARGET).resolve())
                self.assertEqual(Path(cmd[cmd.index("-md")+1]).resolve(),(Path(entry["localDirectory"])/extreme.DRAFT).resolve())
                self.assertEqual(cmd[cmd.index("--spec-draft-n-max")+1],"7")
                self.assertEqual(cmd[cmd.index("--spec-type")+1],"ngram-simple,draft-dspark")
                self.assertIn("--backend-sampling",cmd)
                self.assertIn("--no-context-shift",cmd)
            with self.assertRaises(ValueError):runtime_plan(entry,root,root,backend_override="cpu")

    def test_original_profile_uses_the_published_q8_bytes(self):
        entry = validate_entry(extreme.original_catalog_entry())
        source = continuum_catalog_entry()["files"][0]
        self.assertNotEqual(entry["id"], extreme.IDENTITY)
        self.assertEqual(entry["files"][0]["filename"], source["filename"])
        self.assertEqual(entry["files"][0]["sha256"], source["sha256"])
        self.assertEqual(entry["files"][0]["bytes"], source["bytes"])
        self.assertEqual(engine_for(entry), extreme.ENGINE)
        self.assertEqual(_engine_demand(entry, [], None), {"engine:" + extreme.ENGINE, "engine:" + extreme.LIBS})
        with tempfile.TemporaryDirectory() as t, patch("managed_engines.cuda_driver_version", return_value=13040):
            root = Path(t)
            entry["localDirectory"] = str(root / "models" / "hub" / entry["id"])
            for mode in ("native", "echo"):
                entry["operatingMode"] = mode
                plan = runtime_plan(entry, root, root)
                command = plan["command"]
                self.assertEqual(plan["context"], 8192)
                self.assertEqual(Path(command[command.index("-m") + 1]).name, source["filename"])
                self.assertEqual(Path(command[command.index("-md") + 1]).name, entry["files"][1]["filename"])
                self.assertEqual(command[command.index("--spec-type") + 1], "draft-dspark")
            changed = dict(entry, files=extreme.catalog_entry()["files"])
            with self.assertRaises(ValueError):
                runtime_plan(changed, root, root)

    def test_original_install_stages_exact_files_and_leaves_source_intact(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            fixture = extreme.original_catalog_entry()
            sources = extreme.original_sources()
            files = []
            for source, pin in zip(sources, fixture["files"]):
                content = ("verified " + pin["role"]).encode()
                digest = hashlib.sha256(content).hexdigest()
                source_pin = dict(source["files"][0], bytes=len(content), sha256=digest)
                source.update(files=[source_pin], bytes=len(content))
                fixture_pin = dict(pin, bytes=len(content), sha256=digest)
                source_path = root / "models" / "hub" / source["id"] / pin["filename"]
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_bytes(content)
                files.append(fixture_pin)
            fixture.update(files=files, bytes=sum(pin["bytes"] for pin in files))
            downloader = Downloader(root, Mock())
            pause = threading.Event()
            with patch.object(extreme, "original_sources", return_value=sources), patch.object(
                extreme, "original_catalog_entry", return_value=fixture
            ), patch.object(extreme, "reconstruct") as reconstruct:
                receipt = extreme.install(fixture, downloader, pause, lambda _: None)
                reconstruct.assert_not_called()
            self.assertEqual(receipt["revision"], fixture["revision"])
            self.assertEqual(receipt["id"], fixture["id"])
            for source, installed in zip(sources, receipt["files"]):
                previous = root / "models" / "hub" / source["id"] / installed["filename"]
                self.assertTrue(previous.samefile(Path(installed["localPath"])))
                self.assertEqual(hashlib.sha256(previous.read_bytes()).hexdigest(), installed["actualSha256"])

    def test_reconstruct_rejects_wrong_base_without_creating_output(self):
        with tempfile.TemporaryDirectory() as t:
            a,b=Path(t)/"wrong.gguf",Path(t)/"derived.gguf"
            a.write_bytes(b"not a model")
            with self.assertRaises(ValueError):extreme.reconstruct(a,b,threading.Event())
            self.assertFalse(b.exists())
            self.assertEqual(a.read_bytes(),b"not a model")
