"""Prepare isolated Whisper packages; preserve existing ASR checkpoints and environments."""
import argparse
import json
from pathlib import Path
import subprocess
from runtime_setup import prepare

PACKAGES = {'transformers':'4.48.3','accelerate':'1.2.1','av':'13.1.0','psutil':'6.1.1',
    'faster-whisper':'1.2.1','ctranslate2':'4.8.2'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    python, shared = prepare(root,'whisper-venv',PACKAGES)
    check = subprocess.run([str(python),'-c','import torch,av,numpy,psutil,transformers,faster_whisper,ctranslate2; '
        'from transformers import WhisperForConditionalGeneration; '
        'assert transformers.__version__=="4.48.3"; assert torch.version.cuda'],capture_output=True,text=True)
    if check.returncode:
        raise RuntimeError(check.stderr.strip() or 'Whisper runtime verification failed.')
    (root/'whisper-runtime.json').write_text(json.dumps({'schema':2,'packages':PACKAGES,'sharedCudaRuntime':shared}),encoding='utf-8')
    print('Whisper speech runtime ready.',flush=True)


if __name__=='__main__':
    main()
