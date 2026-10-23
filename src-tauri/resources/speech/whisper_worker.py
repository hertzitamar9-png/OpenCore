"""Offline multilingual Whisper large-v3 / Turbo worker for OpenCore dictation.

The model stays in CPU RAM only in the optional RAM standby mode. It is moved to
CUDA for a recording and returned to CPU after transcription. Cold mode runs in
its own process and is fully released when Rust stops that process.
"""
import argparse
import gc
import json
import os
from pathlib import Path
import sys
import time

ASR_CHUNK_LENGTH_SECONDS = 3
ASR_STRIDE_LENGTH_SECONDS = (0.75, 0.75)


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def memory_gib(value):
    return f"{value / 1024**3:.1f} GiB"


def require_ram(psutil, required, purpose):
    available = psutil.virtual_memory().available
    if available < required:
        raise RuntimeError(f"Not enough free system RAM for {purpose}: need {memory_gib(required)}, have {memory_gib(available)}.")


def gpu_free(torch):
    if not torch.cuda.is_available():
        return 0
    try:
        free, _total = torch.cuda.mem_get_info(0)
        return int(free)
    except Exception:
        return 0


def require_cuda(torch, required, purpose):
    if not torch.cuda.is_available():
        raise RuntimeError(f"CUDA is unavailable for {purpose}. Prepare the CUDA speech runtime and check the NVIDIA driver in Models.")
    available = gpu_free(torch)
    if available < required:
        raise RuntimeError(f"Not enough free VRAM for {purpose}: need {memory_gib(required)}, have {memory_gib(available)}. Stop other GPU work and try again.")


def gpu_error(error):
    return RuntimeError(f"Speech GPU inference failed: {error}. Free VRAM or repair the CUDA speech runtime in Models; CPU inference is disabled.")


def release_cuda_cache(torch):
    gc.collect()
    if torch.cuda.is_available():
        # empty_cache alone retains cuBLAS workspaces on supported Torch builds:
        # https://docs.pytorch.org/docs/stable/notes/cuda.html#cublas-workspaces
        clear_blas = getattr(getattr(torch, '_C', None), '_cuda_clearCublasWorkspaces', None)
        if clear_blas is not None:
            clear_blas()
        torch.cuda.empty_cache()


def load_audio(path, av, np):
    chunks = []
    try:
        with av.open(str(path)) as container:
            stream = next((item for item in container.streams if item.type == "audio"), None)
            if stream is None:
                raise RuntimeError("The recording contains no audio stream.")
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
            for frame in container.decode(stream):
                for output in resampler.resample(frame):
                    chunks.append(output.to_ndarray().reshape(-1))
            for output in resampler.resample(None):
                chunks.append(output.to_ndarray().reshape(-1))
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError(f"Could not decode the microphone recording: {error}") from error
    if not chunks:
        raise RuntimeError("The recording was empty or could not be decoded.")
    return np.concatenate(chunks).astype(np.float32, copy=False)


def transcribe_code_switched(recognizer, audio):
    """Transcribe short overlapping audio chunks with per-chunk language detection.

    A single language guess for a whole recording can suppress code-switched
    words. Whisper's ASR pipeline stitches these overlapping chunks while
    retaining the original spoken language in each chunk.
    """
    return recognizer(
        {"array": audio, "sampling_rate": 16000},
        chunk_length_s=ASR_CHUNK_LENGTH_SECONDS,
        stride_length_s=ASR_STRIDE_LENGTH_SECONDS,
        generate_kwargs={"task": "transcribe", "language": None, "forced_decoder_ids": None},
        return_language=True,
    )


def transcribe_ct2(model, audio):
    segments, info = model.transcribe(audio, task="transcribe", language=None,
        multilingual=True, condition_on_previous_text=False)
    return {"text": " ".join(segment.text.strip() for segment in segments).strip(),
        "language": info.language}


class CTranslateWhisper:
    def __init__(self, directory, torch, psutil, awake):
        self.directory, self.torch, self.psutil = directory, torch, psutil
        self.minimum_gpu = 4 * 1024**3
        self.dll_handles = []
        if os.name == "nt":
            # Reuse the installed CUDA runtime; never download a second DLL set.
            lib = str(Path(torch.__file__).parent / "lib")
            self.dll_handles.append(os.add_dll_directory(lib))
            os.environ["PATH"] = lib + os.pathsep + os.environ.get("PATH", "")
        from faster_whisper import WhisperModel
        self.factory = WhisperModel
        self.model = None
        self.origin = None
        self.device = "cpu"
        if awake:
            require_cuda(torch, self.minimum_gpu, "Whisper dictation")
        self.load("cuda" if awake else "cpu")

    def load(self, device):
        if device == "cpu":
            require_ram(self.psutil, 8 * 1024**3, "full Whisper RAM standby")
        try:
            candidate = self.factory(str(self.directory), device=device,
                compute_type="float16" if device == "cuda" else "float32",
                cpu_threads=2, num_workers=1, local_files_only=True)
        except RuntimeError as error:
            if device == "cuda":
                raise gpu_error(error) from error
            raise
        if self.model is not None:
            self.model.model.unload_model()
        self.model = candidate
        gc.collect()
        self.origin = device
        self.device = "cuda:0" if device == "cuda" else "cpu"

    def activate(self):
        started = time.monotonic()
        require_cuda(self.torch, self.minimum_gpu, "Whisper dictation")
        try:
            if self.origin == "cuda":
                self.model.model.load_model()
                self.device = "cuda:0"
            else:
                self.load("cuda")
        except RuntimeError as error:
            self.sleep()
            raise gpu_error(error) from error
        return round((time.monotonic() - started) * 1000)

    def sleep(self):
        if self.origin == "cuda":
            self.model.model.unload_model(to_cpu=True)
        self.device = "cpu"

    def transcribe(self, audio):
        if self.device != "cuda:0":
            self.activate()
        try:
            return transcribe_ct2(self.model, audio)
        except RuntimeError as error:
            self.sleep()
            raise gpu_error(error) from error


