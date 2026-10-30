"""Pinned Iris GGUF image runtime. Repository Python code is never executed."""
import hashlib
import json
import re
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

REPOSITORY='webmp3/Sakura-Iris-3B-Uncensored-GGUF'
REVISION='eab8cb6f5021ba8948cfe9d18a8578c98f061879'
ENCODER='Sakura-TextEncoder-Qwen3VL-4B-Uncensored-8bit-3.99GiB.gguf'
ENCODER_SHA='ae45d32cb972b12f7e64a2c6c4077f59dcbef4b9108e4c58dbc3f0bc24268b62'
QUANTS={'Q4_K_M':('Sakura-Iris-3B-Q4_K_M-3.14GiB.gguf','a65ca4a773ca9433166d1a3b20ba8f4e66a3193e7198264f23ffd2528a7678a3'),
        'Q5_K_M':('Sakura-Iris-3B-Q5_K_M-3.40GiB.gguf','869200be41e9610315274d86d69f9d191d5ddf86c23ed21cc407df1047245c1f'),
        'Q6_K':('Sakura-Iris-3B-Q6_K-3.66GiB.gguf','51e957208928c3dc23c3dfce0a325665e855fa69959f8648cfe13a300e59f1ff'),
        'Q8_0':('Sakura-Iris-3B-Q8_0-4.15GiB.gguf','4e9821f132f6c076b833ade81e201754268756064dd073881414b62a70afc4a5')}
ENGINE_URL='https://github.com/leejet/stable-diffusion.cpp/releases/download/master-956-1b0ba10/sd-master-1b0ba10-bin-win-vulkan-x64.zip'
ENGINE_BYTES=30123908
ENGINE_SHA='9e8b9baff514fe5dfb77e51bf08d85f11e6f10128eac14d4474c87f563309725'

def digest(path):
    with Path(path).open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()

def supports(entry):
    return (entry.get('repository')==REPOSITORY and entry.get('revision')==REVISION
            and entry.get('category')=='image' and entry.get('imageRuntime')=='iris-sdcpp-v1')

def plan_variants(metadata):
    from hf_catalog import _pin,stable_id
    if metadata.get('id')!=REPOSITORY or metadata.get('sha')!=REVISION:return None
    files={file['rfilename']:file for file in metadata['siblings']}
    result=[]
    for quant,(filename,sha) in QUANTS.items():
        pins=[_pin(REPOSITORY,REVISION,files[name],'model') for name in (filename,ENCODER)]
        if pins[0]['sha256']!=sha or pins[1]['sha256']!=ENCODER_SHA:raise ValueError('The reviewed Iris package checksum changed.')
        key='iris-gguf:'+filename
        result.append({'id':stable_id(REPOSITORY,key),'repository':REPOSITORY,'revision':REVISION,'variantKey':key,
            'name':'Sakura-Iris 3B Uncensored · '+quant,'quantization':quant,'format':'gguf','subdirectory':'','category':'image',
            'files':pins,'bytes':sum(pin['bytes'] for pin in pins),'installable':True,'status':'not-installed','sourceUrl':'https://huggingface.co/'+REPOSITORY,
            'private':False,'gated':False,'modelType':'iris','architectures':[],'imageRuntime':'iris-sdcpp-v1',
            'imageDefaults':{'width':1024,'height':1024,'steps':30,'guidance':3},'catalogOrder':0 if quant=='Q8_0' else 1,
            'runtime':{'adapter':'sdcpp-iris','available':False,'inferenceVerified':False,'reason':'Pinned Vulkan engine and text encoder; actual image generation is verified after installation.'}})
    return result

def model_paths(entry):
    if not supports(entry):raise ValueError('This runtime only supports the reviewed Sakura-Iris checkpoint.')
    folder=Path(entry['localDirectory']).resolve()
    allowed={name:sha for name,sha in QUANTS.values()}
    diffusion=[file for file in entry['files'] if file['filename'] in allowed]
    encoder=[file for file in entry['files'] if file['filename']==ENCODER]
    if len(diffusion)!=1 or len(encoder)!=1:raise ValueError('Sakura requires one image quant and its reviewed text encoder.')
    paths=[]
    for file,expected in [(diffusion[0],allowed[diffusion[0]['filename']]),(encoder[0],ENCODER_SHA)]:
        path=(folder/file['filename']).resolve()
        if not path.is_relative_to(folder) or file['sha256']!=expected or digest(path)!=expected:raise ValueError('Sakura model verification failed.')
        paths.append(path)
    return paths

