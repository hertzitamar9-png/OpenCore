"""One local Unlimited OCR request; image and result stay in memory."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys

os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    request = json.load(sys.stdin)
    if request.get('mode') not in ('verify', 'recognize'):
        raise ValueError('Unsupported OCR request')
    # Third-party diagnostics may contain document data: discard them, rather
    # than forwarding them to setup or service logs.
    with open(os.devnull, 'w', encoding='utf-8') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        import torch
        from PIL import Image, ImageDraw, ImageFont
        from transformers import AutoTokenizer
        from unlimited_ocr_vendor.modeling_unlimitedocr import UnlimitedOCRConfig, UnlimitedOCRForCausalLM
        torch.set_num_threads(2)
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
            raise RuntimeError('Unlimited OCR needs an NVIDIA GPU with CUDA and BF16 support')
        free, _ = torch.cuda.mem_get_info()
        if free < 8 * 1024**3:
            raise RuntimeError('Unlimited OCR needs at least 8 GiB of free GPU memory')
        folder = request['folder']
        tokenizer = AutoTokenizer.from_pretrained(folder, local_files_only=True, trust_remote_code=False)
        config = UnlimitedOCRConfig.from_pretrained(folder, local_files_only=True)
        config.sparse_attention_config['dash_use_triton'] = False
        model = UnlimitedOCRForCausalLM.from_pretrained(folder, config=config, torch_dtype=torch.bfloat16,
            local_files_only=True, use_safetensors=True, device_map='cuda').eval()

        def run(image, max_tokens):
            with torch.inference_mode():
                return model.infer(tokenizer, image_file=image, eval_mode=True, crop_mode=False,
                                   max_new_tokens=max_tokens, max_length=4096)

        if request['mode'] == 'verify':
            try:
                font = ImageFont.truetype(str(Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / 'arial.ttf'), 66)
            except OSError:
                font = ImageFont.load_default(size=66)
            outputs = []
            for phrase in ('OPENCORE 123', 'SUNFLOWER 472', ''):
                image = Image.new('RGB', (900, 350), 'white')
                if phrase:
                    ImageDraw.Draw(image).text((60, 110), phrase, font=font, fill='black')
                outputs.append(run(image, 128))
            from hf_specialist_ocr import readable_text
            actual = [readable_text(value).strip().upper() for value in outputs]
            if actual != ['OPENCORE 123', 'SUNFLOWER 472', '']:
                raise RuntimeError('The OCR verification images did not return the expected text')
            result = {'inferenceVerified': True, 'gpuPeakMiB': torch.cuda.max_memory_allocated() // 1024**2}
        else:
            raw = base64.b64decode(request['image'], validate=True)
            if not 0 < len(raw) <= 5 * 1024 * 1024:
                raise ValueError('Choose an image up to 5 MB')
            Image.MAX_IMAGE_PIXELS = 25_000_000
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ('PNG', 'JPEG', 'WEBP') or image.width * image.height > 25_000_000:
                    raise ValueError('Choose a PNG, JPEG or WebP image up to 25 megapixels')
                image.load()
                output = run(image.convert('RGB'), 2048)
            from hf_specialist_ocr import readable_text
            result = {'text': readable_text(output), 'raw': output}
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else 'The local OCR worker could not process this image'
        print(json.dumps({'error': message[:500]}, ensure_ascii=True), flush=True)
        sys.exit(1)
