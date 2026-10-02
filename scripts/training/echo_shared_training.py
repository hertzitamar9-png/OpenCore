"""Shared-expert SFT uses every complete answer token and native routes."""
import math
import torch
import torch.nn.functional as F


def complete_loss(model,row,device):
    prompt=row['prompt_ids'];response=row['response_ids']
    if not prompt or not response: raise ValueError('Empty complete example')
    pool=model.opencore_expert_pool;pool.position_start=0;pool.decode_start=len(prompt)
    positions=torch.arange(len(prompt)-1,len(prompt)+len(response)-1,device=device)
    output=model(input_ids=torch.tensor([prompt+response[:-1]],device=device),
        use_cache=False,logits_to_keep=positions).logits[0]
    return F.cross_entropy(output.float(),torch.tensor(response,device=device))


def candidate_status(baseline,candidate,before_loss,after_loss):
    speed=candidate.get('speed')
    if speed is None or not math.isfinite(speed) or speed<20: return 'rejected-native-speed'
    if not math.isfinite(after_loss) or after_loss>before_loss+.02: return 'rejected-heldout-loss'
    if candidate['passed']<baseline['passed'] or candidate['mean_case_reward']<baseline['mean_case_reward']:
        return 'rejected-coding-regression'
    if candidate['passed']==baseline['passed']: return 'no-verified-coding-gain'
    return 'qualified-local-coding-gain'
