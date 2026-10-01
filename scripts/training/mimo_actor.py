"""Reward-filtered local MiMo rollout. Failed rollouts never become SFT targets."""
import json
from pathlib import Path
from mimo_verifier import TaskContainer

# Command length is checked by TaskContainer. A 12K grammar repetition exceeds
# llama.cpp's parser bound; don't expand that bound into the sampling grammar.
ACTION_FORMAT={'type':'json_schema','json_schema':{'name':'mimo_action','strict':True,'schema':{'oneOf':[
    {'type':'object','properties':{'command':{'type':'string','minLength':1}},
     'required':['command'],'additionalProperties':False},
    {'type':'object','properties':{'done':{'const':True}},'required':['done'],'additionalProperties':False}
]}}}

def rollout(backend,folder,max_steps=16):
    folder=Path(folder)
    task=json.loads((folder/'mimo-task.json').read_text(encoding='utf-8'))
    messages=[{'role':'system','content':
        'You are repairing a repository in an isolated container. Send exactly one JSON object per turn: '
        '{"command":"a bash command to inspect, edit or test the code"} or {"done":true}. '
        'Commands execute in /testbed. Do not change tests, git metadata or test configuration. '
        'Change only src/action.js, action.yml or README.md. Inspect the source before editing; '
        'run the existing Jest tests after editing. Never claim success before tests pass.'},task['prompt'][0]]
    transcript=[]
    with TaskContainer(folder) as container:
        for step in range(max_steps):
            response=backend.chat(messages,response_format=ACTION_FORMAT)
            text=response['choices'][0]['message'].get('content') or ''
            messages.append({'role':'assistant','content':text})
            transcript.append({'step':step,'response':response})
            try:
                stripped=text.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
                action=json.loads(stripped)
                if action.get('done') is True: break
                output=container.command(action['command'])
            except (KeyError,ValueError,TypeError) as error:
                output={'exit_code':-1,'output':f'Invalid action: {error}. Return one JSON object with command or done.'}
            messages.append({'role':'user','content':json.dumps(output)})
            (folder/'mimo-rollout.json').write_text(json.dumps({'messages':messages,'steps':transcript},indent=2,ensure_ascii=False),encoding='utf-8')
        reward=container.verify('actor')
    record={'reward':int(reward['passed']),'steps':len(transcript),'image':reward['image'],
        'instance':reward['instance'],'source_sha256':reward['source_sha256'],
        'note':'One reward-filtered local task; not full MiMo RL training'}
    (folder/'mimo-reward.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    if reward['passed'] and reward['source_patch']:
        example={'id':reward['instance'],'dataset':'XiaomiMiMo/MiMo-V2.6-RL-oss','split':'train',
            'messages':[task['prompt'][0],{'role':'assistant','content':reward['source_patch']}],
            'reward':1,'verifier_sha256':reward['verifier_sha256'],'source_sha256':reward['source_sha256']}
        (folder/'mimo-sft.jsonl').write_text(json.dumps(example,ensure_ascii=False)+'\n',encoding='utf-8')
    return record
