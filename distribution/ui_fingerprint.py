"""Hash every shipped UI asset and compare independent builds before publication."""
import argparse
import hashlib
import json
from pathlib import Path

def fingerprint(root):
    root=Path(root)
    files={}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                files[path.relative_to(root).as_posix()]=hashlib.file_digest(stream,"sha256").hexdigest()
    if "index.html" not in files or not any(n.endswith(".js") for n in files):
        raise ValueError("No generated UI assets")
    return files

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--compare",action="store_true")
    parser.add_argument("first",type=Path)
    parser.add_argument("second",type=Path)
    args=parser.parse_args()
    if args.compare:
        a=json.loads(args.first.read_text(encoding="utf-8"))
        b=json.loads(args.second.read_text(encoding="utf-8"))
        if not a or a!=b:
            raise ValueError("Packaged and independently tested UI assets differ")
        print("All packaged UI asset hashes match rendered QA")
    else:
        args.second.parent.mkdir(parents=True,exist_ok=True)
        args.second.write_text(json.dumps(fingerprint(args.first),sort_keys=True,indent=2)+"\n",encoding="utf-8")
