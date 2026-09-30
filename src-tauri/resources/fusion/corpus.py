"""Select complete source answers, with explicit provenance and input screening."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import unicodedata
import warnings

from .q6_identity import canonical, file_digest

CONTINUATIONS = (
    'Continue the code from exactly where you stopped. Do not repeat anything already written and do not summarise.',
    'Keep going - finish the implementation. New code only, same style.',
    'Carry on. Complete the remaining functions in full.',
)


def _words(text):
    return re.findall(r'\w+', unicodedata.normalize('NFKC', text).casefold())


def _shingles(text):
    words = _words(text)
    return {hashlib.blake2b('\0'.join(words[index:index + 8]).encode(), digest_size=16).digest()
            for index in range(max(0, len(words) - 7))}


class BenchmarkScreen:
    def __init__(self):
        self.functions, self.shingles, self.inputs = set(), set(), []

    @classmethod
    def from_files(cls, paths):
        screen = cls()
        if not paths:
            raise ValueError('Public benchmark input files are required for screening')
        for path in map(Path, paths):
            value = json.loads(path.read_text(encoding='utf-8'))
            rows = value.get('rows')
            if value.get('schema') != 1 or not isinstance(rows, list) or not rows:
                raise ValueError('Invalid benchmark input identity')
            for row in rows:
                prompt = row.get('prompt')
                if not isinstance(prompt, str) or hashlib.sha256(prompt.encode()).hexdigest() != row.get('prompt_sha256'):
                    raise ValueError('Benchmark prompt identity changed')
                screen.shingles.update(_shingles(prompt))
                screen.functions.update(re.findall(r'\bdef\s+(\w+)\s*\(', prompt))
            screen.inputs.append({'file': str(path.resolve()), 'sha256': file_digest(path),
                'benchmark': value.get('benchmark'), 'release_date': value.get('release_date'), 'samples': len(rows)})
        return screen

    def reason(self, prompt, answer):
        functions = set(re.findall(r'\bdef\s+(\w+)\s*\(', prompt + '\n' + answer))
        if functions & self.functions:
            return 'benchmark_function'
        if (_shingles(prompt) | _shingles(answer)) & self.shingles:
            return 'benchmark_phrase'
        return None


def reconstruct(row):
    messages = row.get('messages')
    if not isinstance(messages, list) or len(messages) < 2 or len(messages) % 2:
        return None
    if any(not isinstance(message, dict) or not isinstance(message.get('content'), str) for message in messages):
        return None
    if any(message.get('role') != ('user' if index % 2 == 0 else 'assistant') for index, message in enumerate(messages)):
        return None
    if any(message['content'] not in CONTINUATIONS for message in messages[2::2]):
        return None
    return messages[0]['content'], '\n\n'.join(message['content'] for message in messages[1::2])


def python_fingerprint(answer):
    blocks = re.findall(r'```[ \t]*(?:python|py)?[ \t]*\r?\n(.*?)```', answer, re.S)
    if '```' in answer and answer.count('```') != 2 * len(blocks):
        return None
    blocks = blocks or [answer]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', SyntaxWarning)
            trees = [ast.parse(block.strip()) for block in blocks]
    except (SyntaxError, ValueError, SyntaxWarning):
        return None
    if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
               for tree in trees for node in tree.body):
        return None
    # Comments and surrounding prose cannot put the same answer in two splits.
    return hashlib.sha256(canonical([ast.dump(tree, include_attributes=False) for tree in trees])).hexdigest()


def prepare(source, destination, benchmark_inputs, *, train_count=512, validation_count=64,
            max_prompt_bytes=384, max_answer_bytes=1024):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if min(train_count, validation_count, max_prompt_bytes, max_answer_bytes) < 1:
        raise ValueError('Corpus counts and size bounds must be positive')
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError('Use a fresh corpus directory under an existing parent')
    screen = BenchmarkScreen.from_files(benchmark_inputs)
    statistics, digest = Counter(), hashlib.sha256()
    prompts, codes, pool = set(), set(), []
    with source.open('rb') as stream:
        for number, line in enumerate(stream, 1):
            digest.update(line)
            statistics['source_rows'] += 1
            if len(line) > 2_000_000:
                statistics['oversized_record'] += 1
                continue
            row = json.loads(line)
            complete = reconstruct(row)
            if complete is None:
                statistics['unsupported_turn_chain'] += 1
                continue
            prompt, answer = complete
            if (not prompt.strip() or not answer.strip() or '\0' in prompt + answer
                    or len(prompt.encode()) > max_prompt_bytes or len(answer.encode()) > max_answer_bytes):
                statistics['length_or_content_rejected'] += 1
                continue
            code = python_fingerprint(answer)
            if code is None:
                statistics['python_syntax_rejected'] += 1
                continue
            reason = screen.reason(prompt, answer)
            if reason:
                statistics[reason] += 1
                continue
            prompt_sha = hashlib.sha256(canonical({'content': prompt.replace('\r\n', '\n')})).hexdigest()
            if prompt_sha in prompts:
                statistics['duplicate_prompt'] += 1
                continue
            if code in codes:
                statistics['duplicate_answer_code'] += 1
                continue
            prompts.add(prompt_sha)
            codes.add(code)
            split = 'validation' if int(code[:8], 16) % 8 == 0 else 'train'
            pool.append({'id': 'local-code-' + code, 'split': split,
                'messages': [{'role': 'user', 'content': prompt}], 'answer': answer,
                'provenance': {'source_line': number, 'prompt_sha256': prompt_sha,
                               'answer_sha256': hashlib.sha256(answer.encode()).hexdigest(), 'code_syntax_sha256': code}})
    source_sha = digest.hexdigest()
    selected = []
    for split, count in (('train', train_count), ('validation', validation_count)):
        candidates = sorted((row for row in pool if row['split'] == split), key=lambda row: row['id'])
        if len(candidates) < count:
            raise ValueError(f'Only {len(candidates)} screened {split} examples, fewer than the requested {count}')
        selected.extend(candidates[:count])
        statistics['selected_' + split] = count
        statistics['available_' + split] = len(candidates)
    for row in selected:
        row['provenance']['source_sha256'] = source_sha
    content = b''.join(canonical(row) + b'\n' for row in selected)
    if shutil.disk_usage(destination.parent).free < 100_000_000_000 + len(content) + 1_048_576:
        raise ValueError('Corpus output would violate the 100 GB free disk reserve')
    report = {'schema': 1, 'source_file': str(source), 'source_sha256': source_sha,
        'source_bytes': source.stat().st_size, 'corpus_sha256': hashlib.sha256(content).hexdigest(),
        'corpus_bytes': len(content), 'statistics': dict(statistics), 'benchmark_inputs': screen.inputs,
        'builder_sha256': file_digest(__file__), 'models_loaded': False, 'semantic_verification': False,
        'screen': 'Exact benchmark function names and normalized eight-word shingles, including answer code. Targets were not read.',
        'scope': 'Complete Python syntax and exact local provenance only. Lexical screening does not prove semantic decontamination or solution correctness.',
        'upstream_provenance': 'The original local builder names Magicoder-Evol-Instruct-110K and CodeFeedback-Filtered-Instruction; immutable upstream revisions and row-level source IDs were not retained.'}
    report['receipt_sha256'] = hashlib.sha256(canonical(report)).hexdigest()
    destination.mkdir(exist_ok=False)
    (destination / 'corpus.jsonl').write_bytes(content)
    (destination / 'preparation.json').write_bytes(canonical(report) + b'\n')
    return report
