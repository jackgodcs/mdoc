from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from mdoc_check.domain import inspect


class DomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "Main").mkdir()
        (self.root / "images").mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_reports_missing_link_and_non_ascii_path(self) -> None:
        page = self.root / "Main" / "页面.md"
        page.write_text("# Page\n\n[Missing](Missing.md)\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/页面.md"], "Main", "images", "Summary.md", False)
        rules = {item["rule"] for item in findings}
        self.assertIn("path.ascii-only", rules)
        self.assertIn("link.target-exists", rules)

    def test_reports_reference_case_mismatch(self) -> None:
        (self.root / "Main" / "Target.md").write_text("# Target\n", encoding="utf-8")
        (self.root / "Main" / "Page.md").write_text("# Page\n\n[Target](target.md)\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertTrue(any(item["rule"] == "path.case-exact" for item in findings))

    def test_pillow_reports_extension_mismatch(self) -> None:
        image = self.root / "images" / "Sample.jpg"
        Image.new("RGB", (10, 10), "white").save(image, format="PNG")
        (self.root / "Main" / "Page.md").write_text("# Page\n\n![Sample](../images/Sample.jpg)\n", encoding="utf-8")
        findings, resources = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertEqual(["en/images/Sample.jpg"], resources["images"])
        self.assertTrue(any(item["rule"] == "image.extension-matches-format" for item in findings))

    def test_non_ascii_or_spaced_resource_path_is_allowed(self) -> None:
        image = self.root / "images" / "中文 Image + Name.png"
        Image.new("RGB", (10, 10), "white").save(image)
        (self.root / "Main" / "Page.md").write_text("# Page\n\n![Sample](../images/中文%20Image%20%2B%20Name.png)\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertFalse(any(item["rule"] == "path.ascii-only" for item in findings))

    def test_referenced_markdown_path_still_requires_ascii_name(self) -> None:
        (self.root / "Main" / "目标 Page.md").write_text("# Target\n", encoding="utf-8")
        (self.root / "Main" / "Page.md").write_text("# Page\n\n[Target](%E7%9B%AE%E6%A0%87%20Page.md)\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md", "Main/目标 Page.md"], "Main", "images", "Summary.md", False)
        self.assertTrue(any(item["rule"] == "path.ascii-only" and item["path"] == "en/Main/目标 Page.md" for item in findings))

    def test_book_reports_page_missing_from_summary(self) -> None:
        (self.root / "Summary.md").write_text("# Summary\n", encoding="utf-8")
        (self.root / "Main" / "Page.md").write_text("# Page\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Summary.md", "Main/Page.md"], "Main", "images", "Summary.md", True)
        self.assertTrue(any(item["rule"] == "navigation.page-linked" for item in findings))

    def test_book_reports_unreferenced_asset(self) -> None:
        (self.root / "Summary.md").write_text("# Summary\n\n- [Page](Main/Page.md)\n", encoding="utf-8")
        (self.root / "Main" / "Page.md").write_text("# Page\n", encoding="utf-8")
        Image.new("RGB", (10, 10), "white").save(self.root / "images" / "Unused.png")
        findings, _ = inspect(self.root, "en", "en", ["Summary.md", "Main/Page.md"], "Main", "images", "Summary.md", True)
        self.assertTrue(any(item["rule"] == "resource.referenced" for item in findings))

    def test_reports_absolute_resource_path_and_html_spacing(self) -> None:
        (self.root / "Main" / "Page.md").write_text("# Page\n\n![Image](C:/Temp/file.png)\n\n<div>Text</div>\nNext\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        rules = {item["rule"] for item in findings}
        self.assertIn("path.no-local-absolute", rules)
        self.assertIn("html.block-blank-line", rules)

    def test_absolute_paths_in_prose_and_code_are_allowed(self) -> None:
        (self.root / "Main" / "Page.md").write_text(
            "# Page\n\nOpen C:\\Temp\\file.png.\n\n`D:\\Data\\input.las`\n\n```powershell\nCopy-Item C:\\Temp\\file.png D:\\Data\n```\n",
            encoding="utf-8",
        )
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertFalse(any(item["rule"] == "path.no-local-absolute" for item in findings))

    def test_html_resource_absolute_path_is_rejected(self) -> None:
        (self.root / "Main" / "Page.md").write_text('# Page\n\n<img src="file:///C:/Temp/file.png">\n', encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertTrue(any(item["rule"] == "path.no-local-absolute" for item in findings))

    def test_https_url_is_not_a_local_absolute_path(self) -> None:
        (self.root / "Main" / "Page.md").write_text("# Page\n\n<https://cdn.example.com/file.tif>\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertFalse(any(item["rule"] == "path.no-local-absolute" for item in findings))

    def test_english_punctuation_ignores_code_and_url(self) -> None:
        (self.root / "Main" / "Page.md").write_text("# Page\n\n`中文，` <https://example.com/a，b>\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertFalse(any(item["rule"] == "locale.en-punctuation" for item in findings))

    def test_html_image_is_checked(self) -> None:
        Image.new("RGB", (10, 10), "white").save(self.root / "images" / "Sample.png")
        (self.root / "Main" / "Page.md").write_text("# Page\n\n<img src=\"../images/Sample.png\" width=\"10\">\n", encoding="utf-8")
        _, resources = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertEqual(["en/images/Sample.png"], resources["images"])

    def test_missing_anchor_is_error(self) -> None:
        (self.root / "Main" / "Target.md").write_text("# Target\n\n## Existing\n", encoding="utf-8")
        (self.root / "Main" / "Page.md").write_text("# Page\n\n[Target](Target.md#missing)\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertTrue(any(item["rule"] == "link.anchor-exists" for item in findings))

    def test_missing_same_page_anchor_is_error(self) -> None:
        (self.root / "Main" / "Page.md").write_text("# Page\n\n[Missing](#missing)\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertTrue(any(item["rule"] == "link.anchor-exists" for item in findings))

    def test_honkit_anchor_slug_is_accepted(self) -> None:
        (self.root / "Main" / "Target.md").write_text("# Target\n\n## API_v2: 中文 / Test!\n", encoding="utf-8")
        (self.root / "Main" / "Page.md").write_text("# Page\n\n[Target](Target.md#api_v2-中文--test)\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertFalse(any(item["rule"] == "link.anchor-exists" for item in findings))

    def test_honkit_anchor_preserves_function_name_underscores(self) -> None:
        (self.root / "Main" / "Page.md").write_text("# Page\n\n[Function](#classify_classify_by_csf)\n\n## `classify_classify_by_csf`\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertFalse(any(item["rule"] == "link.anchor-exists" for item in findings))

    def test_reference_outside_locale_is_rejected_before_lookup(self) -> None:
        outside = self.root.parent / "Outside.md"
        outside.write_text("# Outside\n", encoding="utf-8")
        (self.root / "Main" / "Page.md").write_text("# Page\n\n[Outside](../../Outside.md)\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertTrue(any(item["rule"] == "path.inside-locale" for item in findings))
        self.assertFalse(any(item["rule"] == "link.target-exists" for item in findings))

    def test_image_thresholds_and_allowed_format_are_warnings(self) -> None:
        image = self.root / "images" / "Sample.gif"
        Image.new("RGB", (12, 8), "white").save(image)
        (self.root / "Main" / "Page.md").write_text("# Page\n\n![Sample](../images/Sample.gif)\n", encoding="utf-8")
        findings, _ = inspect(
            self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False,
            {"allowed_formats": ["png", "jpeg"], "max_width_px": 10, "max_height_px": 7, "max_bytes": 1},
        )
        rules = {item["rule"] for item in findings if item["severity"] == "warning"}
        self.assertEqual({"image.allowed-format", "image.max-width", "image.max-height", "image.max-bytes"}, rules)

    def test_parallel_image_checks_match_serial_results(self) -> None:
        for name in ("First.png", "Second.jpg"):
            Image.new("RGB", (12, 8), "white").save(self.root / "images" / name)
        (self.root / "Main" / "Page.md").write_text("# Page\n\n![First](../images/First.png)\n![Second](../images/Second.jpg)\n", encoding="utf-8")
        serial = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False, {"max_width_px": 10}, image_workers=1)
        parallel = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False, {"max_width_px": 10}, image_workers=4)
        self.assertEqual(serial, parallel)

    def test_unbalanced_html_table_is_error(self) -> None:
        (self.root / "Main" / "Page.md").write_text("# Page\n\n<table><tr><td>Value</tr></table>\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertTrue(any(item["rule"] == "html.table-structure" for item in findings))

    def test_book_reports_navigation_structure_and_title_findings(self) -> None:
        (self.root / "Summary.md").write_text(
            "# Summary\n\n- [Wrong](Main/Page.md)\n    - [Deep](Main/Deep.md)\n- [Again](Main/Page.md)\n",
            encoding="utf-8",
        )
        (self.root / "Main" / "Page.md").write_text("# Page\n", encoding="utf-8")
        (self.root / "Main" / "Deep.md").write_text("# Deep\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Summary.md", "Main/Page.md", "Main/Deep.md"], "Main", "images", "Summary.md", True)
        rules = {item["rule"] for item in findings}
        self.assertIn("navigation.duplicate-entry", rules)
        self.assertIn("navigation.title-matches-h1", rules)

    def test_navigation_title_comparison_uses_rendered_text(self) -> None:
        (self.root / "Summary.md").write_text("# Summary\n\n- [**Page** `API`](Main/Page.md)\n", encoding="utf-8")
        (self.root / "Main" / "Page.md").write_text("# **Page** `API`\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Summary.md", "Main/Page.md"], "Main", "images", "Summary.md", True)
        self.assertFalse(any(item["rule"] == "navigation.title-matches-h1" for item in findings))

    def test_internal_navigation_check_works_without_toolchain_bridge(self) -> None:
        (self.root / "Summary.md").write_text("# Summary\n\n- [**Page** `API`](Main/Page.md)\n", encoding="utf-8")
        (self.root / "Main" / "Page.md").write_text("# **Page** `API`\n", encoding="utf-8")
        with patch("mdoc_check.domain.NODE", self.root / "missing-node.exe"), patch("mdoc_check.domain.BRIDGE", self.root / "missing-bridge.mjs"):
            findings, _ = inspect(self.root, "en", "en", ["Summary.md", "Main/Page.md"], "Main", "images", "Summary.md", True)
        self.assertFalse(any(item["rule"] in {"markdown.single-h1", "navigation.title-matches-h1"} for item in findings))

    def test_inline_markdownlint_disable_is_warning(self) -> None:
        (self.root / "Main" / "Page.md").write_text("# Page\n\n<!-- markdownlint-disable MD013 -->\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        issue = next(item for item in findings if item["rule"] == "markdown.inline-disable")
        self.assertEqual("warning", issue["severity"])

    def test_heading_must_start_at_beginning_of_line(self) -> None:
        (self.root / "Main" / "Page.md").write_text(" # Page\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertTrue(any(item["rule"] == "markdown.md023" for item in findings))

    def test_heading_rule_ignores_fenced_code(self) -> None:
        (self.root / "Main" / "Page.md").write_text("# Page\n\n```text\n # Not a heading\n```\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertFalse(any(item["rule"] == "markdown.md023" for item in findings))

    def test_english_chinese_punctuation_is_error(self) -> None:
        (self.root / "Main" / "Page.md").write_text("# Page\n\nEnglish，text.\n", encoding="utf-8")
        findings, _ = inspect(self.root, "en", "en", ["Main/Page.md"], "Main", "images", "Summary.md", False)
        self.assertTrue(any(item["rule"] == "locale.en-punctuation" for item in findings))


if __name__ == "__main__":
    unittest.main()
