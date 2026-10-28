"""Pinned checkpoint admission and tiled learned-super-resolution behavior."""
import importlib
import os
from pathlib import Path
import tempfile
import unittest

from PIL import Image
try:
    import numpy as np
    import torch
    from torch import nn
    from torch.nn import functional as F
except ModuleNotFoundError:
    raise unittest.SkipTest('Tensor integration tests require the managed image runtime')

try:
    ai_upscale = importlib.import_module("ai_upscale")
except ModuleNotFoundError:
    ai_upscale = None


class LocalFilterUpscaler(nn.Module):
    """A bounded real tensor operation with a known full-frame reference."""
    scale = 4
    device = torch.device("cpu")
    dtype = torch.float32

    def __init__(self):
        super().__init__()
        self.input_shapes = []

    def forward(self, pixels):
        self.input_shapes.append(tuple(pixels.shape))
        filtered = F.avg_pool2d(pixels, 5, stride=1, padding=2)
        return F.interpolate(filtered, scale_factor=4, mode="nearest")


def sample_image(width=96, height=72, mode="RGB"):
    yy, xx = np.indices((height, width))
    pixels = np.stack(((xx * 7 + yy * 3) % 256,
                       (xx * 2 + yy * 11) % 256,
                       ((xx // 7 + yy // 5) % 2) * 255), axis=2).astype(np.uint8)
    image = Image.fromarray(pixels)
    if mode == "RGBA":
        image.putalpha(Image.fromarray(((xx * 3 + yy) % 256).astype(np.uint8)))
    return image


class AiUpscaleTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ai_upscale, "The offline AI upscaler helper is missing")

    def test_rejects_untrusted_checkpoint_before_deserialization(self):
        with tempfile.TemporaryDirectory() as folder:
            checkpoint = Path(folder) / "modified.pth"
            checkpoint.write_bytes(b"not the publisher checkpoint")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                ai_upscale.load_upscale_model(checkpoint, device="cpu")

    def test_invalid_target_and_tile_values_fail_before_model_loading(self):
        with sample_image(16, 16) as image:
            for target in ((8193, 64), (64, True), (0, 64), (64.0, 64)):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    ai_upscale.upscale_image(image, "absent.pth", target_size=target)
            for size in (0, -1, True, 1025):
                with self.subTest(tile_size=size), self.assertRaises(ValueError):
                    ai_upscale.upscale_image(image, "absent.pth", target_size=(64, 64), tile_size=size)

    def test_downsize_and_native_copy_do_not_require_a_checkpoint(self):
        with sample_image(16, 16, "RGBA") as image:
            with ai_upscale.upscale_image(image, "absent.pth", target_size=(16, 16)) as native:
                self.assertIsNot(native, image)
                self.assertEqual(native.mode, "RGBA")
                self.assertEqual(native.tobytes(), image.tobytes())
            with ai_upscale.upscale_image(image, "absent.pth", target_size=(8, 8)) as smaller:
                self.assertEqual(smaller.size, (8, 8))
                self.assertEqual(smaller.mode, "RGBA")

    def test_tiled_pixels_match_full_frame_inference_at_native_and_fractional_sizes(self):
        torch.set_num_threads(2)
        with sample_image() as image:
            tensor = torch.from_numpy(np.asarray(image).copy()).permute(2, 0, 1).float().unsqueeze(0) / 255
            with torch.inference_mode():
                values = LocalFilterUpscaler()(tensor)[0].clamp(0, 1)
                raw = (values.permute(1, 2, 0).numpy() * 255).round().astype(np.uint8)
            with Image.fromarray(raw) as full:
                for size in ((384, 288), (213, 159)):
                    model = LocalFilterUpscaler()
                    events = []
                    with self.subTest(size=size), ai_upscale._upscale_pass(
                        image, model, size, tile_size=32, tile_pad=40,
                        progress=lambda stage, **details: events.append((stage, details)),
                    ) as actual, full.resize(size, Image.Resampling.LANCZOS) as expected:
                        error = np.abs(np.asarray(actual).astype(np.int16) - np.asarray(expected).astype(np.int16))
                        self.assertLessEqual(int(error.max()), 1, "Tiling introduced a visible boundary or a sample alignment error")
                        self.assertEqual(actual.size, size)
                    self.assertGreater(len(model.input_shapes), 1)
                    self.assertTrue(all(shape[-1] <= 112 and shape[-2] <= 112 for shape in model.input_shapes))
                    self.assertEqual(events[-1][1]["tile"], events[-1][1]["tiles"])

    @unittest.skipUnless(os.environ.get("OPENCORE_AI_UPSCALE_TEST_MODEL"), "Set an explicit local publisher checkpoint for the CPU integration test")
    def test_pinned_pretrained_model_runs_multiple_passes_and_preserves_alpha(self):
        torch.set_num_threads(2)
        checkpoint = Path(os.environ["OPENCORE_AI_UPSCALE_TEST_MODEL"])
        with sample_image(16, 16, "RGBA") as image:
            events = []
            with ai_upscale.upscale_image(
                image, checkpoint, target_size=(128, 128), device="cpu", tile_size=32,
                progress=lambda stage, **details: events.append((stage, details)),
            ) as actual, image.resize((128, 128), Image.Resampling.LANCZOS) as resized:
                self.assertEqual(actual.size, (128, 128))
                self.assertEqual(actual.mode, "RGBA")
                self.assertEqual(actual.getchannel("A").tobytes(), resized.getchannel("A").tobytes())
                difference = np.abs(np.asarray(actual)[:, :, :3].astype(np.int16) - np.asarray(resized)[:, :, :3].astype(np.int16))
                self.assertGreater(float(difference.mean()), 1, "Output was replaced with plain resizing")
            self.assertEqual(max(details.get("passIndex", 0) for _, details in events), 2)

    @unittest.skipUnless(os.environ.get("OPENCORE_AI_UPSCALE_TEST_MODEL"), "Set an explicit local publisher checkpoint for the CPU integration test")
    def test_mixed_axis_resizing_remains_bounded_and_produces_exact_size(self):
        torch.set_num_threads(2)
        with sample_image(64, 16) as image, ai_upscale.upscale_image(
            image, os.environ["OPENCORE_AI_UPSCALE_TEST_MODEL"],
            target_size=(1, 64), device="cpu", tile_size=16,
        ) as actual:
            self.assertEqual(actual.size, (1, 64))
            self.assertGreater(int(np.asarray(actual).sum()), 0)


if __name__ == "__main__":
    unittest.main()
