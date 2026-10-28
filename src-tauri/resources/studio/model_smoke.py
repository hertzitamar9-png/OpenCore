"""Verify a real studio worker output before marking model preparation Ready."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from PIL import Image

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0,str(Path(__file__).resolve().parent))


def verify_generation(worker,request,output):
    output=Path(output)
    settings=request.get('settings') or {}
    for name in ('outputWidth','outputHeight'):
        if name in settings and (type(settings[name]) is not int or not 128<=settings[name]<=8192 or settings[name]%8):
            raise ValueError('Invalid image export dimensions')
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError('Use an empty verification output directory; old images are not execution evidence')
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    worker(request,output)
    images=sorted(path for path in output.iterdir() if path.suffix.lower() in {'.png','.jpg','.jpeg','.webp'})
    if not images:
        raise RuntimeError('Model verification returned no generated image')
    if len(images)>8:
        raise RuntimeError('Model verification returned too many images')
    limit=8192 if 'outputWidth' in settings or 'outputHeight' in settings else 2048
    results=[]
    for path in images:
        if path.is_symlink() or path.resolve().parent!=output.resolve() or not path.is_file():
            raise RuntimeError('Model verification output escaped its managed directory')
        if path.stat().st_size>64*1024*1024:
            raise RuntimeError('Model verification image exceeds the output size limit')
        try:
            with Image.open(path) as image:
                width,height=image.size
                if width>limit or height>limit:
                    raise RuntimeError('Model verification image dimensions exceed the limit')
                image.verify()
            with Image.open(path) as image:image.load()
        except Exception as error:
            raise RuntimeError(f'invalid generated image: {error}') from error
        expected_width=settings.get('outputWidth',settings.get('width'))
        expected_height=settings.get('outputHeight',settings.get('height'))
        if (expected_width is not None and width!=expected_width) or (expected_height is not None and height!=expected_height):
            raise RuntimeError('Generated image dimensions do not match the requested verification task')
        metadata=path.stat()
        results.append({'path':str(path.resolve()),'bytes':metadata.st_size,'modifiedNanos':str(metadata.st_mtime_ns),
                        'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'width':width,'height':height})
    return {'inferenceVerified':True,'modelId':request['modelId'],'outputs':results}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--request',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from asset_worker import generate
    result=verify_generation(generate,json.loads(args.request.read_text(encoding='utf-8')),args.output)
    (args.output/'smoke-result.json').write_text(json.dumps(result),encoding='utf-8')
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
