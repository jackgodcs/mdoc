from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mdoc_check.checkers import cspell, english_markdown, markdownlint, tool_versions, vale


class CheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_locked_tool_versions_are_available(self) -> None:
        versions = tool_versions()
        self.assertEqual("0.41.1", versions["markdownlint"])
        self.assertEqual("15.0.1", versions["markdown_it"])
        self.assertEqual("10.3.0", versions["cspell"])
        self.assertEqual("3.20.0", versions["vale"])
        self.assertEqual("12.2.0", versions["pillow"])

    def test_markdownlint_returns_structured_finding(self) -> None:
        page = self.root / "Bad.md"
        page.write_text("#Title\n\nText", encoding="utf-8")
        findings = markdownlint([page], {page: "en/Bad.md"})
        self.assertTrue(any(item["native_rule"] == "MD018" for item in findings))
        self.assertTrue(any(item["native_rule"] == "MD047" for item in findings))

    def test_non_blocking_markdown_format_rules_are_warnings(self) -> None:
        page = self.root / "Formatting.md"
        page.write_text("# Title\nText with trailing space. \n\tTabbed\n\n\n\n## Next\nText", encoding="utf-8")
        overrides = {rule: "warning" for rule in {"MD001", "MD003", "MD004", "MD005", "MD009", "MD010", "MD012", "MD022", "MD037", "MD047"}}
        findings = markdownlint([page], {page: "en/Formatting.md"}, severity_overrides=overrides)
        by_rule = {item["native_rule"]: item for item in findings}
        for rule in {"MD009", "MD010", "MD012", "MD022", "MD047"}:
            self.assertIn(rule, by_rule)
            self.assertEqual("warning", by_rule[rule]["severity"])

    def test_vale_remains_available_without_builtin_product_names(self) -> None:
        page = self.root / "Bad.md"
        page.write_text("# Title\n\nUse Lidar360MLS.\n", encoding="utf-8")
        findings = vale([page], {page: "en/Bad.md"})
        self.assertEqual([], findings)

    def test_cspell_reports_english_typo_as_warning(self) -> None:
        page = self.root / "Bad.md"
        page.write_text("# Title\n\nThis sentnce has a typo.\n", encoding="utf-8")
        findings = cspell([page], {page: "en/Bad.md"})
        typo = next(item for item in findings if "sentnce" in item["message"])
        self.assertEqual("en/Bad.md", typo["path"])
        self.assertEqual("warning", typo["severity"])

    def test_cspell_checks_link_text_but_not_target(self) -> None:
        page = self.root / "Bad.md"
        page.write_text("# Title\n\n[Sentnce](https://example.com/sentnce) and `sentnce`.\n", encoding="utf-8")
        findings = cspell([page], {page: "en/Bad.md"})
        self.assertEqual(1, sum("Sentnce" in item["message"] for item in findings))

    def test_long_file_lists_are_checked_in_batches(self) -> None:
        files = []
        displays = {}
        for index in range(400):
            directory = self.root / (f"Section{index:03d}_" + "x" * 100)
            directory.mkdir()
            page = directory / "Page.md"
            page.write_text("# Title\n\nUse Lidar360MLS in this sentnce.\n", encoding="utf-8")
            files.append(page)
            displays[page] = f"en/Main/{index:03d}/Page.md"
        self.assertEqual([], vale(files, displays))
        self.assertEqual(400, sum("sentnce" in item["message"] for item in cspell(files, displays)))

    def test_cspell_uses_workspace_and_language_dictionary_files(self) -> None:
        page = self.root / "Terms.md"
        page.write_text("# Terms\n\nUse Gvscript and Mlsworkbench.\n", encoding="utf-8")
        common = self.root / "check-words.txt"
        language = self.root / "check-words.en.txt"
        common.write_text("Gvscript\n", encoding="utf-8")
        language.write_text("Mlsworkbench\n", encoding="utf-8")
        findings = cspell([page], {page: "en/Terms.md"}, dictionary_files=[common, language])
        self.assertFalse(any("Gvscript" in item["message"] or "Mlsworkbench" in item["message"] for item in findings))

    def test_language_detection_ignores_code_and_link_targets(self) -> None:
        page = self.root / "English.md"
        page.write_text("# English\n\n```text\n日本語\n```\n\n[English](日本語.md)\n", encoding="utf-8")
        self.assertEqual([page], english_markdown([page], 20))

    def test_language_detection_checks_link_display_text(self) -> None:
        page = self.root / "Japanese.md"
        page.write_text("# English\n\n[日本語](English.md)\n", encoding="utf-8")
        self.assertEqual([], english_markdown([page], 20))

    def test_language_detection_checks_visible_html_text(self) -> None:
        page = self.root / "JapaneseHtml.md"
        page.write_text("<div>日本語</div>\n", encoding="utf-8")
        self.assertEqual([], english_markdown([page], 20))


if __name__ == "__main__":
    unittest.main()
