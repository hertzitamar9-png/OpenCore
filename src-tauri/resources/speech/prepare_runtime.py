"""Prepare isolated Whisper packages; preserve existing ASR checkpoints and environments."""
import argparse
from pathlib import Path
from runtime_setup import prepare, verify, write_receipt, receipt_shared_cuda_runtime

PACKAGES = {'transformers':'4.48.3','accelerate':'1.2.1','av':'13.1.0','psutil':'6.1.1',
    'faster-whisper':'1.2.1','ctranslate2':'4.8.2'}
IMPORTS = {'transformers':['WhisperForConditionalGeneration'], 'av':[], 'numpy':[],
    'psutil':[], 'faster_whisper':[], 'ctranslate2':[]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    parser.add_argument('--verify-only',action='store_true')
    parser.add_argument('--refresh-receipt',action='store_true')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    python = root/'whisper-venv/Scripts/python.exe'
    shared = None
    if not args.verify_only:
        python, shared = prepare(root,'whisper-venv',PACKAGES,IMPORTS)
    verification = verify(python, PACKAGES, IMPORTS)
    if args.verify_only and args.refresh_receipt:
        shared = receipt_shared_cuda_runtime(root/'whisper-runtime.json')
    if not args.verify_only or args.refresh_receipt:
        write_receipt(root/'whisper-runtime.json', verification, sharedCudaRuntime=shared)
    print('Whisper speech runtime ready.',flush=True)


if __name__=='__main__':
    main()
