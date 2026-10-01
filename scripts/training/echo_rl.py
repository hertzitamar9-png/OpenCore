"""Online coding policy gradients for the exact ECHO expert training view.

Samples come from this trainable policy, not another model or reference answers.
The policy objective includes failed attempts with negative group advantage.
"""
import math
import time
import torch
import torch.nn.functional as F


def qualification_status(baseline, candidate, minimum_speed=20.):
    """Completed optimization is separate from approval of the exported model."""
    speed = candidate.get('native_tokens_per_second')
    if speed is None or not math.isfinite(speed) or speed < minimum_speed:
        return 'rejected-native-speed'
    if (candidate['passed'] < baseline['passed'] or
            candidate['mean_case_reward'] < baseline['mean_case_reward']):
        return 'rejected-native-heldout-regression'
    return 'complete'


def group_advantages(rewards):
    if len(rewards)<2 or any(not math.isfinite(r) or not 0<=r<=1 for r in rewards):
        raise ValueError('Need a finite group of correctness rewards in [0,1]')
    values=torch.tensor(rewards,dtype=torch.float32)
    deviation=values.std(unbiased=False)
    return (values-values.mean())/(deviation+1e-8)


def policy_loss(new_logprobs,old_logprobs,advantage,clip=.2,beta=.02):
    if new_logprobs.shape!=old_logprobs.shape or not new_logprobs.numel():
        raise ValueError('Policy token alignment mismatch')
    if not torch.isfinite(new_logprobs).all() or not torch.isfinite(old_logprobs).all():
        raise ValueError('Nonfinite policy probabilities')
    delta=new_logprobs-old_logprobs.detach()
    ratio=delta.exp()
    surrogate=torch.minimum(ratio*advantage,ratio.clamp(1-clip,1+clip)*advantage)
    # Sampled KL to the behavior/reference policy, without a second full model.
    kl=(-delta).exp()+delta-1
    return (-surrogate+beta*kl).mean()


def prompt_text(task):
    return ('Return only one Python function, no Markdown, comments, explanation, imports or examples. '
            'Use Python builtins only. Keep the implementation short.\n'+task)


def prompt_ids(tokenizer,task,max_tokens=192):
    text=prompt_text(task)
    ids=tokenizer.apply_chat_template([{'role':'user','content':text}],tokenize=True,
        add_generation_prompt=True,enable_thinking=False,return_dict=False)
    if not ids or len(ids)>max_tokens: raise ValueError('Task exceeds recorded prompt budget')
    return list(ids)


def response_logprobs(model,prompt,response,device):
    if not prompt or not response: raise ValueError('Empty prompt or response')
    model.opencore_expert_pool.position_start=0
    model.opencore_expert_pool.decode_start=len(prompt)
    tokens=torch.tensor([prompt+response[:-1]],device=device)
    positions=torch.arange(len(prompt)-1,len(prompt)+len(response)-1,device=device)
    logits=model(input_ids=tokens,use_cache=False,logits_to_keep=positions).logits[0].float()
    return F.log_softmax(logits,dim=-1).gather(1,torch.tensor(response,device=device).unsqueeze(1)).squeeze(1)


@torch.no_grad()
def sample(model,tokenizer,prompt,device,max_tokens=128,seed=42,greedy=False,deadline=None,on_token=None):
    """Exact unfiltered temperature-one sampling; record actual behavior logps."""
    model.eval();pool=model.opencore_expert_pool;pool.decode_start=None
    generator=torch.Generator(device=device).manual_seed(seed)
    ids=torch.tensor([prompt],device=device);cache=None;generated=[];logps=[]
    eos=tokenizer.eos_token_id
    eos=set(eos if isinstance(eos,list) else [eos])
    started=time.monotonic();stopped=False
    for index in range(max_tokens):
        if deadline is not None and time.monotonic()>deadline: raise TimeoutError('Recorded RL time budget exceeded')
        pool.position_start=0 if index==0 else len(prompt)+index-1
        output=model(input_ids=ids,past_key_values=cache,use_cache=True,logits_to_keep=1)
        cache=output.past_key_values
        if cache is None: raise RuntimeError('Autoregressive policy cache unavailable')
        logits=output.logits[0,-1].float()
        if not torch.isfinite(logits).all(): raise ValueError('Nonfinite sampling logits')
        logprobs=F.log_softmax(logits,dim=-1)
        token=int(logits.argmax() if greedy else torch.multinomial(logprobs.exp(),1,generator=generator)[0])
        generated.append(token);logps.append(float(logprobs[token]))
        if on_token: on_token(index+1)
        if token in eos: stopped=True;break
        ids=torch.tensor([[token]],device=device)
    elapsed=time.monotonic()-started
    del cache,output
    pool.position_start=0
    return {'prompt_ids':prompt,'response_ids':generated,'old_logprobs':logps,
            'text':tokenizer.decode(generated,skip_special_tokens=True),'truncated':not stopped,
            'tokens':len(generated),'seconds':elapsed,'tokens_per_second':len(generated)/elapsed,'seed':seed}
