"""Isolated inference entry point; model download directories supply weights, never code."""
import argparse
import base64
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))

HOBBY_SYSTEM = """You are Hobby, an offline helper on a Mac. Each turn you see the user's request and the current state, and choose exactly ONE next action.
Allowed actions:
OPEN <app>  (apps: Calculator, Notes, Calendar, Finder, Music, Reminders)
TYPE <keys>  (calculator keys only: digits . + - * / =)
KEY BRIGHTNESS_DOWN | KEY BRIGHTNESS_UP | KEY VOLUME_DOWN | KEY VOLUME_UP | KEY MUTE
DARK_MODE ON | DARK_MODE OFF
FOLDER Downloads | FOLDER Desktop | FOLDER Documents
TIMER <minutes>
SCREENSHOT
EVENT <title> | <day> | <time> [timezone] | <person name or email, or none>  (add a calendar event)
DONE  (the request is complete)
UNKNOWN  (the request is not something these actions can do)
For maths, open the calculator, type the numbers and the operation, and let it compute. Reply with the action only."""


def fixture_png():
    width, height = 96, 64
    pixels = b''.join(b'\0'+b''.join(b'\x30\x70\xb0' if 20 < x < 76 and 20 < y < 45 else b'\xff\xff\xff' for x in range(width)) for y in range(height))
    def chunk(kind, value): return struct.pack('!I', len(value))+kind+value+struct.pack('!I', zlib.crc32(kind+value)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0))+chunk(b'IDAT', zlib.compress(pixels))+chunk(b'IEND', b'')


def hobby(request):
    import torch
    from transformers import GPT2TokenizerFast
    from safetensors.torch import load_file
    from hobbylm_vendor.configuration_hobbylm import HobbyLMConfig
    from hobbylm_vendor.modeling_hobbylm import HobbyLMForCausalLM
    torch.set_num_threads(2)
    folder, base = Path(request['folder']), Path(request['base'])
    config = HobbyLMConfig.from_json_file(str(base/'config.json'))
    model = HobbyLMForCausalLM.from_pretrained(base, config=config, local_files_only=True,
                  torch_dtype=torch.float32, low_cpu_mem_usage=True).eval()
    tokenizer = GPT2TokenizerFast.from_pretrained(base, local_files_only=True)
    settings = json.loads((folder/'adapter_config.json').read_text(encoding='utf-8'))
    if settings.get('base_model_name_or_path') != 'harims95/hobbylm-1B-instruct': raise ValueError('HobbyLM adapter base differs')
    state = load_file(str(folder/'adapter_model.safetensors'))
    modules, count = dict(model.named_modules()), 0
    scale = settings['lora_alpha']/settings['r']
    with torch.no_grad():
        for key, a in state.items():
            if '.lora_A.' not in key: continue
            partner = key.replace('.lora_A.', '.lora_B.')
            name = key.split('.lora_A.')[0].removeprefix('base_model.model.')
            if partner not in state or name not in modules or not isinstance(modules[name], torch.nn.Linear):
                raise ValueError('HobbyLM LoRA does not match the pinned base module: '+name)
            delta = scale*(state[partner].float()@a.float())
            if delta.shape != modules[name].weight.shape: raise ValueError('HobbyLM LoRA tensor shape differs')
            modules[name].weight.add_(delta); count += 1
    if not count or count*2 != len(state): raise ValueError('The complete HobbyLM LoRA was not applied')
    verify = request['mode'] == 'verify'
    task = 'Open the calculator' if verify else request['task']
    live = ('Calculator: closed\nFront app: Code\nBrightness: 56%  Volume: 50%\nDark mode: on' if verify else
            request.get('state') or 'The current desktop state has not been supplied.')
    user = 'Request: '+task+'\n\nState:\n'+live+'\n\nNext action:'
    ids = tokenizer('SYSTEM: '+HOBBY_SYSTEM+'\nUSER: '+user+'\nASSISTANT:').input_ids
    if len(ids)+48 > 1024: raise ValueError('HobbyLM action requests must fit its 1,024-token action budget; shorten the task or state')
    generated, past = [], None
    current = torch.tensor([ids], dtype=torch.long)
    with torch.inference_mode():
        for _ in range(48):
            output = model(input_ids=current, past_key_values=past, use_cache=True)
            token = int(output.logits[0, -1].argmax())
            if token == tokenizer.eos_token_id: break
            generated.append(token)
            if '\n' in tokenizer.decode(generated): break
            past, current = output.past_key_values, torch.tensor([[token]], dtype=torch.long)
    text = tokenizer.decode(generated).strip().splitlines()[0] if generated else ''
    if not re.match(r'^(?:OPEN |TYPE |KEY |DARK_MODE |FOLDER |TIMER |EVENT |SCREENSHOT\b|DONE\b|UNKNOWN\b)', text):
        raise ValueError('HobbyLM returned no valid action: '+text[:200])
    if verify and text != 'OPEN Calculator': raise ValueError('HobbyLM verification expected OPEN Calculator, received '+text)
    return {'text': text, 'action': {'protocol': 'hobbylm', 'raw': text}, 'adapterModules': count,
            'inferenceVerified': True, 'outputTokens': len(generated)}


