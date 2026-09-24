"""Persistent, on-demand visual target grounding for Windows desktop windows.

GoClick returns coordinates in a 1000 x 1000 normalized space. A DXGI capture
keeps the screenshot side of the loop fast, and only the selected window is sent
to the model. No image or model weights leave this computer.
"""
from __future__ import annotations

from pathlib import Path
from io import BytesIO
import base64
import re
import sys
import threading
import time


LOCATION = re.compile(r"<loc_(\d+)>\s*,\s*<loc_(\d+)>")
HERE = Path(__file__).resolve().parent


def parse_location(raw: str, width: int, height: int) -> tuple[int, int] | None:
    match = LOCATION.search(raw)
    if not match or width <= 0 or height <= 0:
        return None
    nx, ny = int(match[1]), int(match[2])
    if not (0 <= nx <= 1000 and 0 <= ny <= 1000):
        return None
    return min(width - 1, round(nx * width / 1000)), min(height - 1, round(ny * height / 1000))


def clamp_rect(rect: tuple[int, int, int, int], display: tuple[int, int]) -> tuple[int, int, int, int] | None:
    left, top, right, bottom = rect
    width, height = display
    result = max(0, left), max(0, top), min(width, right), min(height, bottom)
    return result if result[2] > result[0] and result[3] > result[1] else None


class Grounder:
    """One loaded vision model and DXGI capture device, shared by successive calls."""

    def __init__(self, model_dir: str | Path):
        self.model_dir = Path(model_dir)
        self.lock = threading.Lock()
        self.model = None
        self.processor = None
        self.camera = None
        self.load_ms = None

    @property
    def ready(self) -> bool:
        return self.model is not None

    def _load(self) -> None:
        if self.ready:
            return
        if not (self.model_dir / "model.safetensors").is_file():
            raise RuntimeError(f"Visual grounder weights are missing: {self.model_dir}")
        started = time.perf_counter()
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        # Bundled DXGI bindings contain only the capture library and COM types.
        sys.path.insert(0, str(HERE / "vendor"))
        import dxcam

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = torch.float16 if self.device == "cuda" else torch.float32
        processor = AutoProcessor.from_pretrained(self.model_dir, trust_remote_code=True,
                                                  local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(
            self.model_dir, trust_remote_code=True, local_files_only=True,
            torch_dtype=self.dtype, use_safetensors=True,
        ).to(self.device).eval()
        # Transformers 5 omits two tied embedding assignments from this model's
        # Transformers 4 checkpoint. Without these, coordinates become random.
        shared = model.language_model.model.shared.weight
        model.language_model.model.encoder.embed_tokens.weight = shared
        model.language_model.model.decoder.embed_tokens.weight = shared
        camera = dxcam.create(output_idx=0, output_color="RGB", processor_backend="numpy")
        self.processor, self.model, self.camera = processor, model, camera
        self.load_ms = round((time.perf_counter() - started) * 1000, 1)

    def ground(self, hwnd: int, goal: str) -> dict:
        if hwnd <= 0 or not goal.strip():
            raise ValueError("Select a visible window and describe one specific target")
        with self.lock:
            return self._ground(hwnd, goal.strip())

    def predict_image(self, data_url: str, goal: str, crop: tuple[int, int, int, int] | None = None) -> dict:
        """Ground a screenshot supplied by the desktop capture tool, without UI input."""
        if not goal.strip() or not data_url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")):
            raise ValueError("A JPEG or PNG screenshot and a specific target are required")
        from PIL import Image

        image = Image.open(BytesIO(base64.b64decode(data_url.partition(",")[2], validate=True))).convert("RGB")
        offset_x = offset_y = 0
        if crop is not None:
            bounded = clamp_rect(crop, image.size)
            if bounded is None:
                raise ValueError("The screenshot crop is empty or outside the image")
            offset_x, offset_y = bounded[:2]
            image = image.crop(bounded)
        with self.lock:
            self._load()
            result = self._infer(image, goal.strip())
            if result["found"]:
                result["x"] += offset_x
                result["y"] += offset_y
            return result

    def _ground(self, hwnd: int, goal: str) -> dict:
        import win32con
        import win32gui
        import torch
        from PIL import Image

        if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
            raise ValueError("The target window is not visible")
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        if win32gui.GetForegroundWindow() != hwnd:
            raise RuntimeError("Windows did not bring the target window to the front; visual grounding would see an obscured window")

        self._load()
        bounds = tuple(win32gui.GetWindowRect(hwnd))
        started = time.perf_counter()
        frame = self.camera.grab(new_frame_only=False)
        if frame is None:
            raise RuntimeError("DXGI did not capture a desktop frame")
        screen_height, screen_width = frame.shape[:2]
        crop = clamp_rect(bounds, (screen_width, screen_height))
        if crop is None:
            raise ValueError("The target window is outside the primary display")
        left, top, right, bottom = crop
        image = Image.fromarray(frame[top:bottom, left:right]).convert("RGB")
        capture_ms = round((time.perf_counter() - started) * 1000, 1)

        inferred = self._infer(image, goal)
        point = (inferred["x"], inferred["y"]) if inferred["found"] else None
        if point is None:
            return {**inferred, "capture_ms": capture_ms}
        x = left - bounds[0] + point[0]
        y = top - bounds[1] + point[1]
        return {**inferred, "x": x, "y": y, "windowId": hwnd,
                "capture_ms": capture_ms,
                "total_ms": round(capture_ms + inferred["inference_ms"], 1),
                "coordinate_space": "window_relative", "verified": False}

    def _infer(self, image, goal: str) -> dict:
        import torch

        prompt = f"Where is the {goal} element? (Output the center coordinates of the target)"
        started = time.perf_counter()
        inputs = self.processor(images=image, text=prompt, return_tensors="pt", do_resize=True)
        inputs = {key: value.to(self.device, dtype=self.dtype if value.dtype.is_floating_point else None)
                  for key, value in inputs.items()}
        with torch.inference_mode():
            output = self.model.generate(**inputs, do_sample=False, num_beams=1,
                                         max_new_tokens=16, use_cache=False)
        raw = self.processor.tokenizer.batch_decode(output, skip_special_tokens=False)[0]
        if self.device == "cuda":
            torch.cuda.synchronize()
        inference_ms = round((time.perf_counter() - started) * 1000, 1)
        point = parse_location(raw, image.width, image.height)
        if point is None:
            return {"found": False, "reason": "The visual model did not return a valid coordinate",
                    "inference_ms": inference_ms, "model_load_ms": self.load_ms}
        return {"found": True, "x": point[0], "y": point[1],
                "inference_ms": inference_ms, "model_load_ms": self.load_ms}
