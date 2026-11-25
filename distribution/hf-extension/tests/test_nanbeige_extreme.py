"""Regression tests for the exact, locally derived Extreme package."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from hf_download import validate_entry
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
        for name in (extreme.ENGINE,extreme.LIBS):
            self.assertIn("/b12503/",definition(name)["url"])
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
                self.assertEqual(cmd[cmd.index("-m")+1],str(Path(entry["localDirectory"])/extreme.TARGET))
                self.assertEqual(cmd[cmd.index("-md")+1],str(Path(entry["localDirectory"])/extreme.DRAFT))
                self.assertEqual(cmd[cmd.index("--spec-draft-n-max")+1],"7")
                self.assertEqual(cmd[cmd.index("--spec-type")+1],"ngram-simple,draft-dspark")
                self.assertIn("--backend-sampling",cmd)
                self.assertIn("--no-context-shift",cmd)
            with self.assertRaises(ValueError):runtime_plan(entry,root,root,backend_override="cpu")
    def test_reconstruct_rejects_wrong_base_without_creating_output(self):
        with tempfile.TemporaryDirectory() as t:
            a,b=Path(t)/"wrong.gguf",Path(t)/"derived.gguf"
            a.write_bytes(b"not a model")
            with self.assertRaises(ValueError):extreme.reconstruct(a,b,threading.Event())
            self.assertFalse(b.exists())
            self.assertEqual(a.read_bytes(),b"not a model")
