"""Load OpenCore Reflex once and answer typed decisions fast (bf16 weights on the GPU)."""
from __future__ import annotations

import os
import sys
import time

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "laya_core"))
from rl_agent_api import RLAgent  # noqa: E402


class Reflex:
    def __init__(self, model_dir, device=None):
        self.agent = RLAgent(model_dir, device=device)
        if self.agent.device.type == "cuda":
            self.agent.model.to(torch.bfloat16)  # half the memory traffic; autocast already runs bf16
        self.model_dir = model_dir
        self.last_ms = 0.0

    def decide(self, state, questions):
        """questions: {id: {"type": "choice", "instructions": ..., "criteria": {...}}} -> answers."""
        started = time.perf_counter()
        result = self.agent.system_one(state, questions)
        self.last_ms = (time.perf_counter() - started) * 1000
        result["latency_ms"] = round(self.last_ms, 1)
        return result

    def choose(self, state, question):
        answer = self.decide(state, {"q": question})["answers"]["q"]
        return answer["choice"], answer.get("confidence", 0.0), answer.get("probabilities", {})
