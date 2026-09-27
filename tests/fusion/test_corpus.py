"""Corpus integrity, complete code and public-input screening without inference."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src-tauri/resources'))


def module():
    try:
        from fusion import corpus
    except ImportError:
        pytest.fail('The complete-source, benchmark-screened corpus preparation is missing')
    return corpus


def benchmark_file(tmp_path, prompt='Implement this deliberately unusual transformation over integer histories.\ndef benchmark_function(items):\n    pass\n'):
    path = tmp_path / 'inputs.json'
    path.write_text(json.dumps({'schema': 1, 'benchmark': 'humaneval', 'rows': [{
        'id': 'HumanEval/fixture', 'prompt': prompt,
        'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}]}), encoding='utf-8')
    return path


def test_public_benchmark_screen_rejects_function_names_and_wrapped_prompt_overlap(tmp_path):
    screen = module().BenchmarkScreen.from_files([benchmark_file(tmp_path)])
    assert screen.reason('Please solve this', 'def benchmark_function(items):\n    return items') == 'benchmark_function'
    assert screen.reason('New wrapper: implement this deliberately unusual transformation over integer histories. Be concise.', '') == 'benchmark_phrase'
    assert screen.reason('Write a function that computes a weighted score.', 'def weighted_score(x):\n    return x * 3') is None


def test_changed_public_input_hash_cannot_silently_change_the_screen(tmp_path):
    path = benchmark_file(tmp_path)
    value = json.loads(path.read_text())
    value['rows'][0]['prompt'] += 'changed'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='identity'):
        module().BenchmarkScreen.from_files([path])


def test_complete_source_can_reconstruct_a_continuation_chain_but_rejects_a_cutoff_answer():
    corpus = module()
    row = {'messages': [{'role': 'user', 'content': 'Implement a scoring function.'},
        {'role': 'assistant', 'content': '```python\ndef score(x):'},
        {'role': 'user', 'content': corpus.CONTINUATIONS[0]},
        {'role': 'assistant', 'content': '    return x * 7\n```'}]}
    prompt, answer = corpus.reconstruct(row)
    assert prompt == row['messages'][0]['content'] and 'return x * 7' in answer
    assert corpus.python_fingerprint(answer)
    assert corpus.python_fingerprint('```python\ndef unfinished(x):\n') is None


def test_split_has_unique_prompts_and_answer_code_and_exact_source_provenance(tmp_path):
    corpus = module()
    source = tmp_path / 'full.jsonl'
    rows = [{'messages': [{'role': 'user', 'content': f'Implement independent scoring function {i}.'},
                           {'role': 'assistant', 'content': f'```python\ndef score_{i}(x):\n    return x * {i + 2}\n```'}]}
            for i in range(80)]
    # The same answer with different prose must not straddle training and validation.
    rows.append({'messages': [{'role': 'user', 'content': 'Alternative wording.'},
                              {'role': 'assistant', 'content': 'A solution:\n' + rows[0]['messages'][1]['content']}]})
    source.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    destination = tmp_path / 'prepared'
    result = corpus.prepare(source, destination, [benchmark_file(tmp_path)], train_count=8, validation_count=3)
    records = [json.loads(line) for line in (destination / 'corpus.jsonl').read_text().splitlines()]
    assert len(records) == 11 and result['statistics']['duplicate_answer_code'] == 1
    assert {row['split'] for row in records} == {'train', 'validation'}
    assert len({row['provenance']['code_syntax_sha256'] for row in records}) == 11
    assert all(row['provenance']['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest() for row in records)
    assert result['corpus_sha256'] == hashlib.sha256((destination / 'corpus.jsonl').read_bytes()).hexdigest()
    assert result['semantic_verification'] is False and result['models_loaded'] is False
