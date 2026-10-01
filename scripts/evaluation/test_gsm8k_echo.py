import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from gsm8k_echo import numeric_answer, load_captures

class CaptureIntegrity(unittest.TestCase):
    def test_numeric_answer_requires_final_marker(self):
        self.assertIsNone(numeric_answer('Intermediate result is 42'))
        self.assertEqual(numeric_answer('#### 1,200.00'), '1.2E+3')
        self.assertEqual(numeric_answer('#### 41\nCorrection: #### 42'), '42')

    def test_resume_preserves_valid_records_and_rejects_changed_data(self):
        data = [{'question': 'Six times seven?', 'answer': '#### 42'}]
        row = dict(index=0, question_sha256=hashlib.sha256(data[0]['question'].encode()).hexdigest(),
                   response='#### 42', gold='42', predicted='42', correct=True)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'responses.jsonl'
            raw = json.dumps(row)+'\n'; path.write_text(raw)
            self.assertEqual(load_captures(path, data)[0], row)
            for bad in [dict(row, correct=False), dict(row, question_sha256='wrong'), dict(row, index=1)]:
                saved = json.dumps(bad)+'\n'; path.write_text(saved)
                with self.assertRaisesRegex(RuntimeError, 'original file preserved'):
                    load_captures(path, data)
                self.assertEqual(path.read_text(), saved)
            path.write_text(raw+raw)
            with self.assertRaisesRegex(RuntimeError, 'duplicate'):
                load_captures(path, data)
            path.write_text(raw+'{"index":')
            with self.assertRaisesRegex(RuntimeError, 'line 2'):
                load_captures(path, data)

if __name__ == '__main__': unittest.main()
