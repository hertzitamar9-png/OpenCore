"""Prepare full coding answers from existing data; no model loading or downloads."""
import argparse
import json
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / 'src-tauri/resources'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--benchmark-input', type=Path, action='append', required=True)
    parser.add_argument('--train-count', type=int, default=512)
    parser.add_argument('--validation-count', type=int, default=64)
    args = parser.parse_args()
    from fusion.corpus import prepare
    result = prepare(args.source, args.output, args.benchmark_input,
                     train_count=args.train_count, validation_count=args.validation_count)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
