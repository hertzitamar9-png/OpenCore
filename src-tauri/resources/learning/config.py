"""Finite, explicit training configuration shared by planning and execution."""
from copy import deepcopy
import math
import re

DEFAULT_CONFIG = {
    "method": "sft", "precision": "bf16-lora", "epochs": 1.0,
    "maxSteps": 100, "learningRate": 0.0002, "loraRank": 16,
    "loraAlpha": 32, "loraDropout": 0.0, "batchSize": 1,
    "gradientAccumulation": 4, "maxSeqLength": 1024,
    "optimizer": "adamw_torch", "checkpointEvery": 25, "seed": 3407,
    "maxMinutes": 30.0, "maxDiskBytes": 10 * 1024**3,
    "minimumImprovement": 0.01, "minEvaluationSamples": 8,
    "maxRegression": 0.0, "dpoBeta": 0.1, "regressionGates": [],
}

INTEGER_BOUNDS = {
    "maxSteps": (1, 100000), "loraRank": (1, 256), "loraAlpha": (1, 4096),
    "batchSize": (1, 64), "gradientAccumulation": (1, 1024),
    "maxSeqLength": (32, 131072), "checkpointEvery": (1, 100000),
    "seed": (0, 2**31 - 1), "maxDiskBytes": (1024**2, 1024**4),
    "minEvaluationSamples": (2, 100000),
}
NUMBER_BOUNDS = {
    "epochs": (0, 1000, False), "learningRate": (0, 1, False),
    "loraDropout": (0, 0.5, True), "maxMinutes": (0, 10080, False),
    "minimumImprovement": (0, 1000000, True), "maxRegression": (0, 1000000, True),
    "dpoBeta": (0, 10, False),
}


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_config(value):
    if not isinstance(value, dict):
        raise ValueError("config must be a JSON object")
    unknown = set(value) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError("unknown config keys: " + ", ".join(sorted(unknown)))
    result = deepcopy(DEFAULT_CONFIG)
    result.update(value)
    for key, choices in {"method": ("sft", "dpo"), "precision": ("bf16-lora", "qlora-4bit"), "optimizer": ("adamw_torch", "adamw_torch_fused", "adamw_8bit", "paged_adamw_8bit")}.items():
        if result[key] not in choices:
            raise ValueError(f"{key} must be one of {', '.join(choices)}")
    for key, (low, high) in INTEGER_BOUNDS.items():
        item = result[key]
        if type(item) is not int or not low <= item <= high:
            raise ValueError(f"{key} must be an integer in [{low}, {high}]")
    for key, (low, high, inclusive) in NUMBER_BOUNDS.items():
        item = result[key]
        if not finite_number(item) or item > high or (item < low if inclusive else item <= low):
            raise ValueError(f"{key} must be finite in {'[' if inclusive else '('}{low}, {high}]")
        result[key] = float(item)
    gates = result["regressionGates"]
    if not isinstance(gates, list) or len(gates) > 32:
        raise ValueError("regressionGates must be an array with at most 32 gates")
    seen = set()
    for gate in gates:
        if not isinstance(gate, dict) or set(gate) != {"metric", "direction", "maximumRegression"}:
            raise ValueError("each regression gate requires metric, direction and maximumRegression")
        metric = gate["metric"]
        if not isinstance(metric, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_/.-]{0,127}", metric) or metric in seen:
            raise ValueError("regression metric must be a unique nonempty metric name")
        seen.add(metric)
        if gate["direction"] not in ("lower", "higher") or not finite_number(gate["maximumRegression"]) or gate["maximumRegression"] < 0:
            raise ValueError("regression direction/maximumRegression is invalid")
    return result
