"""OpenCore Reflex server: the fast "System One" part of OpenCore's computer use.

usage: server.py MODEL_DIR [--port 8815]

POST /decide        {"state": ..., "questions": {...}}        typed choices with probabilities
POST /desktop/pick  {"goal", "title", "elements", "recent"}   which control does the goal mean
POST /snake/play    {"hwnd": int, "seconds": float}           play a visible Snake game live
GET  /health
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import desktop_task as task

app = FastAPI(title="OpenCore Reflex")
MODEL = None
MODEL_DIR: str | None = None
PLAYING = threading.Lock()
LOADING = threading.Lock()
LAST_ACTIVE = time.monotonic()
IDLE_SECONDS = 120


def touch() -> None:
    global LAST_ACTIVE
    LAST_ACTIVE = time.monotonic()


def reflex_model() -> Reflex:
    """Load the text controller only if an accessibility or Snake action needs it."""
    global MODEL
    touch()
    with LOADING:
        if MODEL is None:
            if MODEL_DIR is None:
                raise RuntimeError("Reflex model is not configured")
            from reflex_model import Reflex
            model = Reflex(MODEL_DIR)
            model.decide("", {"warmup": {"type": "choice", "criteria": {"a": "Alpha", "b": "Beta"},
                                        "instructions": "Choose Beta"}})
            MODEL = model
    return MODEL


def idle_watchdog() -> None:
    """Exit the child server after inactivity; the app restarts it on next use."""
    while True:
        time.sleep(10)
        if time.monotonic() - LAST_ACTIVE > IDLE_SECONDS and not PLAYING.locked() and not LOADING.locked():
            os._exit(0)


class DecideRequest(BaseModel):
    state: dict | str
    questions: dict


class PickRequest(BaseModel):
    goal: str
    title: str = ""
    elements: list[dict]
    recent: list = []


class PlayRequest(BaseModel):
    hwnd: int
    seconds: float = 90.0


@app.get("/health")
def health():
    return {"ready": True, "text_model_ready": MODEL is not None,
            "model": MODEL.model_dir if MODEL else MODEL_DIR,
            "device": str(MODEL.agent.device) if MODEL else None}


@app.post("/decide")
def decide(request: DecideRequest):
    return reflex_model().decide(request.state, request.questions)


@app.post("/desktop/pick")
def pick(request: PickRequest):
    table = task.candidates(request.elements)
    if not table:
        return {"operation": "BLOCKED", "confidence": 1.0, "reason": "no actionable controls"}
    goal = task.clean(request.goal).casefold()
    named = [element for element in request.elements
             if element.get("controlType") in task.ACTIONABLE and element.get("enabled", True)
             and len(task.clean(element.get("name"))) >= 3
             and re.search(r"(?<!\w)" + re.escape(task.clean(element.get("name")).casefold()) + r"(?!\w)", goal)]
    if len(named) == 1 and str(named[0]["elementId"]) not in table:
        table = task.candidates(request.elements, keep=named[0]["elementId"])
    result = reflex_model().decide(task.state(request.title, request.elements, request.recent),
                          task.questions(request.goal, table))
    answers = result["answers"]
    choice = answers["target"]["choice"]
    ranked = sorted(answers["target"]["probabilities"].items(), key=lambda kv: -kv[1])[:3]
    return {"operation": answers["operation"]["choice"],
            "operation_confidence": answers["operation"]["confidence"],
            "elementId": int(choice), "label": table[choice],
            "confidence": answers["target"]["confidence"],
            "alternatives": [{"elementId": int(k), "label": table[k], "probability": p} for k, p in ranked],
            "latency_ms": result["latency_ms"]}


@app.post("/stop")
def stop():
    """Cancel a running game when OpenCore's run is stopped."""
    if PLAYING.locked():
        import live_snake
        live_snake.STOP.set()
    touch()
    return {"stopping": PLAYING.locked()}


@app.post("/snake/play")
def snake_play(request: PlayRequest):
    import live_snake  # imported lazily: pulls in screen capture only when a game is played
    if not PLAYING.acquire(blocking=False):
        raise HTTPException(409, "A game is already being played")
    try:
        return live_snake.play(reflex_model(), request.hwnd, max(5.0, min(request.seconds, 600.0)), log=lambda *_: None)
    except RuntimeError as error:
        raise HTTPException(422, str(error))
    finally:
        PLAYING.release()


def main():
    global MODEL_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("--port", type=int, default=8815)
    args = parser.parse_args()
    MODEL_DIR = args.model
    threading.Thread(target=idle_watchdog, name="reflex-idle-release", daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())
