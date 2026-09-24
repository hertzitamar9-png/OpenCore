import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from visual_data import DatasetError, validate_dataset


class VisualDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "images").mkdir()
        Image.new("RGB", (100, 80), "white").save(self.root / "images" / "0.png")
        Image.new("RGB", (100, 80), "black").save(self.root / "images" / "1.png")

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, episodes):
        with (self.root / "episodes.jsonl").open("w", encoding="utf-8") as out:
            for episode in episodes:
                out.write(json.dumps(episode) + "\n")

    def episode(self, episode_id="paint-001", split="train", source="local-demo"):
        return {
            "episode_id": episode_id,
            "split": split,
            "source": source,
            "app": "paint",
            "instruction": "Draw a flower and save it",
            "steps": [
                {"screenshot": "images/0.png", "action": {"type": "click", "x": 0.25, "y": 0.5}},
                {"screenshot": "images/1.png", "action": {"type": "key", "keys": ["ctrl", "s"]}},
            ],
        }

    def test_accepts_closed_loop_episode_and_reports_actions(self):
        self.write([self.episode()])
        result = validate_dataset(self.root)
        self.assertEqual(result["episodes"], 1)
        self.assertEqual(result["steps"], 2)
        self.assertEqual(result["action_types"], {"click": 1, "key": 1})

    def test_rejects_osworld_derived_training_examples(self):
        self.write([self.episode(source="markov-ai/computer-use OSWorld")])
        with self.assertRaisesRegex(DatasetError, "benchmark leakage"):
            validate_dataset(self.root)

    def test_rejects_episode_shared_across_splits(self):
        self.write([self.episode(), self.episode(split="eval")])
        with self.assertRaisesRegex(DatasetError, "duplicate episode_id"):
            validate_dataset(self.root)

    def test_rejects_same_screenshot_across_splits(self):
        self.write([self.episode(), self.episode("paint-002", "eval")])
        with self.assertRaisesRegex(DatasetError, "screenshot appears in multiple splits"):
            validate_dataset(self.root)

    def test_rejects_unbounded_or_unverifiable_actions(self):
        episode = self.episode()
        episode["steps"][0]["action"]["x"] = 1.2
        self.write([episode])
        with self.assertRaisesRegex(DatasetError, "normalized coordinate"):
            validate_dataset(self.root)

    def test_rejects_missing_screenshot_and_arbitrary_code(self):
        episode = self.episode()
        episode["steps"][0]["screenshot"] = "../secret.png"
        self.write([episode])
        with self.assertRaisesRegex(DatasetError, "screenshot path"):
            validate_dataset(self.root)
        episode["steps"][0]["screenshot"] = "images/0.png"
        episode["steps"][0]["action"] = {"type": "python", "code": "print(1)"}
        self.write([episode])
        with self.assertRaisesRegex(DatasetError, "action type"):
            validate_dataset(self.root)


if __name__ == "__main__":
    unittest.main()
