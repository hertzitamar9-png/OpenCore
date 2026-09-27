"""Render each checkpoint's stored Jinja template without importing Transformers."""
from functools import lru_cache
import json

from jinja2 import TemplateError, nodes
from jinja2.ext import Extension
from jinja2.sandbox import ImmutableSandboxedEnvironment


class GenerationBlock(Extension):
    # Training masks use these tags; plain prompt rendering preserves the body.
    tags = {'generation'}

    def parse(self, parser):
        line = next(parser.stream).lineno
        body = parser.parse_statements(['name:endgeneration'], drop_needle=True)
        return nodes.CallBlock(self.call_method('_render'), [], [], body).set_lineno(line)

    def _render(self, caller):
        return caller()


def _raise(message):
    raise TemplateError(str(message))


def _tojson(value, ensure_ascii=False, indent=None, separators=None, sort_keys=False):
    return json.dumps(value, ensure_ascii=ensure_ascii, indent=indent,
                      separators=separators, sort_keys=sort_keys, allow_nan=False)


@lru_cache(maxsize=8)
def _compile(source):
    environment = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True,
        extensions=[GenerationBlock, 'jinja2.ext.loopcontrols'])
    environment.filters['tojson'] = _tojson
    environment.globals['raise_exception'] = _raise
    return environment.from_string(source)


def render_chat_template(source, messages, *, bos_token='', eos_token='', enable_thinking=False):
    if (not isinstance(source, str) or not source or '\0' in source
            or not isinstance(messages, list) or not messages):
        raise ValueError('Native chat template and messages must be nonempty')
    if any(not isinstance(message, dict) or message.get('role') not in
           ('system', 'user', 'assistant', 'tool') or not isinstance(message.get('content'), str)
           or '\0' in message['content'] for message in messages):
        raise ValueError('Native chat template requires supported roles and text messages')
    try:
        output = _compile(source).render(messages=messages, tools=None,
            bos_token=bos_token, eos_token=eos_token, add_generation_prompt=True,
            enable_thinking=enable_thinking)
        if not output or '\0' in output:
            raise TemplateError('Chat template rendered an empty or invalid prompt')
        return output
    except TemplateError as error:
        raise ValueError(f'Native checkpoint chat template failed: {error}') from error
