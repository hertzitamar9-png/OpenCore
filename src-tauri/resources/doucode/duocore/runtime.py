from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from typing import Any, Callable

from .selection import (
    average_candidate_scores,
    normalize_candidate,
    only_requested_list,
    parse_review,
    requested_list_count,
    review_messages,
)
from .spec import BackboneSpec, DuoCoreConfig


@dataclass
class BackboneReply:
    content: str
    message: dict[str, Any]
    raw: dict[str, Any]


def read_chat_stream(response, on_delta: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Read OpenAI-compatible SSE deltas into the complete, validated reply."""
    message: dict[str, Any] = {"role": "assistant", "content": ""}
    calls: dict[int, dict[str, Any]] = {}
    result: dict[str, Any] = {"choices": [{"index": 0, "message": message, "finish_reason": None}]}
    finished = False
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="strict") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            finished = True
            break
        if not data:
            continue
        event = json.loads(data)
        if event.get("error"):
            raise RuntimeError(f"Model stream failed: {event['error']}")
        for key in ("id", "model", "usage", "timings"):
            if event.get(key) is not None:
                result[key] = event[key]
        for choice in event.get("choices") or []:
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") or {}
            if on_delta and any(isinstance(delta.get(key), str) and delta[key] for key in ("content", "reasoning_content")):
                on_delta(delta)
            for key in ("content", "reasoning_content"):
                if isinstance(delta.get(key), str):
                    message[key] = message.get(key, "") + delta[key]
            for position, call in enumerate(delta.get("tool_calls") or []):
                index = int(call.get("index", position))
                target = calls.setdefault(index, {
                    "id": "", "type": "function", "function": {"name": "", "arguments": ""},
                })
                if call.get("id"):
                    target["id"] = call["id"]
                if call.get("type"):
                    target["type"] = call["type"]
                function = call.get("function") or {}
                for key in ("name", "arguments"):
                    if isinstance(function.get(key), str):
                        target["function"][key] += function[key]
            if choice.get("finish_reason") is not None:
                result["choices"][0]["finish_reason"] = choice["finish_reason"]
    if not finished or result["choices"][0]["finish_reason"] is None:
        raise RuntimeError("Model stream ended before completion; partial output is unverified")
    if calls:
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
    return result


class LlamaBackbone:
    def __init__(self, spec: BackboneSpec, model_path: Path):
        self.spec = spec
        self.model_path = model_path

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.spec.port}"

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/health", timeout=2) as response:
                return response.status == 200
        except Exception:
            return False

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.35,
        repeat_penalty: float = 1.08,
        json_mode: bool = False,
        json_schema: dict[str, Any] | None = None,
        feedback_embedding: list[float] | None = None,
        on_delta: Callable[[dict[str, Any]], None] | None = None,
    ) -> BackboneReply:
        body: dict[str, Any] = {
            "model": self.spec.name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 0.95,
            "repeat_penalty": repeat_penalty,
        }
        if getattr(self.spec, "reasoning_format", None):
            body["reasoning_format"] = self.spec.reasoning_format
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if on_delta is not None:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if json_schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "candidate_review", "strict": True, "schema": json_schema,
            }}
        if feedback_embedding:
            body["duocore_feedback_embedding"] = feedback_embedding
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
            body["parallel_tool_calls"] = False
        def request_chat(data: dict[str, Any]) -> dict[str, Any]:
            request = urllib.request.Request(
                self.base_url + "/v1/chat/completions",
                data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=900) as response:
                    return read_chat_stream(response, on_delta) if data.get("stream") else json.load(response)
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                if tools and error.code >= 500:
                    fallback = dict(data)
                    fallback.pop("tools", None)
                    fallback.pop("tool_choice", None)
                    fallback.pop("parallel_tool_calls", None)
                    fallback["temperature"] = 0.0
                    fallback["messages"] = [
                        *messages,
                        {"role": "user", "content": "The tool-call parser rejected the attempted action. Complete this turn directly without emitting a tool call."},
                    ]
                    retry = urllib.request.Request(
                        self.base_url + "/v1/chat/completions",
                        data=json.dumps(fallback).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(retry, timeout=900) as response:
                        return read_chat_stream(response, on_delta) if fallback.get("stream") else json.load(response)
                raise RuntimeError(f"{self.spec.name} chat HTTP {error.code}: {detail[:1200]}") from error

        raw = request_chat(body)
        message = raw["choices"][0]["message"]
        return BackboneReply(str(message.get("content") or ""), message, raw)

    def embedding(self, text: str) -> list[float]:
        body = {"model": self.spec.name, "input": text}
        request = urllib.request.Request(
            self.base_url + "/v1/embeddings",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=900) as response:
            raw = json.load(response)
        return [float(x) for x in raw["data"][0]["embedding"]]


class DuoCoreEngine:
    def __init__(self, config: DuoCoreConfig, release_root: Path):
        self.config = config
        self.release_root = release_root
        self.k2 = LlamaBackbone(
            config.k2, release_root / "backbones" / "k2" / config.k2.gguf_file
        )
        self.nanbeige = LlamaBackbone(
            config.nanbeige,
            release_root / "backbones" / "nanbeige" / config.nanbeige.gguf_file,
        )
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="duocore")

    def _collect_pair(self, left, right):
        """Run two model calls concurrently while retaining a successful peer result."""
        futures = (self.pool.submit(left), self.pool.submit(right))
        values = []
        errors = []
        for name, future in zip(("left", "right"), futures):
            try:
                values.append(future.result())
                errors.append(None)
            except Exception as error:
                values.append(None)
                errors.append(f"{type(error).__name__}: {error}")
        return tuple(values), tuple(errors)

    @staticmethod
    def _task_text(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str):
                    return content
                return json.dumps(content, ensure_ascii=False)
        return json.dumps(messages[-4:], ensure_ascii=False)

    @staticmethod
    def _candidate_from_message(message: dict[str, Any]) -> dict[str, Any] | None:
        calls = message.get("tool_calls") or []
        content = str(message.get("content") or "")
        if len(calls) > 1:
            return {"content": content, "tool_call": {"name": "__invalid_multiple_tool_calls__", "arguments": {}}}
        tool_call = None
        if calls:
            function = calls[0].get("function") or {}
            tool_call = {
                "name": function.get("name"),
                "arguments": function.get("arguments"),
            }
        candidate = normalize_candidate({"content": content, "tool_call": tool_call})
        if candidate is None:
            return {"content": content, "tool_call": {"name": "__invalid_tool_call__", "arguments": {}}}
        return candidate

    @staticmethod
    def _candidate_is_valid(
        candidate: dict[str, Any] | None,
        tools: list[dict[str, Any]] | None,
        exact_list_count: int | None,
    ) -> bool:
        if candidate is None:
            return False
        tool_call = candidate.get("tool_call")
        if tool_call is not None:
            if not tools:
                return False
            available = {
                str((tool.get("function") or {}).get("name") or "")
                for tool in tools if isinstance(tool, dict)
            }
            if tool_call["name"] not in available:
                return False
        elif not str(candidate.get("content") or "").strip():
            return False
        if exact_list_count is not None and tool_call is None:
            content = str(candidate.get("content") or "").strip()
            if only_requested_list(content, exact_list_count) != content:
                return False
        return True

    @staticmethod
    def _same_candidate(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(
            right, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @staticmethod
    def _message_for_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": candidate["content"]}
        tool_call = candidate.get("tool_call")
        if tool_call is not None:
            message["tool_calls"] = [{
                "id": f"call_duocore_{uuid.uuid4().hex}",
                "type": "function",
                "function": {
                    "name": tool_call["name"],
                    "arguments": json.dumps(tool_call["arguments"], ensure_ascii=False, separators=(",", ":")),
                },
            }]
        return message

    def _review(
        self,
        evaluator: LlamaBackbone,
        messages: list[dict[str, Any]],
        candidates: dict[str, dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        swapped: bool,
    ):
        first, second = (("Nanbeige", "K2") if swapped else ("K2", "Nanbeige"))
        prompt = review_messages(messages, candidates[first], candidates[second], tools)
        reply = evaluator.chat(prompt, max_tokens=160, temperature=0.0, json_mode=True)
        parsed = parse_review(reply.content)
        if parsed is None:
            raise ValueError(f"{evaluator.spec.name} returned an invalid candidate score")
        return parsed, swapped, self._usage_for_reply(reply)

    @staticmethod
    def _usage_for_reply(reply: BackboneReply | None) -> dict[str, int]:
        usage = reply.raw.get("usage") if reply is not None else None
        usage = usage if isinstance(usage, dict) else {}
        counts = {}
        for field in ("prompt_tokens", "completion_tokens"):
            try:
                value = int(usage.get(field) or 0)
            except (TypeError, ValueError, OverflowError):
                value = 0
            counts[field] = max(0, value)
        counts["total_tokens"] = counts["prompt_tokens"] + counts["completion_tokens"]
        return counts

    @staticmethod
    def _add_usage(total: dict[str, int], value: dict[str, int]) -> None:
        for field in ("prompt_tokens", "completion_tokens"):
            total[field] += max(0, int(value.get(field) or 0))
        total["total_tokens"] = total["prompt_tokens"] + total["completion_tokens"]

    def _select_candidates(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        on_preview: Callable[[dict[str, Any]], None] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        k2_reply, nanbeige_reply = None, None
        generation_errors = {}
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        (k2_reply, nanbeige_reply), (k2_error, nanbeige_error) = self._collect_pair(
            lambda: self.k2.chat(
                messages, tools=tools, max_tokens=max_tokens, temperature=0.35,
                on_delta=on_preview,
            ),
            lambda: self.nanbeige.chat(
                messages, tools=tools, max_tokens=max_tokens, temperature=0.35,
            ),
        )
        if k2_error:
            generation_errors["K2"] = k2_error
        if nanbeige_error:
            generation_errors["Nanbeige"] = nanbeige_error
        self._add_usage(usage, self._usage_for_reply(k2_reply))
        self._add_usage(usage, self._usage_for_reply(nanbeige_reply))

        exact_count = requested_list_count(messages)
        candidates = {
            "K2": self._candidate_from_message(k2_reply.message) if k2_reply else None,
            "Nanbeige": self._candidate_from_message(nanbeige_reply.message) if nanbeige_reply else None,
        }
        valid = {
            name: self._candidate_is_valid(candidate, tools, exact_count)
            for name, candidate in candidates.items()
        }
        reviews = []
        review_records = []
        review_errors = {}
        review_confidence = 0.0
        candidate_scores = {}
        selected = None
        selection_method = ""

        if valid["K2"] and valid["Nanbeige"] and self._same_candidate(candidates["K2"], candidates["Nanbeige"]):
            selected = "K2"
            selection_method = "both_models_returned_the_same_valid_candidate"
            candidate_scores = {"K2": 100.0, "Nanbeige": 100.0}
        elif valid["K2"] != valid["Nanbeige"]:
            selected = "K2" if valid["K2"] else "Nanbeige"
            selection_method = "only_valid_candidate"
            candidate_scores = {selected: 100.0}
        elif valid["K2"] and valid["Nanbeige"]:
            k2_swapped = bool(secrets.randbelow(2))
            (k2_review, nanbeige_review), (k2_review_error, nanbeige_review_error) = self._collect_pair(
                lambda: self._review(self.k2, messages, candidates, tools, k2_swapped),
                lambda: self._review(self.nanbeige, messages, candidates, tools, not k2_swapped),
            )
            if k2_review_error:
                review_errors["K2"] = k2_review_error
            if nanbeige_review_error:
                review_errors["Nanbeige"] = nanbeige_review_error
            for evaluator, reviewed in (("K2", k2_review), ("Nanbeige", nanbeige_review)):
                if reviewed is None:
                    continue
                review, swapped, review_usage = reviewed
                reviews.append((review, swapped))
                self._add_usage(usage, review_usage)
                review_records.append({
                    "evaluator": evaluator,
                    "candidate_order_swapped": swapped,
                    **review.to_dict(),
                })
            candidate_scores, review_confidence = average_candidate_scores(reviews)
            if candidate_scores:
                selected = max(("K2", "Nanbeige"), key=lambda name: (candidate_scores.get(name, -1.0), name == "K2"))
                selection_method = "mean_of_independent_blind_pairwise_scores" if len(reviews) == 2 else "single_available_pairwise_score"
            elif tools:
                selection_method = "tool_candidates_unreviewed_no_action"
                candidates["K2"] = None
                candidates["Nanbeige"] = None
            else:
                selection_method = "joint_review_failed_no_selection"
        else:
            selection_method = "both_candidates_failed_validation"

        if selected is None:
            detail = "; ".join(
                f"{name}: {error}" for name, error in {**generation_errors, **review_errors}.items()
            )
            if selection_method == "tool_candidates_unreviewed_no_action":
                content = "K2 and Nanbeige produced tool candidates, but their joint review failed. No tool action was selected."
            elif selection_method == "both_candidates_failed_validation":
                content = "Neither K2 nor Nanbeige produced a valid answer candidate."
            elif selection_method == "joint_review_failed_no_selection":
                content = "K2 and Nanbeige produced candidates, but their joint review failed. No candidate was selected."
            else:
                content = "K2 and Nanbeige could not complete candidate generation and selection."
            if detail:
                content += " " + detail
            final = {"role": "assistant", "content": content}
            status = "failed"
        else:
            final = self._message_for_candidate(candidates[selected])
            status = "selected"

        evidence = {
            "status": status,
            "selection_method": selection_method,
            "selected_model": selected,
            "candidates_valid": valid,
            "candidate_scores": {name: round(score, 2) for name, score in candidate_scores.items()},
            "review_confidence_mean": round(review_confidence, 4),
            "review_confidence_note": "Model-reported and uncalibrated; not a correctness probability.",
            "reviews": review_records,
            "generation_errors": generation_errors,
            "review_errors": review_errors,
            "usage": usage,
        }
        return final, evidence

    def chat_completion(
        self,
        payload: dict[str, Any],
        on_preview: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        messages = list(payload.get("messages") or [])
        tools = payload.get("tools")
        requested_tokens = payload.get("max_tokens")
        if requested_tokens is None:
            requested_tokens = payload.get("max_completion_tokens")
        requested_tokens = int(requested_tokens) if requested_tokens is not None else self.config.live_window_tokens
        if requested_tokens <= 0:
            requested_tokens = self.config.live_window_tokens
        # There is no arbitrary answer-length ceiling: the backend can use its
        # remaining active context, while ECHO continues across context-sized
        # passes. The only per-pass bound is the real configured model window.
        max_tokens = min(requested_tokens, self.config.live_window_tokens)
        started = time.perf_counter()
        message, selection = self._select_candidates(messages, tools, max_tokens, on_preview)
        elapsed = time.perf_counter() - started
        return {
            "id": f"duocore-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.config.model_id,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
            }],
            "usage": selection["usage"],
            "duocore": {
                **selection,
                "models": ["K2", "Nanbeige"],
                "inference_passes_max": 4,
                "seconds": elapsed,
                "live_window_tokens": self.config.live_window_tokens,
                "execution_mode": "pairwise_candidate_selection",
            },
        }




def launch_llama_server(
    executable: Path,
    backbone: LlamaBackbone,
    *,
    context: int,
    gpu_layers: int = 99,
    embeddings: bool = True,
    gpu_kv: bool = False,
    jinja: bool = False,
    chat_template: str | None = None,
) -> subprocess.Popen:
    # Both model servers share the host CPU for any layers that do not fit in VRAM.
    # Keep each backend to one quarter of the logical processors, capped at four.
    logical_cpus = os.cpu_count() or 8
    cpu_threads = max(1, min(4, logical_cpus // 4))
    args = [
        str(executable), "-m", str(backbone.model_path),
        "--host", "127.0.0.1", "--port", str(backbone.spec.port),
        "--threads", str(cpu_threads), "--threads-batch", str(cpu_threads),
        "-c", str(context), "-ngl", str(gpu_layers), "-np", "1", "-b", "512", "-ub", "512",
        "--flash-attn", "on", "--cache-type-k", "f16" if gpu_kv else "q4_0",
        "--cache-type-v", "f16" if gpu_kv else "q4_0", "--reasoning", "off", "--no-webui",
    ]
    if not gpu_kv:
        args.append("--no-kv-offload")
    if embeddings:
        args.extend(["--embedding", "--pooling", "last"])
    if jinja:
        args.append("--jinja")
    if chat_template:
        args.extend(["--chat-template", chat_template])
    # Keep a bounded-lifetime file instead of an unread PIPE: llama.cpp can fill a
    # PIPE during multi-GB startup. On early exit, wait_healthy includes its tail.
    fd, raw_log_path = tempfile.mkstemp(prefix=f"opencore-{backbone.spec.port}-", suffix=".log")
    log_path = Path(raw_log_path)
    log_handle = os.fdopen(fd, "w+b")
    try:
        process = subprocess.Popen(args, stdout=log_handle, stderr=subprocess.STDOUT)
    except Exception:
        log_handle.close()
        log_path.unlink(missing_ok=True)
        raise
    process._duocore_log_path = log_path
    process._duocore_log_handle = log_handle
    return process


def wait_healthy(
    backbone: LlamaBackbone,
    process: subprocess.Popen | None = None,
    timeout: float = 180.0,
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if backbone.healthy():
            return
        if process is not None and process.poll() is not None:
            log_path = getattr(process, "_duocore_log_path", None)
            log_handle = getattr(process, "_duocore_log_handle", None)
            if log_handle is not None and not log_handle.closed:
                log_handle.flush()
                log_handle.close()
            log_tail = ""
            if log_path is not None and log_path.is_file():
                try:
                    with log_path.open("rb") as stream:
                        stream.seek(max(0, log_path.stat().st_size - 5000))
                        log_tail = stream.read().decode("utf-8", errors="replace").strip()
                except OSError:
                    pass
            raise RuntimeError(
                f"{backbone.spec.name} llama-server exited during startup with code {process.returncode}"
                + (f"; log tail: {log_tail}" if log_tail else "")
            )
        time.sleep(0.5)
    raise TimeoutError(f"{backbone.spec.name} did not become healthy on port {backbone.spec.port}")


def stop_llama_server(process: subprocess.Popen, timeout: float = 10.0) -> None:
    """Stop a managed backend and remove its temporary startup log."""
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)
    log_handle = getattr(process, "_duocore_log_handle", None)
    if log_handle is not None and not log_handle.closed:
        log_handle.close()
    log_path = getattr(process, "_duocore_log_path", None)
    if log_path is not None:
        log_path.unlink(missing_ok=True)
