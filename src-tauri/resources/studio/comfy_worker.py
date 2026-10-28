"""Owned, offline ComfyUI jobs for pinned MOSS ConvRot packages. No persistent server."""
import hashlib
import json
import math
import os
import shutil
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

from asset_worker import bounded_integer,progress,validate_output_format,save_image_output,prepare_reference_images

def canonical_path(value):
    path=Path(value).resolve()
    text=str(path)
    if os.name=='nt' and text.startswith('\\\\?\\'):
        text='\\\\'+text[8:] if text.startswith('\\\\?\\UNC\\') else text[4:]
        path=Path(text)
    return path


def owned_listener(psutil,process,port):
    owner=psutil.Process(process.pid)
    return any(connection.status==psutil.CONN_LISTEN and connection.laddr.port==port
               for child in [owner,*owner.children(recursive=True)]
               for connection in child.net_connections(kind='tcp'))

VARIANTS={
    'moss-image-sft-int8-convrot':'moss-image-sft-dit-int8-convrot.safetensors',
    'moss-image-sft-int4-convrot':'moss_image_sft_int4_convrot.safetensors',
}
ENCODER='moss-image-sft-qwen35-int8-convrot.safetensors'
VAE='diffusion_pytorch_model.safetensors'
VAE_DIR='models/library/moss-image-sft/vae'
QWEN_VARIANTS={
    'qwen-image-21-uc':'qwen-image-2.1-UC-Q4_K_M.gguf',
    **{'qwen-image-21-uc-'+precision.lower().replace('_','-'):f'qwen-image-2.1-UC-{precision}.gguf' for precision in ['BF16','Q8_0','Q6_K','Q5_K_M','Q4_0']},
    'qwen-image-21-uc-fp8':'qwen-image-2.1-UC-fp8.safetensors',
    'qwen-image-21-uc-int8-convrot':'qwen-image-2.1-UC-int8_convrot.safetensors',
    'qwen-image-21-uc-nvfp4':'qwen-image-2.1-UC-NVFP4.safetensors',
}
QWEN_ENCODER='qwen3vl_8b_int8_convrot.safetensors'
QWEN_VAE='qwen_image_2.1_vae_bf16.safetensors'

