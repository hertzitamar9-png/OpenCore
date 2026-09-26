from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import time
import uuid
import urllib.error
import urllib.request
from typing import Any, Callable

try:
    import torch
except ImportError:
    torch = None

from .latent_bridge import BridgeConfig, build_bridge
from .consensus import (
    TwinBlackboard,
    TwinCycle,
    commit_instruction,
    consensus_score,
    coordination_state,
    deliberation_instruction,
    negotiation_instruction,
    normalize_candidate,
    only_requested_list,
    parse_accord,
    parse_proposal,
    requested_list_count,
    work_packet_instruction,
)
from .spec import BackboneSpec, TwinCoreConfig


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
        if on_delta is not None:
            body["stream"] = True
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if feedback_embedding:
            body["twincore_feedback_embedding"] = feedback_embedding
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


class TwinCoreEngine:
    def __init__(self, config: TwinCoreConfig, release_root: Path):
        self.config = config
        self.release_root = release_root
        self.k2 = LlamaBackbone(config.k2, release_root / "backbones" / "k2" / config.k2.gguf_file)
        self.nanbeige = LlamaBackbone(
            config.nanbeige,
            release_root / "backbones" / "nanbeige" / config.nanbeige.gguf_file,
        )
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="twincore")
        self.bridge = None
        bridge_path = release_root / config.bridge_checkpoint
        if torch is not None and bridge_path.is_file():
            bridge_cfg = BridgeConfig(
                k2_hidden=config.k2.hidden_size,
                nanbeige_hidden=config.nanbeige.hidden_size,
                shared_hidden=config.latent_size,
                rank=config.bridge_rank,
                slots=config.latent_slots,
            )
            bridge = build_bridge(bridge_cfg)
            payload = torch.load(bridge_path, map_location="cpu", weights_only=False)
            state = payload.get("state_dict", payload) if isinstance(payload, dict) else payload
            bridge.load_state_dict(state, strict=True)
            bridge.eval()
            self.bridge = bridge

    def _pair(self, left, right):
        a = self.pool.submit(left)
        b = self.pool.submit(right)
        return a.result(), b.result()

    @staticmethod
    def _task_text(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str):
                    return content
                return json.dumps(content, ensure_ascii=False)
        return json.dumps(messages[-4:], ensure_ascii=False)

    def _latent_exchange(self, k2_text: str, nb_text: str) -> dict[str, Any] | None:
        if self.bridge is None or torch is None:
            return None
        k2_emb, nb_emb = self._pair(
            lambda: self.k2.embedding(k2_text),
            lambda: self.nanbeige.embedding(nb_text),
        )
        with torch.inference_mode():
            k2_t = torch.tensor(k2_emb, dtype=torch.float32).view(1, 1, -1)
            nb_t = torch.tensor(nb_emb, dtype=torch.float32).view(1, 1, -1)
            out = self.bridge(k2_t, nb_t)
            return {
                "conflict": float(out["conflict"].item()),
                "feedback_k2": out["feedback_k2"].mean(dim=1).squeeze(0).tolist(),
                "feedback_nanbeige": out["feedback_nanbeige"].mean(dim=1).squeeze(0).tolist(),
                "gate": out["gate"].mean(dim=(0, 1)).tolist(),
            }

    def deliberate(self, messages: list[dict[str, Any]]) -> TwinBlackboard:
        board = TwinBlackboard(task=self._task_text(messages))
        prior_k2 = prior_nb = None
        feedback_k2 = feedback_nb = None
        for index in range(self.config.max_consensus_cycles):
            k2_messages = deliberation_instruction("K2", "Nanbeige", messages, board, prior_nb)
            nb_messages = deliberation_instruction("Nanbeige", "K2", messages, board, prior_k2)
            k2_reply, nb_reply = self._pair(
                lambda: self.k2.chat(k2_messages, max_tokens=768, temperature=0.35, json_mode=True, feedback_embedding=feedback_k2),
                lambda: self.nanbeige.chat(nb_messages, max_tokens=768, temperature=0.35, json_mode=True, feedback_embedding=feedback_nb),
            )
            k2 = parse_proposal("K2", k2_reply.content)
            nb = parse_proposal("Nanbeige", nb_reply.content)
            agreement, confidence = consensus_score(k2, nb, index)
            latent = self._latent_exchange(
                " ".join([k2.hypothesis, k2.proposal, *k2.implementation]),
                " ".join([nb.hypothesis, nb.proposal, *nb.implementation]),
            )
            if latent is not None:
                # Bridge feedback informs revision but cannot declare agreement.
                feedback_k2 = latent["feedback_k2"]
                feedback_nb = latent["feedback_nanbeige"]
            board.append(
                TwinCycle(
                    index=index,
                    k2=k2,
                    nanbeige=nb,
                    agreement=agreement,
                    confidence=confidence,
                )
            )
            prior_k2, prior_nb = k2, nb
            if (
                index > 0
                and agreement >= self.config.agreement_threshold
                and confidence >= self.config.confidence_threshold
                and min(k2.peer_agreement, nb.peer_agreement) >= self.config.agreement_threshold
            ):
                break
        return board

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
                for tool in tools
                if isinstance(tool, dict)
            }
            if tool_call["name"] not in available:
                return False
        elif not str(candidate.get("content") or "").strip():
            return False
        if exact_list_count is not None and tool_call is None:
            text = str(candidate.get("content") or "").strip()
            if only_requested_list(text, exact_list_count) != text:
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
                "id": f"call_twincore_{uuid.uuid4().hex}",
                "type": "function",
                "function": {
                    "name": tool_call["name"],
                    "arguments": json.dumps(tool_call["arguments"], ensure_ascii=False, separators=(",", ":")),
                },
            }]
        return message

    def commit(
        self,
        messages: list[dict[str, Any]],
        board: TwinBlackboard,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        on_preview: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        # Both heads contribute to the shared plan, then each writes a complete
        # candidate. A result is returned only after the peer accepts that exact
        # answer/action or both independently produce the same valid candidate.
        k2_work_messages = work_packet_instruction("K2", "Nanbeige", messages, board)
        nb_work_messages = work_packet_instruction("Nanbeige", "K2", messages, board)
        k2_work, nb_work = self._pair(
            lambda: self.k2.chat(k2_work_messages, max_tokens=1024, temperature=0.25),
            lambda: self.nanbeige.chat(nb_work_messages, max_tokens=1024, temperature=0.25),
        )
        work_packets = {"K2": k2_work.content, "Nanbeige": nb_work.content}
        k2_messages = commit_instruction("K2", "Nanbeige", messages, board, work_packets)
        nb_messages = commit_instruction("Nanbeige", "K2", messages, board, work_packets)
        state = board.latest
        latent = self._latent_exchange(
            " ".join([state.k2.hypothesis, state.k2.proposal, *state.k2.implementation]) if state else self._task_text(messages),
            " ".join([state.nanbeige.hypothesis, state.nanbeige.proposal, *state.nanbeige.implementation]) if state else self._task_text(messages),
        )
        feedback_k2 = latent["feedback_k2"] if latent else None
        feedback_nb = latent["feedback_nanbeige"] if latent else None
        k2_reply, nb_reply = self._pair(
            lambda: self.k2.chat(k2_messages, tools=tools, max_tokens=max_tokens, temperature=0.25,
                feedback_embedding=feedback_k2, on_delta=on_preview),
            lambda: self.nanbeige.chat(nb_messages, tools=tools, max_tokens=max_tokens, temperature=0.25, feedback_embedding=feedback_nb),
        )
        drafts = {
            "K2": self._candidate_from_message(k2_reply.message),
            "Nanbeige": self._candidate_from_message(nb_reply.message),
        }
        exact_count = requested_list_count(messages)
        k2_candidate, nb_candidate = drafts["K2"], drafts["Nanbeige"]
        if (
            self._candidate_is_valid(k2_candidate, tools, exact_count)
            and self._candidate_is_valid(nb_candidate, tools, exact_count)
            and self._same_candidate(k2_candidate, nb_candidate)
        ):
            return self._message_for_candidate(k2_candidate), {
                "status": "agreed",
                "rounds": 0,
                "both_heads_accepted": True,
                "laya_used": False,
                "agreement_mode": "independent_identical_candidates",
            }

        offerer, reviewer = "K2", "Nanbeige"
        offer = k2_candidate
        protocol_note = ""
        max_rounds = max(1, int(self.config.max_negotiation_rounds))
        for round_index in range(max_rounds):
            review_messages = negotiation_instruction(
                reviewer,
                offerer,
                messages,
                board,
                work_packets,
                offer,
                drafts[reviewer],
                tools,
                exact_count,
                protocol_note,
            )
            backbone = self.k2 if reviewer == "K2" else self.nanbeige
            review = backbone.chat(
                review_messages,
                tools=None,
                max_tokens=min(max_tokens, 4096),
                temperature=0.1,
                json_mode=True,
            )
            accord = parse_accord(review.content)
            protocol_note = ""
            if accord is None:
                protocol_note = "Your previous response did not match the accept/counteroffer JSON schema. Return that schema exactly."
                continue
            if accord["decision"] == "accept":
                if (
                    self._same_candidate(accord["candidate"], offer)
                    and self._candidate_is_valid(offer, tools, exact_count)
                ):
                    return self._message_for_candidate(offer), {
                        "status": "agreed",
                        "rounds": round_index + 1,
                        "both_heads_accepted": True,
                        "laya_used": False,
                        "agreement_mode": "peer_acceptance",
                    }
                protocol_note = (
                    "The attempted acceptance did not exactly echo the current offer, or the offer failed the user's "
                    "tool/output-format requirements. Make a valid counteroffer instead."
                )
                continue

            counteroffer = accord["candidate"]
            if not self._candidate_is_valid(counteroffer, tools, exact_count):
                protocol_note = "That counteroffer failed the available-tool or exact-output-format checks. Provide a valid counteroffer."
                continue
            if self._same_candidate(counteroffer, offer):
                protocol_note = "A counteroffer must change the current offer; otherwise explicitly accept it."
                continue
            offer, offerer, reviewer = counteroffer, reviewer, offerer
            drafts[offerer] = counteroffer

        return {
            "role": "assistant",
            "content": (
                "The two model backbones could not reach an agreed final response/action after "
                f"{max_rounds} negotiation rounds. No tool action was run. Please clarify the disputed requirement or retry."
            ),
        }, {
            "status": "no_agreement",
            "rounds": max_rounds,
            "both_heads_accepted": False,
            "laya_used": False,
            "last_offer_from": offerer,
        }

    def chat_completion(
        self,
        payload: dict[str, Any],
        on_preview: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        messages = list(payload.get("messages") or [])
        tools = payload.get("tools")
        max_tokens = int(payload.get("max_tokens") or payload.get("max_completion_tokens") or 2048)
        max_tokens = max(64, min(max_tokens, 12288))
        started = time.perf_counter()
        board = self.deliberate(messages)
        message, agreement_result = self.commit(messages, board, tools, max_tokens, on_preview)
        elapsed = time.perf_counter() - started
        return {
            "id": f"twincore-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.config.model_id,
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "twincore": {
                "cycles": len(board.cycles),
                "agreement": board.latest.agreement if board.latest else 0.0,
                "confidence": board.latest.confidence if board.latest else 0.0,
                "laya_preference": None,
                "judge": None,
                "agreement_result": agreement_result,
                "judge_status": {"enabled": False, "reason": "TwinCore now uses bilateral negotiation; Laya does not rank or select candidates."},
                "collaboration": {
                    "work_packets_parallel": True,
                    **coordination_state(board),
                },
                "seconds": elapsed,
                "live_window_tokens": self.config.live_window_tokens,
            },
        }


def launch_llama_server(
    executable: Path,
    backbone: LlamaBackbone,
    *,
    context: int,
    gpu_layers: int = 99,
) -> subprocess.Popen:
    args = [
        str(executable), "-m", str(backbone.model_path),
        "--host", "127.0.0.1", "--port", str(backbone.spec.port),
        "-c", str(context), "-ngl", str(gpu_layers), "-np", "1", "-b", "512", "-ub", "512",
        "--flash-attn", "on", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0", "--no-kv-offload",
        "--reasoning", "off", "--no-webui", "--embedding", "--pooling", "last",
    ]
    # Do not leave model logs on an unread PIPE: llama.cpp can fill it while loading
    # a multi-GB checkpoint and deadlock before /health ever becomes available.
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True)


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
            raise RuntimeError(
                f"{backbone.spec.name} llama-server exited during startup with code {process.returncode}"
            )
        time.sleep(0.5)
    raise TimeoutError(f"{backbone.spec.name} did not become healthy on port {backbone.spec.port}")
