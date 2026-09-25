"""Collect grounding samples from built-in Windows apps in the user's own UI language.

usage: collect_desktop_grounding.py OUT_DIR SPLIT TARGETS_FILE

For each target (an app, Settings page or Control Panel applet) the window it opens is
brought to the front, pinned on top for the capture, photographed at its visible
(DWM) bounds like OpenCore's window capture, and read with UI Automation. Every named
control that is unique in that window becomes a sample whose box is in screenshot
pixels. Nothing is clicked or typed; each window is closed with WM_CLOSE afterwards.
Personal apps and apps that reopen user files (Notepad, Photos, mail) are never opened.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

import win32con
import win32gui
from PIL import ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
ctypes.windll.shcore.SetProcessDpiAwareness(2)
KINDS = {"Button", "MenuItem", "TabItem", "ListItem", "Hyperlink", "CheckBox", "RadioButton", "ComboBox",
         "Edit", "TreeItem", "SplitButton", "Text", "Slider", "Spinner", "DataItem", "HeaderItem"}


def visible():
    found = {}
    win32gui.EnumWindows(lambda h, _: found.__setitem__(h, win32gui.GetWindowText(h))
                         if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) else None, None)
    return found


def dwm_bounds(hwnd):
    rect = wintypes.RECT()
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def elements(hwnd, scratch):
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    os.path.join(HERE, "dump_uia.ps1"), "-Out", scratch, "-Handles", str(hwnd)],
                   capture_output=True, timeout=120)
    windows = json.load(open(scratch, encoding="utf-8-sig"))
    return windows[0] if windows else None


def capture(hwnd, out_dir, split, source, sink):
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
    try:
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
    except win32gui.error:
        print(f"skip {source}: the window runs as administrator", flush=True)
        return 0
    time.sleep(1.2)
    try:
        left, top, right, bottom = dwm_bounds(hwnd)
        if right - left < 200 or bottom - top < 150:
            print(f"skip {source}: window is only {right - left}x{bottom - top}", flush=True)
            return 0
        image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True).convert("RGB")
        window = elements(hwnd, os.path.join(out_dir, "uia-scratch.json"))
    finally:
        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    if not window:
        print(f"skip {source}: UI Automation returned no tree", flush=True)
        return 0
    rows = []
    for row in window["elements"][1:]:
        bounds = row.get("bounds")
        name = (row.get("name") or "").replace("‏", "").replace("‎", "").replace("‪", "").replace("‬", "").strip()
        if not bounds or row.get("controlType") not in KINDS or not (2 <= len(name) <= 50):
            continue
        x1, y1 = bounds["left"] - left, bounds["top"] - top
        x2, y2 = x1 + bounds["width"], y1 + bounds["height"]
        if x1 < 0 or y1 < 0 or x2 > image.width or y2 > image.height or bounds["width"] < 6 or bounds["height"] < 6:
            continue
        rows.append({"name": name, "controlType": row["controlType"], "box": [x1, y1, x2, y2]})
    counts = {}
    for row in rows:
        counts[row["name"]] = counts.get(row["name"], 0) + 1
    unique = [row for row in rows if counts[row["name"]] == 1]
    if len(unique) < 3:
        print(f"skip {source}: {len(unique)} uniquely named controls", flush=True)
        return 0
    key = hashlib.sha1(f"{source} {window['title']} {image.width}x{image.height}".encode()).hexdigest()[:14]
    path = f"images/{key}.png"
    image.save(os.path.join(out_dir, path))
    for row in unique:
        sink.write(json.dumps({"image": path, "source": source, "title": window["title"], "split": split,
                               "size": [image.width, image.height], "kind": "desktop",
                               "lang": "he" if any("֐" <= c <= "׿" for c in row["name"]) else "other",
                               "tag": "", "role": row["controlType"].lower(), **row}, ensure_ascii=False) + "\n")
    print(f"{source[:40]:40s} {window['title'][:30]:30s} {len(unique):3d} controls", flush=True)
    return len(unique)


def main():
    out_dir, split, targets_file = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
    targets = [line.strip() for line in open(targets_file, encoding="utf-8")
               if line.strip() and not line.startswith("#")]
    preexisting = set(visible())
    total, settings = 0, None
    with open(os.path.join(out_dir, f"{split}.jsonl"), "a", encoding="utf-8") as sink:
        for index, command in enumerate(targets):
            before = set(visible())
            subprocess.Popen(command, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=subprocess.DETACHED_PROCESS)
            time.sleep(4.5)
            opened = [h for h in visible() if h not in before and h not in preexisting]
            if not opened and not command.startswith("start ms-settings"):
                print(f"skip {command}: no new window appeared", flush=True)
            # Settings is one window that each ms-settings: link navigates, so keep it open
            # across consecutive Settings pages and close it after the last one.
            is_settings = command.startswith("start ms-settings")
            if is_settings and (settings is None or not win32gui.IsWindow(settings)):
                settings = opened[0] if opened else None
            handles = ([settings] if settings else []) if is_settings else opened[:1]
            for hwnd in handles:
                try:
                    total += capture(hwnd, out_dir, split, command, sink)
                except Exception as error:
                    print("capture failed", command, error, flush=True)
            next_is_settings = index + 1 < len(targets) and targets[index + 1].startswith("start ms-settings")
            to_close = [h for h in opened if h != settings]
            if is_settings and not next_is_settings and settings:
                to_close.append(settings)
                settings = None
            for hwnd in to_close:
                if hwnd not in preexisting:
                    try:
                        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    except win32gui.error:
                        print(f"could not close {win32gui.GetWindowText(hwnd)!r} (administrator window)", flush=True)
            time.sleep(1.5)
    print("samples", total, flush=True)


if __name__ == "__main__":
    main()