def ternary_action(text):
    match = re.search(r'(?im)^\s*(?:Action:\s*)?((?:click|double_click|right_click|move|drag|write|press|hotkey|scroll|wait|done)\s*\([^\r\n]*\))\s*$', text)
    if not match: raise ValueError('TernaryPilot returned no complete next action; retry with a shorter task')
    return match.group(1)


def ternary(request):
    folder, base = Path(request['folder']), Path(request['base'])
    raw = fixture_png() if request['mode'] == 'verify' else base64.b64decode(request['image'], validate=True)
    mime = 'image/png' if raw.startswith(b'\x89PNG') else 'image/jpeg' if raw.startswith(b'\xff\xd8\xff') else None
    if mime is None: raise ValueError('TernaryPilot accepts PNG or JPEG screenshots')
    with socket.socket() as sock: sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    key = secrets.token_urlsafe(32)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def call(path, payload=None, timeout=2):
        headers = {'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'}
        data = None if payload is None else json.dumps(payload).encode()
        with opener.open(urllib.request.Request('http://127.0.0.1:'+str(port)+path, data=data, headers=headers), timeout=timeout) as response:
            return json.load(response)
    with tempfile.TemporaryDirectory(prefix='opencore-ternary-') as directory:
        keyfile = Path(directory)/'key'; keyfile.write_text(key, encoding='utf-8')
        command = [request['engine'], '--model', str(base/'Ternary-Bonsai-2-9B-PQ2_0-MTP-Q8_0.gguf'),
                   '--mmproj', str(base/'Ternary-Bonsai-2-9B-mmproj-Q8_0.gguf'), '--lora',
                   str(folder/'TernaryPilot-v2-Ternary-Bonsai-2-9B-LoRA-F16.gguf'), '--host', '127.0.0.1', '--port', str(port),
                   '--api-key-file', str(keyfile), '--ctx-size', '16384', '--threads', '2', '--threads-batch', '2',
                   '--gpu-layers', '0', '--image-max-tokens', '1024', '--jinja', '--parallel', '1', '--no-warmup']
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = time.monotonic()+180
            while time.monotonic() < deadline:
                if process.poll() is not None: raise RuntimeError('The PrismML engine could not load the base and LoRA')
                try:
                    if call('/health').get('status') == 'ok': break
                except OSError: pass
                time.sleep(.2)
            else: raise RuntimeError('The PrismML runtime did not become ready')
            task = 'Click the blue button.' if request['mode'] == 'verify' else request['task']
            prompt = ('You are operating a Windows computer.\nTask: '+task+'\nPrevious actions:\n'+request.get('state', '')+
                      '\nLook at the current screen and give the next action. Coordinates are integers normalized to 0-1000.\n'
                      'Available actions: click(x, y), double_click(x, y), right_click(x, y), move(x, y), drag(x, y), write(text), press(key), hotkey(keys), scroll(amount), wait(), done()\n'
                      'Reply as:\nThought: <short reasoning>\nAction: <one action>')
            result = call('/v1/chat/completions', {'messages': [{'role': 'user', 'content': [
                    {'type': 'image_url', 'image_url': {'url': 'data:'+mime+';base64,'+base64.b64encode(raw).decode()}},
                    {'type': 'text', 'text': prompt}]}], 'max_tokens': 192, 'temperature': 0,
                    'chat_template_kwargs': {'enable_thinking': False}}, timeout=180)
            text = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
            action = ternary_action(text)
            return {'text': text, 'action': {'protocol': 'ternarypilot', 'raw': action}, 'adapterApplied': True,
                    'inferenceVerified': True, 'outputTokens': result.get('usage', {}).get('completion_tokens')}
        finally:
            if process.poll() is None: process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


