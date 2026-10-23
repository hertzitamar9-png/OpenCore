"""Pinned, app-owned model runtimes and read-only reuse of managed Torch packages."""
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile


def runtime_package_pins(recipe):
    flavor=recipe['torchIndex'].rstrip('/').split('/')[-1]
    pins={name:version if '+' in version else version+'+'+flavor for name,version in recipe['torchPackages'].items()}
    if set(pins)&set(recipe['packages']):raise ValueError('Worker package pins cannot override the CUDA runtime pins')
    return {**pins,**recipe['packages']}


def managed_package_source(root,source):
    if source.is_relative_to(root):return True
    local=os.environ.get('LOCALAPPDATA')
    return bool(local and source.is_relative_to((Path(local)/'OpenCore/training-envs').resolve()))


def attach_shared_packages(root,environment,source):
    root,environment,source=Path(root).resolve(),Path(environment).resolve(),Path(source).resolve()
    if not environment.is_relative_to(root/'runtime-setup/environments') or not (environment/'pyvenv.cfg').is_file():
        raise ValueError('Shared packages require a new managed environment')
    if not managed_package_source(root,source) or not source.is_dir() or '\n' in str(source) or '\r' in str(source):
        raise ValueError('Only app-owned managed packages may be shared read-only')
    site=environment/('Lib/site-packages' if sys.platform=='win32' else f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages')
    site.mkdir(parents=True,exist_ok=True)
    marker=site/'opencore-shared-torch.pth'
    content=str(source)+'\n'
    if not marker.is_file() or marker.read_text(encoding='utf-8')!=content:marker.write_text(content,encoding='utf-8')
    return marker


def shared_torch_source(root,recipe,runner):
    if not recipe.get('shareManagedTorch'):return None
    root=Path(root).resolve()
    candidate=root/'speech/phonon-venv'/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    if not candidate.is_file():return None
    code="import importlib.metadata as m,json,sys;d=m.distribution('torch');print(json.dumps({'version':d.version,'site':str(d.locate_file('')),'python':sys.executable,'bits':__import__('struct').calcsize('P')*8}))"
    try:
        result=runner.run([candidate,'-I','-c',code],timeout=15)
        value=json.loads(result.stdout.splitlines()[-1])
        expected=recipe['torchPackages']['torch']
        flavor=recipe['torchIndex'].rstrip('/').split('/')[-1]
        site=Path(value['site']).resolve()
        if value['bits']!=64 or value['version']!=expected+'+'+flavor or not managed_package_source(root,site):return None
        return {'python':candidate,'site':site,'torch':value['version']}
    except (OSError,RuntimeError,ValueError,KeyError):return None


def install_moss_plugin(engine,resources,recipe):
    engine,resources=Path(engine).resolve(),Path(resources).resolve()
    plugin=recipe.get('pluginName','moss-image')
    source_name=recipe.get('pluginSource','moss-comfy')
    if (plugin,source_name) not in [('moss-image','moss-comfy'),('qwen-image-gguf','qwen-image-gguf')]:raise ValueError('Unknown managed plugin source')
    target=engine/'custom_nodes'/plugin
    target.mkdir(parents=True,exist_ok=True)
    for pin in recipe['pluginFiles']:
        name=pin['path']
        if Path(name).name!=name or '/' in name or '\\' in name:raise ValueError('Invalid pinned plugin path')
        source=resources/'model-runtime-sources'/source_name/name
        body=source.read_bytes()
        if hashlib.sha256(body).hexdigest()!=pin['sha256']:raise ValueError('MOSS plugin checksum does not match the pinned source')
        destination=target/name
        if not destination.resolve().is_relative_to(engine):raise ValueError('Plugin destination escaped the managed runtime')
        if not destination.is_file() or destination.read_bytes()!=body:destination.write_bytes(body)


def prepare_comfy_source(root,resources,recipe,runner):
    from setup_manager import download
    root=Path(root).resolve();pin=recipe['sourceArchive']
    archive=download(pin['url'],root/'runtime-setup/downloads'/('comfy-'+pin['revision']+'.zip'),pin['sha256'],runner,max_bytes=50_000_000)
    parent=root/'runtime-setup/sources'/('comfy-'+pin['revision'])
    engine=parent/pin['prefix']
    parent.mkdir(parents=True,exist_ok=True)
    runner.reporter('preparing-runtime-source',detail='Verifying the pinned ComfyUI source and official MOSS plugin')
    with zipfile.ZipFile(archive) as source:
        if sum(item.file_size for item in source.infolist())>200_000_000:raise ValueError('ComfyUI source archive exceeds its size limit')
        for item in source.infolist():
            runner.check_cancel()
            if item.is_dir():continue
            if not item.filename.startswith(pin['prefix']+'/') or '\\' in item.filename or ((item.external_attr>>16)&0o170000)==0o120000:
                raise ValueError('Source archive contains an invalid path')
            destination=parent/item.filename
            if not destination.resolve().is_relative_to(engine.resolve()) or item.file_size>25_000_000:raise ValueError('Source archive escaped its managed directory')
            body=source.read(item)
            if destination.is_file() and hashlib.sha256(destination.read_bytes()).digest()==hashlib.sha256(body).digest():continue
            destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(body)
    if not (engine/'main.py').is_file():raise ValueError('Pinned ComfyUI archive has no runtime entry point')
    install_moss_plugin(engine,resources,recipe)
    return engine
