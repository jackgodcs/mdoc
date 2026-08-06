from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import manual_visual_validation as visual


class VisualValidationTests(unittest.TestCase):
    def test_create_review_and_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, state = root / "report.json", root / "visual.json"
            report.write_text(json.dumps({"findings": [{"rule_id": "GVMD-LIST-COMPAT", "file": "en/Page.md", "line": 3, "suppressed": False}]}), encoding="utf-8")
            self.assertEqual(0, visual.create(report, state, None))
            self.assertEqual("pending", json.loads(state.read_text(encoding="utf-8"))["status"])
            self.assertEqual(0, visual.review(state, "VV-001", "approved", "tester", None))
            self.assertEqual("approved", json.loads(state.read_text(encoding="utf-8"))["status"])
            report.write_text(json.dumps({"findings": []}), encoding="utf-8")
            self.assertEqual(1, visual.verify(state, report, None))
            self.assertEqual("stale", json.loads(state.read_text(encoding="utf-8"))["status"])


if __name__ == "__main__":
    unittest.main()
