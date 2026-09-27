"""Teacher forcing with frozen native decoders and truncated feedback gradients."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F

from .native import NativeHead
from .canonical import CanonicalPrefixes

GRADIENT_MODE = 'frozen_native_decoders_truncated_feedback_exact_f32_head'


def prompt_fingerprint(messages):
    normalized = [{'role': message['role'], 'content': message['content'].replace('\r\n', '\n')}
                  for message in messages]
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


@dataclass
class Corpus:
    train: list[dict]
    validation: list[dict]
    sha256: str


def read_corpus(path: Path, *, expected_sha256=None, forbidden_prompt_hashes=()):
    digest = hashlib.sha256()
    train, validation, ids, prompts = [], [], set(), {}
    blocked = set(forbidden_prompt_hashes)
    with Path(path).open('rb') as source:
        for line_number, line in enumerate(source, 1):
            digest.update(line)
            if not line.strip():
                continue
            if len(line) > 2_000_000:
                raise ValueError('Training corpus record exceeds the bounded loader capacity')
            row = json.loads(line)
            identity, split = row.get('id'), row.get('split')
            if not isinstance(identity, str) or not identity or identity in ids:
                raise ValueError(f'Missing or duplicate corpus ID on line {line_number}')
            if any(name in identity.casefold() for name in ('humaneval', 'livebench')):
                raise ValueError('Evaluation benchmark IDs cannot enter the training corpus')
            if split not in ('train', 'validation'):
                raise ValueError('Every training record needs an explicit train/validation split')
            messages, answer = row.get('messages'), row.get('answer')
            if (not isinstance(messages, list) or not messages or not isinstance(answer, str) or not answer
                    or any(not isinstance(m, dict) or m.get('role') not in ('system', 'user', 'assistant')
                           or not isinstance(m.get('content'), str) or '\0' in m['content'] for m in messages)):
                raise ValueError('Malformed supervised training messages or answer')
            if messages[-1]['role'] != 'user' or '\0' in answer:
                raise ValueError('Training prompts must end with a user message and contain no NUL bytes')
            prompt = prompt_fingerprint(messages)
            if prompt in blocked:
                raise ValueError('Evaluation prompt leaked into the training corpus')
            if prompt in prompts:
                raise ValueError('Duplicate prompt or train/validation prompt leak')
            ids.add(identity)
            prompts[prompt] = split
            (train if split == 'train' else validation).append(row)
    sha256 = digest.hexdigest()
    if expected_sha256 is not None and sha256 != expected_sha256:
        raise ValueError('Training corpus identity changed')
    if not train or not validation:
        raise ValueError('Training and held-out validation must both be nonempty')
    return Corpus(train, validation, sha256)


@dataclass
class TeacherReport:
    loss: torch.Tensor
    tokens: int
    complete: bool


def encode_target(native, answer):
    """Exact answer bytes, without a SentencePiece standalone dummy prefix."""
    expected = answer.encode('utf-8')
    if not expected:
        raise ValueError('Supervised answer has no native target tokens')
    for separator in ('', '\n', '\n\n', 'a\n'):
        target = native.tokenize(0, separator + answer, special=False)
        pieces = [native.piece(0, int(token)) for token in target]
        prefix = bytearray()
        for start in range(len(target)):
            if ((not separator and start == 0) or
                    (separator and prefix.endswith(separator.encode('utf-8')))):
                if b''.join(pieces[start:]) == expected:
                    ids = target[start:]
                    if any(native.token_flags(0, int(token)) for token in ids):
                        raise ValueError('Supervised answers cannot contain special native target tokens')
                    return ids, pieces[start:]
            prefix.extend(pieces[start])
    raise ValueError('Supervised answer is not reproduced by native token pieces')


def inspect_corpus(native, corpus, *, max_tokens):
    """Validate complete labels and every native prefix before any decoder work."""
    if type(max_tokens) is not int or max_tokens < 1:
        raise ValueError('Training target budget must be a positive integer')
    rows, total, largest_target, largest_prefix = [], 0, 0, [0, 0]
    for example in corpus.train + corpus.validation:
        ids, pieces = encode_target(native, example['answer'])
        if len(ids) > max_tokens:
            raise ValueError(f"Complete target for {example['id']} needs {len(ids)} tokens; budget is {max_tokens}")
        prefixes = [native.format(brain, example['messages']) for brain in (0, 1)]
        canonical = CanonicalPrefixes(native, prefixes)
        suffix, maxima = bytearray(), [0, 0]
        # Include the complete answer too, even though its last prefix has no
        # next-token label. Retokenization can change either tower's token count.
        for piece in [None, *pieces]:
            if piece is not None:
                suffix.extend(piece)
            encoded = canonical.encode(bytes(suffix))
            for brain in (0, 1):
                count = len(encoded[brain])
                if count > native.geometry[brain]['capacity']:
                    raise ValueError(f"Training example {example['id']} exceeds brain {brain}'s measured native capacity")
                maxima[brain] = max(maxima[brain], count)
        rows.append({'id': example['id'], 'split': example['split'], 'target_tokens': len(ids),
                     'maximum_native_prefix_tokens': maxima})
        total += len(ids)
        largest_target = max(largest_target, len(ids))
        largest_prefix = [max(largest_prefix[brain], maxima[brain]) for brain in (0, 1)]
    return {'corpus_sha256': corpus.sha256, 'train_samples': len(corpus.train),
            'validation_samples': len(corpus.validation), 'target_tokens': total,
            'max_target_tokens': max_tokens, 'maximum_target_tokens': largest_target,
            'maximum_native_prefix_tokens': largest_prefix, 'samples': rows,
            'scope': 'Exact tokenizer and capacity checks; no decoder, training or quality score.'}


def teacher_forced(native, bridge, example, *, max_tokens: int):
    if max_tokens < 1:
        raise ValueError('Training token budget must be positive')
    native.clear()
    prefixes = [native.format(brain, example['messages']) for brain in (0, 1)]
    canonical_prefixes = CanonicalPrefixes(native, prefixes)
    target, pieces = encode_target(native, example['answer'])
    suffix = bytearray()
    feedback = None
    losses = []
    heads = [NativeHead(native, brain) for brain in (0, 1)]
    for token, piece in zip(target[:max_tokens], pieces[:max_tokens]):
        # Incomplete Unicode is represented with each vocabulary's actual byte
        # IDs. Native text tokenization receives only the complete UTF-8 prefix.
        ids = canonical_prefixes.encode(bytes(suffix))
        for brain in (0, 1):
            if len(ids[brain]) > native.geometry[brain]['capacity']:
                raise ValueError('Training example exceeds the measured finite native context capacity')
        bias = (None, None) if feedback is None else (
            feedback.nanbeige.detach().float().cpu().numpy(), feedback.k2.detach().float().cpu().numpy())
        features = native.step(*ids, *bias)
        if bridge is None:
            scores = features.nanbeige_logits
        else:
            prediction = bridge(features.nanbeige_hidden.detach(), features.k2_hidden.detach(),
                features.nanbeige_logits.detach(), features.k2_logits.detach(), *heads)
            scores, feedback = prediction.logits, prediction.feedback
        loss = F.cross_entropy(scores, torch.tensor([int(token)], dtype=torch.long, device=scores.device))
        if not torch.isfinite(loss):
            raise ValueError('Native training loss is nonfinite')
        losses.append(loss)
        suffix.extend(piece)
    if not losses:
        raise ValueError('Supervised example produced no training losses')
    return TeacherReport(torch.stack(losses).mean(), len(losses), len(losses) == len(target))


def evaluate(native, bridge, examples, *, max_tokens):
    total, tokens, complete, samples = 0.0, 0, 0, []
    with torch.no_grad():
        for example in examples:
            report = teacher_forced(native, bridge, example, max_tokens=max_tokens)
            value = float(report.loss)
            total += value * report.tokens
            tokens += report.tokens
            complete += int(report.complete)
            samples.append({'id': example['id'], 'tokens': report.tokens,
                            'complete': report.complete, 'loss': value})
    if not tokens or not math.isfinite(total):
        raise ValueError('Validation has no finite measured token losses')
    return {'loss': total / tokens, 'tokens': tokens, 'samples': len(samples),
            'complete_samples': complete, 'per_sample': samples}
