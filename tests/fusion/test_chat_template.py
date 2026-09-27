"""Both native GGUF templates, not a guessed shared ChatML replacement."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src-tauri/resources'))


def renderer():
    from fusion.chat_template import render_chat_template
    return render_chat_template


def test_generation_blocks_and_latest_user_are_preserved_in_order():
    template = ("{{ bos_token }}{% for message in messages %}"
                "{{ message.role }}:{{ message.content }}{{ eos_token }}{% endfor %}"
                "{% generation %}{% if add_generation_prompt %}assistant:{% endif %}{% endgeneration %}")
    text = renderer()(template, [{'role': 'user', 'content': 'old'},
                                 {'role': 'assistant', 'content': 'previous'},
                                 {'role': 'user', 'content': '\u05e9\u05dc\u05d5\u05dd'}],
                      bos_token='<bos>', eos_token='<eos>')
    assert text == '<bos>user:old<eos>assistant:previous<eos>user:\u05e9\u05dc\u05d5\u05dd<eos>assistant:'


@pytest.mark.parametrize('name', ['nanbeige', 'k2'])
def test_full_stored_gguf_template_renders_its_own_controls(name):
    fixture = json.loads((Path(__file__).with_name('fixtures') / f'{name}-chat-template.json').read_text(encoding='utf-8'))
    text = renderer()(fixture['template'], [{'role': 'user', 'content': 'latest \u05d0'}],
                      bos_token='<BOS>', eos_token='<EOS>')
    assert 'latest \u05d0' in text and '\ufffd' not in text
    if name == 'nanbeige':
        assert text.startswith('<|im_start|>system') and '<|ifm|im_start|>' not in text
        assert '<|im_start|>assistant' in text and '</think>' in text
    else:
        assert text.startswith('<BOS><|ifm|im_start|>user')
        assert '<|ifm|im_start|>assistant' in text and '</ifm|think>' in text


def test_template_exception_and_sandbox_violation_cannot_fall_back_to_chatml():
    render = renderer()
    messages = [{'role': 'user', 'content': 'hello'}]
    with pytest.raises(ValueError, match='template'):
        render('{{ raise_exception("unsupported message") }}', messages)
    with pytest.raises(ValueError, match='template'):
        render('{{ messages.__class__.__mro__ }}', messages)
