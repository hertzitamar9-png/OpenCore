"""Build the pinned, lightweight Windows Phonon-2.5 dictation engine."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

SOURCE_REVISION = '2eac2e77d9c7ef8899f7a86a46e693ac5ac1a5cd'
GGML_REVISION = 'e705c5fed490514458bdd2eaddc43bd098fcce9b'
HERE = Path(__file__).resolve().parent
ARCHIVE_SHA256 = 'd81e3af03d6752b3923350e9c75ebf978bbc37845ff5c6fa85564833af5ca106'


def patch_quantizer(source):
    path = source / 'examples/cli/main.cpp'
    text = path.read_text(encoding='utf-8')
    old = 'if (is_quantizable_name(name) && t->type == GGML_TYPE_F32) {'
    new = 'if (is_quantizable_name(name) && (t->type == GGML_TYPE_F32 || t->type == GGML_TYPE_F16)) {'
    if text.count(old) != 1:
        raise ValueError('The pinned speech quantizer source changed')
    text = text.replace(old, new)
    old = 'const float* fsrc = (const float*)t->data;'
    new = '''// OpenCore also accepts the published F16 source. Expand each eligible
            // tensor transiently using ggml's own conversion before quantization.
            std::vector<float> expanded;
            const float* fsrc = (const float*)t->data;
            if (t->type == GGML_TYPE_F16) {
                expanded.resize(static_cast<size_t>(ggml_nelements(t)));
                ggml_fp16_to_fp32_row((const ggml_fp16_t*)t->data,
                                      expanded.data(), expanded.size());
                fsrc = expanded.data();
            }'''
    if text.count(old) != 1:
        raise ValueError('The pinned speech quantizer conversion point changed')
    path.write_text(text.replace(old, new), encoding='utf-8')
    server = source / 'examples/server/CMakeLists.txt'
    server.write_text(server.read_text(encoding='utf-8') + '\nif(WIN32)\n  target_link_libraries(parakeet-server PRIVATE ws2_32)\nendif()\n', encoding='utf-8')


def build(app, inputs):
    work = Path(inputs) / 'phonon25-engine'
    source, build = work / 'source', work / 'build'
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'remote', 'add', 'origin', 'https://github.com/mudler/parakeet.cpp.git'], check=True)
    subprocess.run(['git', '-C', str(source), 'fetch', '--depth', '1', 'origin', SOURCE_REVISION], check=True)
    subprocess.run(['git', '-C', str(source), 'checkout', '--detach', 'FETCH_HEAD'], check=True)
    subprocess.run(['git', '-C', str(source), 'submodule', 'update', '--init', '--depth', '1', 'third_party/ggml'], check=True)
    pin = subprocess.check_output(['git', '-C', str(source / 'third_party/ggml'), 'rev-parse', 'HEAD'], text=True).strip()
    if pin != GGML_REVISION:
        raise ValueError('The speech engine dependency differs from its reviewed source')
    patch_quantizer(source)
    git_exe = shutil.which('git')
    git_bash = Path(git_exe).resolve().parents[1] / 'bin/bash.exe'
    command = ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja',
               '-DCMAKE_C_COMPILER=gcc', '-DCMAKE_CXX_COMPILER=g++', '-DCMAKE_BUILD_TYPE=Release',
               '-DGGML_NATIVE=OFF', '-DGGML_CCACHE=OFF', '-DBUILD_SHARED_LIBS=OFF',
               '-DGGML_OPENMP=OFF', '-DCMAKE_EXE_LINKER_FLAGS=-static',
               '-DPARAKEET_WITH_CED=OFF', '-DPARAKEET_WITH_VOICEDETECT=OFF',
               '-DPARAKEET_BUILD_TESTS=OFF', '-DPARAKEET_SHARED=OFF']
    if git_bash.is_file():
        command.append('-DBASH_EXECUTABLE=' + str(git_bash))
    subprocess.run(command, check=True)
    subprocess.run(['cmake', '--build', str(build), '--config', 'Release', '--parallel', '4',
                    '--target', 'parakeet-cli', 'parakeet-server'], check=True)
    stage_binaries(app, build, source)


def stage(app, inputs=None):
    archive = HERE / 'vendor/phonon25-windows-x64.zip'
    if file_hash(archive) != ARCHIVE_SHA256:
        raise ValueError('The bundled Phonon speech engine failed archive verification')
    target = Path(app) / 'speech/parakeet'
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            if '/' in entry.filename or '\\' in entry.filename or entry.filename in ('.', '..'):
                raise ValueError('The speech archive contains an unexpected path')
            (target / entry.filename).write_bytes(package.read(entry))


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stage_binaries(app, build, source):
    target = Path(app) / 'speech/parakeet'
    target.mkdir(parents=True, exist_ok=True)
    for name in ('parakeet-cli.exe', 'parakeet-server.exe'):
        found = list(Path(build).rglob(name))
        if len(found) != 1:
            raise ValueError('The speech build did not produce one ' + name)
        shutil.copy2(found[0], target / name)
    shutil.copy2(Path(source) / 'LICENSE', target / 'LICENSE.txt')
    shutil.copy2(Path(source) / 'third_party/ggml/LICENSE', target / 'GGML-LICENSE.txt')
    for license_file in (HERE / 'vendor/phonon25-licenses').glob('*.txt'):
        shutil.copy2(license_file, target / license_file.name)
    shutil.copy2(HERE / '../src-tauri/resources/speech/PHONON25-NOTICE.txt', target / 'PHONON25-NOTICE.txt')
    shutil.copy2(HERE / 'speech-fixtures/phonon25-verification.wav', target / 'verification.wav')
    files = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
             for path in target.iterdir() if path.is_file() and path.name != 'engine.json'}
    (target / 'engine.json').write_text(json.dumps({'schema': 1, 'sourceRevision': SOURCE_REVISION,
                                                  'build': 'gnu-simd-v1',
                                                  'f16Quantizer': 1, 'files': files}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--rebuild', action='store_true')
    args = parser.parse_args()
    (build if args.rebuild else stage)(args.app, args.inputs)
