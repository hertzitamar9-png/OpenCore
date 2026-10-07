import unittest
from asset_worker import bounded_integer, checkpoint_dtypes, generate, supported_generation_kwargs, validate_output_format
import json, struct, tempfile, sys, types
from pathlib import Path
from unittest.mock import patch

class StudioBounds(unittest.TestCase):
    def write_safetensors_header(self, path, dtype):
        path.parent.mkdir(parents=True, exist_ok=True)
        header=json.dumps({"weight":{"dtype":dtype,"shape":[1],"data_offsets":[0,2]}}).encode()
        path.write_bytes(struct.pack("<Q",len(header))+header+b"00")

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

    def test_sana_runs_through_the_builtin_fp16_diffusers_worker(self):
        class Image:
            def save(self, path, format=None): Path(path).write_bytes(b"image")
        class Result: images = [Image()]
        class Pipeline:
            loaded = None
            @classmethod
            def from_pretrained(cls, path, **kwargs):
                cls.loaded = (path, kwargs)
                return cls()
            def enable_model_cpu_offload(self): pass
            def __call__(self, prompt, num_inference_steps, width, height, generator,
                         negative_prompt=None, guidance_scale=1, num_images_per_prompt=1):
                return Result()
        class Generator:
            def __init__(self, device): self.device=device
            def manual_seed(self, seed): self.seed=seed; return self
        torch=types.ModuleType("torch")
        torch.float32="float32"; torch.float16="float16"; torch.bfloat16="bfloat16"
        torch.Generator=Generator
        diffusers=types.ModuleType("diffusers")
        diffusers.__version__="0.35.0"; diffusers.DiffusionPipeline=Pipeline
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/"models"/"library"/"sana-16").mkdir(parents=True)
            self.write_safetensors_header(root/"models"/"library"/"sana-16"/"transformer"/"diffusion_pytorch_model.fp16.safetensors", "F16")
            output=root/"output"
            request={"modelId":"sana-16","modelRoot":str(root),"prompt":"a game icon","settings":{}}
            with patch.dict(sys.modules,{"torch":torch,"diffusers":diffusers}):
                generate(request,output)
            self.assertTrue((output/"image-1.png").is_file())
            self.assertEqual(Pipeline.loaded[1]["variant"],"fp16")
            self.assertTrue(Pipeline.loaded[1]["local_files_only"])

    def test_hunyuan_dit_runs_through_the_builtin_cpu_offload_worker(self):
        class Image:
            def save(self, path, format=None): Path(path).write_bytes(b"image")
        class Result: images = [Image()]
        class Pipeline:
            loaded = None
            @classmethod
            def from_pretrained(cls, path, **kwargs):
                cls.loaded = (path, kwargs)
                return cls()
            def enable_model_cpu_offload(self): pass
            def __call__(self, prompt, num_inference_steps, width, height, generator,
                         negative_prompt=None, guidance_scale=1, num_images_per_prompt=1):
                return Result()
        class Generator:
            def __init__(self, device): self.device=device
            def manual_seed(self, seed): self.seed=seed; return self
        torch=types.ModuleType("torch")
        torch.float32="float32"; torch.float16="float16"; torch.bfloat16="bfloat16"
        torch.Generator=Generator
        diffusers=types.ModuleType("diffusers")
        diffusers.__version__="0.35.0"; diffusers.DiffusionPipeline=Pipeline
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/"models"/"library"/"hunyuan-dit-v12-distilled").mkdir(parents=True)
            self.write_safetensors_header(root/"models"/"library"/"hunyuan-dit-v12-distilled"/"transformer"/"diffusion_pytorch_model.safetensors", "BF16")
            output=root/"output"
            request={"modelId":"hunyuan-dit-v12-distilled","modelRoot":str(root),"prompt":"a game icon","settings":{}}
            with patch.dict(sys.modules,{"torch":torch,"diffusers":diffusers}):
                generate(request,output)
            self.assertTrue((output/"image-1.png").is_file())
            self.assertTrue(Pipeline.loaded[1]["local_files_only"])

    def test_animation_diffusion_checkpoint_generates_a_still_image(self):
        # ModelsLab/3D-Animation-Diffusion is a Stable Diffusion image model.
        # Its name does not make its pipeline produce temporal frames.
        class Image:
            def save(self, path, format=None): Path(path).write_bytes(b"image")
        class Pipeline:
            @classmethod
            def from_pretrained(cls, path, **kwargs): return cls()
            def enable_model_cpu_offload(self): pass
            def __call__(self, prompt, num_inference_steps, width, height, generator):
                return types.SimpleNamespace(images=[Image()])
        class Generator:
            def __init__(self, device): pass
            def manual_seed(self, seed): return self
        torch=types.ModuleType("torch")
        torch.float32="float32"; torch.float16="float16"; torch.bfloat16="bfloat16"; torch.Generator=Generator
        diffusers=types.ModuleType("diffusers")
        diffusers.__version__="0.41.0"; diffusers.DiffusionPipeline=Pipeline
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            self.write_safetensors_header(root/"models/library/animation-diffusion-2d/unet/model.safetensors", "F16")
            output=root/"output"
            request={"modelId":"animation-diffusion-2d","modelRoot":str(root),"prompt":"a character sheet","settings":{}}
            with patch.dict(sys.modules,{"torch":torch,"diffusers":diffusers}):
                generate(request,output)
            self.assertTrue((output/"image-1.png").is_file())
            self.assertFalse((output/"animation.gif").exists())

if __name__ == "__main__": unittest.main()
