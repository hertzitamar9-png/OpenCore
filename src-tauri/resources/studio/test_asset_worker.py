import unittest
from asset_worker import bounded_integer, checkpoint_dtypes
import json, struct, tempfile
from pathlib import Path

class StudioBounds(unittest.TestCase):
    def test_bounds_reject_wrong_types_and_excessive_allocations(self):
        for value in [-1, 2049, True, "768"]:
            with self.assertRaises(ValueError):
                bounded_integer({"width": value}, "width", 768, 128, 2048)
        self.assertEqual(bounded_integer({}, "width", 768, 128, 2048), 768)
    def test_reads_original_component_precision_without_loading_weights(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,dtype in [("transformer","BF16"),("vae","F32")]:
                part=root/name; part.mkdir()
                header=json.dumps({"weight":{"dtype":dtype,"shape":[1],"data_offsets":[0,2]}}).encode()
                (part/"model.safetensors").write_bytes(struct.pack("<Q",len(header))+header+b"00")
            self.assertEqual(checkpoint_dtypes(root), {"transformer":"BF16","vae":"F32"})
    def test_unknown_or_mixed_precision_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as folder:
            part=Path(folder)/"transformer"; part.mkdir()
            header=json.dumps({"a":{"dtype":"BF16"},"b":{"dtype":"F32"}}).encode()
            (part/"model.safetensors").write_bytes(struct.pack("<Q",len(header))+header)
            with self.assertRaises(ValueError): checkpoint_dtypes(Path(folder))

if __name__ == "__main__": unittest.main()
