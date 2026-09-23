from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skill.mdoc.tests.support import cli


class PdfCliTests(unittest.TestCase):
    def test_open_output_is_public_build_option(self) -> None:
        result = cli("pdf", "build", "--file", "Missing.md", "--open-output", "--json", expected=2)
        self.assertEqual("MDOC-PDF-FILE-INVALID", result["error"]["code"])

    def test_file_mode_does_not_require_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "Missing.md"
            result = cli("pdf", "build", "--file", str(missing), "--json", expected=2)
        self.assertEqual("MDOC-PDF-FILE-INVALID", result["error"]["code"])

    def test_file_mode_rejects_workspace_build_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Page.md"; source.write_text("# Page\n", encoding="utf-8")
            result = cli("pdf", "build", "--file", str(source), "--book", "guide", "--json", expected=2)
        self.assertEqual("MDOC-PDF-FILE-OPTION-CONFLICT", result["error"]["code"])


if __name__ == "__main__":
    unittest.main()
