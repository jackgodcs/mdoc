from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mdoc_check.editor import choose_editor, load_preferences, open_default, open_source


class EditorTests(unittest.TestCase):
    def test_editor_picker_runs_in_a_separate_process(self) -> None:
        completed = __import__("subprocess").CompletedProcess([], 0, stdout="C:/Tools/Editor.exe", stderr="")
        with patch("mdoc_check.editor.subprocess.run", return_value=completed) as run:
            selected = choose_editor()
        self.assertEqual(Path("C:/Tools/Editor.exe").resolve(), selected)
        self.assertEqual("-c", run.call_args.args[0][1])
        self.assertEqual("utf-8", run.call_args.kwargs["env"]["PYTHONIOENCODING"])

    def test_windows_default_is_remembered_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict("os.environ", {"LOCALAPPDATA": temporary}), patch("mdoc_check.editor.os.name", "nt"), patch("mdoc_check.editor.os.startfile", create=True) as startfile:
            source = Path(temporary) / "Page.md"
            source.write_text("# Page\n", encoding="utf-8")
            open_source(source, 1, 1, "windows-default")
            self.assertEqual("windows-default", load_preferences()["editor"]["mode"])
            open_source(source, 1, 1)
        self.assertEqual(2, startfile.call_count)

    def test_resource_uses_windows_default_program_without_changing_editor_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("mdoc_check.editor.os.name", "nt"), patch("mdoc_check.editor.os.startfile", create=True) as startfile:
            source = Path(temporary) / "Image + File_Name.png"
            source.write_bytes(b"image")
            self.assertEqual("opened", open_default(source)["status"])
        startfile.assert_called_once_with(str(source.resolve()))


if __name__ == "__main__":
    unittest.main()
