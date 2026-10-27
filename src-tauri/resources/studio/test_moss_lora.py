"""Unit tests for the optional OpenCore-owned MOSS native LoRA path."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from comfy_worker import build_moss_workflow, managed_moss_lora


class MossLoRATest(unittest.TestCase):
    def test_base_and_lora_workflows_have_different_model_edges(self):
        request = {'prompt': 'a test photo', 'settings': {'steps': 30, 'guidanceScale': 3, 'loraStrength': 0.8}}
        base = build_moss_workflow(request, 'model.safetensors')
        self.assertEqual(base['15']['inputs']['model'], ['1', 0])
        self.assertNotIn('19', base)
        with_lora = build_moss_workflow(request, 'model.safetensors', 'opencore-moss-lora.safetensors')
        self.assertEqual(with_lora['15']['inputs']['model'], ['19', 0])
        self.assertEqual(with_lora['19']['class_type'], 'LoraLoaderModelOnly')
        self.assertEqual(with_lora['19']['inputs']['strength_model'], 0.8)
        for value in [-1, 2.1, True, float('nan')]:
            with self.assertRaises(ValueError):
                build_moss_workflow({'prompt': 'test', 'settings': {'loraStrength': value}}, 'model.safetensors', 'test.safetensors')

    def test_only_verified_native_moss_lora_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'models' / 'hub' / 'hf-test'
            folder.mkdir(parents=True)
            filename = 'MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors'
            path = folder / filename
            path.write_bytes(b'example placeholder safetensors')
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            (folder / 'install-receipt.json').write_text(json.dumps({
                'repository': 'axiomofmind/Moss-image-nsfw-uncensored-lora',
                'files': [{'role': 'model', 'filename': filename, 'sha256': sha, 'actualSha256': sha}]
            }))
            self.assertEqual(managed_moss_lora(root, {'loraPath': str(path)}), path)
            path.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'hash'):
                managed_moss_lora(root, {'loraPath': str(path)})
            other = folder / 'pytorch_lora_weights.safetensors'
            other.write_bytes(b'example')
            with self.assertRaisesRegex(ValueError, 'ComfyUI'):
                managed_moss_lora(root, {'loraPath': str(other)})


if __name__ == '__main__':
    unittest.main()
