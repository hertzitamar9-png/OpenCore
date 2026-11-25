"""Build a pinned CPU engine inside OpenCore's owned runtime directory."""
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import subprocess
import time
import urllib.request
import uuid
import zipfile


def run(command, log, report, cancel, label='Raon nano CPU'):
    with log.open('ab') as stream:
        child=subprocess.Popen([str(value) for value in command],stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0))
        deadline=time.monotonic()+1800
        try:
            while child.poll() is None:
                cancel()
                if time.monotonic()>deadline:raise RuntimeError(f'The {label} runtime build timed out. See {log}')
                time.sleep(.2)
        finally:
            if child.poll() is None:
                from hf_specialist_ocr import OCRManager
                OCRManager._terminate(child)
                child.wait(timeout=15)
        if child.returncode:
            with log.open('rb') as source:source.seek(max(0,log.stat().st_size-1800));detail=source.read().decode('utf-8','replace')
            raise RuntimeError(f'The {label} runtime could not be built: '+detail)


def cmake_commands(source, build, cmake):
    return [
        [cmake,'-S',source,'-B',build,'-G','Visual Studio 17 2022','-A','x64',
         '-DLLAMA_BUILD_TESTS=OFF','-DLLAMA_BUILD_EXAMPLES=OFF','-DLLAMA_BUILD_SERVER=ON',
         '-DLLAMA_BUILD_UI=OFF','-DLLAMA_OPENSSL=OFF','-DLLAMA_CURL=OFF',
         '-DGGML_CUDA=OFF','-DGGML_VULKAN=OFF','-DGGML_OPENCL=OFF','-DGGML_SYCL=OFF','-DGGML_RPC=OFF',
         '-DGGML_BLAS=OFF','-DGGML_NATIVE=OFF','-DGGML_AVX512=OFF','-DGGML_AVX512_VBMI=OFF','-DGGML_AVX512_VNNI=OFF'],
        [cmake,'--build',build,'--config','Release','--target','llama-server','--parallel','2'],
    ]


def extract_source(archive, target, prefix):
    if not prefix.endswith('/') or '..' in prefix:raise ValueError('Invalid source layout pin')
    target=Path(target).resolve();seen=set()
    with zipfile.ZipFile(archive) as package:
        files=package.infolist()
        if len(files)>10000 or sum(file.file_size for file in files)>512*1024**2:raise ValueError('The CPU engine source exceeds its extraction limit')
        for file in files:
            name=file.filename
            if not name.startswith(prefix):raise ValueError('CPU engine source left its pinned layout')
            name=name[len(prefix):]
            if not name:continue
            if '\\' in name or ':' in name or '\x00' in name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
                raise ValueError('The CPU engine source contains an unsafe path')
            if file.external_attr>>16&0o170000==0o120000:raise ValueError('The CPU engine source contains a symbolic link')
            local=(target/name).resolve()
            if not local.is_relative_to(target) or name.casefold() in seen:raise ValueError('The CPU engine source contains a colliding path')
            seen.add(name.casefold())
            if file.is_dir():local.mkdir(parents=True,exist_ok=True);continue
            local.parent.mkdir(parents=True,exist_ok=True)
            with package.open(file) as reader,local.open('xb') as writer:shutil.copyfileobj(reader,writer,1024*1024)


def install(target,pin,engine_id,report,cancel,source_archive=None):
    label=pin['label']
    if os.name!='nt':raise ValueError(f'The managed {label} build targets Windows x64')
    cmake=shutil.which('cmake')
    if not cmake:
        raise RuntimeError(f'{label} needs CMake and Visual Studio 2022 C++ Build Tools to prepare its pinned runtime. The model files are kept.')
    target=Path(target).resolve();parent=target.parent.resolve()
    work=parent/(engine_id+'.build-'+uuid.uuid4().hex);stage=parent/(engine_id+'.staging-'+uuid.uuid4().hex)
    work.mkdir(parents=True);stage.mkdir()
    archive=work/'source.zip';log=work/'build.log'
    try:
        if shutil.disk_usage(parent).free<2*1024**3:raise RuntimeError('At least 2 GiB of free disk space is needed to build the CPU runtime')
        report(stage='installing-runtime',detail=f'Preparing the pinned {label} source')
        digest=hashlib.sha256();size=0
        response=(Path(source_archive).open('rb') if source_archive is not None else
                  urllib.request.urlopen(urllib.request.Request(pin['url'],headers={'User-Agent':'OpenCore managed CPU engine'}),timeout=30))
        with response,archive.open('wb') as writer:
            while chunk:=response.read(262144):
                cancel();size+=len(chunk)
                if size>pin['bytes']:raise ValueError('CPU engine source exceeded its immutable size pin')
                digest.update(chunk);writer.write(chunk)
        if size!=pin['bytes'] or digest.hexdigest()!=pin['sha256']:raise ValueError('CPU engine source failed checksum verification')
        source,build=work/'source',work/'build';source.mkdir()
        extract_source(archive,source,pin['stripPrefix'])
        for command in cmake_commands(source,build,cmake):
            report(stage='installing-runtime',detail=f'Compiling {label} · two build workers')
            run(command,log,report,cancel,label)
        binary=next((path for path in (build/'bin/Release/llama-server.exe',build/'bin/llama-server.exe') if path.is_file()),None)
        if binary is None:raise RuntimeError(f'The {label} build produced no llama-server.exe. See {log}')
        for file in binary.parent.iterdir():
            if file.is_file() and (file.suffix.lower()=='.dll' or file.name=='llama-server.exe'):shutil.copy2(file,stage/file.name)
        shutil.copy2(source/'LICENSE',stage/'LICENSE')
        # This checks the executable, without loading a checkpoint or probing any GPU backend.
        run([stage/'llama-server.exe','--version'],log,report,cancel,label)
        shutil.copy2(log,stage/'build-log.txt')
        (stage/'build-info.json').write_text(json.dumps({'source':pin['source'],'revision':pin['revision'],'backend':'cpu',
             'modelArchitecture':pin.get('modelArchitecture','raon1_nano'),'inferenceVerified':False,'gpuBackendsBuilt':False},indent=2)+'\n',encoding='utf-8')
        files=[{'path':file.name,'bytes':file.stat().st_size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
               for file in stage.iterdir() if file.is_file()]
        (stage/'engine-receipt.json').write_text(json.dumps({'schema':1,'id':engine_id,'archiveSha256':pin['sha256'],
             'source':pin['source'],'revision':pin['revision'],'files':files},indent=2)+'\n',encoding='utf-8')
        cancel()
        if target.exists():
            if target.parent.resolve()!=parent:raise ValueError('The CPU engine target left its managed directory')
            target.rename(parent/(engine_id+'.previous-'+uuid.uuid4().hex))
        stage.rename(target)
        if work.resolve().parent==parent:shutil.rmtree(work)
        return target/'llama-server.exe'
    except Exception:
        # Keep the source and log when preparation fails, so the visible error can be investigated.
        raise
    finally:
        if stage.exists() and stage.resolve().parent==parent:shutil.rmtree(stage)
