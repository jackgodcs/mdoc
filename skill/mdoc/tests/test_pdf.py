from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

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

    def test_summary_entries_support_mixed_two_space_and_tab_indentation(self) -> None:
        summary = self.root / "Summary.md"
        summary.write_text(
            "* [Road](Road.md)\n\t* [Analysis](Analysis.md)\n* [Trench](Trench.md)\n  - [Extraction](Extraction.md)\n    - [Pipe](Pipe.md)\n* [Color](Color.md)\n  * [Height](Height.md)\n",
            encoding="utf-8",
        )
        entries = pdf.summary_entries(summary)
        self.assertEqual([0, 1, 0, 1, 2, 0, 1], [item["level"] for item in entries])
        self.assertEqual(["1", "1.1", "2", "2.1", "2.1.1", "3", "3.1"], [item["number"] for item in entries])

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

    def test_pdf_layout_splits_tables_and_code(self) -> None:
        intermediate = self.root / "ebook"
        chapter = intermediate / "Main" / "Chapter.html"
        css = intermediate / "gitbook" / "pdf.css"
        chapter.parent.mkdir(parents=True)
        css.parent.mkdir(parents=True)
        chapter.write_text('<title>Chapter</title><h1 class="book-chapter book-chapter-2">Chapter</h1>', encoding="utf-8")
        css.write_text(".page .section table,.page .section pre{page-break-inside:avoid}", encoding="utf-8")
        (intermediate / "SUMMARY.html").write_text('<a href="Main/Chapter.html">Chapter</a>', encoding="utf-8")
        entries = [{"number": "1.1", "title": "Chapter", "path": "Main/Chapter.md"}]
        pdf._patch_html(intermediate, None, entries)
        layout = css.read_text(encoding="utf-8")
        self.assertIn("table,.page .section pre{page-break-inside:auto;break-inside:auto}", layout)
        self.assertIn("tr{page-break-inside:avoid;break-inside:avoid}", layout)
        options = pdf._calibre_options({"title": "Guide", "language": "en", "pdf": {"fontFamily": "Arial"}}, pdf.DEFAULTS["defaults"])
        self.assertEqual("descendant-or-self::*[contains(concat(' ', normalize-space(@class), ' '), ' book-chapter ')]", options[options.index("--chapter") + 1])
        self.assertEqual("pagebreak", options[options.index("--chapter-mark") + 1])

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
            pdf._available_memory = lambda: 10 * 1024**3 - 1
            self.assertEqual(1, pdf.effective_jobs(3, False))
            pdf._available_memory = lambda: 10 * 1024**3
            self.assertEqual(2, pdf.effective_jobs(3, False))
            self.assertEqual(1, pdf.effective_jobs(1, False))
            pdf._available_memory = lambda: 14 * 1024**3
            self.assertEqual(3, pdf.effective_jobs(3, False))
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

    def test_structural_check_accepts_type3_font_with_embedded_charprocs(self) -> None:
        class Page(dict):
            def extract_text(self):
                return "icon"

        font = DictionaryObject({
            NameObject("/Subtype"): NameObject("/Type3"),
            NameObject("/CharProcs"): DictionaryObject({NameObject("/glyph"): DictionaryObject()}),
            NameObject("/ToUnicode"): DictionaryObject(),
        })
        page = Page({"/Resources": {"/Font": {"/F1": font}}})
        source = self.root / "type3.pdf"
        source.write_bytes(b"pdf")
        reader = type("Reader", (), {"pages": [page], "outline": []})()
        with patch("pypdf.PdfReader", return_value=reader):
            report = pdf._structural_check(source)
        self.assertEqual("passed", report["status"])
        self.assertTrue(report["fonts"]["/F1"]["embedded"])

    def test_pdf_images_request_smooth_interpolation(self) -> None:
        source = self.root / "source.pdf"
        output = self.root / "output.pdf"
        writer = PdfWriter()
        page = writer.add_blank_page(width=100, height=100)
        image = StreamObject()
        image.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): writer._add_object(image)})})
        with source.open("wb") as stream:
            writer.write(stream)

        reader = PdfReader(source)
        self.assertEqual(1, pdf._enable_image_interpolation(reader))
        writer = PdfWriter(clone_from=reader)
        with output.open("wb") as stream:
            writer.write(stream)

        rewritten = PdfReader(output).pages[0]["/Resources"]["/XObject"]["/Im1"].get_object()
        self.assertTrue(rewritten["/Interpolate"])

    def test_flatten_outline_reports_bookmark_levels(self) -> None:
        parent = object()
        child = object()
        leaf = object()
        self.assertEqual([(parent, 0), (child, 1), (leaf, 2)], list(pdf._flatten_outline([parent, [child, [leaf]]])))


if __name__ == "__main__":
    unittest.main()