def build_qwen_workflow(request,dit,lora_name=None,reference_names=()):
    settings=request.get('settings') or {}
    width=bounded_integer(settings,'width',1024,128,2048)
    height=bounded_integer(settings,'height',1024,128,2048)
    if width%16 or height%16:raise ValueError('Qwen image dimensions must be multiples of 16')
    if bounded_integer(settings,'numImages',1,1,8)!=1:raise ValueError('This Qwen worker generates one image per job')
    cfg=settings.get('guidanceScale',1.0)
    if isinstance(cfg,bool) or not isinstance(cfg,(int,float)) or not 0<=cfg<=30:raise ValueError('guidanceScale must be from 0 to 30')
    def node(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    if lora_name:
        strength=settings.get('loraStrength',0.8)
        if isinstance(strength,bool) or not isinstance(strength,(int,float)) or not math.isfinite(strength) or not 0<=strength<=2:
            raise ValueError('Qwen LoRA strength must be between 0 and 2')
    graph={
        '1':node('UnetLoaderGGUF',unet_name=dit) if dit.endswith('.gguf') else node('UNETLoader',unet_name=dit,weight_dtype='default'),
        '2':node('CLIPLoader',clip_name=QWEN_ENCODER,type='qwen_image',device='default'),
        '3':node('VAELoader',vae_name=QWEN_VAE),
        '4':node('TextEncodeQwenImage21',clip=['2',0],prompt=request['prompt'],negative_prompt=settings.get('negativePrompt',''),resolution=max(width,height)),
        '6':node('EmptyLatentImage',width=width,height=height,batch_size=1),
        '16':node('KSampler',seed=bounded_integer(settings,'seed',831001,0,2**32-1),steps=bounded_integer(settings,'steps',40,1,100),cfg=cfg,
            sampler_name='euler',scheduler='simple',denoise=1.0,model=['19',0] if lora_name else ['1',0],positive=['4',0],negative=['4',1],latent_image=['6',0]),
        '17':node('VAEDecode',samples=['16',0],vae=['3',0]),
        '18':node('SaveImage',images=['17',0],filename_prefix='OpenCore-Qwen'),
    }
    if lora_name:graph['19']=node('LoraLoaderModelOnly',model=['1',0],lora_name=lora_name,strength_model=strength)
    if reference_names:
        graph['4']['inputs']['vae']=['3',0]
        for index,name in enumerate(reference_names):
            key=str(100+index);graph[key]=node('LoadImage',image=name)
            graph['4']['inputs'][f'images.image_{index+1}']=[key,0]
        graph['16']['inputs']['latent_image']=['4',2]
    return graph


def build_moss_workflow(request,dit,lora_name=None,reference_names=()):
    settings=request.get('settings') or {}
    width=bounded_integer(settings,'width',1024,128,2048)
    height=bounded_integer(settings,'height',1024,128,2048)
    if width%16 or height%16:raise ValueError('MOSS image dimensions must be multiples of 16')
    if bounded_integer(settings,'numImages',1,1,8)!=1:raise ValueError('This MOSS worker generates one image per job')
    cfg=settings.get('guidanceScale',3.0)
    if isinstance(cfg,bool) or not isinstance(cfg,(int,float)) or not 0<=cfg<=30:raise ValueError('guidanceScale must be from 0 to 30')
    def node(kind,**inputs):return {'class_type':kind,'inputs':inputs}
    if lora_name:
        strength=settings.get('loraStrength',0.8)
        if isinstance(strength,bool) or not isinstance(strength,(int,float)) or not math.isfinite(strength) or not 0<=strength<=2:
            raise ValueError('MOSS LoRA strength must be between 0 and 2')
    graph={
        '1':node('MOSSImageUNETLoader',unet_name=dit,weight_dtype='default'),
        '2':node('MOSSImageCLIPLoader',clip_name=ENCODER,device='default'),
        '3':node('VAELoader',vae_name=VAE),
        '4':node('CLIPTextEncode',text=request['prompt'],clip=['2',0]),
        '5':node('CLIPTextEncode',text=settings.get('negativePrompt',''),clip=['2',0]),
        '6':node('EmptyFlux2LatentImage',width=width,height=height,batch_size=1),
        '15':node('ModelSamplingSD3',shift=5.0,model=['19',0] if lora_name else ['1',0]),
        '13':node('SkipLayerGuidanceDiTSimple',double_layers='',single_layers='22',start_percent=0.0,end_percent=1.0,model=['15',0]),
        '16':node('KSampler',seed=bounded_integer(settings,'seed',831001,0,2**32-1),steps=bounded_integer(settings,'steps',50,1,100),cfg=cfg,sampler_name='euler',scheduler='sgm_uniform',denoise=1.0,model=['13',0],positive=['4',0],negative=['5',0],latent_image=['6',0]),
        '17':node('VAEDecode',samples=['16',0],vae=['3',0]),
        '18':node('SaveImage',filename_prefix='opencore-moss',images=['17',0]),
    }
    if lora_name:graph['19']=node('LoraLoaderModelOnly',model=['1',0],lora_name=lora_name,strength_model=strength)
    if reference_names:
        if len(reference_names)!=1:raise ValueError('MOSS supports one reference image; use Qwen Image 2.1 for multiple references')
        graph['100']=node('LoadImage',image=reference_names[0])
        graph['102']=node('ImageScale',image=['100',0],upscale_method='lanczos',width=width,height=height,crop='disabled')
        graph['101']=node('VAEEncode',pixels=['102',0],vae=['3',0])
        graph['16']['inputs'].update(latent_image=['101',0],denoise=0.65)
    return graph


def managed_moss_lora(root,settings):
    """Accept only an intact, installed native ComfyUI MOSS LoRA from the OpenCore Hub."""
    raw=settings.get('loraPath')
    if raw in ('',None):return None
    if not isinstance(raw,str):raise ValueError('MOSS LoRA path must be a string')
    hub=(root/'models'/'hub').resolve()
    path=Path(raw).resolve(strict=True)
    if not path.is_file() or not path.is_relative_to(hub) or path.parent.parent!=hub:
        raise ValueError('Choose a downloaded LoRA from OpenCore/models/hub')
    if not path.name.endswith('-ComfyUI.safetensors'):
        raise ValueError('This MOSS runtime requires a ComfyUI-format LoRA. Download the ComfyUI variant, not the Diffusers file.')
    if path.stat().st_size>256_000_000:
        raise ValueError('MOSS LoRA exceeds the 256 MB adapter limit')
    receipt_file=path.parent/'install-receipt.json'
    if not receipt_file.is_file():raise ValueError('LoRA download receipt is missing')
    receipt=json.loads(receipt_file.read_text(encoding='utf-8'))
    matches=[entry for entry in receipt.get('files',[]) if entry.get('filename')==path.name and entry.get('role')=='model']
    if len(matches)!=1 or receipt.get('repository')!='axiomofmind/Moss-image-nsfw-uncensored-lora':
        raise ValueError('The selected MOSS LoRA is not a verified compatible Hub adapter')
    with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=matches[0].get('actualSha256') or actual!=matches[0].get('sha256'):
        raise ValueError('MOSS LoRA file hash no longer matches its installation receipt')
    return path


def managed_qwen_lora(root,settings):
    """Only the pinned Qwen 2.1 LoRA format with an intact OpenCore Hub download receipt."""
    raw=settings.get('loraPath')
    if raw in ('',None):return None
    if not isinstance(raw,str):raise ValueError('Qwen LoRA path must be a string')
    hub=(root/'models'/'hub').resolve()
    path=Path(raw).resolve(strict=True)
    if not path.is_file() or path.parent.parent!=hub or not path.is_relative_to(hub):
        raise ValueError('Choose an installed OpenCore Hub LoRA')
    if path.name!='NSFW Qwen Lora.safetensors':
        raise ValueError('This Qwen 2.1 worker supports the pinned f23gg LoRA format only')
    if path.stat().st_size>256_000_000:raise ValueError('Qwen LoRA exceeds the 256 MB adapter limit')
    receipt=json.loads((path.parent/'install-receipt.json').read_text(encoding='utf-8'))
    if receipt.get('repository')!='f23gg/NSFW-LORA-Qwen-Image-2.1':
        raise ValueError('The LoRA repository is not approved for this Qwen workflow')
    files=[f for f in receipt.get('files',[]) if f.get('role')=='model' and f.get('filename')==path.name]
    if len(files)!=1 or path.stat().st_size!=files[0].get('bytes'):
        raise ValueError('Qwen LoRA does not match its installed receipt')
    with path.open('rb') as stream: actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=files[0].get('sha256') or actual!=files[0].get('actualSha256'):
        raise ValueError('Qwen LoRA checksum differs from the verified download')
    return path


def managed_image_lora(root,settings,base_repository):
    """Resolve a downloaded adapter whose publisher declares this exact base."""
    raw=settings.get('loraPath')
    if raw in ('',None):return None
    if not isinstance(raw,str):raise ValueError('Image LoRA path must be a string')
    root=canonical_path(root);hub=root/'models/hub';path=canonical_path(raw)
    if not path.is_file() or not path.is_relative_to(hub):
        raise ValueError('Choose an installed OpenCore Hub LoRA')
    relative=path.relative_to(hub)
    if len(relative.parts)<2 or path.suffix.lower()!='.safetensors' or path.stat().st_size>2_000_000_000:
        raise ValueError('Choose a supported safetensors image LoRA under the managed Hub folder')
    receipt=json.loads((hub/relative.parts[0]/'install-receipt.json').read_text(encoding='utf-8'))
    # Earlier installed versions saved these two pinned adapters before the
    # general Hub catalog included base-model metadata. Keep those receipts usable.
    if receipt.get('repository')=='f23gg/NSFW-LORA-Qwen-Image-2.1' and base_repository.lower()=='qwen/qwen-image-2.1':
        return managed_qwen_lora(root,{'loraPath':str(path)})
    if receipt.get('repository')=='axiomofmind/Moss-image-nsfw-uncensored-lora' and base_repository.lower()=='openmoss-team/moss-image-sft' and path.name.endswith('-ComfyUI.safetensors'):
        return managed_moss_lora(root,{'loraPath':str(path)})
    catalog_path=root/'models/hub-imports.json'
    if not catalog_path.is_file():raise ValueError('The LoRA base metadata is missing; recheck its repository in Models')
    catalog=json.loads(catalog_path.read_text(encoding='utf-8'))
    entry=next((e for e in catalog.get('entries',[]) if e.get('id')==receipt.get('id') and e.get('repository')==receipt.get('repository') and e.get('revision')==receipt.get('revision')),None)
    bases=([entry.get('baseRepository')] + entry.get('baseRepositories',[])) if entry else []
    if not entry or base_repository.lower() not in [base.lower() for base in bases if isinstance(base,str)]:
        raise ValueError('This LoRA does not declare the selected image model as its base')
    if entry.get('packageKind')!='adapter' and entry.get('format')!='lora':
        raise ValueError('The selected package is not an image adapter')
    filename='/'.join(relative.parts[1:])
    matches=[f for f in receipt.get('files',[]) if f.get('filename')==filename and f.get('role')=='model']
    pins=[f for f in entry.get('files',[]) if f.get('filename')==filename and f.get('role')=='model']
    if len(matches)!=1 or len(pins)!=1 or path.stat().st_size!=pins[0].get('bytes') or matches[0].get('bytes')!=pins[0].get('bytes'):
        raise ValueError('The image LoRA does not match its installed download receipt')
    with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=matches[0].get('actualSha256') or actual!=(pins[0].get('sha256') or matches[0].get('actualSha256')):
        raise ValueError('The image LoRA file hash changed after installation')
    return path


def validate_history_output(root,image):
    filename=image.get('filename','')
    subfolder=image.get('subfolder','')
    if not isinstance(filename,str) or Path(filename).name!=filename or '/' in filename or '\\' in filename:
        raise ValueError('Invalid ComfyUI output filename')
    if not isinstance(subfolder,str) or Path(subfolder).is_absolute() or '..' in Path(subfolder).parts:
        raise ValueError('Invalid ComfyUI output subfolder')
    target=(root/subfolder/filename).resolve()
    if not target.is_relative_to(root.resolve()):raise ValueError('ComfyUI output escaped the managed job')
    return target


def generate_comfy(request,output):
    import psutil
    from PIL import Image
    root=canonical_path(request['modelRoot'])
    target=request['modelId']
    qwen=target in QWEN_VARIANTS
    if target not in VARIANTS and not qwen:raise RuntimeError('No managed ComfyUI adapter for this model')
    label='Qwen Image 2.1' if qwen else 'MOSS'
    variant=QWEN_VARIANTS[target] if qwen else VARIANTS[target]
    encoder=QWEN_ENCODER if qwen else ENCODER
    vae_file=QWEN_VAE if qwen else VAE
    plugin='qwen-image-gguf' if qwen else 'moss-image'
    required_nodes={'CLIPLoader','TextEncodeQwenImage21','UnetLoaderGGUF' if variant.endswith('.gguf') else 'UNETLoader'} if qwen else {'MOSSImageUNETLoader','MOSSImageCLIPLoader'}
    engine=canonical_path(request.get('sourceDir') or '')
    if not Path(request.get('sourceDir') or '').is_absolute() or not engine.is_relative_to(root/'runtime-setup'/'sources') or not (engine/'main.py').is_file():
        raise RuntimeError('The pinned managed ComfyUI source is not prepared')
    settings=request.get('settings') or {}
    if qwen:
        lora=managed_image_lora(root,settings,'Qwen/Qwen-Image-2.1')
    else:
        lora=managed_image_lora(root,settings,'OpenMOSS-Team/MOSS-Image-SFT')
        if lora and not (lora.name.endswith('-ComfyUI.safetensors') or 'comfy' in lora.name.lower()):
            raise ValueError('Select a ComfyUI-format MOSS LoRA for this quantized runtime')
    if lora:required_nodes.add('LoraLoaderModelOnly')
    lora_name=('opencore-qwen-lora.safetensors' if qwen else 'opencore-moss-lora.safetensors') if lora else None
    reference_names=prepare_reference_images(settings,output/'comfy-inputs')
    if reference_names:required_nodes.update({'LoadImage','VAEEncode'} if not qwen else {'LoadImage'})
    graph=build_qwen_workflow(request,variant,lora_name,reference_names) if qwen else build_moss_workflow(request,variant,lora_name,reference_names)
    output_format=validate_output_format(settings.get('outputFormat','png'),{'png','jpeg','webp'},'outputFormat')
    component=root/'models'/'components'/('qwen-image-21-abenzerps' if qwen else 'moss-int8-convrot')
    dit=root/'models'/'library'/target/'diffusion_models'
    vae=component/'vae' if qwen else root/VAE_DIR
    weight_paths=[dit/variant,component/'text_encoders'/encoder,vae/vae_file]
    for path in weight_paths:
        if not path.is_file():raise RuntimeError(f'A required pinned {label} component is missing: '+path.name)
    # Avoid starting a worker that would page its active weights onto the SSD.
    weight_bytes=sum(path.stat().st_size for path in weight_paths)+(lora.stat().st_size if lora else 0)
    required_ram=weight_bytes+4_000_000_000
    if psutil.virtual_memory().available<required_ram:
        raise RuntimeError(f'This {label} variant needs about {required_ram/1e9:.1f} GB available RAM including load buffers. Close other applications and retry; disk weight offload is disabled.')
    output.mkdir(parents=True,exist_ok=True)
    paths=output/'comfy-model-paths.json'
    model_folders={'base_path':str(root),'diffusion_models':str(dit),'text_encoders':str(component/'text_encoders'),'vae':str(vae)}
    if lora:
        lora_folder=output/'image-loras';lora_folder.mkdir(exist_ok=True)
        shutil.copyfile(lora,lora_folder/lora_name)
        model_folders['loras']=str(lora_folder)
    paths.write_text(json.dumps({'opencore':model_folders}),encoding='utf-8')
    images=output/'comfy-output';images.mkdir(exist_ok=True)
    with socket.socket() as port_socket:
        port_socket.bind(('127.0.0.1',0));port=port_socket.getsockname()[1]
    address=f'http://127.0.0.1:{port}'
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(path,payload=None):
        body=None if payload is None else json.dumps(payload).encode()
        with opener.open(urllib.request.Request(address+path,data=body,headers={'Content-Type':'application/json'}),timeout=10) as response:
            raw=response.read(16_000_001)
        if len(raw)>16_000_000:raise RuntimeError('ComfyUI response exceeded its size limit')
        return json.loads(raw)
    process=None;finished_image=None
    log_path=output/'comfy-runtime.log'
    for directory in ('comfy-user','comfy-temp'):
        (output/directory).mkdir(parents=True,exist_ok=True)
    try:
        with log_path.open('wb') as log:
            process=subprocess.Popen([sys.executable,str(engine/'main.py'),'--listen','127.0.0.1','--port',str(port),
                '--disable-auto-launch','--offline','--disable-mmap','--disable-metadata',
                '--disable-all-custom-nodes','--whitelist-custom-nodes',plugin,
                '--extra-model-paths-config',str(paths),'--output-directory',str(images),'--input-directory',str(output/'comfy-inputs'),
                '--user-directory',str(output/'comfy-user'),'--temp-directory',str(output/'comfy-temp')],
                cwd=engine,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'},
                creationflags=0x08000000 if sys.platform=='win32' else 0)
        progress(output,f'Starting the managed {label} runtime')
        startup=time.monotonic()+120
        while True:
            if process.poll() is not None:raise RuntimeError(f'Managed {label} runtime exited during startup')
            if time.monotonic()>startup:raise RuntimeError(f'Managed {label} runtime did not become ready within two minutes')
            try:
                listening=owned_listener(psutil,process,port)
                if listening:
                    nodes=api('/object_info')
                    if not required_nodes.issubset(nodes):raise RuntimeError(f'Pinned {label} loader nodes are missing from the managed runtime')
                    break
            except (OSError,urllib.error.URLError):pass
            time.sleep(0.25)
        queued=api('/prompt',{'prompt':graph,'client_id':str(uuid.uuid4())})
        prompt_id=queued.get('prompt_id')
        if not prompt_id:raise RuntimeError(f'{label} workflow was rejected: '+json.dumps(queued))
        progress(output,f'Generating image with the selected {label} quantization')
        deadline=time.monotonic()+1200
        while True:
            if process.poll() is not None:raise RuntimeError(f'Managed {label} runtime exited during generation')
            if time.monotonic()>deadline:raise RuntimeError(f'{label} generation exceeded twenty minutes')
            result=api('/history/'+prompt_id).get(prompt_id)
            if result:
                if result.get('status',{}).get('status_str')=='error':raise RuntimeError(f'{label} generation failed: '+json.dumps(result.get('status',{}))[-4000:])
                generated=result.get('outputs',{}).get('18',{}).get('images',[])
                if generated:
                    source=validate_history_output(images,generated[0])
                    with Image.open(source) as image:
                        image.load()
                        finished_image=image.convert('RGB')
                    break
            time.sleep(0.75)
    except Exception as error:
        tail=''
        if log_path.is_file():
            with log_path.open('rb') as log:
                log.seek(max(0,log_path.stat().st_size-4096));tail=log.read().decode('utf-8',errors='replace')
        raise RuntimeError(f'{error}\n{tail}') from error
    finally:
        if process is not None and process.poll() is None:
            try:
                descendants=psutil.Process(process.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                descendants=[]
            for child in reversed(descendants):
                try:child.terminate()
                except psutil.NoSuchProcess:pass
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
            _,alive=psutil.wait_procs(descendants,timeout=5)
            for child in alive:
                try:child.kill()
                except psutil.NoSuchProcess:pass
    if finished_image is not None:
        try:save_image_output(finished_image,output,1,settings)
        finally:finished_image.close()
    progress(output,'Generation complete')
