"""Reproduce the pinned Android CPU runtime without downloading publisher executable code."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def digest(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def sources():
    return {name: hashlib.sha256((HERE/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for name in ('CMakeLists.txt', 'phone_runtime.cpp', 'runtime.json')}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--sdk', type=Path, required=True)
    args=parser.parse_args()
    pin=json.loads((HERE/'runtime.json').read_text())
    ndk=args.sdk/'ndk'/pin['ndk']
    if not (ndk/'build/cmake/android.toolchain.cmake').is_file(): raise ValueError('Install the pinned Android NDK '+pin['ndk'])
    work=ROOT/'distribution/.output/android-runtime'
    work.mkdir(parents=True, exist_ok=True)
    archive=work/'llama.cpp.tar.gz'
    if not archive.exists():
        req=urllib.request.Request('https://api.github.com/repos/ggml-org/llama.cpp/tarball/'+pin['revision'],headers={'User-Agent':'OpenCore-Android-build'})
        with urllib.request.urlopen(req,timeout=90) as source,archive.open('wb') as dest:shutil.copyfileobj(source,dest)
    if digest(archive)!=pin['archiveSha256']:raise ValueError('Pinned llama.cpp source archive SHA-256 mismatch')
    unpacked=work/'source'
    if not unpacked.is_dir():
        with tarfile.open(archive) as source:source.extractall(unpacked,filter='data')
    source=next(unpacked.glob('*/include/llama.h')).parents[1]
    shutil.copyfile(source/'LICENSE',HERE.parent/'vendor/LLAMA-LICENSE.txt')
    files={}
    for abi in pin['abis']:
        build=work/abi
        subprocess.run(['cmake','-S',str(HERE),'-B',str(build),'-G','Ninja',
                        '-DOPENCORE_LLAMA_SOURCE='+str(source),'-DCMAKE_TOOLCHAIN_FILE='+str(ndk/'build/cmake/android.toolchain.cmake'),
                        '-DANDROID_ABI='+abi,'-DANDROID_PLATFORM=android-'+str(pin['androidApi']),'-DANDROID_STL=c++_static','-DCMAKE_BUILD_TYPE=Release'],check=True)
        subprocess.run(['cmake','--build',str(build),'--target','opencore_phone','--parallel','2'],check=True)
        target=HERE/abi/'libopencore_phone.so';target.parent.mkdir(exist_ok=True)
        shutil.copyfile(build/'libopencore_phone.so',target)
        strip=ndk/'toolchains/llvm/prebuilt/windows-x86_64/bin/llvm-strip.exe'
        subprocess.run([str(strip),'--strip-unneeded',str(target)],check=True)
        files[target.relative_to(HERE).as_posix()]={'bytes':target.stat().st_size,'sha256':digest(target)}
    (HERE/'binaries.json').write_text(json.dumps({'revision':pin['revision'],'sources':sources(),'files':files},indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
