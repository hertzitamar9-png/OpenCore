"""Two independent LFM brains exchange drafts and blind reviews."""
from concurrent.futures import ThreadPoolExecutor
import codecs
import ctypes as C
import secrets
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'doucode'))
from duocore.runtime import DuoCoreEngine, LlamaBackbone, BackboneReply
from duocore.selection import parse_review, review_messages
from protocol import OutputStream, with_tools

REVIEW_SCHEMA = {'type': 'object', 'properties': {
    'score_a': {'type': 'number', 'minimum': 0, 'maximum': 100},
    'score_b': {'type': 'number', 'minimum': 0, 'maximum': 100},
    'confidence': {'type': 'number', 'minimum': 0, 'maximum': 100},
    'reason': {'type': 'string', 'maxLength': 180},
}, 'required': ['score_a', 'score_b', 'confidence', 'reason'], 'additionalProperties': False}


class LfmBackbone(LlamaBackbone):
    def chat(self, messages, *, tools=None, on_delta=None, **kwargs):
        decoder = OutputStream()
        def preview(delta):
            for parsed in decoder.feed(delta.get('content') or ''):
                on_delta(parsed)
        reply = super().chat(with_tools(messages, tools), on_delta=preview if on_delta else None, **kwargs)
        if on_delta:
            for delta in decoder.feed('', final=True):
                on_delta(delta)
        else:
            decoder.feed(reply.content, final=True)
        message = decoder.message(tools)
        raw = dict(reply.raw)
        raw['choices'] = [{**reply.raw['choices'][0], 'message': message}]
        return BackboneReply(message['content'], message, raw)


class NativeEchoBrain:
    def __init__(self, model, index):
        self.model, self.index = model, index

    def chat(self, messages, *, tools=None, max_tokens=1024, on_delta=None, json_schema=None, **_):
        prompt = self.model.format(with_tools(messages, tools))
        if json_schema:
            prompt += b'<think>\n\n</think>\n\n'
        lib = self.model.lib
        tokens = lib.fc_brain_start(self.model.handle, self.index, prompt, int(bool(json_schema)))
        if tokens < 0:
            raise RuntimeError(self.model.error())
        limit = min(max_tokens, self.model.context - tokens)
        if limit <= 0:
            raise ValueError('Independent brain prompt leaves no output space')
        decoder = OutputStream()
        utf8 = codecs.getincrementaldecoder('utf-8')()
        buffer = C.create_string_buffer(32768); token = C.c_int(); count = 0; finish = 'length'
        for _ in range(limit):
            length = lib.fc_brain_next(self.model.handle, self.index, buffer, len(buffer), C.byref(token))
            if length < 0:
                raise RuntimeError(self.model.error())
            if length == 0:
                finish = 'stop'; break
            count += 1
            for delta in decoder.feed(utf8.decode(buffer.raw[:length - 1])):
                if on_delta:
                    on_delta(delta)
        for delta in decoder.feed(utf8.decode(b'', final=True), final=True):
            if on_delta:
                on_delta(delta)
        message = decoder.message(tools)
        lib.fc_clear_brain(self.model.handle, self.index)
        usage = {'prompt_tokens': tokens, 'completion_tokens': count, 'total_tokens': tokens + count}
        return BackboneReply(message['content'], message,
                             {'choices': [{'message': message, 'finish_reason': finish}], 'usage': usage})


class DualCoreEngine:
    def __init__(self, checkpoint: Path, port: int, native=None):
        self.native = native
        self.parameters = native.parameters if native else None
        self.brains = [NativeEchoBrain(native, i) for i in range(2)] if native else [LfmBackbone(SimpleNamespace(name=f'LFM brain {i+1}', port=port+i+1,
                                                   reasoning_format='none'), checkpoint)
                       for i in range(2)]
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='dualcore')

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
        if self.native:
            self.native.close()

    def complete(self, messages, tools, max_tokens, preview=None, *, temperature=0.35):
        instructions = ('Construct a complete answer while preserving all user requirements.',
                        'Independently solve the task and check errors and missing requirements.')
        futures = [self.pool.submit(brain.chat, [{'role': 'system', 'content': instructions[i]}, *messages],
                                   tools=tools, max_tokens=max_tokens, temperature=temperature,
                                   on_delta=preview if i == 0 else None)
                   for i, brain in enumerate(self.brains)]
        replies = [future.result() for future in futures]
        candidates = [DuoCoreEngine._candidate_from_message(reply.message) for reply in replies]
        valid = [DuoCoreEngine._candidate_is_valid(candidate, tools, None) for candidate in candidates]
        usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
        for reply in replies:
            DuoCoreEngine._add_usage(usage, DuoCoreEngine._usage_for_reply(reply))
        finish_reasons = [reply.raw['choices'][0].get('finish_reason') or 'stop' for reply in replies]
        if not any(valid) and all(
            finish == 'length' and not reply.message.get('tool_calls')
            and not reply.content.strip() and reply.message.get('reasoning_content', '').strip()
            for reply, finish in zip(replies, finish_reasons)
        ):
            # A generation budget can expire entirely in reasoning. Preserve
            # the actual unfinished output; it is neither a chosen answer nor
            # permission to execute an invalid or truncated tool call.
            return dict(replies[0].message), usage, {
                'status': 'incomplete', 'selected_brain': None, 'reviews': [],
                'finish_reason': 'length', 'candidate_finish_reasons': finish_reasons,
                'draft_temperature': 0.0 if self.native else temperature,
                'review_temperature': 0.0,
                'execution_mode': 'cooperating_candidate_brains',
            }
        reviews = []
        if all(valid) and DuoCoreEngine._same_candidate(*candidates):
            selected = 0
        elif sum(valid) == 1:
            selected = valid.index(True)
        elif all(valid):
            first_swapped = bool(secrets.randbelow(2))
            orders = (first_swapped, not first_swapped)
            futures = [self.pool.submit(brain.chat,
                        review_messages(messages, candidates[int(swapped)], candidates[int(not swapped)], tools),
                        max_tokens=384, temperature=0.0, json_schema=REVIEW_SCHEMA)
                       for brain, swapped in zip(self.brains, orders)]
            scores = [0.0, 0.0]
            for future, swapped in zip(futures, orders):
                reply = future.result()
                review = parse_review(reply.content)
                if review is None:
                    raise RuntimeError('LFM joint candidate review returned invalid scores; no tool action selected')
                record = review.to_dict()
                reviews.append({'swapped': swapped, **record})
                # A/B are displayed in opposite orders to the two reviewers.
                scores[int(swapped)] += record['score_a']
                scores[int(not swapped)] += record['score_b']
                DuoCoreEngine._add_usage(usage, DuoCoreEngine._usage_for_reply(reply))
            selected = max(range(2), key=lambda index: (scores[index], -index))
        else:
            raise RuntimeError('Neither LFM brain produced a valid candidate')
        message = DuoCoreEngine._message_for_candidate(candidates[selected])
        if replies[selected].message.get('reasoning_content'):
            message['reasoning_content'] = replies[selected].message['reasoning_content']
        return message, usage, {
            'selected_brain': selected+1, 'reviews': reviews,
            'finish_reason': replies[selected].raw['choices'][0].get('finish_reason') or 'stop',
            'draft_temperature': 0.0 if self.native else temperature,
            'review_temperature': 0.0,
            'confidence_note': 'Self-reported scores are uncalibrated, not correctness probabilities.',
            'execution_mode': 'cooperating_candidate_brains',
        }
