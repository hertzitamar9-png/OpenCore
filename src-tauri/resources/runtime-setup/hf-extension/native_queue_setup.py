"""Run the bundled dependency recipe and native speech model verification."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from setup_manager import Cancelled, Runner, emit, install


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--options-file', type=Path, required=True)
    parser.add_argument('--cancel-file', type=Path, required=True)
    args = parser.parse_args()
    runner = Runner(args.cancel_file)
    try:
        options = json.loads(args.options_file.read_text(encoding='utf-8'))
        install(args.root, args.resources, args.target, options, runner)
        if args.target == 'phonon-2':
            emit('verifying-speech-model', detail='Extracting the pinned Phonon container and verifying actual model loading')
            runner.run([sys.executable, args.resources / 'speech/prepare_phonon_runtime.py',
                        '--root', args.root / 'speech', '--prepare-model'],
                       env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'OPENCORE_SETUP_CANCEL_FILE': str(args.cancel_file)})
            emit('speech-model-verified', detail='Speech runtime and actual model loading verified')
        runner.check_cancel()
        return 0
    except Cancelled as error:
        emit('cancelled', error=str(error))
        return 2
    except Exception as error:
        emit('failed', error=str(error))
        return 1


if __name__ == '__main__': raise SystemExit(main())
