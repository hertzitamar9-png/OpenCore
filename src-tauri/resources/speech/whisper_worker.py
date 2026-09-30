"""Offline Whisper Large V3 Turbo worker for OpenCore dictation.

The model stays in CPU RAM only in the optional RAM standby mode. It is moved to
CUDA for a recording and returned to CPU after transcription. Cold mode runs in
its own process and is fully released when Rust stops that process.
"""
import argparse
import gc
import json
from pathlib import Path
import sys
import time

MODEL_MIN_GPU_FREE = 6 * 1024**3
CPU_MODEL_MIN_FREE = 15 * 1024**3
RAM_STANDBY_MIN_FREE = 9 * 1024**3
CPU_WAKE_MIN_FREE = 10 * 1024**3
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
        from transformers import AutoProcessor, WhisperForConditionalGeneration, pipeline
        torch.set_num_threads(2)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass

        if not (model_dir / "config.json").is_file() or not (
            (model_dir / "model.safetensors").is_file() or
            (model_dir / "model.safetensors.index.json").is_file()
        ):
            raise RuntimeError(f"Whisper Large V3 Turbo checkpoint is incomplete: {model_dir}")

        started = time.monotonic()
        cuda_room = gpu_free(torch) >= MODEL_MIN_GPU_FREE
        if args.idle_mode == "cold" and not cuda_room:
            require_ram(psutil, CPU_MODEL_MIN_FREE, "CPU speech recognition")
            dtype = torch.float32
        else:
            require_ram(psutil, RAM_STANDBY_MIN_FREE, "Whisper model standby")
            dtype = torch.float16

        processor = AutoProcessor.from_pretrained(str(model_dir), local_files_only=True)
        model = WhisperForConditionalGeneration.from_pretrained(
            str(model_dir), local_files_only=True, use_safetensors=True,
            torch_dtype=dtype, low_cpu_mem_usage=True,
        )
        model.generation_config.language = None
        model.generation_config.task = "transcribe"
        model.generation_config.forced_decoder_ids = None
        recognizer = pipeline(
            "automatic-speech-recognition", model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            device=-1,
        )
        current_device = "cpu"
        current_dtype = dtype

        def activate():
            nonlocal current_device, current_dtype
            wake_started = time.monotonic()
            available_gpu = gpu_free(torch)
            if torch.cuda.is_available() and available_gpu >= MODEL_MIN_GPU_FREE:
                try:
                    model.to(device="cuda:0", dtype=torch.float16)
                    recognizer.device = torch.device("cuda:0")
                    current_device = "cuda:0"
                    current_dtype = torch.float16
                    return round((time.monotonic() - wake_started) * 1000)
                except torch.cuda.OutOfMemoryError:
                    model.to(device="cpu", dtype=torch.float16)
                    torch.cuda.empty_cache()
            if current_dtype != torch.float32:
                require_ram(psutil, CPU_WAKE_MIN_FREE, "CPU fallback transcription")
                model.to(device="cpu", dtype=torch.float32)
                current_dtype = torch.float32
            recognizer.device = torch.device("cpu")
            current_device = "cpu"
            return round((time.monotonic() - wake_started) * 1000)

        def sleep_in_ram():
            nonlocal current_device, current_dtype
            if current_device.startswith("cuda"):
                model.to(device="cpu", dtype=torch.float16)
                torch.cuda.empty_cache()
                gc.collect()
                current_dtype = torch.float16
            current_device = "cpu"
            recognizer.device = torch.device("cpu")

        wake_ms = None
        if args.awake:
            wake_ms = activate()
        emit({"ready": True, "coldStartMs": round((time.monotonic() - started) * 1000), "wakeMs": wake_ms})

        for line in sys.stdin:
            try:
                request = json.loads(line)
                action = request.get("action")
                if action == "shutdown":
                    break
                if action == "wake":
                    wake_ms = activate()
                    emit({"awake": True, "wakeMs": wake_ms, "device": current_device})
                    continue
                if action == "transcribe":
                    audio = load_audio(request["audio"], av, np)
                    # Always preserve the spoken language; never invoke Whisper translation.
                    result = transcribe_code_switched(recognizer, audio)
                    text = result.get("text", "").strip()
                    language = result.get("language") or "auto"
                    if args.idle_mode == "ram":
                        sleep_in_ram()
                    emit({"text": text, "language": language, "device": current_device})
                    continue
                emit({"error": f"Unknown speech action: {action}"})
            except Exception as error:
                if args.idle_mode == "ram":
                    try:
                        sleep_in_ram()
                    except Exception:
                        pass
                emit({"error": str(error)})

        del recognizer, model, processor
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()
    except Exception as error:
        emit({"error": str(error)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
