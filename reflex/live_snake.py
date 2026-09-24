"""Play a Snake game in a real window with OpenCore Reflex.

usage: live_snake.py MODEL_DIR "window title part" [seconds=90]

The game window is focused and receives real arrow-key input. Stops on game over,
the time limit, or a double Escape.
"""
from __future__ import annotations

import ctypes
import json
import re
import sys
import threading
import time

import win32api
import win32con
import win32gui

import perception
import snake
from reflex_model import Reflex

STOP = threading.Event()  # set by the Reflex server when OpenCore cancels the run
VK = {"up": win32con.VK_UP, "down": win32con.VK_DOWN, "left": win32con.VK_LEFT, "right": win32con.VK_RIGHT}


def find_window(title_part):
    found = []

    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and title_part.lower() in win32gui.GetWindowText(hwnd).lower():
            found.append(hwnd)
    win32gui.EnumWindows(visit, None)
    return found[0] if found else None


def page_window(hwnd):
    """Chromium's render widget receives keyboard input for the page."""
    target = []

    def visit(child, _):
        if win32gui.GetClassName(child) == "Chrome_RenderWidgetHostHWND":
            target.append(child)
    win32gui.EnumChildWindows(hwnd, visit, None)
    return target[0] if target else hwnd


def press(target, move):
    if win32gui.GetForegroundWindow() != target:
        raise RuntimeError("Snake window is not foreground; keyboard input was not sent")
    vk = VK[move]
    scan = win32api.MapVirtualKey(vk, 0)
    flags = win32con.KEYEVENTF_EXTENDEDKEY
    win32api.keybd_event(vk, scan, flags, 0)
    win32api.keybd_event(vk, scan, flags | win32con.KEYEVENTF_KEYUP, 0)


def client_rect(hwnd):
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    x, y = win32gui.ClientToScreen(hwnd, (left, top))
    return x, y, x + right - left, y + bottom - top


def score_of(hwnd):
    match = re.search(r"score (\d+)", win32gui.GetWindowText(hwnd))
    return int(match.group(1)) if match else None


def keep_visible(hwnd, on):
    """Pin the game above other windows while playing, without taking keyboard focus."""
    flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST if on else win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)


def bring_forward(hwnd):
    """Browsers stop painting windows they consider hidden, so the game must be in front.

    OpenCore grants this process foreground rights (AllowSetForegroundWindow) before a
    game starts. Never synthesize an Alt press to force it: that key reaches whichever
    window is in front, and in OpenCore's window it deadlocks the UI thread when focus
    moves during key handling.
    """
    if win32gui.IsIconic(hwnd):  # a minimized window sits at -32000 and never paints
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass  # without rights the window still comes to the top below


def play(model, hwnd, seconds=90.0, log=print):
    STOP.clear()
    bring_forward(hwnd)
    keep_visible(hwnd, True)
    try:
        return _play(model, hwnd, seconds, log)
    finally:
        keep_visible(hwnd, False)


def _play(model, hwnd, seconds, log):
    target = hwnd
    rect = client_rect(hwnd)
    time.sleep(1.0)  # let the window repaint in front before the first look
    first = perception.look(rect)
    if first is None:
        raise RuntimeError("No Snake board visible in the window")
    board = first.board

    log("Model loaded and board seen. Focus the Snake window to begin.")
    focus_deadline = time.monotonic() + 30.0
    while win32gui.GetForegroundWindow() != hwnd and time.monotonic() < focus_deadline:
        time.sleep(0.05)
    if win32gui.GetForegroundWindow() != hwnd:
        raise RuntimeError("Snake window was not focused; keyboard input was not sent")
    tracker = perception.SnakeTracker()
    decisions, latencies, last_head, heading = 0, [], None, "right"
    started = time.time()
    escape_down, last_escape = False, 0.0
    press(target, "right")  # starts the game
    while time.time() - started < seconds:
        down = bool(win32api.GetAsyncKeyState(win32con.VK_ESCAPE) & 0x8000)  # same rule as the app: two presses within 0.65 s
        if down and not escape_down:
            now = time.time()
            if now - last_escape < 0.65:
                log("stopped by double Escape")
                break
            last_escape = now
        escape_down = down
        if STOP.is_set() or not win32gui.IsWindow(hwnd) or "game over" in win32gui.GetWindowText(hwnd).lower():
            break
        frame = perception.look(rect, board)
        if frame is None or frame.head is None:
            time.sleep(0.005)
            continue
        body = tracker.update(frame.snake, frame.head)
        if not body or body[0] == last_head:
            time.sleep(0.004)
            continue
        last_head = body[0]
        heading = perception.heading_of(body, heading)
        facts = snake.analyze(body, frame.apple, heading, board.cols, board.rows)
        move, confidence, _ = model.choose(snake.describe(body, frame.apple, heading, board.cols, board.rows),
                                           snake.question(facts))
        latencies.append(model.last_ms)
        decisions += 1
        if move != heading:
            press(target, move)
    return {"apples": score_of(hwnd), "decisions": decisions, "seconds": round(time.time() - started, 1),
            "grid": [board.cols, board.rows],
            "model_ms_avg": round(sum(latencies) / max(1, len(latencies)), 1),
            "model_ms_max": round(max(latencies or [0]), 1),
            "game_over": win32gui.IsWindow(hwnd) and "game over" in win32gui.GetWindowText(hwnd).lower(),
            "stopped": STOP.is_set()}


def main():
    model = Reflex(sys.argv[1])
    hwnd = find_window(sys.argv[2])
    if not hwnd:
        raise SystemExit("window not found: " + sys.argv[2])
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 90.0
    print(json.dumps(play(model, hwnd, seconds)))


if __name__ == "__main__":
    main()
