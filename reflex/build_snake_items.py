"""Snake training items for OpenCore Reflex, labelled by the expert policy.

usage: build_snake_items.py BASE_MODEL_DIR OUT.pt [states=60000]
States come from expert games with random detours, so the model also sees crowded,
risky positions the expert alone rarely reaches. Board sizes vary so a live game's
grid does not have to match the simulator's.
"""
import os
import random
import sys

import torch
from transformers import AutoTokenizer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "laya_core"))
import snake  # noqa: E402
from rl_common import QTYPES, build_sequence  # noqa: E402

SIZES = [(17, 15), (17, 15), (17, 15), (10, 9), (15, 15), (20, 20), (24, 21), (12, 12)]


def states(count, seed=7):
    rng = random.Random(seed)
    game_seed = 0
    while True:
        width, height = rng.choice(SIZES)
        game = snake.Game(width, height, seed=seed * 100_003 + game_seed)
        game_seed += 1
        explore = rng.choice([0.0, 0.05, 0.15, 0.3])
        while game.alive and game.apple is not None and game.steps < 4000:
            facts = snake.analyze(game.body, game.apple, game.heading, width, height)
            best = snake.expert_move(game.body, game.apple, game.heading, width, height)
            yield snake.describe(game.body, game.apple, game.heading, width, height), snake.question(facts), best
            count -= 1
            if count <= 0:
                return
            open_moves = [m for m, f in facts.items() if "blocked" not in f]
            move = rng.choice(open_moves) if open_moves and rng.random() < explore else best
            game.step(move)


def main():
    base, out = sys.argv[1], sys.argv[2]
    total = int(sys.argv[3]) if len(sys.argv) > 3 else 60_000
    tok = AutoTokenizer.from_pretrained(os.path.join(base, "tokenizer"))
    rng = random.Random(11)
    items, labels = [], {}
    for text, q, best in states(total):
        keys = list(q["criteria"])
        order = list(range(len(keys)))
        rng.shuffle(order)  # position must carry no information
        internal = {"t": "choice", "ins": q["instructions"], "crit": q["criteria"]}
        ids, markers = build_sequence(tok, text, internal, 1024, 512, option_order=order)
        target = [1.0 if keys[i] == best else 0.0 for i in order]
        items.append({"ids": ids, "markers": markers, "qtype": QTYPES["choice"], "target": target,
                      "label": target.index(1.0), "task": "snake"})
        labels[best] = labels.get(best, 0) + 1
    torch.save(items, out)
    lengths = [len(item["ids"]) for item in items]
    print("snake items %d | labels %s | tokens mean %.0f max %d" % (len(items), labels, sum(lengths) / len(lengths), max(lengths)))


if __name__ == "__main__":
    main()
