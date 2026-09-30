"""Single generation loop over two full native LFM towers; no answer candidates."""
from __future__ import annotations
import codecs
import ctypes as C
import json
import os
from pathlib import Path


def native_library_path():
    return Path(__file__).parent / 'native' / 'fusioncore-v2.dll'


def disable_native_reasoning(prompt: bytes) -> bytes:
    """Close Qwen's default open thinking section before generating the answer."""
    if prompt.endswith(b'<think>\n'):
        return prompt + b'\n</think>\n\n'
    return prompt + b'<think>\n\n</think>\n\n'


def prepare_native_prompt(prompt: bytes, thinking_budget_tokens: int | None) -> bytes:
    if thinking_budget_tokens is None:
        return disable_native_reasoning(prompt)
    if (isinstance(thinking_budget_tokens, bool) or not isinstance(thinking_budget_tokens, int)
            or thinking_budget_tokens < 0):
        raise ValueError('thinking_budget_tokens must be a non-negative integer')
    if prompt.endswith(b'<think>\n'):
        return prompt
    return prompt + b'<think>\n'


class FusionCoreModel:
    def __init__(self, checkpoint: Path, runtime: Path, context: int, recompute: bool):
        self.directories = [os.add_dll_directory(str(runtime.resolve())),
                            os.add_dll_directory(str((Path(__file__).parent / 'native').resolve()))]
        self.lib = C.CDLL(str(native_library_path()))
        self.lib.fc_error.restype = C.c_char_p
        self.lib.fc_create.argtypes = [C.c_char_p, C.c_int, C.c_int, C.c_int]
        self.lib.fc_create.restype = C.c_void_p
        self.lib.fc_destroy.argtypes = [C.c_void_p]
        self.lib.fc_clear_brain.argtypes = [C.c_void_p, C.c_int]
        self.lib.fc_format.argtypes = [C.c_void_p, C.POINTER(C.c_char_p), C.POINTER(C.c_char_p), C.c_int, C.c_void_p, C.c_int]
        self.lib.fc_start.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p, C.c_int]
        self.lib.fc_next.argtypes = [C.c_void_p, C.c_void_p, C.c_int, C.POINTER(C.c_int)]
        self.lib.fc_brain_start.argtypes = [C.c_void_p, C.c_int, C.c_char_p, C.c_int]
        self.lib.fc_brain_next.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_int)]
        self.lib.fc_capacity.argtypes = [C.c_void_p]
        self.lib.fc_count.argtypes = [C.c_void_p, C.c_char_p]
        self.lib.fc_tokenize.argtypes = [C.c_void_p, C.c_char_p, C.POINTER(C.c_int), C.c_int]
        self.lib.fc_detokenize.argtypes = [C.c_void_p, C.POINTER(C.c_int), C.c_int, C.c_void_p, C.c_int]
        self.lib.fc_parameters.argtypes = [C.c_void_p]
        self.lib.fc_parameters.restype = C.c_ulonglong
        self.handle = self.lib.fc_create(os.fsencode(checkpoint), context, 99, int(recompute))
        if not self.handle:
            raise RuntimeError(self.error())
        self.context = self.lib.fc_capacity(self.handle)
        self.parameters = self.lib.fc_parameters(self.handle)
        self.recompute = recompute
        self.completion_tokens = 0

    def error(self):
        return self.lib.fc_error().decode('utf-8', errors='replace')

    def close(self):
        if self.handle:
            self.lib.fc_destroy(self.handle)
            self.handle = None
        for directory in self.directories:
            directory.close()

    def format(self, messages):
        texts = []
        for message in messages:
            content = message.get('content') or ''
            if not isinstance(content, str):
                raise ValueError('LFM is a text model; select the vision-capable OpenCore model for image attachments')
            texts.append(content.encode('utf-8'))
        roles = [message['role'].encode('utf-8') for message in messages]
        role_array = (C.c_char_p * len(roles))(*roles)
        text_array = (C.c_char_p * len(texts))(*texts)
        size = self.lib.fc_format(self.handle, role_array, text_array, len(roles), None, 0)
        if size < 0:
            raise RuntimeError(self.error())
        buffer = C.create_string_buffer(size + 1)
        result = self.lib.fc_format(self.handle, role_array, text_array, len(roles), buffer, size + 1)
        if result < 0:
            raise RuntimeError(self.error())
        return bytes(buffer.raw[:result])

    def start(self, messages, thinking_budget_tokens=None):
        prompts = []
        for instruction in (
            'Construct the requested answer directly, preserving every user constraint.',
            'Verify the requested answer carefully for correctness and missing constraints.',
        ):
            prompts.append(prepare_native_prompt(
                self.format([{'role': 'system', 'content': instruction}, *messages]),
                thinking_budget_tokens))
        budget = -1 if thinking_budget_tokens is None else thinking_budget_tokens
        prompt_tokens = self.lib.fc_start(self.handle, prompts[0], prompts[1], budget)
        if prompt_tokens < 0:
            raise RuntimeError(self.error())
        self.completion_tokens = 0
        return prompt_tokens

    def count(self, text):
        result = self.lib.fc_count(self.handle, text.encode('utf-8'))
        if result < 0:
            raise RuntimeError(self.error())
        return result

    def tokenize(self, text):
        encoded = text.encode('utf-8')
        count = self.lib.fc_tokenize(self.handle, encoded, None, 0)
        if count < 0:
            raise RuntimeError(self.error())
        ids = (C.c_int * count)()
        result = self.lib.fc_tokenize(self.handle, encoded, ids, count)
        if result != count:
            raise RuntimeError('Native tokenizer length changed')
        return list(ids)

    def detokenize(self, ids):
        native = (C.c_int * len(ids))(*ids)
        size = self.lib.fc_detokenize(self.handle, native, len(ids), None, 0)
        size = abs(size)
        buffer = C.create_string_buffer(size + 1)
        length = self.lib.fc_detokenize(self.handle, native, len(ids), buffer, len(buffer))
        if length < 0:
            raise RuntimeError('Native detokenizer failed')
        return buffer.raw[:length].decode('utf-8')

    def stream(self, maximum):
        decoder = codecs.getincrementaldecoder('utf-8')()
        buffer = C.create_string_buffer(32768)
        token = C.c_int()
        self.finish_reason = 'length'
        try:
            for _ in range(maximum):
                length = self.lib.fc_next(self.handle, buffer, len(buffer), C.byref(token))
                if length < 0:
                    raise RuntimeError(self.error())
                if length == 0:
                    self.finish_reason = 'stop'
                    break
                self.completion_tokens += 1
                text = decoder.decode(buffer.raw[:length - 1])
                if text:
                    yield text
            tail = decoder.decode(b'', final=True)
            if tail:
                yield tail
        finally:
            for brain in range(2):
                self.lib.fc_clear_brain(self.handle, brain)
