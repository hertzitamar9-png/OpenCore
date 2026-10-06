"""Pin optional models from inspected upstream metadata; download no weights."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
records = {row['repo']: row for row in json.loads((ROOT/'scripts/research/platform-models.json').read_text(encoding='utf-8'))}
path = ROOT/'src-tauri/resources/model-catalog.json'
catalog = json.loads(path.read_text(encoding='utf-8'))

def add(id, label, repo, category, precision, note, *, names=None, prefix=None, backend='external', selectable=False, setup=None, context=0):
    upstream = records[repo]
    if upstream.get('error'): raise RuntimeError(f'{repo}: {upstream["error"]}')
    files = [file for file in upstream['files'] if (file['rfilename'] in names if names is not None else
        (not prefix or file['rfilename'].startswith(prefix)) and file['rfilename'].endswith(('.safetensors','.json','.yaml','.yml','.txt','.jinja')) and
        not file['rfilename'].startswith(('assets/','speculator/','vllm/')))]
    artifacts = []
    if upstream.get('gated'):
        files=[]
        note += ' Hugging Face access approval is required before downloading this gated repository.'
    for file in files:
        filename = file['rfilename']; artifact_id = id+'-'+hashlib.sha256(filename.encode()).hexdigest()[:12]
        sha = (file.get('lfs') or {}).get('sha256'); size = file['size']
        if not sha:
            with urllib.request.urlopen(f'https://huggingface.co/{repo}/resolve/{upstream["revision"]}/{filename}', timeout=45) as response:
                data = response.read(20_000_000)
            if len(data) != size: raise RuntimeError(f'Unexpected metadata size: {repo}/{filename}')
            sha = hashlib.sha256(data).hexdigest()
        artifact = {'id':artifact_id,'path':f'models/library/{id}/{filename}','repo':repo,'revision':upstream['revision'],
                    'filename':filename,'sha256':sha,'bytes':size}
        catalog['artifacts'] = [old for old in catalog['artifacts'] if old['id'] != artifact_id] + [artifact]
        artifacts.append(artifact_id)
    if not artifacts and not upstream.get('gated'): raise RuntimeError(f'No pinned files for {id}')
    model = {'id':id,'label':label,'description':f'{label} · {category.replace("-"," ")}','precision':precision,'contextTokens':context,
             'artifacts':artifacts,'license':(upstream.get('card') or {}).get('license_name') or (upstream.get('card') or {}).get('license') or 'See upstream license',
             'experimental':True,'note':note,'selectable':selectable,'category':category,'backend':backend,
             'sourceUrl':f'https://huggingface.co/{repo}/tree/{upstream["revision"]}', 'setupUrl':setup or f'https://huggingface.co/{repo}',
             'runtimeReady':selectable}
    model['installable']=bool(artifacts)
    if backend=='gguf':
        model['runtimeModelPath'] = next(file['path'] for file in catalog['artifacts'] if file['id'] in artifacts and file['filename'].endswith('.gguf') and not file['filename'].startswith('mmproj'))
        projectors=[file['path'] for file in catalog['artifacts'] if file['id'] in artifacts and file['filename'].startswith('mmproj')]
        if projectors: model['visionProjectorPath']=projectors[0]
    catalog['models']=[old for old in catalog['models'] if old['id']!=id]+[model]

add('swift-27b','Swift 1.5 27B','ukisai/Swift-1.5-Qwen3.8-27B-GSQ-RCO-GGUF','text','IQ2_S',
    'Optional IQ2_S download (9.26 GB). Quantized 27B checkpoint; quality and speed depend on the selected hardware and are unverified. ECHO recall enabled; no vision projector.',
    names=['Swift-1.5-Qwen3.8-27B-GSQ-RCO-IQ2_S.gguf'],backend='gguf',selectable=True,context=16384)
add('dirk-27b','Dirk 27B Vision','peculiar-ragdoll/Dirk-Qwen3.8-27B-GGUF','text','IQ2_S + F16 vision',
    '9.61 GB weights plus 0.93 GB vision projector. Tight on 12 GB; CPU KV and bounded 8K attention. Includes MTP weights, but speculative decoding is not enabled. ECHO recall enabled.',
    names=['Dirk-Qwen3.8-27B-GSQ-RCO-IQ2_S.gguf','mmproj-F16.gguf'],backend='gguf',selectable=True,context=8192)
repo='DavidAU/Qwen3.8-27B-TURBO-Fable-Cold-Fusion-735-882-Heretic-Uncensored-NEO-CODER-MAX-MTP-GGUF'
names=['Qwen3.8-27B-TurboFCFusion-735-882-Here-Uncen-NEO-CODER-MAX-IQ2_M.gguf']
names += [file['rfilename'] for file in records[repo]['files'] if file['rfilename'].startswith('mmproj') and file['rfilename'].endswith('.gguf')][:1]
add('davidau-27b','DavidAU Turbo Cold Fusion 27B',repo,'text','IQ2_M',
    'At least 11.67 GB weights; CPU layer offload is required on 12 GB. This is an optional heavy model, not a speed upgrade. ECHO recall enabled.',
    names=names,backend='gguf',selectable=True,context=8192)
add('mirai-s-27b','Mirai S experimental 27B','trymirai/Qwen3.8-27B-S-experimental','text','GSQ custom format',
    'Requires the publisher’s experimental vLLM plugin. Downloaded checkpoints alone do not enable inference. Text only; setup and hardware testing required.')
add('thinkingcap-27b','ThinkingCap 27B','bottlecapai/ThinkingCap-Qwen3.8-27B','text','BF16',
    'About 55 GB of weights. CPU offload or a larger GPU is required; no precision conversion is applied. Requires a compatible Transformers or vLLM backend.')
add('qwen-image-21','Qwen Image 2.1','Qwen/Qwen-Image-2.1','image','BF16',
    'Image generation/editing and transparent RGBA assets. Full pipeline includes text encoder and VAE. Requires Diffusers and CPU offload on 12 GB; upstream license restricts commercial use.',setup='https://github.com/QwenLM/Qwen-Image')
add('qwen-image-21-gguf','Qwen Image 2.1 GGUF','unsloth/Qwen-Image-2.1-GGUF','image','Q6_K',
    '6.27 GB visual component only. Also needs Qwen Image’s text encoder and VAE plus a GGUF-compatible image backend. Do not use as a chat GGUF.',names=['qwen-image-2.1-Q6_K.gguf'],setup='https://huggingface.co/unsloth/Qwen-Image-2.1-GGUF')
add('animation-diffusion-2d','Animation Diffusion · 2D','ModelsLab/3D-Animation-Diffusion','image','Original SD weights',
    'Despite the repository name, this is a 2D Stable Diffusion image pipeline. It does not export animated 3D meshes. Requires Diffusers.')
add('holo4-27b','Holo4 27B','Hcompany/Holo4-27B','computer-use','BF16',
    'Desktop/browser vision-action model. BF16 weights require substantial CPU offload or a larger GPU. Runtime setup required; noncommercial weights.',setup='https://github.com/Hcompany/holo')
add('holo15-3b','Holo1.5 3B','Hcompany/Holo1.5-3B','computer-use','BF16',
    'Smaller vision-action model; original BF16 weights. Requires the Holo action parser and compatible inference runtime. Not automatically substituted for the chat model.',setup='https://github.com/Hcompany/holo')
add('spar3d','SPAR3D','stabilityai/stable-point-aware-3d','3d','Original weights',
    'Image to textured, UV-unwrapped mesh. Upstream low-memory mode reports about 7 GB VRAM. Requires its native inference dependencies.',names=['config.yaml','model.safetensors','LICENSE.md'],setup='https://github.com/Stability-AI/stable-point-aware-3d')
add('pixal3d','Pixal3D','TencentARC/Pixal3D','3d','Original BF16/FP16',
    'Image to geometry and PBR textures. Full checkpoint set is large; low-memory mode is tight on 12 GB. Requires upstream CUDA extensions.',setup='https://github.com/TencentARC/Pixal3D')
add('triposr','TripoSR','stabilityai/TripoSR','3d','Original weights',
    'Fast image to 3D mesh. Requires TripoSR runtime and image preprocessing; weights are downloaded separately.',names=['config.yaml','model.ckpt','README.md'],setup='https://github.com/VAST-AI-Research/TripoSR')
add('hy-motion-1','HY-Motion 1.0','tencent/HY-Motion-1.0','3d-animation','Original 1.0B checkpoint',
    'Text to humanoid skeleton motion. Upstream minimum is 26 GB VRAM. Text encoders and SMPL assets are separate dependencies; not a universal mesh animator.',names=['HY-Motion-1.0/config.yml','HY-Motion-1.0/latest.ckpt','LICENSE.txt'],setup='https://github.com/Tencent-Hunyuan/HY-Motion-1.0')
add('hy-motion-1-lite','HY-Motion 1.0 Lite','tencent/HY-Motion-1.0','3d-animation','Original 0.46B checkpoint',
    'Smaller motion checkpoint from the same official repository. Published minimum is 24 GB VRAM; unverified CPU-offload optimizations are not enabled.',names=['HY-Motion-1.0-Lite/config.yml','HY-Motion-1.0-Lite/latest.ckpt','LICENSE.txt'],setup='https://github.com/Tencent-Hunyuan/HY-Motion-1.0')
for id,label,category,url,note in [
    ('unimate','UniMate','3d-animation','https://github.com/JH9384/unimate','Rigged skeleton animation. Upstream setup and checkpoint selection required; no verified 12 GB runtime yet.'),
    ('animate-any-mesh','AnimateAnyMesh','3d-animation','https://github.com/JarrentWu1031/AnimateAnyMesh','Text-driven mesh deformation. Upstream runtime and weights required; does not automatically add skeleton rigging.'),
    ('tooncrafter','ToonCrafter','2d-animation','https://github.com/Doubiiu/ToonCrafter','Cartoon frame interpolation. Published reference uses about 24 GB VRAM. Requires a separately configured video runtime.'),
]:
    catalog['models']=[model for model in catalog['models'] if model['id']!=id]+[{'id':id,'label':label,'category':category,'description':note,'precision':'Upstream checkpoint','contextTokens':0,'artifacts':[],
        'license':'See upstream license','experimental':True,'note':note,'selectable':False,'backend':'external','runtimeReady':False,'installable':False,'sourceUrl':url,'setupUrl':url}]
for model in catalog['models']:
    model.setdefault('category','speech' if model.get('speechLanguage') else 'text' if model['selectable'] else 'computer-use')
    model.setdefault('runtimeReady',True)
path.write_text(json.dumps(catalog,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
print(json.dumps({'models':len(catalog['models']),'artifacts':len(catalog['artifacts']),
                  'categories':{category:sum(model['category']==category for model in catalog['models']) for category in {model['category'] for model in catalog['models']}}}))
