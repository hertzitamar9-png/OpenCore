"""HTTP engine for a trained, resource-qualified full-Q6 TwinCore pair."""
import json
from pathlib import Path
import threading

from .q6_identity import file_digest
from .q6_preflight import preflight
from .qualification import execution_configuration, validate_qualification


class TwinCoreEngine:
    def __init__(self, nanbeige, k2, adapter, qualification, dll, runtime, *,
                 context_tokens=1024, rank=256, seed=7, recompute=False):
        configuration = execution_configuration(context=context_tokens, rank=rank, seed=seed, recompute=recompute)
        resource = preflight(context_tokens, Path(adapter).resolve().parent)
        report = json.loads(Path(qualification).read_text(encoding='utf-8'))
        validate_qualification(report, configuration, resource['gpu']['uuid'])
        # Reject a missing training artifact before allocating any model.
        if not (Path(adapter) / 'receipt.json').is_file():
            raise ValueError('A trained, hash-bound TwinCore adapter is required')
        from .adapter import load_adapter
        from .generation import SingleStreamDecoder
        from .q6_pair import open_pair
        self.pair = open_pair(nanbeige, k2, dll, runtime, context=context_tokens,
                              rank=rank, seed=seed, recompute=recompute)
        try:
            validate_qualification(report, self.pair.configuration,
                                   self.pair.resource_plan['gpu']['uuid'], binding=self.pair.binding)
            receipt = load_adapter(Path(adapter), self.pair.bridge, self.pair.binding)
            self.decoder = SingleStreamDecoder(self.pair.native, self.pair.bridge)
            self.context_tokens = context_tokens
            self.model_id = 'opencore-twincore-q6-' + ('echo' if recompute else 'kv')
            self.configuration = configuration
            self.evidence = {'precision': 'Q6_K', 'binding': self.pair.binding,
                'adapter_receipt_sha256': receipt['receipt_sha256'], 'qualification_sha256': file_digest(qualification),
                'scope': 'Trained frozen-Q6 coupling. Native generation and coding benchmarks remain independent qualification gates.'}
            self._cancel = threading.Event()
        except Exception:
            self.pair.close()
            raise

    @property
    def closed(self):
        return self.pair.native.closed

    def close(self):
        self.cancel()
        self.pair.close()

    def begin_request(self, event):
        self._cancel = event

    def cancel(self):
        self._cancel.set()
        self.pair.native.cancel()

    def tokenize(self, text):
        return self.pair.native.tokenize(0, text, special=False).tolist()

    def prepare(self, messages, tools, max_new_tokens):
        from .tool_protocol import prepare_messages
        return self.decoder.prepare(prepare_messages(messages, tools), max_tokens=max_new_tokens)

    def generate(self, prepared, max_new_tokens):
        yield from self.decoder.generate(prepared, max_tokens=max_new_tokens, cancel=self._cancel)
