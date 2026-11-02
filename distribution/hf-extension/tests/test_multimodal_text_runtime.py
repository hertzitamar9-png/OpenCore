"""Offline image/video-to-text request safety and runtime admission tests."""
import sys
from pathlib import Path
import unittest

REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO/'src-tauri/resources/runtime-setup/hf-extension'))
sys.path.insert(0,str(REPO/'src-tauri/resources/text-runtime'))
from hf_runtime import visual_text_probe
from serve_text import has_multimodal_parts, multimodal_messages


class MultimodalTextRuntimeTests(unittest.TestCase):
    def entry(self,kind):
        return {'format':'safetensors','runtime':{'adapter':'transformers'},
                'inputModalities':[kind,'text'],'outputModalities':['text']}

    def test_synthetic_image_has_real_pixels_and_stays_local(self):
        kind,probe=visual_text_probe(self.entry('image'))
        self.assertEqual(kind,'image')
        self.assertTrue(has_multimodal_parts(probe))
        parsed=multimodal_messages(probe)
        picture=parsed[0]['content'][1]['image']
        self.assertEqual(picture.size,(224,224))
        self.assertEqual(picture.mode,'RGB')
        self.assertNotIn('http',str(probe))

    def test_video_is_sampled_into_multiple_bounded_images(self):
        kind,probe=visual_text_probe(self.entry('video'))
        self.assertEqual(kind,'video-frames')
        messages=multimodal_messages(probe)
        frames=[item for item in messages[0]['content'] if item['type']=='image']
        self.assertEqual(len(frames),2)
        self.assertEqual(len([x for x in messages[0]['content'] if x['type']=='text']),3)

    def test_audio_requires_separate_backend_and_generic_runtime_is_not_claimed(self):
        self.assertIsNone(visual_text_probe(self.entry('audio')))
        with self.assertRaisesRegex(ValueError,'audio'):
            multimodal_messages({'messages':[{'role':'user','content':[{'type':'audio','data':'data:audio/wav;base64,YQ=='}]}]})

    def test_no_outbound_url_or_filesystem_loading(self):
        for value in ('https://example.com/photo.png','file:///C:/private.png',
                      'data:image/svg+xml;base64,PHN2Zz4=','http://127.0.0.1/'):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'inline|format|Invalid'):
                multimodal_messages({'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':value}}]}]})

    def test_excess_frames_are_rejected(self):
        _,probe=visual_text_probe(self.entry('image'))
        image=probe['messages'][0]['content'][1]
        with self.assertRaisesRegex(ValueError,'twelve'):
            multimodal_messages({'messages':[{'role':'user','content':[image]*13}]})


if __name__=='__main__':
    unittest.main()
