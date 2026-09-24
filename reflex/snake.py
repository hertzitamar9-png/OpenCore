"""Snake for OpenCore Reflex: simulator, expert policy and the model's state/question.

The same `describe()` state is produced two ways: here from the simulator (training
data), and by `perception.py` from a real screen. The model only ever sees that
text, so what it learns in simulation is what it uses live.
"""
from __future__ import annotations

import random
from collections import deque

MOVES = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left"}
QUESTION = {
    "type": "choice",
    "instructions": ("Snake: choose the next move. Eat the apple, never hit a wall or the body, "
                     "and keep enough open space to reach your own tail."),
    "criteria": {"up": None, "down": None, "left": None, "right": None},
}


class Game:
    def __init__(self, width=17, height=15, seed=None):
        self.width, self.height = width, height
        self.rng = random.Random(seed)
        mid = height // 2
        self.body = deque([(4, mid), (3, mid), (2, mid)])  # head first
        self.heading = "right"
        self.apple = None
        self.alive, self.apples, self.steps = True, 0, 0
        self.place_apple()

    def place_apple(self):
        free = [(x, y) for x in range(self.width) for y in range(self.height) if (x, y) not in self.body]
        self.apple = self.rng.choice(free) if free else None

    def step(self, move):
        if move == OPPOSITE[self.heading] and len(self.body) > 1:
            move = self.heading  # real games ignore reversing into yourself
        dx, dy = MOVES[move]
        hx, hy = self.body[0]
        head = (hx + dx, hy + dy)
        grows = head == self.apple
        body = list(self.body)[:len(self.body) if grows else -1]
        if not (0 <= head[0] < self.width and 0 <= head[1] < self.height) or head in body:
            self.alive = False
            return
        self.body.appendleft(head)
        if not grows:
            self.body.pop()
        else:
            self.apples += 1
            self.place_apple()
        self.heading = move
        self.steps += 1


def _neighbors(cell, width, height):
    x, y = cell
    for name, (dx, dy) in MOVES.items():
        nx, ny = x + dx, y + dy
        if 0 <= nx < width and 0 <= ny < height:
            yield name, (nx, ny)


def _bfs(start, goal, blocked, width, height):
    """Shortest path length and first move from start to goal, or (None, None)."""
    if start == goal:
        return 0, None
    seen, queue = {start}, deque([(start, 0, None)])
    while queue:
        cell, dist, first = queue.popleft()
        for name, nxt in _neighbors(cell, width, height):
            if nxt in seen or nxt in blocked:
                continue
            if nxt == goal:
                return dist + 1, first or name
            seen.add(nxt)
            queue.append((nxt, dist + 1, first or name))
    return None, None


def _space(start, blocked, width, height, cap=10_000):
    seen, queue = {start}, deque([start])
    while queue and len(seen) < cap:
        cell = queue.popleft()
        for _, nxt in _neighbors(cell, width, height):
            if nxt not in seen and nxt not in blocked:
                seen.add(nxt)
                queue.append(nxt)
    return len(seen)


def analyze(body, apple, heading, width, height):
    """Per-move facts a player can see: blocked, open space, apple distance, tail reachable."""
    body = list(body)
    head, facts = body[0], {}
    for move, (dx, dy) in MOVES.items():
        nxt = (head[0] + dx, head[1] + dy)
        if move == OPPOSITE[heading] and len(body) > 1:
            facts[move] = {"blocked": "reverse"}
            continue
        if not (0 <= nxt[0] < width and 0 <= nxt[1] < height):
            facts[move] = {"blocked": "wall"}
            continue
        eats = nxt == apple
        after = [nxt] + body[:len(body) if eats else -1]
        if nxt in after[1:]:
            facts[move] = {"blocked": "body"}
            continue
        blocked = set(after[1:-1])  # the tail moves away next step
        space = _space(nxt, set(after[1:]), width, height)
        tail_dist, _ = _bfs(nxt, after[-1], blocked, width, height)
        apple_dist = 0 if eats else _bfs(nxt, apple, set(after[1:]), width, height)[0] if apple else None
        facts[move] = {"space": space, "apple": apple_dist, "tail": tail_dist is not None or len(after) < 3}
    return facts


def expert_move(body, apple, heading, width, height):
    """Take the shortest route to the apple when it keeps a way back to the tail; otherwise survive."""
    body = list(body)
    facts = analyze(body, apple, heading, width, height)
    open_moves = {m: f for m, f in facts.items() if "blocked" not in f}
    if not open_moves:
        return heading
    safe = {m: f for m, f in open_moves.items() if f["tail"] and f["apple"] is not None}
    if safe:
        return min(safe, key=lambda m: (safe[m]["apple"], -safe[m]["space"]))
    tail_ok = {m: f for m, f in open_moves.items() if f["tail"]}
    pool = tail_ok or open_moves
    return max(pool, key=lambda m: (pool[m]["space"], -(pool[m]["apple"] if pool[m]["apple"] is not None else 999)))


def describe(body, apple, heading, width, height):
    """The board summary the Reflex model reads (identical for simulator and screen)."""
    body = list(body)
    head = body[0]
    lines = ["game: snake | board %dx%d | length %d | heading %s" % (width, height, len(body), heading)]
    if apple is not None:
        dx, dy = apple[0] - head[0], apple[1] - head[1]
        horiz = "%d right" % dx if dx > 0 else "%d left" % -dx if dx < 0 else "same column"
        vert = "%d down" % dy if dy > 0 else "%d up" % -dy if dy < 0 else "same row"
        lines.append("apple: %s, %s of the head" % (horiz, vert))
    else:
        lines.append("apple: not visible")
    return "\n".join(lines)


def option_text(fact):
    if "blocked" in fact:
        return "blocked by %s" % fact["blocked"]
    apple = "apple in %d" % fact["apple"] if fact["apple"] is not None else "apple unreachable"
    return "open, space %d, %s, tail %s" % (fact["space"], apple, "reachable" if fact["tail"] else "cut off")


def question(facts):
    """One choice question whose options carry their own facts, scored at each option's marker."""
    return {"type": "choice", "instructions": QUESTION["instructions"],
            "criteria": {move: option_text(facts[move]) for move in ("up", "down", "left", "right")}}


def play(policy, games=20, width=17, height=15, max_steps=3000, seed=0):
    """Average apples for a policy(state_text, question, game) -> move."""
    results = []
    for index in range(games):
        game = Game(width, height, seed=seed + index)
        while game.alive and game.steps < max_steps and game.apple is not None:
            facts = analyze(game.body, game.apple, game.heading, width, height)
            text = describe(game.body, game.apple, game.heading, width, height)
            game.step(policy(text, question(facts), game))
        results.append(game.apples)
    return sum(results) / len(results), results