def ensure_engine(root,cancel,progress):
    folder=Path(root)/'runtime-setup/runtimes/iris-sdcpp-v1'
    folder.mkdir(parents=True,exist_ok=True)
    archive=folder/'runtime.zip'
    manifest=folder/'verified-files.json'
    try:
        receipt=json.loads(manifest.read_text())
        if receipt['archiveSha256']==ENGINE_SHA and all((folder/name).resolve().is_relative_to(folder.resolve()) and digest(folder/name)==sha for name,sha in receipt['files'].items()) and 'sd-cli.exe' in receipt['files']:
            return folder/'sd-cli.exe'
    except (OSError,ValueError,KeyError,TypeError):pass
    if not archive.is_file() or archive.stat().st_size!=ENGINE_BYTES or digest(archive)!=ENGINE_SHA:
        partial=folder/'runtime.zip.download'
        size=0;hasher=hashlib.sha256()
        try:
            with urllib.request.urlopen(ENGINE_URL,timeout=45) as response,partial.open('wb') as output:
                while chunk:=response.read(65536):
                    if cancel.is_set():raise RuntimeError('Image runtime preparation cancelled.')
                    size+=len(chunk)
                    if size>ENGINE_BYTES:raise ValueError('Image runtime archive exceeds its verified size.')
                    output.write(chunk);hasher.update(chunk)
                    progress('Downloading the Iris image runtime · '+str(round(100*size/ENGINE_BYTES))+'%')
            if size!=ENGINE_BYTES or hasher.hexdigest()!=ENGINE_SHA:raise ValueError('Iris runtime checksum differs from its pinned release.')
            partial.replace(archive)
        finally:partial.unlink(missing_ok=True)
    files={};total=0
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            if cancel.is_set():raise RuntimeError('Image runtime preparation cancelled.')
            target=(folder/item.filename).resolve();total+=item.file_size
            if not target.is_relative_to(folder.resolve()) or '\\' in item.filename or ':' in item.filename or (item.external_attr>>16)&0o170000==0o120000 or total>256*1024**2:
                raise ValueError('Unsafe or oversized image runtime archive.')
            if item.is_dir():target.mkdir(parents=True,exist_ok=True);continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with package.open(item) as source,target.open('wb') as output:shutil.copyfileobj(source,output)
            files[item.filename]=digest(target)
    if 'sd-cli.exe' not in files:raise ValueError('The reviewed runtime does not contain sd-cli.exe.')
    manifest.write_text(json.dumps({'archiveSha256':ENGINE_SHA,'files':files},indent=2),encoding='utf-8')
    return folder/'sd-cli.exe'

def command(entry,request,output,engine):
    model,encoder=model_paths(entry)
    settings=request.get('settings',{})
    if settings.get('referencePaths'):raise ValueError('Sakura-Iris currently supports text-to-image; choose an image-editing model for reference images.')
    if settings.get('loraPath'):raise ValueError('Use a LoRA-compatible image runtime for this adapter. Sakura GGUF LoRA execution has not been verified.')
    width,height=settings.get('width',1024),settings.get('height',1024)
    if width%16 or height%16:raise ValueError('Iris image dimensions must be multiples of 16.')
    return [str(engine),'--diffusion-model',str(model),'--llm',str(encoder),'-p',request['prompt'],
            '-W',str(width),'-H',str(height),'--steps',str(settings.get('steps',30)),
            '--cfg-scale',str(settings.get('guidanceScale',3)),'--sampling-method','dpm++2m',
            '--diffusion-fa','--offload-to-cpu','--seed',str(settings.get('seed',42)),
            '--batch-count',str(settings.get('numImages',1)),'-o',str(Path(output)/'native-image.png')]

def run(payload,output):
    from PIL import Image
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'studio'))
    from asset_worker import save_image_output,SamplingProgress
    output=Path(output);output.mkdir(exist_ok=True,parents=True)
    entry,request=payload['entry'],payload['request']
    args=command(entry,request,output,Path(payload['engine']))
    tracker=SamplingProgress(output,request['settings'].get('steps',30))
    process=subprocess.Popen(args,cwd=Path(payload['engine']).parent,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
                             stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
    for line in process.stdout:
        sys.stdout.write(line);sys.stdout.flush()
        cleaned=re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',line)
        match=re.search(r'(\d+)\s*/\s*(\d+).*?(?:s/it|it/s)',cleaned)
        if match:tracker.update(int(match[1]),total=int(match[2]))
    process.wait()
    if process.returncode:raise subprocess.CalledProcessError(process.returncode,args)
    paths=[]
    for index,path in enumerate(sorted(output.glob('native-image*.png')),1):
        with Image.open(path) as image:
            image.load()
            if image.width!=request['settings']['width'] or image.height!=request['settings']['height']:raise ValueError('The image runtime returned the wrong dimensions.')
            exported=save_image_output(image,output,index,request['settings'])
        paths.append(str(exported))
    if len(paths)!=request['settings'].get('numImages',1):raise ValueError('The runtime did not return every requested image.')
    (output/'smoke-result.json').write_text(json.dumps({'modelId':entry['id'],'inferenceVerified':True,'outputs':[{'path':path} for path in paths]}),encoding='utf-8')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--request',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(json.loads(args.request.read_text(encoding='utf-8')),args.output)
