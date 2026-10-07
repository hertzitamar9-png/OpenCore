"""Exact finite five-level expansion, including the FP16 sign of zero."""
import importlib.util
from types import SimpleNamespace
import unittest


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'Requires the speech runtime NumPy')
class FiveValueExpansionTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np

    def fixture(self, codes, lo_bits, hi_bits, high=None):
        np = self.np
        codes = np.asarray(codes, dtype=np.uint8)
        o, i = codes.shape
        padded = np.ones((o, ((i + 4) // 5) * 5), dtype=np.uint8)
        padded[:, :i] = codes
        packed = sum(padded.reshape(o, -1, 5)[:, :, k].astype(np.uint16) * 3**k
                     for k in range(5)).astype(np.uint8).tobytes()
        high = np.asarray(high if high is not None else np.zeros_like(codes), dtype=bool)
        nz = codes != 1
        bits = np.packbits(high[nz], bitorder='little').tobytes()
        lo = np.asarray(lo_bits, dtype=np.uint16).view(np.float16)
        hi = np.asarray(hi_bits, dtype=np.uint16).view(np.float16)
        blob = packed + bits + lo.tobytes() + hi.tobytes()
        raw = dict(sign=codes.astype(np.int8) - 1, is_hi=high & nz, lo=lo.copy(), hi=hi.copy())
        with np.errstate(invalid='ignore'):
            expected = (raw['sign'].astype(np.float16) * np.where(raw['is_hi'], hi[:, None], lo[:, None])).astype(np.float16)
        return blob, codes.shape, expected, raw

    def trits(self, buf, o, i):
        # Independent fixture unpacker; production always uses publisher _trits.
        np = self.np
        packed = np.frombuffer(buf, dtype=np.uint8).reshape(o, -1)
        return np.stack([(packed.astype(np.uint16) // 3**k) % 3 for k in range(5)], -1).reshape(o, -1)[:, :i].astype(np.uint8)

    def test_finite_values_signed_zeros_subnormals_negative_levels_and_odd_rows(self):
        from phonon_loading import optimized_five_value
        np = self.np
        levels = [0x0000, 0x8000, 0x0001, 0x8001, 0x3c00, 0xbc00, 0x7bff, 0xfbff]
        for width in (1, 3, 5, 7, 11):
            codes = np.resize(np.array([0, 1, 2, 0, 2, 1], dtype=np.uint8), (len(levels), width))
            high = np.resize(np.array([False, True, True], dtype=bool), codes.shape)
            blob, shape, expected, expected_raw = self.fixture(codes, levels, levels[::-1], high)
            raw = {}
            def forbidden(*args):
                self.fail('Finite learned levels must use the exact accelerated path')
            actual = optimized_five_value(blob, shape, trits=self.trits, original=forbidden, raw=raw)
            self.assertTrue(np.array_equal(actual.view(np.uint16), expected.view(np.uint16)), width)
            self.assertEqual(actual.dtype, np.float16)
            for name, value in expected_raw.items():
                self.assertTrue(np.array_equal(raw[name].view(np.uint8), value.view(np.uint8)), name)

    def test_nonfinite_levels_delegate_without_changing_nan_bits_or_raw(self):
        from phonon_loading import optimized_five_value
        np = self.np
        for level in (0x7c00, 0xfc00, 0x7e35, 0xfe35, 0x7c01):
            blob, shape, expected, _ = self.fixture([[0, 1, 2]], [level], [0x3c00])
            raw, calls = {}, []
            def original(given_blob, given_shape, given_raw):
                calls.append((given_blob, given_shape, given_raw))
                given_raw['publisher'] = True
                return expected
            actual = optimized_five_value(blob, shape, trits=self.trits, original=original, raw=raw)
            self.assertIs(actual, expected)
            self.assertEqual(calls, [(blob, shape, raw)])
            self.assertEqual(raw, {'publisher': True})

    def test_every_finite_fp16_level_matches_multiply_bit_for_bit(self):
        from phonon_loading import optimized_five_value
        np = self.np
        patterns = np.arange(65536, dtype=np.uint16)
        levels = patterns[np.isfinite(patterns.view(np.float16))]
        codes = np.tile(np.array([0, 1, 2], dtype=np.uint8), (len(levels), 1))
        blob, shape, expected, _ = self.fixture(codes, levels, levels[::-1], codes == 2)
        actual = optimized_five_value(blob, shape, trits=self.trits, original=lambda *args: self.fail('finite fallback'))
        self.assertTrue(np.array_equal(actual.view(np.uint16), expected.view(np.uint16)))

    def test_truncated_and_trailing_payloads_are_rejected(self):
        from phonon_loading import optimized_five_value
        blob, shape, _, _ = self.fixture([[0, 1, 2, 0, 2, 1, 0]], [0x0001], [0x7bff])
        for damaged in (blob[:1], blob[:-1], blob[:-2], blob + b'x'):
            with self.subTest(bytes=len(damaged)), self.assertRaises((ValueError, AssertionError)):
                optimized_five_value(damaged, shape, trits=self.trits, original=lambda *args: None)

    def test_scoped_decoder_interposition_restores_on_success_and_exception(self):
        from phonon_loading import accelerated_five_value_expansion
        original = lambda *args: None
        reader = SimpleNamespace(_five_value=original, _trits=self.trits)
        for fail in (False, True):
            try:
                with accelerated_five_value_expansion(reader):
                    self.assertIsNot(reader._five_value, original)
                    if fail:
                        raise RuntimeError('fixture error')
            except RuntimeError:
                pass
            self.assertIs(reader._five_value, original)


if __name__ == '__main__':
    unittest.main()
