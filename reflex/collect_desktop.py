"""Collect UI Automation trees from built-in Windows apps for Reflex desktop training.

Opens each app (or Settings page), dumps only the window it opened with
dump_uia.ps1 (same shape as OpenCore's inspect), and closes it gracefully.
Windows are recognised by handle, not title, because the system UI may be in any
language. Apps that restore user documents (Notepad) and personal apps are never
opened or inspected; the already-open OpenCore window is read but left open.
"""
import json
import os
import subprocess
import sys
import time

import win32con
import win32gui

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1]
TARGETS = [
    "calc.exe",
    "mspaint.exe",
    "charmap.exe",
    "control.exe",
    "explorer.exe C:\\Windows\\Web",
    "explorer.exe C:\\Windows\\Media",
    "explorer.exe C:\\Windows\\Fonts",
    "start ms-settings:display",
    "start ms-settings:sound",
    "start ms-settings:bluetooth",
    "start ms-settings:personalization-background",
    "start ms-settings:personalization-colors",
    "start ms-settings:dateandtime",
    "start ms-settings:regionlanguage",
    "start ms-settings:mousetouchpad",
    "start ms-settings:powersleep",
    "start ms-settings:windowsupdate",
    "start ms-settings:privacy",
    "start ms-settings:network-status",
    "start ms-settings:notifications",
    "start ms-clock:",
]


def visible():
    out = {}
    win32gui.EnumWindows(lambda h, _: out.__setitem__(h, win32gui.GetWindowText(h))
                         if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) else None, None)
    return out


def dump(handle, path, source, dumps):
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    os.path.join(HERE, "dump_uia.ps1"), "-Out", path, "-Handles", str(handle)],
                   capture_output=True, timeout=120)
    try:
        for window in json.load(open(path, encoding="utf-8-sig")):
            window["source"] = source
            dumps.append(window)
            print("%-42s %-40s %3d elements" % (source[:42], window["title"][:40], len(window["elements"])), flush=True)
    except Exception as error:
        print("dump failed for", source, error, flush=True)


def main():
    dumps = []
    preexisting = set(visible())
    folder = os.path.dirname(OUT)
    for hwnd, title in visible().items():  # OpenCore's own window: read, never closed
        if title == "OpenCore":
            dump(hwnd, os.path.join(folder, "uia-opencore.json"), "OpenCore", dumps)
    settings = None
    for index, command in enumerate(TARGETS):
        before = set(visible())
        subprocess.Popen(command, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=subprocess.DETACHED_PROCESS)
        time.sleep(4.5)
        opened = [h for h in visible() if h not in before and h not in preexisting]
        is_settings = command.startswith("start ms-settings")
        if is_settings:
            settings = settings or (opened[0] if opened else None)
            handles = [settings] if settings else []
        else:
            handles = opened[:1]
        for handle in handles:
            dump(handle, os.path.join(folder, "uia-%02d.json" % index), command, dumps)
        last_settings = is_settings and not (index + 1 < len(TARGETS) and TARGETS[index + 1].startswith("start ms-settings"))
        to_close = opened if not is_settings else ([settings] if last_settings and settings else [])
        for handle in to_close:
            if handle not in preexisting:
                win32gui.PostMessage(handle, win32con.WM_CLOSE, 0, 0)
        time.sleep(1.0)
    json.dump(dumps, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("windows:", len(dumps), "elements:", sum(len(d["elements"]) for d in dumps), flush=True)


if __name__ == "__main__":
    main()
