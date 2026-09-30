import unittest

from fusion import native_library_path, disable_native_reasoning, prepare_native_prompt


class FusionNativeLibraryTests(unittest.TestCase):
    def test_runtime_uses_side_by_side_incremental_kv_library(self):
        library = native_library_path()
        self.assertEqual(library.name, "fusioncore-v2.dll")
        self.assertTrue(library.is_file())

    def test_native_prompt_closes_the_default_thinking_section(self):
        self.assertEqual(
            b"<|im_start|>assistant\n<think>\n\n</think>\n\n",
            disable_native_reasoning(b"<|im_start|>assistant\n<think>\n"),
        )

    def test_native_prompt_adds_an_explicit_no_thinking_section_when_missing(self):
        self.assertEqual(
            b"<|im_start|>assistant\n<think>\n\n</think>\n\n",
            disable_native_reasoning(b"<|im_start|>assistant\n"),
        )

    def test_native_prompt_preserves_reasoning_when_budget_is_set(self):
        prompt = b"<|im_start|>assistant\n<think>\n"
        self.assertEqual(prompt, prepare_native_prompt(prompt, 256))

    def test_native_prompt_rejects_invalid_reasoning_budget(self):
        with self.assertRaises(ValueError):
            prepare_native_prompt(b"prompt", -1)


if __name__ == "__main__":
    unittest.main()