class TransformersWhisper:
    def __init__(self, directory, torch, psutil, awake):
        from transformers import AutoProcessor, WhisperForConditionalGeneration, pipeline
        config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        self.minimum_gpu = (2.5 if config.get("decoder_layers") == 4 else 4) * 1024**3
        self.cpu_ram = (4 if config.get("decoder_layers") == 4 else 8) * 1024**3
        self.torch, self.psutil = torch, psutil
        require_ram(psutil, self.cpu_ram, "Whisper checkpoint loading")
        processor = AutoProcessor.from_pretrained(str(directory), local_files_only=True)
        self.model = WhisperForConditionalGeneration.from_pretrained(str(directory),
            local_files_only=True, use_safetensors=True, torch_dtype=torch.float16,
            low_cpu_mem_usage=True)
        self.model.generation_config.language = None
        self.model.generation_config.task = "transcribe"
        self.model.generation_config.forced_decoder_ids = None
        self.recognizer = pipeline("automatic-speech-recognition", model=self.model,
            tokenizer=processor.tokenizer, feature_extractor=processor.feature_extractor, device=-1)
        self.device = "cpu"
        if awake:
            self.activate()

    def activate(self):
        started = time.monotonic()
        require_cuda(self.torch, self.minimum_gpu, "Whisper dictation")
        try:
            self.model.to(device="cuda:0", dtype=self.torch.float16)
            self.recognizer.device = self.torch.device("cuda:0")
            self.device = "cuda:0"
        except Exception as error:
            self.sleep()
            raise gpu_error(error) from error
        return round((time.monotonic() - started) * 1000)

    def sleep(self):
        self.model.to(device="cpu", dtype=self.torch.float16)
        self.device = "cpu"
        self.recognizer.device = self.torch.device("cpu")
        release_cuda_cache(self.torch)

    def transcribe(self, audio):
        if self.device != "cuda:0":
            self.activate()
        try:
            return transcribe_code_switched(self.recognizer, audio)
        except Exception as error:
            self.sleep()
            raise gpu_error(error) from error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--idle-mode", choices=("cold", "ram"), required=True)
    parser.add_argument("--awake", action="store_true")
    args = parser.parse_args()
    model_dir = Path(args.model).resolve()

    try:
        import av
        import numpy as np
        import psutil
        import torch
        torch.set_num_threads(2)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass

        started = time.monotonic()
        if (model_dir / "model.bin").is_file():
            engine = CTranslateWhisper(model_dir, torch, psutil, args.awake)
        elif (model_dir / "config.json").is_file() and ((model_dir / "model.safetensors").is_file() or
                (model_dir / "model.safetensors.index.json").is_file()):
            engine = TransformersWhisper(model_dir, torch, psutil, args.awake)
        else:
            raise RuntimeError(f"Whisper checkpoint is incomplete: {model_dir}")
        elapsed = round((time.monotonic() - started) * 1000)
        emit({"ready": True, "coldStartMs": elapsed, "wakeMs": elapsed if args.awake else None,
            "device": engine.device})

        for line in sys.stdin:
            try:
                request = json.loads(line)
                action = request.get("action")
                if action == "shutdown":
                    break
                if action == "wake":
                    wake_ms = engine.activate()
                    emit({"awake": True, "wakeMs": wake_ms, "device": engine.device})
                    continue
                if action == "transcribe":
                    audio = load_audio(request["audio"], av, np)
                    # Always preserve the spoken language; never invoke Whisper translation.
                    result = engine.transcribe(audio)
                    text = result.get("text", "").strip()
                    language = result.get("language") or "auto"
                    used_device = engine.device
                    engine.sleep()
                    emit({"text": text, "language": language, "device": used_device,
                        "standbyDevice": engine.device, "gpuModelBytes": sum(t.numel()*t.element_size()
                            for t in list(engine.model.parameters())+list(engine.model.buffers()) if t.is_cuda)
                            if isinstance(engine,TransformersWhisper) else
                            (0 if engine.origin=="cpu" or not engine.model.model.model_is_loaded else None),
                        "torchGpuBytes": torch.cuda.memory_allocated() if torch.cuda.is_available() else 0,
                        "torchReservedBytes": torch.cuda.memory_reserved() if torch.cuda.is_available() else 0})
                    continue
                emit({"error": f"Unknown speech action: {action}"})
            except Exception as error:
                try:
                    engine.sleep()
                except Exception as cleanup_error:
                    emit({"error": f"{error}; GPU offload failed: {cleanup_error}"})
                    return 1
                emit({"error": str(error)})

        del engine
        release_cuda_cache(torch)
    except Exception as error:
        emit({"error": f"Speech CUDA runtime could not start: {error}. Prepare or repair speech in Models."})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