def gui_jepa(request):
    import random
    import numpy as np
    import torch
    from PIL import Image
    from gui_jepa_vendor.models import make_vit, extract_patch_tokens, LatentPredictor, StructuralHead, CrossEncoderMatcher
    from gui_jepa_vendor.actions import A_DIM, build_candidate_actions, kind_id
    from gui_jepa_vendor.roi import batch_roi, roi_pool
    from gui_jepa_vendor.clip_text import CLIPTextEncoder
    from gui_jepa_vendor.rankers import ClickReranker, rerank_click
    from gui_jepa_vendor.text_utils import span_candidates, predict_app
    random.seed(1); torch.manual_seed(1); torch.set_num_threads(2)
    folder, device = Path(request['folder']), torch.device('cpu')
    def state(path): return torch.load(path, map_location='cpu', weights_only=True)
    encoder = make_vit(pretrained=False)
    weights = state(folder/'encoder/encoder_1.pt'); weights = weights.get('state_dict', weights)
    missing, unexpected = encoder.load_state_dict(weights, strict=False)
    if any(not key.startswith('head.') for key in [*missing, *unexpected]): raise ValueError('GUI-JEPA encoder tensors differ')
    encoder.eval()
    predictor = LatentPredictor(embed_dim=1024, action_dim=A_DIM+1024, num_patches_side=14, depth=3, heads=16).eval()
    structural = StructuralHead(zdim=1024, cond_extra=A_DIM+1024).eval()
    checkpoint = state(folder/'policy/heads.pt')
    predictor.load_state_dict(checkpoint['predictor']); structural.load_state_dict(checkpoint['wm_head'])
    matcher = CrossEncoderMatcher(device, str(folder/'policy/matcher_lm')).eval()
    matcher.head.load_state_dict(checkpoint['matcher_head'])
    span = CrossEncoderMatcher(device, str(folder/'span_ranker/ranker_lm')).eval()
    weights = state(folder/'span_ranker/ranker_head.pt'); span.head.load_state_dict(weights.get('head', weights))
    reranker = ClickReranker(device, str(folder/'click_reranker/reranker_lm'), dropout=0.0).eval()
    weights = state(folder/'click_reranker/reranker_head.pt'); reranker.head.load_state_dict(weights.get('head', weights))
    clip = CLIPTextEncoder(device, str(request['base']))
    verify = request['mode'] == 'verify'
    image = fixture_png() if verify else base64.b64decode(request['image'], validate=True)
    candidates = ([{'text': 'Save', 'bounds': [.2, .3, .8, .7], 'clickable': True}] if verify else request['candidates'])
    task = 'Tap Save' if verify else request['task']
    with Image.open(io.BytesIO(image)) as source:
        if source.width*source.height > 40_000_000: raise ValueError('Screenshot exceeds the pixel limit')
        pixels = np.asarray(source.convert('RGB').resize((224, 224)), dtype=np.float32)/255
    tensor = torch.from_numpy((pixels-np.array([.485, .456, .406], dtype=np.float32))/np.array([.229, .224, .225], dtype=np.float32)).permute(2, 0, 1).unsqueeze(0)
    step = {'cand_bounds': torch.tensor([row['bounds'] for row in candidates], dtype=torch.float32),
            'cand_flags': torch.tensor([[row.get('clickable', True), row.get('longClickable', False), row.get('editable', False), row.get('scrollable', False)] for row in candidates], dtype=torch.float32)}
    texts = [row['text'] for row in candidates]
    with torch.inference_mode():
        z = extract_patch_tokens(encoder, tensor)[0]
        actions, metas = build_candidate_actions(step)
        rois = batch_roi(z, metas, step['cand_bounds'])
        for index, meta in enumerate(metas):
            if meta.get('cand') is None: rois[index].zero_()
        full = torch.cat([actions, rois], -1)
        condition = torch.cat([clip.encode([task])[0], torch.zeros(A_DIM+1024)])
        scores = []
        for start in range(0, len(metas), 8):
            current = metas[start:start+8]
            prediction = z.unsqueeze(0)+predictor(z.unsqueeze(0).expand(len(current), -1, -1), full[start:start+8])
            roi = torch.stack([roi_pool(prediction[index], step['cand_bounds'][meta['cand']].tolist()) if meta.get('cand') is not None else prediction[index].mean(0) for index, meta in enumerate(current)])
            kinds = torch.tensor([kind_id(meta) for meta in current])
            scores.append(structural(roi, prediction.mean(1), condition.unsqueeze(0).expand(len(current), -1), kinds))
        scores = torch.cat(scores)
        selected = [index for index, meta in enumerate(metas) if meta.get('cand') is not None]
        if selected: scores[torch.tensor(selected)] += matcher(task, [texts[metas[index]['cand']] for index in selected])
        if not torch.isfinite(scores).all(): raise ValueError('GUI-JEPA produced invalid scores')
        index = rerank_click(reranker, task, scores, metas, int(scores.argmax()), step, texts)
        action = dict(metas[index])
        if action['kind'] == 'input_text':
            spans = span_candidates(task, max_words=16, cap=800)
            values = torch.cat([span(task, spans[start:start+32]) for start in range(0, len(spans), 32)])
            action['text'] = spans[int(values.argmax())]
        if action['kind'] == 'open_app':
            vocab = json.loads((folder/'metadata/app_vocab.json').read_text(encoding='utf-8'))
            action['app'] = predict_app(task, task, list(vocab))
    return {'text': json.dumps(action, ensure_ascii=False), 'action': action, 'candidateCount': len(candidates),
            'inferenceVerified': True, 'scope': 'One-step Android GUI policy prediction; execution and live task completion are separate.'}


def main():
    try:
        request = json.loads(sys.stdin.buffer.read(10*1024*1024))
        start = time.monotonic()
        with redirect_stdout(sys.stderr):
            result = {'hobbylm-v1': hobby, 'ternarypilot-v2': ternary, 'gui-jepa-v1': gui_jepa}[request['runtime']](request)
        result['elapsedSeconds'] = time.monotonic()-start
        print(json.dumps(result, ensure_ascii=True))
    except Exception as error:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({'error': str(error)}, ensure_ascii=True)); return 1
    return 0


if __name__ == '__main__': sys.exit(main())
