"""Bounded UltraData sample for complete-answer shared-expert training."""
import argparse
import hashlib
import json
from pathlib import Path
from echo_shared_data import canonical_solution,complete_tokens,literal_cases,split_records,prompt_key,read_json
from prepare_echo_pilot import ULTRA_REV,ULTRA_FILE
from rl_verifier import CodingVerifier
from train_echo_pilot import digest,save


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--research-root',type=Path,required=True)
    parser.add_argument('--folder',type=Path,required=True)
    args=parser.parse_args();root=args.research_root;folder=args.folder
    folder.mkdir(parents=True,exist_ok=True)
    if (folder/'data-protocol.json').exists(): raise FileExistsError('Immutable prepared sample exists')
    import fsspec
    import pyarrow.parquet as pq
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(root/'release/base-seeds/OpenCore-APEX-Dequantised-HF-corrected-v1',local_files_only=True)
    prior=root/'artifacts/training/mimo-ultradata-20261001/ultra-sft.jsonl'
    excluded=[json.loads(line)['messages'][0]['content'] for line in prior.read_text(encoding='utf-8').splitlines()]
    url=f'https://huggingface.co/datasets/openbmb/UltraData-Code/resolve/{ULTRA_REV}/{ULTRA_FILE}'
    columns=['uuid','task','solution','test'];cache=folder/'bounded-source.jsonl'
    metadata=folder/'bounded-source-protocol.json'
    if cache.exists():
        receipt=read_json(metadata)
        if digest(cache)!=receipt['sha256']: raise ValueError('Bounded source cache changed')
        rows=[json.loads(line) for line in cache.read_text(encoding='utf-8').splitlines()]
    else:
        with fsspec.open(url,block_size=1<<20).open() as file:
            parquet=pq.ParquetFile(file);rg=parquet.metadata.row_group(0)
            compressed=sum(rg.column(i).total_compressed_size for i in range(rg.num_columns)
                           if rg.column(i).path_in_schema in columns)
            if compressed>64<<20: raise ValueError('Range-read budget exceeded')
            rows=next(parquet.iter_batches(batch_size=8192,row_groups=[0],columns=columns)).to_pylist()
        cache.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
        save(folder,metadata.name,{'revision':ULTRA_REV,'file':ULTRA_FILE,'row_group':0,'rows':len(rows),
            'compressed_selected_columns_bytes':compressed,'sha256':digest(cache)})
    eligible=[];excluded_set=set(excluded)
    for row in rows:
        try:
            if not all(row.get(k) for k in columns): continue
            if row['task'] in excluded_set or any(s in row['task'].lower() for s in ('humaneval','gsm8k','swe-bench')): continue
            name,solution=canonical_solution(row['solution'])
            cases=literal_cases(row['test'],name)
            if len(cases)<3: continue
            prompt,response=complete_tokens(tokenizer,row['task'],solution)
        except (SyntaxError,ValueError,TypeError): continue
        eligible.append({'id':row['uuid'],'name':name,'prompt':row['task'],'solution':solution,'cases':cases,
            'prompt_ids':prompt,'response_ids':response,'raw_solution':row['solution'],'raw_tests':row['test'],
            'source_row_sha256':hashlib.sha256(json.dumps(row,sort_keys=True,ensure_ascii=False).encode()).hexdigest()})
    # Split before verification, never select hold-out tasks based on how well
    # ECHO answers them. Reference correctness is checked uniformly for all.
    groups=split_records(eligible,excluded,train=144,validation=32,heldout=64)
    selection=folder/'unverified-selection.json'
    if selection.exists():
        # Keep the first failed selection and its receipts. The larger pool is
        # fixed before model inference; only reference validity selects targets.
        original=read_json(selection)
        original_ids={row['id'] for rows in original.values() for row in rows}
        original_prompts={prompt_key(row['prompt']) for rows in original.values() for row in rows}
        original_code={row['solution'] for rows in original.values() for row in rows}
        supplementary=split_records([row for row in eligible if row['id'] not in original_ids
            and prompt_key(row['prompt']) not in original_prompts and row['solution'] not in original_code],excluded,
                                    train=96,validation=24,heldout=40)
        groups={split:original[split]+supplementary[split] for split in original}
        selection=folder/'extended-selection.json'
    if selection.exists():
        if read_json(selection)!=groups: raise ValueError('Reference pool changed')
    else: save(folder,selection.name,groups)
    with CodingVerifier(folder) as verifier:
        for split,records in groups.items():
            for index,row in enumerate(records):
                label=f'reference-{split}-{index:03d}';receipt=folder/(label+'-reward.json')
                if receipt.exists():
                    reward=read_json(receipt)
                    expected=hashlib.sha256(row['solution'].encode()).hexdigest()
                    if reward['source_sha256']!=expected or reward['task_id']!=row['id'] or reward['worker_sha256']!=verifier.worker_sha:
                        raise ValueError('Saved reference verification identity changed')
                else: reward=verifier.verify(row,row['solution'],label)
                row['reference_verification']=reward
                save(folder,'data-progress.json',{'phase':'reference-verification','split':split,'completed':index+1,
                    'total':len(records),'eligible':len(eligible)})
    # Failed source targets never enter the optimizer or inflate a test score.
    # Keep the original selection and every failure. No replacing failed
    # hold-out references with easier questions after seeing model responses.
    accepted={split:[row for row in records if row['reference_verification']['all_passed']]
              for split,records in groups.items()}
    for split,minimum in [('train',96),('validation',16),('heldout',32)]:
        if len(accepted[split])<minimum: raise RuntimeError(f'Insufficient verified {split} examples; selection preserved')
        accepted[split]=accepted[split][:minimum]
    target=folder/'complete-coding.json'
    target.write_text(json.dumps(accepted,indent=2,ensure_ascii=False),encoding='utf-8')
    protocol={'dataset':'openbmb/UltraData-Code','revision':ULTRA_REV,'file':ULTRA_FILE,'data_sha256':digest(target),
        'prior_sample_sha256':digest(prior),'selected':{k:len(v) for k,v in groups.items()},
        'verified':{k:len(v) for k,v in accepted.items()},'max_prompt_tokens':256,'max_response_tokens':256,
        'answers':'Complete canonicalized functions plus EOS, no answer/prompt truncation',
        'selection':'Exact prompt and solution deduplication, prior sample excluded, stable hash ordering; fixed before inference',
        'reference_verifier':'Offline isolated pinned container, direct literal contracts only',
        'scope':'Finite same-source held-out coding sample; not proof of pretraining decontamination or global superiority',
        'automatic_promotion':False}
    save(folder,'data-protocol.json',protocol);print(json.dumps(protocol,indent=2),flush=True)


if __name__=='__main__': main()
