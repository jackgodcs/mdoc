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
INTERACTION = Path(__file__).with_name("screenshot_interaction.py")


def load_assistant_module():
    script_directory = str(SCRIPT.parent)
    if script_directory not in sys.path:
        sys.path.insert(0, script_directory)
    spec = importlib.util.spec_from_file_location("screenshot_assistant_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_interaction_module():
    script_directory = str(INTERACTION.parent)
    if script_directory not in sys.path:
        sys.path.insert(0, script_directory)
    spec = importlib.util.spec_from_file_location("screenshot_interaction_under_test", INTERACTION)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ScreenshotAssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.assistant = load_assistant_module()
        cls.interaction = load_interaction_module()

    def test_capture_overlay_geometry_and_dimming_regressions(self) -> None:
        overlay = self.interaction.CaptureOverlay
        source = Image.new("RGB", (2, 2), (200, 100, 50))

        self.assertEqual((110, 55, 27), overlay.dim_image(source).getpixel((0, 0)))
        self.assertEqual((10, 5, 40, 30), overlay.normalize((40, 30, 10, 5)))
        self.assertEqual(((10, 10, 120, 100), "e"), overlay.resize_result((10, 10, 100, 100), "e", 120, 50))
        self.assertEqual(((5, 10, 10, 100), "w"), overlay.resize_result((10, 10, 100, 100), "e", 5, 50))
        self.assertEqual(((5, 4, 10, 10), "nw"), overlay.resize_result((10, 10, 100, 100), "se", 5, 4))

    def test_screenshot_changes_remain_available_before_final_review(self) -> None:
        allowed = self.assistant.screenshot_changes_allowed

        self.assertTrue(allowed({"status": "waiting_for_authoring"}))
        self.assertFalse(allowed({"status": "ready_for_review"}))
        self.assertFalse(allowed({"status": "accepted"}))
        self.assertFalse(allowed({"status": "cancelled"}))

    def test_modal_editor_blocks_capture_before_the_assistant_is_hidden(self) -> None:
        assistant = object.__new__(self.assistant.Assistant)
        assistant.modal_count = 1
        assistant.capture_in_progress = False
        assistant.last_capture_request = 0.0
        assistant.ensure_editable = lambda: True
        assistant.root = SimpleNamespace(withdraw=lambda: self.fail("modal capture hid the assistant"))

        self.assistant.Assistant.request_capture(assistant, "global", {"cursor": (1, 1)})

        self.assertFalse(assistant.capture_in_progress)
        self.assertEqual(0.0, assistant.last_capture_request)

    def test_editor_modal_count_is_released_only_when_the_editor_is_destroyed(self) -> None:
        assistant = object.__new__(self.assistant.Assistant)
        assistant.modal_count = 0
        assistant.root = object()
        assistant.task = object()
        assistant.items = {"INPUT:zh": {"item": object()}}
        assistant.selected = lambda: "INPUT:zh"
        assistant.refresh = lambda: None
        assistant.contributor = False
        editor = SimpleNamespace()
        editor.transient = lambda root: None
        editor.grab_set = lambda: None
        editor.bind = lambda event, callback, add=None: setattr(editor, "destroy_callback", callback)
        original_editor = self.assistant.ImageTextEditor
        self.assistant.ImageTextEditor = lambda *args, **kwargs: editor
        try:
            self.assistant.Assistant.edit_current(assistant)
        finally:
            self.assistant.ImageTextEditor = original_editor

        self.assertEqual(1, assistant.modal_count)
        editor.destroy_callback(SimpleNamespace(widget=object()))
        self.assertEqual(1, assistant.modal_count)
        editor.destroy_callback(SimpleNamespace(widget=editor))
        self.assertEqual(0, assistant.modal_count)

    def test_screenshot_list_is_filtered_by_the_selected_locale(self) -> None:
        manifest_items = {
            "INPUT:zh": {"locale": "zh", "required": True},
            "INPUT:en": {"locale": "en", "required": True},
            "LOG:zh": {"locale": "zh", "required": True},
            "INPUT:ja": {"locale": "ja", "required": True},
        }

        self.assertEqual(["zh", "en", "ja"], self.assistant.screenshot_locales(manifest_items))
        self.assertEqual(
            ["INPUT:zh", "LOG:zh"],
            list(self.assistant.items_for_locale(manifest_items, "zh")),
        )
        self.assertEqual(["INPUT:en"], list(self.assistant.items_for_locale(manifest_items, "en")))
        self.assertEqual(["INPUT:ja"], list(self.assistant.items_for_locale(manifest_items, "ja")))

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
