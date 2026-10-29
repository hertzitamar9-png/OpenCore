import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import asset_worker
import comfy_worker


class QwenPhaseTests(unittest.TestCase):
    def test_prompt_encoder_exits_before_diffusion_weights_are_loaded(self):
        self.assertTrue(hasattr(comfy_worker, 'split_qwen_workflow'), 'Qwen needs separate prompt and sampling processes')
        graph = comfy_worker.build_qwen_workflow({'prompt': 'A blue ceramic mug', 'settings': {'width': 512, 'height': 512}}, 'model.safetensors', 'adapter.safetensors', ['reference.png'])
        encode, sample = comfy_worker.split_qwen_workflow(graph)
        self.assertIn('CLIPLoader', [node['class_type'] for node in encode.values()])
        self.assertNotIn('UNETLoader', [node['class_type'] for node in encode.values()])
        self.assertNotIn('CLIPLoader', [node['class_type'] for node in sample.values()])
        self.assertIn('LoraLoaderModelOnly', [node['class_type'] for node in sample.values()])
        for phase in (encode, sample):
            for node in phase.values():
                for value in node['inputs'].values():
                    if isinstance(value, list):
                        self.assertIn(value[0], phase, 'A phase must not reference nodes in the exited process')
        self.assertEqual(sample['16']['inputs']['latent_image'], ['20', 2])

    def test_phased_load_fits_the_reported_ram_without_counting_vram_as_ram(self):
        self.assertTrue(hasattr(comfy_worker, 'qwen_memory_plan'), 'Budget the peak of the two isolated phases')
        plan = comfy_worker.qwen_memory_plan([4_197_574_696, 9_350_798_360, 675_509_688, 159_436_456], 14_600_000_000, 11_600_000_000)
        self.assertTrue(plan['fits'])
        self.assertLess(plan['hostRequiredBytes'], 13_000_000_000)
        self.assertGreaterEqual(plan['hostRequiredBytes'], 9_350_798_360 + 2_000_000_000)
        self.assertFalse(comfy_worker.qwen_memory_plan([4_197_574_696, 9_350_798_360, 675_509_688], 10_000_000_000, 24_000_000_000)['fits'])

    def test_staged_native_worker_finds_phase_nodes_in_installed_app_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            helper=root/'.helpers'/'studio-release'
            helper.mkdir(parents=True)
            app_resources=root/'LocalAppData'/'OpenCore'/'studio'
            app_resources.mkdir(parents=True)
            source=app_resources/'qwen_phase_nodes.py'
            source.write_text('# installed phase plugin',encoding='utf-8')
            with patch.object(asset_worker,'__file__',str(helper/'asset_worker.py')), \
                 patch.dict(os.environ,{'LOCALAPPDATA':str(root/'LocalAppData'),'OPENCORE_RESOURCES_DIR':''}):
                self.assertEqual(asset_worker.qwen_phase_nodes_path(),source.resolve())


if __name__ == '__main__':
    unittest.main()
