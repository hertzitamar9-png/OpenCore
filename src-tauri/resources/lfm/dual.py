"""Two independent LFM brains exchange drafts and blind reviews."""
from concurrent.futures import ThreadPoolExecutor
import codecs
import ctypes as C
import json
import secrets
import sys
import urllib.error
import urllib.request
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
        template_kwargs = dict(kwargs.pop('chat_template_kwargs', {}) or {})
        template_kwargs['skip_think'] = bool(kwargs.get('json_mode') or kwargs.get('json_schema'))
        template_kwargs['preserve_thinking'] = (
            isinstance(kwargs.get('thinking_budget_tokens'), int)
            and kwargs['thinking_budget_tokens'] > 0
            and not template_kwargs['skip_think']
        )
        kwargs['chat_template_kwargs'] = template_kwargs
        decoder = OutputStream()
        def preview(delta):
            for parsed in decoder.feed(delta.get('content') or '', tools=tools):
                on_delta(parsed)
        prepared_messages = with_tools(messages, tools)
        try:
            reply = super().chat(prepared_messages, on_delta=preview if on_delta else None, **kwargs)
        except RuntimeError as error:
            # llama.cpp's peg-native parser can reject ordinary generated text
            # (notably malformed UTF-8 emitted by multilingual tokenizers)
            # after generation has completed. Retry non-streaming requests via
            # its template and raw completion endpoints; keep streaming calls
            # on the normal path so a retry cannot duplicate visible tokens.
            if on_delta or 'expected peg-native format' not in str(error):
                raise
            template_request = urllib.request.Request(
                self.base_url + '/apply-template',
                data=json.dumps({'messages': prepared_messages,
                                 'chat_template_kwargs': template_kwargs}, ensure_ascii=False).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
            )
            try:
                with urllib.request.urlopen(template_request, timeout=900) as response:
                    prompt = json.load(response)['prompt']
            except (urllib.error.HTTPError, KeyError, TypeError) as template_error:
                raise RuntimeError(f'{self.spec.name} could not apply its chat template after peg parse failure: '
                                   f'{template_error}') from template_error
            completion_request = urllib.request.Request(
                self.base_url + '/v1/completions',
                data=json.dumps({
                    'prompt': prompt,
                    'max_tokens': kwargs.get('max_tokens', 1024),
                    'temperature': kwargs.get('temperature', 0.35),
                    'top_p': 0.95,
                    'repeat_penalty': kwargs.get('repeat_penalty', 1.08),
                    'stream': False,
                }, ensure_ascii=False).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
            )
            try:
                with urllib.request.urlopen(completion_request, timeout=900) as response:
                    result = json.load(response)
            except urllib.error.HTTPError as completion_error:
                detail = completion_error.read().decode('utf-8', errors='replace')
                raise RuntimeError(f'{self.spec.name} raw completion retry failed: HTTP '
                                   f'{completion_error.code}: {detail[:1200]}') from completion_error
            choice = (result.get('choices') or [{}])[0]
            content = str(choice.get('text') or '')
            raw = {'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content},
                                'finish_reason': choice.get('finish_reason') or 'stop'}],
                   'usage': result.get('usage', {})}
            reply = BackboneReply(content, raw['choices'][0]['message'], raw)
        if on_delta:
            for delta in decoder.feed('', final=True, tools=tools):
                on_delta(delta)
        else:
            decoder.feed(reply.content, final=True, tools=tools)
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
            for delta in decoder.feed(utf8.decode(buffer.raw[:length - 1]), tools=tools):
                if on_delta:
                    on_delta(delta)
        for delta in decoder.feed(utf8.decode(b'', final=True), final=True, tools=tools):
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

    def complete(self, messages, tools, max_tokens, preview=None, *, temperature=0.35,
                 thinking_budget_tokens=None):
        instructions = ('Construct a complete answer while preserving all user requirements.',
                        'Independently solve the task and check errors and missing requirements.')
        futures = [self.pool.submit(brain.chat, [{'role': 'system', 'content': instructions[i]}, *messages],
                                   tools=tools, max_tokens=max_tokens, temperature=temperature,
                                   thinking_budget_tokens=thinking_budget_tokens,
                                   on_delta=preview if i == 0 else None)
                   for i, brain in enumerate(self.brains)]
        replies = []
        candidate_errors = []
        for index, future in enumerate(futures):
            try:
                replies.append(future.result())
            except Exception as error:
                # A malformed stream from one backend must not discard a
                # complete, valid answer from the other independent brain.
                replies.append(None)
                candidate_errors.append({
                    'brain': index + 1,
                    'error': f'{type(error).__name__}: {error}'[:1200],
                })
        candidates = [DuoCoreEngine._candidate_from_message(reply.message) if reply else None
                      for reply in replies]
        valid = [DuoCoreEngine._candidate_is_valid(candidate, tools, None) for candidate in candidates]
        usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
        for reply in replies:
            if reply:
                DuoCoreEngine._add_usage(usage, DuoCoreEngine._usage_for_reply(reply))
        finish_reasons = [
            reply.raw['choices'][0].get('finish_reason') or 'stop' if reply else 'error'
            for reply in replies
        ]
        if not any(valid) and all(
            reply is not None and finish == 'length' and not reply.message.get('tool_calls')
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
            for reviewer_index, (future, swapped) in enumerate(zip(futures, orders)):
                reply = future.result()
                review = parse_review(reply.content)
                retried = False
                if review is None:
                    DuoCoreEngine._add_usage(usage, DuoCoreEngine._usage_for_reply(reply))
                    retried = True
                    retry_messages = [
                        *review_messages(messages, candidates[int(swapped)], candidates[int(not swapped)], tools),
                        {'role': 'assistant', 'content': reply.content},
                        {'role': 'user', 'content': (
                            'Your previous response was not valid score JSON. Return exactly one JSON object '
                            'with numeric score_a, score_b, and confidence from 0 to 100, plus a short string reason. '
                            'Do not include markdown or any text outside the JSON object.'
                        )},
                    ]
                    reply = self.brains[reviewer_index].chat(
                        retry_messages, max_tokens=192, temperature=0.0, json_schema=REVIEW_SCHEMA)
                    review = parse_review(reply.content)
                if review is None:
                    preview = reply.content[:400].replace('\r', ' ').replace('\n', ' ')
                    finish = reply.raw.get('choices', [{}])[0].get('finish_reason', 'unknown')
                    raise RuntimeError(
                        'LFM joint candidate review remained invalid after one correction retry '
                        f'(finish_reason={finish!r}, content={preview!r}); no tool action selected'
                    )
                record = review.to_dict()
                reviews.append({'swapped': swapped, 'retried': retried, **record})
                # A/B are displayed in opposite orders to the two reviewers.
                scores[int(swapped)] += record['score_a']
                scores[int(not swapped)] += record['score_b']
                DuoCoreEngine._add_usage(usage, DuoCoreEngine._usage_for_reply(reply))
            selected = max(range(2), key=lambda index: (scores[index], -index))
        else:
            details = '; '.join(item['error'] for item in candidate_errors)
            suffix = f': {details}' if details else ''
            raise RuntimeError(f'Neither LFM brain produced a valid candidate{suffix}')
        message = DuoCoreEngine._message_for_candidate(candidates[selected])
        if replies[selected].message.get('reasoning_content'):
            message['reasoning_content'] = replies[selected].message['reasoning_content']
        metadata = {
            'selected_brain': selected+1, 'reviews': reviews,
            'finish_reason': replies[selected].raw['choices'][0].get('finish_reason') or 'stop',
            'draft_temperature': 0.0 if self.native else temperature,
            'review_temperature': 0.0,
            'confidence_note': 'Self-reported scores are uncalibrated, not correctness probabilities.',
            'execution_mode': 'cooperating_candidate_brains',
        }
        if candidate_errors:
            metadata['candidate_errors'] = candidate_errors
        return message, usage, metadata
