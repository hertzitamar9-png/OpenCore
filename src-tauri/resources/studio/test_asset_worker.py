import unittest
from asset_worker import bounded_integer, checkpoint_dtypes, supported_generation_kwargs, validate_output_format
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
    def test_maps_game_dev_controls_to_pipeline_parameters_without_ignoring_them(self):
        class Pipeline:
            def __call__(self, prompt, num_inference_steps, width, height, generator,
                         negative_prompt=None, guidance_scale=1, num_images_per_prompt=1):
                pass
        settings={"steps":40,"width":1024,"height":512,"seed":42,"negativePrompt":"blur",
                  "guidanceScale":6.5,"numImages":3}
        values=supported_generation_kwargs(Pipeline(),{"prompt":"A test"},settings,"seed")
        self.assertEqual(values,{"num_inference_steps":40,"width":1024,"height":512,"generator":"seed",
                                 "negative_prompt":"blur","guidance_scale":6.5,"num_images_per_prompt":3,"prompt":"A test"})
    def test_rejects_unsupported_runtime_settings_and_output_formats(self):
        class Pipeline:
            def __call__(self, prompt, num_inference_steps, width, height, generator): pass
        with self.assertRaisesRegex(RuntimeError,"does not support negative prompts"):
            supported_generation_kwargs(Pipeline(),{"prompt":"A test"},{"negativePrompt":"blur"},"seed")
        self.assertEqual(validate_output_format("WEBP",{"webp","png"},"outputFormat"),"webp")
        with self.assertRaises(ValueError): validate_output_format("exe",{"webp","png"},"outputFormat")

if __name__ == "__main__": unittest.main()
