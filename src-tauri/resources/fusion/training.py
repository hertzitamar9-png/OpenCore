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


def read_hf_split_pair(
    train_path: Path,
    validation_path: Path,
    *,
    expected_train_sha256: str,
    expected_validation_sha256: str,
    expected_source: str,
    expected_source_config: str,
    expected_license: str,
    forbidden_prompt_hashes=(),
):
    """Read source JSONL splits whose final assistant message is the target.

    The caller must bind both exact file hashes and explicitly choose the
    reviewed source/license identity. This prevents a changing Hub card or an
    unreviewed local conversion from silently entering a training run.
    """
    bindings = (
        ('train', Path(train_path), expected_train_sha256),
        ('validation', Path(validation_path), expected_validation_sha256),
    )
    for split, _, expected in bindings:
        if (not isinstance(expected, str) or len(expected) != 64
                or any(char not in '0123456789abcdefABCDEF' for char in expected)):
            raise ValueError(f'{split} source SHA-256 binding is invalid')
    if not all(isinstance(value, str) and value.strip() for value in
               (expected_source, expected_source_config, expected_license)):
        raise ValueError('Source, configuration and reviewed license bindings are required')

    forbidden = set(forbidden_prompt_hashes)
    seen_ids, seen_prompts = set(), set()
    output = {'train': [], 'validation': []}
    hashes = {}
    for split, path, expected in bindings:
        digest = hashlib.sha256()
        with path.open('rb') as source_file:
            for line_number, raw_line in enumerate(source_file, 1):
                digest.update(raw_line)
                if not raw_line.strip():
                    continue
                if len(raw_line) > 2_000_000:
                    raise ValueError(f'{split} source row exceeds the bounded loader capacity')
                try:
                    row = json.loads(raw_line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f'Malformed {split} source JSON on line {line_number}') from exc
                if not isinstance(row, dict):
                    raise ValueError(f'{split} source row must be a JSON object on line {line_number}')
                if (row.get('source') != expected_source
                        or row.get('source_config') != expected_source_config
                        or row.get('license') != expected_license):
                    raise ValueError(f'{split} source/config/license identity does not match the reviewed binding')
                source_problem_id, fingerprint = row.get('source_problem_id'), row.get('fingerprint')
                identity = f'{source_problem_id}:{fingerprint}'
                if (not isinstance(source_problem_id, str) or not source_problem_id
                        or not isinstance(fingerprint, str) or not fingerprint
                        or identity in seen_ids):
                    raise ValueError(f'Missing or duplicate {split} source identity on line {line_number}')
                if any(name in identity.casefold() for name in ('humaneval', 'livebench')):
                    raise ValueError('Evaluation benchmark IDs cannot enter the training corpus')
                messages = row.get('messages')
                if (not isinstance(messages, list) or len(messages) < 2
                        or any(not isinstance(message, dict)
                               or message.get('role') not in ('system', 'user', 'assistant')
                               or not isinstance(message.get('content'), str)
                               or '\0' in message['content'] for message in messages)
                        or messages[-1].get('role') != 'assistant'
                        or messages[-2].get('role') != 'user'):
                    raise ValueError(f'Malformed source conversation on line {line_number}')
                prompt_messages = messages[:-1]
                answer = messages[-1]['content']
                if not answer:
                    raise ValueError(f'Empty assistant target on line {line_number}')
                prompt_hash = prompt_fingerprint(prompt_messages)
                if prompt_hash in forbidden or prompt_hash in seen_prompts:
                    raise ValueError('Benchmark prompt or duplicate prompt in source corpus')
                seen_ids.add(identity)
                seen_prompts.add(prompt_hash)
                output[split].append({
                    'id': identity,
                    'split': split,
                    'messages': prompt_messages,
                    'answer': answer,
                    'source': expected_source,
                    'source_config': expected_source_config,
                    'source_problem_id': source_problem_id,
                    'source_fingerprint': fingerprint,
                    'license': expected_license,
                })
        actual = digest.hexdigest()
        if actual.casefold() != expected.casefold():
            raise ValueError(f'{split} source SHA-256 mismatch')
        hashes[split] = actual
    if not output['train'] or not output['validation']:
        raise ValueError('Source training and validation splits must both be nonempty')
    combined = hashlib.sha256(json.dumps(
        hashes, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()
    return Corpus(output['train'], output['validation'], combined)


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


def teacher_forced_hf(model, qwen_tokenizer, k2_tokenizer, example, *, max_tokens: int):
    """Compute one complete-answer loss for the frozen HF Qwen/K2 towers.

    The trainable object is ``model``'s coupling bridge. Each tower reuses its
    native KV cache; bridge feedback is detached between answer tokens so the
    base checkpoints stay frozen and backpropagation remains bounded to the
    bridge at each step.
    """
    if type(max_tokens) is not int or max_tokens < 1:
        raise ValueError('Training token budget must be a positive integer')
    messages, answer = example.get('messages'), example.get('answer')
    if (not isinstance(messages, list) or not messages or not isinstance(answer, str) or not answer
            or any(not isinstance(message, dict) or not isinstance(message.get('content'), str)
                   for message in messages)):
        raise ValueError('HF Fusion examples need text-only messages and a nonempty answer')

    def prefix(tokenizer):
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )
        if not isinstance(text, str) or not text:
            raise ValueError('Tokenizer chat template returned an empty prompt')
        return text

    def encode(tokenizer, text):
        ids = tokenizer.encode(text, add_special_tokens=False)
        if not ids:
            raise ValueError('Tokenizer produced an empty Fusion sequence')
        return [int(token) for token in ids]

    def decode(tokenizer, ids, *, skip_special_tokens=False):
        return tokenizer.decode(
            ids, skip_special_tokens=skip_special_tokens,
            clean_up_tokenization_spaces=False,
        )

    q_prefix = prefix(qwen_tokenizer)
    k_prefix = prefix(k2_tokenizer)
    q_prefix_ids = encode(qwen_tokenizer, q_prefix)
    q_full_ids = encode(qwen_tokenizer, q_prefix + answer)
    if q_full_ids[:len(q_prefix_ids)] != q_prefix_ids:
        raise ValueError('Qwen tokenization changes across the prompt/answer boundary')
    target_ids = q_full_ids[len(q_prefix_ids):]
    if not target_ids or decode(qwen_tokenizer, target_ids) != answer:
        raise ValueError('Complete Qwen answer target does not round-trip exactly')
    eos = getattr(qwen_tokenizer, 'eos_token_id', None)
    if eos is not None:
        target_ids.append(int(eos))
    if len(target_ids) > max_tokens:
        raise ValueError(
            f'Cannot fit complete target: needs {len(target_ids)} tokens; budget is {max_tokens}'
        )

    k_ids = encode(k2_tokenizer, k_prefix)
    n_embedding = model.nanbeige.get_input_embeddings().weight
    k_embedding = model.k2.get_input_embeddings().weight
    if n_embedding.device.type == 'meta' or k_embedding.device.type == 'meta':
        raise RuntimeError('Fusion input embeddings must not be left on meta memory')
    n_device, k_device = n_embedding.device, k_embedding.device
    output = model.step(
        torch.tensor([q_prefix_ids], dtype=torch.long, device=n_device),
        torch.tensor([k_ids], dtype=torch.long, device=k_device),
        use_cache=True,
    )
    if output.first_cache is None or output.second_cache is None:
        raise RuntimeError('Both HF Fusion towers must return incremental decode caches')

    losses = []
    for index, target_id in enumerate(target_ids):
        label = torch.tensor([target_id], dtype=torch.long, device=output.logits.device)
        loss = F.cross_entropy(output.logits.float(), label)
        if not torch.isfinite(loss):
            raise ValueError('HF Fusion training loss is nonfinite')
        losses.append(loss)
        if index + 1 == len(target_ids):
            continue
        if eos is not None and target_id == int(eos):
            raise ValueError('Qwen EOS appeared before the complete answer ended')

        decoded = decode(qwen_tokenizer, target_ids[:index + 1], skip_special_tokens=True)
        if '\ufffd' not in decoded:
            if not answer.startswith(decoded):
                raise ValueError('Qwen target prefix does not reproduce the answer text')
            next_k_ids = encode(k2_tokenizer, k_prefix + decoded)
        else:
            # A tokenizer may split a UTF-8 code point across Qwen tokens.
            # Keep K2 on the last complete text prefix and replay its cache.
            next_k_ids = k_ids

        if len(next_k_ids) > len(k_ids) and next_k_ids[:len(k_ids)] == k_ids:
            next_k_input = next_k_ids[len(k_ids):]
            next_k_cache = output.second_cache
        else:
            next_k_input = next_k_ids
            next_k_cache = None
        if not next_k_input:
            next_k_input = next_k_ids
            next_k_cache = None

        output = model.step(
            torch.tensor([[target_id]], dtype=torch.long, device=n_device),
            torch.tensor([next_k_input], dtype=torch.long, device=k_device),
            feedback=type(output.feedback)(
                output.feedback.nanbeige.detach(), output.feedback.k2.detach(),
            ),
            first_cache=output.first_cache,
            second_cache=next_k_cache,
            use_cache=True,
        )
        if output.first_cache is None or output.second_cache is None:
            raise RuntimeError('Both HF Fusion towers must retain incremental decode caches')
        k_ids = next_k_ids

    return TeacherReport(torch.stack(losses).mean(), len(losses), len(losses) == len(target_ids))


def evaluate_hf_bridge(model, qwen_tokenizer, k2_tokenizer, examples, *, max_tokens: int):
    """Measure complete, held-out answer-token loss without updating weights."""
    rows = list(examples)
    if not rows:
        raise ValueError('HF Fusion validation split must not be empty')
    weighted_loss, token_count = 0.0, 0
    with torch.no_grad():
        for example in rows:
            report = teacher_forced_hf(
                model, qwen_tokenizer, k2_tokenizer, example, max_tokens=max_tokens,
            )
            if not report.complete:
                raise ValueError(f"Incomplete held-out target: {example.get('id', '<unknown>')}")
            value = float(report.loss)
            if not math.isfinite(value):
                raise ValueError('HF Fusion validation produced a nonfinite loss')
            weighted_loss += value * report.tokens
            token_count += report.tokens
    if token_count == 0:
        raise ValueError('HF Fusion validation produced no target tokens')
    return {'loss': weighted_loss / token_count, 'tokens': token_count, 'examples': len(rows)}


def train_hf_bridge(
    model,
    qwen_tokenizer,
    k2_tokenizer,
    train_examples,
    validation_examples,
    *,
    epochs: int,
    max_tokens: int,
    learning_rate: float = 1e-4,
    gradient_clip: float = 1.0,
    seed: int = 0,
):
    """Train the coupling bridge while leaving both pretrained towers frozen.

    Examples are shuffled deterministically each epoch. Optimization uses only
    bridge parameters; validation is measured after each epoch and never enters
    the gradient path. The caller owns checkpoint persistence and provenance.
    """
    if type(epochs) is not int or epochs < 1:
        raise ValueError('HF Fusion training epochs must be a positive integer')
    if type(max_tokens) is not int or max_tokens < 1:
        raise ValueError('HF Fusion training token budget must be a positive integer')
    if type(seed) is not int:
        raise ValueError('HF Fusion shuffle seed must be an integer')
    if not math.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError('HF Fusion learning rate must be finite and positive')
    if not math.isfinite(gradient_clip) or gradient_clip <= 0:
        raise ValueError('HF Fusion gradient clip must be finite and positive')

    train_rows, validation_rows = list(train_examples), list(validation_examples)
    if not train_rows or not validation_rows:
        raise ValueError('HF Fusion needs nonempty training and held-out validation splits')
    train_ids = [row.get('id') for row in train_rows]
    validation_ids = [row.get('id') for row in validation_rows]
    if (any(not isinstance(identity, str) or not identity for identity in train_ids + validation_ids)
            or len(set(train_ids + validation_ids)) != len(train_ids + validation_ids)):
        raise ValueError('HF Fusion train/validation examples need unique nonempty IDs')
    if any(name in identity.casefold() for identity in train_ids + validation_ids
           for name in ('humaneval', 'livebench')):
        raise ValueError('HF Fusion training cannot include evaluation benchmark IDs')
    train_prompts = {prompt_fingerprint(row['messages']) for row in train_rows}
    validation_prompts = {prompt_fingerprint(row['messages']) for row in validation_rows}
    if train_prompts & validation_prompts:
        raise ValueError('HF Fusion prompt leakage between train and validation splits')

    bridge_parameters = list(model.bridge_parameters())
    if not bridge_parameters or any(not parameter.requires_grad for parameter in bridge_parameters):
        raise ValueError('HF Fusion bridge must expose trainable parameters')
    for tower in (model.nanbeige, model.k2):
        tower.requires_grad_(False)
        tower.eval()
    optimizer = torch.optim.AdamW(bridge_parameters, lr=learning_rate)
    shuffle = torch.Generator(device='cpu').manual_seed(seed)
    history = []

    for epoch in range(epochs):
        model.train()
        permutation = torch.randperm(len(train_rows), generator=shuffle).tolist()
        total_loss, total_tokens = 0.0, 0
        for row_index in permutation:
            optimizer.zero_grad(set_to_none=True)
            report = teacher_forced_hf(
                model, qwen_tokenizer, k2_tokenizer, train_rows[row_index],
                max_tokens=max_tokens,
            )
            if not report.complete:
                raise ValueError(f"Incomplete training target: {train_rows[row_index].get('id')}")
            # Weight each example by its target length, making the reported
            # epoch loss and optimizer objective token-weighted as well.
            loss = report.loss * report.tokens
            if not torch.isfinite(loss):
                raise ValueError('HF Fusion training produced a nonfinite loss')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(bridge_parameters, gradient_clip)
            optimizer.step()
            total_loss += float(report.loss.detach()) * report.tokens
            total_tokens += report.tokens
        if not total_tokens:
            raise ValueError('HF Fusion epoch produced no target tokens')

        validation = evaluate_hf_bridge(
            model, qwen_tokenizer, k2_tokenizer, validation_rows,
            max_tokens=max_tokens,
        )
        history.append({
            'epoch': epoch + 1,
            'train_loss': total_loss / total_tokens,
            'train_tokens': total_tokens,
            'train_examples': len(train_rows),
            'validation_loss': validation['loss'],
            'validation_tokens': validation['tokens'],
            'validation_examples': validation['examples'],
        })
    return {
        'epochs': epochs,
        'learning_rate': learning_rate,
        'gradient_clip': gradient_clip,
        'shuffle_seed': seed,
        'max_tokens': max_tokens,
        'train_examples': len(train_rows),
        'validation_examples': len(validation_rows),
        'history': history,
        'scope': 'Bridge-only optimization; no benchmark or general-quality claim.',
    }
