"""Play simulated Snake with a Reflex checkpoint and compare with the expert.

usage: eval_snake.py MODEL_DIR [games=10]
"""
import sys
import time

import snake
from reflex_model import Reflex


def main():
    model = Reflex(sys.argv[1])
    games = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    calls, spent = [0], [0.0]

    def policy(text, question, game):
        started = time.perf_counter()
        move, _, _ = model.choose(text, question)
        spent[0] += time.perf_counter() - started
        calls[0] += 1
        return move

    avg, results = snake.play(policy, games=games, seed=1000)
    print("reflex: avg apples %.1f  min %d  max %d  | %.1f ms per move over %d moves"
          % (avg, min(results), max(results), spent[0] / max(1, calls[0]) * 1000, calls[0]))
    expert_avg, expert_results = snake.play(
        lambda text, q, g: snake.expert_move(g.body, g.apple, g.heading, g.width, g.height), games=games, seed=1000)
    print("expert: avg apples %.1f  min %d  max %d" % (expert_avg, min(expert_results), max(expert_results)))


if __name__ == "__main__":
    main()
