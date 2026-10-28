import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import asset_worker

class ImageProgressTests(unittest.TestCase):
    def test_sampler_reports_real_steps_and_speed_without_claiming_the_image_is_ready(self):
        with tempfile.TemporaryDirectory() as folder,patch('asset_worker.time.monotonic',side_effect=[102,108]):
            root=Path(folder);tracker=asset_worker.SamplingProgress(root,4)
            tracker.update(1);value=json.loads((root/'progress.json').read_text())
            self.assertEqual((value['step'],value['steps']),(1,4));self.assertEqual(value['stepsPerSecond'],0)
            tracker.update(4);value=json.loads((root/'progress.json').read_text())
            self.assertEqual(value['step'],4);self.assertEqual(value['stepsPerSecond'],.5);self.assertNotEqual(value['stage'],'Generation complete')
            asset_worker.progress(root,'Saving image');value=json.loads((root/'progress.json').read_text())
            self.assertEqual((value['step'],value['steps']),(4,4))

    def test_unrelated_or_invalid_progress_cannot_corrupt_the_image_step_count(self):
        with tempfile.TemporaryDirectory() as folder:
            tracker=asset_worker.SamplingProgress(Path(folder),40)
            self.assertFalse(tracker.update(9,total=12))
            self.assertFalse(tracker.update(-1,total=40))
            self.assertFalse(tracker.update(41,total=40))
            self.assertFalse((Path(folder)/'progress.json').exists())

if __name__=='__main__':unittest.main()
