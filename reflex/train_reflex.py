"""Fine-tune OpenCore Reflex from a Laya checkpoint (laya-browser's RLCD recipe, single GPU).

usage: train_reflex.py BASE_DIR OUT_DIR ITEMS.pt [ITEMS.pt ...] [--epochs N] [--lr-enc X] [--lr-head X]

Loss = RLCD (policy gradient on Gaussian-perturbed logits with a strictly proper
scoring reward, so reported probabilities stay calibrated) + soft cross-entropy.
"""
import argparse
import json
import os
import random
import shutil
import sys
import time

import torch
from safetensors.torch import load_file, save_file
from transformers import AutoTokenizer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "laya_core"))
from rl_common import build_model, proper_reward  # noqa: E402


def collate(items, pad_id):
    n, length = len(items), max(len(it["ids"]) for it in items)
    kmax = max(len(it["markers"]) for it in items)
    ids = torch.full((n, length), pad_id, dtype=torch.long)
    att = torch.zeros((n, length), dtype=torch.long)
    mpos = torch.zeros((n, kmax), dtype=torch.long)
    mmask = torch.zeros((n, kmax), dtype=torch.bool)
    target = torch.zeros((n, kmax))
    for i, it in enumerate(items):
        ids[i, :len(it["ids"])] = torch.tensor(it["ids"])
        att[i, :len(it["ids"])] = 1
        k = len(it["markers"])
        mpos[i, :k] = torch.tensor(it["markers"])
        mmask[i, :k] = True
        target[i, :len(it["target"])] = torch.tensor(it["target"])
    return dict(input_ids=ids, attention_mask=att, marker_pos=mpos, marker_mask=mmask, target=target,
                qtype=torch.tensor([it["qtype"] for it in items]))


def batches(items, max_tokens, max_seqs, rng):
    """Length-bucketed batches under a padded-token budget."""
    order = sorted(range(len(items)), key=lambda i: len(items[i]["ids"]))
    out, cur, cur_max = [], [], 0
    for i in order:
        length = len(items[i]["ids"])
        if cur and (max(cur_max, length) * (len(cur) + 1) > max_tokens or len(cur) >= max_seqs):
            out.append(cur)
            cur, cur_max = [], 0
        cur.append(i)
        cur_max = max(cur_max, length)
    if cur:
        out.append(cur)
    rng.shuffle(out)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("out")
    parser.add_argument("items", nargs="+")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--lr-enc", type=float, default=1e-5)
    parser.add_argument("--lr-head", type=float, default=5e-5)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--max-seqs", type=int, default=48)
    args = parser.parse_args()

    device = torch.device("cuda")
    cfg = json.load(open(os.path.join(args.base, "rl_agent_config.json")))
    tok = AutoTokenizer.from_pretrained(os.path.join(args.base, "tokenizer"))
    model = build_model(cfg, encoder_dir=os.path.join(args.base, "encoder"))
    model.load_state_dict(load_file(os.path.join(args.base, "model.safetensors")), strict=True)
    model.encoder.config.reference_compile = False
    model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.to(device).train()

    items = []
    for path in args.items:
        part = torch.load(path, weights_only=False)
        print("loaded %d items from %s" % (len(part), path), flush=True)
        items.extend(part)
    rng = random.Random(42)
    enc = [p for n, p in model.named_parameters() if n.startswith("encoder.")]
    head = [p for n, p in model.named_parameters() if not n.startswith("encoder.")]
    opt = torch.optim.AdamW([{"params": enc, "lr": args.lr_enc}, {"params": head, "lr": args.lr_head}], weight_decay=0.01)
    plan = [batches(items, args.max_tokens, args.max_seqs, rng) for _ in range(args.epochs)]
    total = sum(len(p) for p in plan)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[args.lr_enc, args.lr_head], total_steps=total, pct_start=0.05)
    started, step, group = time.time(), 0, 4
    for epoch, epoch_batches in enumerate(plan):
        sigma = 0.4 - 0.3 * epoch / max(1, args.epochs - 1)
        for indices in epoch_batches:
            batch = {k: v.to(device) for k, v in collate([items[i] for i in indices], tok.pad_token_id).items()}
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits, act = model(batch["input_ids"], batch["attention_mask"], batch["marker_pos"],
                                    batch["marker_mask"], batch["qtype"])
            logits, mask, target = logits.float(), batch["marker_mask"], batch["target"]
            k = mask.sum(-1, keepdim=True).float()
            eps = torch.randn((group,) + logits.shape, device=device) * sigma * mask
            eps = (eps - eps.sum(-1, keepdim=True) / k) * mask
            z = logits.detach().unsqueeze(0) + eps
            q = torch.softmax(z.masked_fill(~mask, -1e4), -1)
            with torch.no_grad():
                reward = proper_reward(q, target.unsqueeze(0), batch["qtype"], mask, w_sph=0.75, w_rps=1.0)
                adv = reward - reward.mean(0, keepdim=True)
                adv = adv / (adv.std() + 1e-6)
            logp = -(((z - logits.unsqueeze(0)) ** 2) * mask).sum(-1) / (2 * sigma ** 2)
            loss_rl = -(adv * logp).mean()
            loss_ce = -(target * torch.log_softmax(logits.masked_fill(~mask, -1e4), -1)).sum(-1).mean()
            loss = loss_rl + loss_ce + 0.0 * act.sum()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            if step % 50 == 0 or step == total:
                acc = (logits.masked_fill(~mask, -1e4).argmax(-1) == target.argmax(-1)).float().mean().item()
                print("  ep %d step %d/%d ce %.3f acc %.3f %.0fs" % (epoch + 1, step, total, loss_ce.item(), acc,
                                                                      time.time() - started), flush=True)
    os.makedirs(args.out, exist_ok=True)
    model.eval()
    save_file({k: v.half().contiguous().cpu() for k, v in model.state_dict().items()},
              os.path.join(args.out, "model.safetensors"))
    for sub in ("encoder", "tokenizer"):
        shutil.copytree(os.path.join(args.base, sub), os.path.join(args.out, sub), dirs_exist_ok=True)
    cfg.update(model_name="opencore-reflex", fine_tuned_from=os.path.basename(os.path.normpath(args.base)),
               temperature=[1.0, 1.0, 1.0], temperature_by_options={},
               reflex_training={"items": len(items), "epochs": args.epochs, "steps": total,
                                "hours": round((time.time() - started) / 3600, 3)})
    json.dump(cfg, open(os.path.join(args.out, "rl_agent_config.json"), "w"), indent=2)
    print("saved", args.out, flush=True)


if __name__ == "__main__":
    main()
