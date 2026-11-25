"""JSON-line worker for pinned Haru audio frames and the reviewed deskpaw pointer head."""
import base64
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))


class Worker:
    def load(self, request):
        import numpy as np
        import torch
        torch.set_num_threads(2)
        self.kind, self.folder = request['kind'], Path(request['folder'])
        self.device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
        self.started = time.monotonic()
        if self.kind == 'haru': return self.load_haru(request)
        if self.kind == 'deskpaw': return self.load_deskpaw()
        raise ValueError('Unknown interactive model')

    def load_haru(self, request):
        import numpy as np
        import torch
        from accelerate import load_checkpoint_and_dispatch, init_empty_weights
        from haru import HaruConfig, HaruModel, DuplexConfig, DuplexSession
        from haru.audio import decode_audio
        from haru.prompt import card_templates, render_card
        from tokenizers import Tokenizer
        from safetensors import safe_open
        if not self.device.startswith('cuda'):
            raise RuntimeError('Haru live audio currently requires an NVIDIA CUDA GPU; the model files are kept')
        config = HaruConfig.from_file(self.folder / 'config.json')
        # Non-persistent rotary buffers are absent from safetensors. Initialize
        # those on CPU while keeping the large trainable weights on meta.
        with init_empty_weights(include_buffers=False): model = HaruModel(config)
        free, total = torch.cuda.mem_get_info()
        # Reserve working memory for the streaming codec, recurrent state and prefill.
        budget = free - 1536 * 1024**2
        weight_bytes = (self.folder / 'model.safetensors').stat().st_size
        device_map, offloaded, moved = {'': 0}, 0, 0
        for index in reversed(range(len(model.lm.layers))):
            if weight_bytes - moved <= budget: break
            layer = model.lm.layers[index]
            moved += sum(p.numel() * 2 for p in layer.parameters())
            device_map['lm.layers.' + str(index)] = 'cpu'; offloaded += 1
        if weight_bytes - moved > budget or budget < 3 * 1024**3:
            raise RuntimeError('Not enough free GPU memory for Haru audio. Close a GPU-heavy app and retry; weights are kept')
        import psutil
        if psutil.virtual_memory().available < moved + 2 * 1024**3:
            raise RuntimeError('Not enough free system RAM for Haru offload. Free some memory and retry; weights are kept')
        model = load_checkpoint_and_dispatch(model, str(self.folder / 'model.safetensors'), device_map=device_map,
                                             dtype=torch.bfloat16, offload_buffers=False, force_hooks=bool(offloaded))
        model.tokenizer = Tokenizer.from_file(str(self.folder / 'tokenizer.json'))
        self.model = model.eval().requires_grad_(False)
        if request.get('verify'):
            t = np.arange(72000, dtype=np.float32) / 24000
            reference = (.09 * np.sin(2*np.pi*130*t) + .025*np.sin(2*np.pi*780*t)).astype(np.float32)
        else:
            reference = decode_audio(base64.b64decode(request['reference'], validate=True))
        templates = card_templates()['templates']
        card = render_card({'persona': request.get('persona') or 'Haru is a friendly conversational partner.',
                            'name': 'Haru', 'other_name': 'User', 'situation': 'A friendly voice call.'}, templates['en'])
        self.session = DuplexSession(model, system_prompt=card, reference=reference,
                                     config=DuplexConfig(speak_first=True, max_frames=4500, reference_seconds=30,
                                                         codec_backend='torch', seed=7 if request.get('verify') else None))
        first = self.session.start()
        self.initial = self.audio_result(first)
        return {'device': self.device, 'offloadedLayers': offloaded, 'initial': self.initial,
                'loadSeconds': time.monotonic()-self.started, 'sampleRate': 24000, 'frameSamples': 3200}

    def audio_result(self, frame):
        import numpy as np
        samples = np.asarray(frame.audio, dtype=np.float32).reshape(-1)
        if samples.size != 3200 or not np.isfinite(samples).all(): raise ValueError('Haru returned invalid audio')
        return {'audio': base64.b64encode((samples.clip(-1, 1) * 32767).astype('<i2').tobytes()).decode(),
                'text': frame.text, 'index': frame.index, 'seconds': frame.seconds, 'done': frame.done}

    def frame(self, request):
        import numpy as np
        from hf_interactive import validate_frame
        samples = np.frombuffer(validate_frame(request['audio']), dtype='<i2').astype(np.float32) / 32768
        return self.audio_result(self.session.step(samples))

    def load_deskpaw(self):
        import torch
        from transformers import AutoProcessor, Qwen3VLConfig
        from deskpaw_vendor import Qwen3VLForConditionalGenerationWithPointer
        config = Qwen3VLConfig.from_pretrained(str(self.folder), local_files_only=True)
        self.processor = AutoProcessor.from_pretrained(str(self.folder), local_files_only=True, trust_remote_code=False,
                                                       min_pixels=28*28*16, max_pixels=28*28*1024)
        self.model, info = Qwen3VLForConditionalGenerationWithPointer.from_pretrained(
            str(self.folder), config=config, local_files_only=True, trust_remote_code=False,
            dtype=torch.bfloat16 if self.device.startswith('cuda') else torch.float32,
            device_map={'': self.device}, attn_implementation='sdpa', output_loading_info=True)
        if info.get('missing_keys') or info.get('mismatched_keys'):
            raise RuntimeError('The checkpoint did not load the complete pointer head: ' + str(info)[:600])
        self.model.eval().requires_grad_(False)
        return {'device': self.device, 'loadSeconds': time.monotonic()-self.started}

    def predict(self, request):
        import numpy as np
        import torch
        from PIL import Image
        from hf_interactive import screen_point
        from deskpaw_vendor import grounding_system_message
        start = time.monotonic()
        raw = base64.b64decode(request['image'], validate=True)
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > 6_000_000: raise ValueError('Screenshot exceeds the 6-million-pixel limit')
            image = source.convert('RGB')
        messages = [{'role': 'system', 'content': [{'type': 'text', 'text': grounding_system_message}]},
                    {'role': 'user', 'content': [{'type': 'image', 'image': image}, {'type': 'text', 'text': request['task']}]}]
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        text += '<|pointer_start|>'
        inputs = self.processor(text=[text], images=[image], return_tensors='pt').to(self.device)
        with torch.inference_mode():
            output = self.model(**inputs, output_hidden_states=True, use_cache=False, return_dict=True,
                                visual_token_indices_of_coordinates=[torch.tensor([0], device=self.device)])
        if len(output.pointer_scores) != 1: raise ValueError('The model returned no pointer scores')
        scores = output.pointer_scores[0].float().reshape(-1).tolist()
        grid = inputs['image_grid_thw'][0].tolist()
        merge = self.model.config.vision_config.spatial_merge_size
        rows, columns = int(grid[1]) // merge, int(grid[2]) // merge
        point = screen_point(scores, image.width, image.height, rows, columns)
        return {'action': {'kind': 'click', **point}, 'text': json.dumps(point), 'inferenceVerified': True,
                'elapsedSeconds': time.monotonic()-start, 'imageWidth': image.width, 'imageHeight': image.height,
                'scope': 'One screenshot-grounded click prediction. Execution is a separate user-approved action.'}

    def verify(self):
        if self.kind == 'haru':
            import numpy as np
            frames = [self.initial]
            silence = base64.b64encode(bytes(6400)).decode()
            for _ in range(12): frames.append(self.frame({'audio': silence}))
            pcm = b''.join(base64.b64decode(frame['audio']) for frame in frames)
            signal = np.frombuffer(pcm, dtype='<i2').astype(np.float32)
            if not np.isfinite(signal).all() or signal.std() < .1: raise ValueError('Haru produced no audible test signal')
            seconds = [f['seconds'] for f in frames[4:]]
            return {'inferenceVerified': True, 'frames': len(frames), 'audioSamples': len(signal),
                    'meanFrameSeconds': sum(seconds)/len(seconds), 'realtime': max(seconds) <= 3200/24000,
                    'text': ''.join(f['text'] for f in frames)}
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new('RGB', (640, 360), '#eef2f5'); draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((190, 125, 450, 235), radius=12, fill='#1262d2')
        try: font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 36)
        except OSError: font = ImageFont.load_default()
        draw.text((320, 180), 'Save', font=font, fill='white', anchor='mm')
        buffer = io.BytesIO(); image.save(buffer, format='PNG')
        result = self.predict({'image': base64.b64encode(buffer.getvalue()).decode(), 'task': 'Click the Save button.'})
        point = result['action']
        if not (190 <= point['x'] <= 450 and 125 <= point['y'] <= 235):
            raise ValueError('deskpaw missed the Save button in its actual grounding check: ' + str(point))
        return {**result, 'fixtureTargetHit': True}


def main():
    worker = Worker()
    for line in sys.stdin.buffer:
        try:
            if len(line) > 10 * 1024 * 1024: raise ValueError('Interactive request exceeded its limit')
            request = json.loads(line)
            with redirect_stdout(sys.stderr):
                command = request.get('command')
                if command == 'load': result = worker.load(request)
                elif command == 'verify': result = worker.verify()
                elif command == 'frame': result = worker.frame(request)
                elif command == 'predict': result = worker.predict(request)
                else: raise ValueError('Unknown interactive worker command')
            print(json.dumps(result, ensure_ascii=True), flush=True)
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            print(json.dumps({'error': str(error)}, ensure_ascii=True), flush=True)


if __name__ == '__main__': main()
