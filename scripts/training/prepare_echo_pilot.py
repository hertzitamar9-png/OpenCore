"""Bounded, pinned local training data. Never downloads complete UltraData.

UltraData examples are supervised coding solutions. MiMo rows are environment
tasks, NOT completed answers: they enter training only after executable checks.
Local derived data is not included in Git or redistributed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ULTRA_REV = '85182d829f2ce7ea07cca72ebfc509deea1d9f5f'
MIMO_REV = '639865fd3374018d6cb29b9fb82dd531406fcf5f'
MIMO_SHA = 'e15733cf2451cfbc5492a4120f7f8cfddbad818aa9f0b324c79888dd1fece161'
ULTRA_FILE = 'data/UltraData-Code-L3/py/UltraData-Code-L3-py-part-00001-of-00147.parquet'

def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda:file.read(1<<20), b''): result.update(block)
    return result.hexdigest()

def main():
    import fsspec
    import pyarrow.parquet as pq
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--examples', type=int, default=32)
    args = parser.parse_args()
    if not 8 <= args.examples <= 128: raise ValueError('Local pilot supports 8–128 examples')
    out = args.output; out.mkdir(parents=True,exist_ok=True)
    if (out/'ultra-sft.jsonl').exists(): raise FileExistsError('Prepared sample already exists; keep it immutable')
    mimo = out/'code.parquet'
    if not mimo.exists():
        urllib.request.urlretrieve(f'https://huggingface.co/datasets/XiaomiMiMo/MiMo-V2.6-RL-oss/resolve/{MIMO_REV}/code.parquet',mimo)
    if sha(mimo) != MIMO_SHA: raise ValueError('MiMo source hash mismatch')
    task = pq.read_table(mimo).to_pylist()[0]
    instance = json.loads(task['extra_info']['instance_json'])
    # Persist raw task and verifier patch; never manufacture a target answer.
    (out/'mimo-task.json').write_text(json.dumps(task,indent=2,ensure_ascii=False),encoding='utf-8')
    (out/'mimo-verifier.patch').write_text(instance['test_patch'],encoding='utf-8')
    url=f'https://huggingface.co/datasets/openbmb/UltraData-Code/resolve/{ULTRA_REV}/{ULTRA_FILE}'
    with fsspec.open(url,block_size=1<<20).open() as file:
        parquet=pq.ParquetFile(file)
        columns=['uuid','task','solution','test']
        rowgroup=parquet.metadata.row_group(0)
        read_bytes=sum(rowgroup.column(i).total_compressed_size for i in range(rowgroup.num_columns)
                       if rowgroup.column(i).path_in_schema in columns)
        if read_bytes > 64<<20: raise ValueError('First row group exceeds the bounded 64 MB I/O budget')
        rows=next(parquet.iter_batches(batch_size=args.examples,row_groups=[0],columns=columns)).to_pylist()
    records=[]
    for index,row in enumerate(rows):
        if not row['task'] or not row['solution']: continue
        records.append({'id':row['uuid'],'dataset':'openbmb/UltraData-Code','revision':ULTRA_REV,
            'split':'validation' if index % 4 == 0 else 'train',
            'messages':[{'role':'user','content':row['task']},{'role':'assistant','content':row['solution']}],
            'tests':row['test'],'source_sha256':hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()})
    source=out/'ultra-sft.jsonl'
    source.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in records),encoding='utf-8')
    protocol={'status':'prepared','ultra_revision':ULTRA_REV,'ultra_file':ULTRA_FILE,'ultra_range_read_budget_bytes':read_bytes,
        'sample_sha256':sha(source),'examples':len(records),'train':sum(r['split']=='train' for r in records),
        'validation':sum(r['split']=='validation' for r in records),'mimo_revision':MIMO_REV,'mimo_sha256':MIMO_SHA,
        'mimo_instance':instance['instance_id'],'mimo_training_targets':0,
        'mimo_note':'Requires model rollout and passing isolated verifier before SFT; task prompt alone is not a target',
        'method':'Bounded local pilot, not full-corpus training or an improvement claim'}
    (out/'data-protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    print(json.dumps(protocol,indent=2),flush=True)

if __name__=='__main__': main()
