"""See a checkerboard Snake game on screen (Google Snake palette and clones).

Finds the board, measures the cell grid from the checker pattern, classifies each
cell (snake / apple / empty), locates the head by its eyes, and orders the body by
walking adjacent snake cells from the head (tracked across frames when the body
touches itself). Output feeds snake.analyze/describe exactly like the simulator.
"""
from __future__ import annotations

import ctypes
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from PIL import ImageGrab

try:  # physical pixels, so window rectangles and captures agree on scaled displays
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

BOARD_COLORS = np.array([[0xAA, 0xD7, 0x51], [0xA2, 0xD1, 0x49]], dtype=np.int16)


@dataclass
class Board:
    left: int
    top: int
    cell_w: float
    cell_h: float
    cols: int
    rows: int


@dataclass
class Frame:
    board: Board
    snake: set = field(default_factory=set)
    apple: tuple | None = None
    head: tuple | None = None


def grab(rect):
    left, top, right, bottom = rect
    return np.asarray(ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True).convert("RGB"), dtype=np.int16)


def _is_board(pixels, tolerance=18):
    diff = np.abs(pixels[..., None, :] - BOARD_COLORS[None, None, :, :]).max(-1)
    return diff.min(-1) <= tolerance


def find_board(pixels):
    """Bounding box of the checkerboard and its cell size, from the colour runs."""
    mask = _is_board(pixels)
    rows = np.where(mask.sum(1) > mask.shape[1] * 0.2)[0]
    cols = np.where(mask.sum(0) > mask.shape[0] * 0.2)[0]
    if len(rows) < 20 or len(cols) < 20:
        return None
    top, bottom, left, right = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1

    def period(line):
        light = np.abs(line - BOARD_COLORS[0]).max(-1) <= 6
        dark = np.abs(line - BOARD_COLORS[1]).max(-1) <= 6
        kind = np.where(light, 1, np.where(dark, 2, 0))
        runs, current, length = [], None, 0
        for value in kind:
            if value == current:
                length += 1
                continue
            if current in (1, 2) and length > 3:
                runs.append(length)
            current, length = value, 1
        return float(np.median(runs)) if runs else None

    samples_x = [period(pixels[y, left:right]) for y in np.linspace(top + 3, bottom - 4, 9).astype(int)]
    samples_y = [period(pixels[top:bottom, x]) for x in np.linspace(left + 3, right - 4, 9).astype(int)]
    cell_w = np.median([s for s in samples_x if s]) if any(samples_x) else None
    cell_h = np.median([s for s in samples_y if s]) if any(samples_y) else None
    if not cell_w or not cell_h:
        return None
    return Board(int(left), int(top), float(cell_w), float(cell_h),
                 int(round((right - left) / cell_w)), int(round((bottom - top) / cell_h)))


def read_cells(pixels, board):
    snake, apple, eyes = set(), None, []
    for cx in range(board.cols):
        for cy in range(board.rows):
            x0 = int(board.left + cx * board.cell_w)
            y0 = int(board.top + cy * board.cell_h)
            patch = pixels[y0 + int(board.cell_h * 0.3):y0 + int(board.cell_h * 0.7),
                           x0 + int(board.cell_w * 0.3):x0 + int(board.cell_w * 0.7)]
            if patch.size == 0:
                continue
            r, g, b = patch.reshape(-1, 3).mean(0)
            whole = pixels[y0 + 2:y0 + int(board.cell_h) - 2, x0 + 2:x0 + int(board.cell_w) - 2].reshape(-1, 3)
            white = ((whole > 220).all(-1)).mean() if whole.size else 0
            red_pixels = ((patch[..., 0] > 160) & (patch[..., 0] > patch[..., 1] + 50)
                          & (patch[..., 2] < 130)).mean()
            if red_pixels > 0.2:
                apple = (cx, cy)
            elif b > 150 and b > r + 50:
                snake.add((cx, cy))
                if white > 0.02:
                    eyes.append((white, (cx, cy)))
            elif white > 0.05 and (b > r):
                snake.add((cx, cy))
                eyes.append((white, (cx, cy)))
    head = max(eyes)[1] if eyes else None
    return snake, apple, head


class SnakeTracker:
    """Keeps the ordered body across frames; the screen only shows an unordered set of cells."""

    def __init__(self):
        self.body = None

    def update(self, snake, head):
        if not snake or head is None or head not in snake:
            return self.body
        if self.body and head == self.body[0] and set(self.body) == snake:
            return self.body
        if self.body and head != self.body[0] and self._adjacent(head, self.body[0]):
            grown = len(snake) > len(self.body)
            candidate = [head] + (self.body if grown else self.body[:-1])
            if set(candidate) == snake:
                self.body = candidate
                return self.body
        self.body = self._walk(snake, head)
        return self.body

    @staticmethod
    def _adjacent(a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1

    def _walk(self, snake, head):
        """Order cells from the head along the body, preferring the previous order when ambiguous."""
        previous = {cell: index for index, cell in enumerate(self.body or [])}
        order, seen, current = [head], {head}, head
        while True:
            options = [c for c in ((current[0] + 1, current[1]), (current[0] - 1, current[1]),
                                   (current[0], current[1] + 1), (current[0], current[1] - 1))
                       if c in snake and c not in seen]
            if not options:
                break
            options.sort(key=lambda c: (previous.get(c, 10_000), self._degree(c, snake, seen)))
            current = options[0]
            order.append(current)
            seen.add(current)
        return order + [c for c in snake if c not in seen]

    def _degree(self, cell, snake, seen):
        return sum((n in snake and n not in seen) for n in ((cell[0] + 1, cell[1]), (cell[0] - 1, cell[1]),
                                                            (cell[0], cell[1] + 1), (cell[0], cell[1] - 1)))


def look(rect, board=None):
    """One frame: returns Frame with board geometry, snake cells, apple and head."""
    pixels = grab(rect)
    board = board or find_board(pixels)
    if board is None:
        return None
    snake, apple, head = read_cells(pixels, board)
    return Frame(board, snake, apple, head)


def heading_of(body, fallback="right"):
    if not body or len(body) < 2:
        return fallback
    (hx, hy), (nx, ny) = body[0], body[1]
    return {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}.get((hx - nx, hy - ny), fallback)
