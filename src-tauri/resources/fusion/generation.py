"""One coupled native decoding loop; both complete towers contribute each step."""
from dataclasses import dataclass
import threading

import torch

from .canonical import CanonicalPrefixes, inspect_surfaces, split_utf8_prefix
from .native import NativeCancelled, NativeHead


class GenerationCancelled(NativeCancelled):
    pass


@dataclass(frozen=True)
class GenerationEvent:
    text: str
    completion_tokens: int
    finish_reason: str | None = None


class SingleStreamDecoder:
    def __init__(self, native, bridge):
        self.native, self.bridge = native, bridge.eval()
        self._surfaces = None

    def prepare(self, messages, *, max_tokens):
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError('The generation token budget must be a positive integer')
        prefixes = [self.native.format(brain, messages) for brain in (0, 1)]
        counts = [len(self.native.tokenize(brain, prefixes[brain], special=False)) for brain in (0, 1)]
        if any(count + max_tokens > self.native.geometry[brain]['capacity'] for brain, count in enumerate(counts)):
            raise ValueError('Requested response exceeds one of the two finite native context capacities')
        return prefixes, max(counts)

    def _vocabulary(self):
        if self._surfaces is None:
            self._surfaces = self.native.surfaces() if hasattr(self.native, 'surfaces') else inspect_surfaces(self.native)
        return self._surfaces[0]

    def _select(self, scores, suffix):
        if scores.shape != (1, self.native.geometry[0]['vocab']) or not torch.isfinite(scores).all():
            raise RuntimeError('The coupled model returned nonfinite or invalid next-token scores')
        vocabulary = self._vocabulary()
        allowed = scores[0].float().clone()
        _, pending = split_utf8_prefix(suffix)
        while True:
            token = int(allowed.argmax())
            if not torch.isfinite(allowed[token]):
                raise RuntimeError('No valid UTF-8 token can continue the coupled response')
            flag, piece = vocabulary.flags[token], vocabulary.pieces[token]
            if flag & 1 and not pending:
                return token, b'', True
            if not flag and piece:
                try:
                    split_utf8_prefix(suffix + piece)
                    return token, piece, False
                except ValueError:
                    pass
            allowed[token] = -torch.inf

    def generate(self, prepared, *, max_tokens, cancel=None):
        cancel = threading.Event() if cancel is None else cancel
        prefixes = CanonicalPrefixes(self.native, prepared)
        heads = [NativeHead(self.native, brain) for brain in (0, 1)]
        suffix, feedback, emitted, count = bytearray(), None, '', 0
        self.native.clear()
        try:
            with torch.no_grad():
                for _ in range(max_tokens):
                    if cancel.is_set():
                        raise GenerationCancelled('TwinCore generation cancelled')
                    ids = prefixes.encode(bytes(suffix))
                    if any(len(ids[brain]) > self.native.geometry[brain]['capacity'] for brain in (0, 1)):
                        raise ValueError('The coupled response reached its measured native context capacity')
                    bias = (None, None) if feedback is None else (
                        feedback.nanbeige.detach().float().cpu().numpy(), feedback.k2.detach().float().cpu().numpy())
                    features = self.native.step(*ids, *bias)
                    prediction = self.bridge(features.nanbeige_hidden, features.k2_hidden,
                                             features.nanbeige_logits, features.k2_logits, *heads)
                    if cancel.is_set():
                        raise GenerationCancelled('TwinCore generation cancelled')
                    token, piece, eos = self._select(prediction.logits, bytes(suffix))
                    count += 1
                    feedback = prediction.feedback
                    if eos:
                        yield GenerationEvent('', count, 'stop')
                        return
                    suffix.extend(piece)
                    complete, pending = split_utf8_prefix(bytes(suffix))
                    if not complete.startswith(emitted):
                        raise RuntimeError('The native generated text changed an already emitted prefix')
                    delta, emitted = complete[len(emitted):], complete
                    yield GenerationEvent(delta, count)
                if pending:
                    raise RuntimeError('The token budget ended with an incomplete UTF-8 character')
                yield GenerationEvent('', count, 'length')
        except NativeCancelled:
            raise
        except Exception:
            self.native.close()
            raise
        finally:
            if not self.native.closed:
                self.native.clear()
