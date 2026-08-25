from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from skill.mdoc.scripts.mdoc_core.virtual_book import VirtualBook


class VirtualBookTests(unittest.TestCase):
    def test_formal_plus_overlay_minus_delete_is_resolved_without_copying_book(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repository = Path(root)
            locale = repository / "Guide" / "zh"
            (locale / "Main").mkdir(parents=True)
            (locale / "images").mkdir()
            (locale / "Main" / "Old.md").write_text("old", encoding="utf-8")
            (locale / "images" / "formal.png").write_bytes(b"png")
            task_dir = repository / ".mdoc" / "tasks" / "one"
            staged = task_dir / "staging" / "zh" / "Main" / "New.md"
            staged.parent.mkdir(parents=True)
            staged.write_text("[asset](../images/formal.png)", encoding="utf-8")
            workspace = SimpleNamespace(repository=repository, config={"books": {"guide": {
                "root": "Guide", "source_locale": "zh", "locales": {"zh": {"root": "zh", "language": "zh"}},
                "content_root": "Main", "assets_root": "images", "navigation": {"summary": "Summary.md"},
            }}})
            definition = {"task": {"book": "guide"}, "manifest": [
                {"action": "delete", "locale": "zh", "path": "Main/Old.md", "kind": "page"},
                {"action": "create", "locale": "zh", "path": "Main/New.md", "kind": "page"},
            ]}
            task = SimpleNamespace(workspace=workspace, directory=task_dir, definition=definition)

            candidate = VirtualBook.task(task)
            files = {item.path: item for item in candidate.files("zh")}
            self.assertNotIn("Main/Old.md", files)
            self.assertEqual("staging", files["Main/New.md"].origin)
            self.assertEqual("images/formal.png", candidate.resolve_link(files["Main/New.md"], "../images/formal.png").path)
            self.assertFalse((task_dir / "candidate-book").exists())


if __name__ == "__main__":
    unittest.main()
