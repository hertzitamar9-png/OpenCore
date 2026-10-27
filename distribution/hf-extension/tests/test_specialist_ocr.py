"""Contract tests for the dedicated, local OCR model adapter."""
import base64
import hashlib
import json
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

RESOURCE = pathlib.Path(__file__).resolve().parents[3] / 'src-tauri/resources/runtime-setup/hf-extension'
sys.path.insert(0, str(RESOURCE))
import hf_specialist_ocr as ocr


class OCRContract(unittest.TestCase):
    def entry(self):
        return {'id': ocr.MODEL_ID, 'repository': ocr.REPOSITORY, 'revision': ocr.REVISION, 'status': 'installed'}

    def test_only_the_pinned_exact_checkpoint_is_supported(self):
        self.assertTrue(ocr.supports(self.entry()))
        for field in ('id', 'repository', 'revision'):
            bad = dict(self.entry(), **{field: 'other'})
            self.assertFalse(ocr.supports(bad))

    def test_image_payload_is_bounded_and_strictly_base64(self):
        valid = base64.b64encode(b'not decoded until the image worker').decode('ascii')
        self.assertEqual(ocr.decode_payload(valid), b'not decoded until the image worker')
        for invalid in ('', 'not base64!', 'a' * (ocr.MAX_IMAGE_BYTES * 2)):
            with self.assertRaises(ValueError):
                ocr.decode_payload(invalid)

    def test_structured_model_output_is_readable_without_losing_text(self):
        raw = '<PAGE><BLOCK>title[66, 338, 628, 500]<FORK>OPENCORE 123<BLOCK>text[1, 2, 3, 4]<FORK>Line two'
        self.assertEqual(ocr.readable_text(raw), 'OPENCORE 123\nLine two')

    def test_blank_page_is_not_mistaken_for_text(self):
        raw = '<PAGE><BLOCK>image[0, 0, 1000, 1000]<FORK>'
        self.assertEqual(ocr.readable_text(raw), '')

    def test_ready_requires_a_matching_verified_cuda_dependency_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            resources = RESOURCE.parent.parent
            manager = ocr.OCRManager(root, resources)
            manager.python().parent.mkdir(parents=True)
            manager.python().touch()
            proof = {'inferenceVerified': True, 'revision': ocr.REVISION, 'workerVersion': 1}
            self.assertFalse(manager.ready(self.entry(), proof))
            recipes = json.loads((resources / 'runtime-setup/recipes.json').read_text(encoding='utf-8'))['recipes']
            recipe = next(item for item in recipes if item['id'] == ocr.RECIPE)
            fingerprint = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            receipt = {'schema': 1, 'targetId': ocr.MODEL_ID, 'recipeFingerprint': fingerprint, 'dependenciesVerified': True,
                       'hardware': {'cudaAvailable': True, 'kernelVerified': True}, 'python': str(manager.python())}
            target = pathlib.Path(root) / 'runtime-setup/receipts' / (ocr.MODEL_ID + '.json')
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(receipt), encoding='utf-8')
            self.assertTrue(manager.ready(self.entry(), proof))
            receipt['recipeFingerprint'] = 'obsolete'
            target.write_text(json.dumps(receipt), encoding='utf-8')
            self.assertFalse(manager.ready(self.entry(), proof))

    def test_user_image_is_only_passed_to_worker_and_not_returned_in_status(self):
        with tempfile.TemporaryDirectory() as root:
            manager = ocr.OCRManager(root, RESOURCE.parent.parent)
            entry = dict(self.entry(), specialistReady=True)
            with patch.object(manager, '_worker', return_value={'text': 'HELLO', 'raw': 'HELLO'}) as worker:
                manager.start(entry, base64.b64encode(b'private-image').decode())
                for _ in range(100):
                    status = manager.status()
                    if status['status'] != 'running': break
                    time.sleep(.01)
                self.assertEqual(status['text'], 'HELLO')
                self.assertNotIn('private-image', json.dumps(status))
                self.assertEqual(worker.call_args.args[2], b'private-image')

    def test_bundled_publisher_code_matches_pinned_blobs_with_the_two_safety_patches(self):
        repo = pathlib.Path(__file__).resolve().parents[3]
        metadata = json.loads((repo / 'distribution/hf-research/requested-models/baidu--Unlimited-OCR-2.api.json').read_text(encoding='utf-8'))
        pins = {item['rfilename']: item for item in metadata['siblings']}
        self.assertEqual(metadata['sha'], ocr.REVISION)
        folder = RESOURCE / 'unlimited_ocr_vendor'
        files = [path for path in folder.glob('*.py') if path.name != '__init__.py']
        self.assertEqual(len(files), 10)
        for file in files:
            text = file.read_text(encoding='utf-8')
            if file.name == 'modeling_unlimitedocr.py':
                self.assertEqual(text.count('cor_list = ast.literal_eval(ref_text[2])'), 1)
                text = text.removeprefix('import ast\n').replace('cor_list = ast.literal_eval(ref_text[2])', 'cor_list = eval(ref_text[2])')
            if file.name == 'deepencoder.py':
                self.assertEqual(text.count('torch.load(checkpoint, weights_only=True)'), 1)
                text = text.replace('torch.load(checkpoint, weights_only=True)', 'torch.load(checkpoint)')
            raw = text.encode('utf-8')
            digest = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            with self.subTest(file=file.name):
                self.assertEqual((len(raw), digest), (pins[file.name]['size'], pins[file.name]['blobId']))


if __name__ == '__main__':
    unittest.main()
