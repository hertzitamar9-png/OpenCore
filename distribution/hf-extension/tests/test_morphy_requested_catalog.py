"""The requested Morphy beta must use only the publisher's pinned package."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from requested_catalog import append_requested


class MorphyCatalogTests(unittest.TestCase):
    def test_morphy_quant_is_the_exact_publisher_file(self):
        entries = append_requested([], ROOT / 'hf-research/requested-models.json', ROOT / 'hf-research/requested-models')
        entries = [row for row in entries if row['repository'] == 'moolvylabs/Morphy1.1-Coder-Beta-7B-GGUF']
        self.assertEqual(len(entries), 1)
        row = entries[0]
        self.assertEqual(row['quantization'], 'Q8_0')
        self.assertEqual(row['revision'], '9be2b85463ce4ead1bf05db0628e3b89db18107b')
        self.assertEqual(row['contextTokens'], 8192)
        self.assertEqual(row['runtime']['adapter'], 'gguf')
        model = next(file for file in row['files'] if file['role'] == 'model')
        self.assertEqual(model['filename'], 'Morphy1.1-Coder-7B.Q8_0.gguf')
        self.assertEqual(model['bytes'], 8098525472)
        self.assertEqual(model['sha256'], '2206695d30c447a9ba8c4d071f24fd26d7890e7e2f0c020f7a79eec6986f0af6')


if __name__ == '__main__':
    unittest.main()
