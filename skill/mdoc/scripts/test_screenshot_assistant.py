"""Regression tests for capture operations that do not require a Tk window."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


SCRIPT = Path(__file__).with_name("screenshot_assistant.py")


def load_assistant_module():
    script_directory = str(SCRIPT.parent)
    if script_directory not in sys.path:
        sys.path.insert(0, script_directory)
    spec = importlib.util.spec_from_file_location("screenshot_assistant_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ScreenshotAssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.assistant = load_assistant_module()

    def test_reference_copy_is_byte_exact_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.png"
            target = root / "captures" / "replacement.png"
            Image.new("RGBA", (18, 11), "#2878B8").save(source, format="PNG")

            copied = self.assistant.copy_reference_to_capture(source, target)

            self.assertEqual(source.read_bytes(), target.read_bytes())
            self.assertEqual("PNG", copied["format"])
            self.assertEqual((18, 11), (copied["width"], copied["height"]))

    def test_reference_copy_rejects_target_with_a_different_format(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.png"
            target = root / "captures" / "replacement.jpg"
            Image.new("RGBA", (18, 11), "#2878B8").save(source, format="PNG")

            with self.assertRaisesRegex(OSError, "格式"):
                self.assistant.copy_reference_to_capture(source, target)

            self.assertFalse(target.exists())

    def test_reference_copy_clears_existing_image_edit_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = SimpleNamespace(directory=root / "task")
            entry = {"item": {"id": "WINDOW:en", "locale": "en"}}
            record = task.directory / "image-edits" / "en" / "WINDOW_en.json"
            snapshot = task.directory / "image-edits" / "en" / "WINDOW_en.base.png"
            record.parent.mkdir(parents=True)
            record.write_text("{}", encoding="utf-8")
            Image.new("RGB", (3, 2), "white").save(snapshot, format="PNG")

            self.assistant.clear_image_edit_artifacts(task, entry)

            self.assertFalse(record.exists())
            self.assertFalse(snapshot.exists())


if __name__ == "__main__":
    unittest.main()
