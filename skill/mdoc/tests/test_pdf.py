from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from skill.mdoc.mdoc_core import pdf
from skill.mdoc.mdoc_core.errors import MdocError
from skill.mdoc.mdoc_core.models import freeze


class PdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_summary_scope_keeps_full_book_numbers(self) -> None:
        summary = self.root / "Summary.md"
        summary.write_text(
            "# Summary\n\n* [First](Main/First.md)\n    * [Child](Main/Child.md)\n* [Second](Main/Second.md)\n    * [Target](Main/Target.md)\n        * [Leaf](Main/Leaf.md)\n",
            encoding="utf-8",
        )
        entries = pdf.summary_entries(summary)
        self.assertEqual(["1", "1.1", "2", "2.1", "2.1.1"], [item["number"] for item in entries])
        self.assertEqual(["2.1", "2.1.1"], [item["number"] for item in pdf.select_entries(entries, "Main/Target.md", "section")])
        self.assertEqual(["2.1"], [item["number"] for item in pdf.select_entries(entries, "Main/Target.md", "page")])

    def test_summary_target_must_be_unique(self) -> None:
        entries = [
            {"path": "Main/A.md", "anchor": "", "level": 0},
            {"path": "main/a.md", "anchor": "", "level": 0},
        ]
        with self.assertRaises(MdocError) as caught:
            pdf.select_entries(entries, "Main/A.md", "page")
        self.assertEqual("MDOC-PDF-SCOPE-AMBIGUOUS", caught.exception.code)

    def test_html_image_optimization_creates_jpeg_and_rewrites_references(self) -> None:
        html = self.root / "chapter.html"
        css = self.root / "style.css"
        image = self.root / "images" / "capture.png"
        image.parent.mkdir()
        Image.effect_noise((1000, 700), 100).convert("RGBA").save(image)
        html.write_text('<img src="images/capture.png">', encoding="utf-8")
        css.write_text("body{background:url('images/capture.png')}", encoding="utf-8")
        stats = pdf.optimize_generated_images(self.root, {**pdf.DEFAULTS["defaults"]["image_optimization"], "min_bytes": 0})
        rewritten = self.root / "images" / "capture.mdoc.jpg"
        self.assertTrue(rewritten.is_file())
        self.assertIn("images/capture.mdoc.jpg", html.read_text(encoding="utf-8"))
        self.assertIn("images/capture.mdoc.jpg", css.read_text(encoding="utf-8"))
        self.assertEqual(1, stats["optimized"])
        self.assertTrue(image.is_file())

    def test_effective_settings_merge_book_override_without_losing_defaults(self) -> None:
        config = {"pdf": {"defaults": pdf.DEFAULTS["defaults"]}}
        book = {"pdf": {"margins_pt": {"top": 80}, "image_optimization": {"jpeg_quality": 65}}}
        settings = pdf.effective_settings(config, book)
        self.assertEqual(80, settings["margins_pt"]["top"])
        self.assertEqual(67, settings["margins_pt"]["left"])
        self.assertEqual(65, settings["image_optimization"]["jpeg_quality"])
        self.assertEqual(20480, settings["image_optimization"]["min_bytes"])

    def test_effective_settings_accept_frozen_workspace_configuration(self) -> None:
        config = freeze({"pdf": {"defaults": pdf.DEFAULTS["defaults"]}})
        book = freeze({"pdf": {"margins_pt": {"top": 80}}})
        settings = pdf.effective_settings(config, book)
        self.assertEqual(80, settings["margins_pt"]["top"])
        self.assertEqual(67, settings["margins_pt"]["left"])

    def test_output_names_are_stable_and_memory_guard_never_returns_zero(self) -> None:
        self.assertEqual("guide-en.pdf", pdf._output_name("guide", "en", "book", None))
        first = pdf._output_name("guide", "en", "page", "Main/Topic.md")
        second = pdf._output_name("guide", "en", "section", "Main/Topic.md")
        self.assertRegex(first, r"^Topic-[0-9a-f]{8}-en\.pdf$")
        self.assertRegex(second, r"^Topic-[0-9a-f]{8}-section-en\.pdf$")
        original = pdf._available_memory
        try:
            pdf._available_memory = lambda: 1
            self.assertEqual(1, pdf.effective_jobs(3, False))
            self.assertEqual(3, pdf.effective_jobs(3, True))
        finally:
            pdf._available_memory = original

    def test_doctor_rejects_an_available_tool_with_the_wrong_version(self) -> None:
        original_paths = pdf.tool_paths
        original_version = pdf._version
        try:
            pdf.tool_paths = lambda root=None: {name: self.root / f"{name}.exe" for name in pdf.TOOL_VERSIONS}
            for path in pdf.tool_paths().values():
                path.touch()
            pdf._version = lambda command: "0.0.0" if "node" in Path(command[0]).name else next(version for name, version in pdf.TOOL_VERSIONS.items() if name in " ".join(command))
            report = pdf.doctor(None)
            self.assertEqual("failed", report["status"])
            self.assertIn("node", report["invalid"])
        finally:
            pdf.tool_paths = original_paths
            pdf._version = original_version


if __name__ == "__main__":
    unittest.main()
