from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ruamel.yaml import YAML

from mdoc_check.autofix import _custom, configuration, run
from mdoc_check.checkers import BRIDGE, NODE


class AutoFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "manual"
        self.locale = self.root / "Guide" / "en"
        (self.locale / "Main").mkdir(parents=True)
        (self.locale / "images").mkdir()
        (self.locale / "Main" / "Target.md").write_text("# Target\n", encoding="utf-8")
        (self.root / ".mdoc").mkdir()
        workspace = {"schema_version": 1, "workspace": {"id": "auto-fix-test"}, "books": {"guide": {"root": "Guide", "source_locale": "en", "content_root": "Main", "assets_root": "images", "navigation": {"summary": "Summary.md"}, "locales": {"en": {"root": "en", "language": "en"}}}}}
        stream = __import__("io").StringIO(); YAML().dump(workspace, stream)
        (self.root / ".mdoc" / "workspace.yaml").write_text(stream.getvalue(), encoding="utf-8")
        self.report = {"context": "book", "scope": {"kind": "page", "book": "guide", "locale": "en"}}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_configuration_uses_replacement_lists_and_validates_rules(self) -> None:
        (self.root / ".mdoc" / "check.yaml").write_text("auto_fix:\n  timeout_seconds: 12\n  markdownlint:\n    enabled_rules: [MD009]\n  mdoc:\n    enabled_rules: []\n", encoding="utf-8")
        value = configuration(self.root)
        self.assertEqual((12, ["MD009"], []), (value["timeout_seconds"], value["markdownlint"], value["mdoc"]))
        (self.root / ".mdoc" / "check.yaml").write_text("auto_fix:\n  mdoc:\n    enabled_rules: [unknown.rule]\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            configuration(self.root)

    def test_custom_fixes_html_heading_slashes_and_exact_case(self) -> None:
        content = "   ## Heading\n\n</div>\nNext\n\n[Target](target.md) ![Image](..\\images\\Sample.png)\n"
        (self.locale / "images" / "Sample.png").write_bytes(b"x")
        fixed, actions, skipped = _custom(content, sorted({"html.block-blank-line", "markdown.md023", "path.forward-slash", "path.case-exact"}), self.locale, "Main/Page.md", [])
        self.assertIn("## Heading", fixed)
        self.assertIn("</div>\n\nNext", fixed)
        self.assertIn("[Target](Target.md)", fixed)
        self.assertIn("![Image](../images/Sample.png)", fixed)
        self.assertEqual({"html.block-blank-line", "markdown.md023", "path.forward-slash", "path.case-exact"}, {item["rule"] for item in actions})
        self.assertEqual([], skipped)

    def test_run_applies_markdownlint_and_returns_ranges_without_saving(self) -> None:
        page = self.locale / "Main" / "Page.md"; source = "#Title \n\n\nText"; page.write_text(source, encoding="utf-8")
        state = {"size": page.stat().st_size, "mtime_ns": str(page.stat().st_mtime_ns)}
        with patch("mdoc_check.autofix.vale", return_value=[]), patch("mdoc_check.autofix.cspell", return_value=[]):
            result = run(self.root, self.report, "en/Main/Page.md", page, source, state, state, [])
        self.assertTrue(result["changed"]); self.assertIn("# Title", result["content"]); self.assertTrue(result["content"].endswith("\n"))
        self.assertNotIn("\r", result["content"])
        self.assertEqual("行尾空格数量不符合规范", result["rule_help"]["MD009"]["title"])
        self.assertEqual("文件结尾缺少单个换行符", result["rule_help"]["MD047"]["title"])
        self.assertGreaterEqual(result["applied"], 3); self.assertTrue(result["ranges"]); self.assertTrue(all("before_from" in item and "before_to" in item for item in result["ranges"])); self.assertEqual(source, page.read_text(encoding="utf-8"))

    def test_bridge_applies_official_fixes_for_every_markdownlint_whitelist_rule(self) -> None:
        cases = {
            "MD004": "- one\n* two\n",
            "MD005": "1. one\n   1. nested\n  2. wrong\n",
            "MD009": "text \n",
            "MD010": "\ttext\n",
            "MD012": "a\n\n\n\nb\n",
            "MD018": "#Title\n",
            "MD022": "text\n# Title\ntext\n",
            "MD031": "text\n```\ncode\n```\ntext\n",
            "MD032": "text\n- item\ntext\n",
            "MD034": "https://example.com\n",
            "MD037": "** text**\n",
            "MD038": "`  code `\n",
            "MD039": "[ text](x)\n",
            "MD047": "text",
            "MD053": "[unused]: target.md\n",
        }
        default_config = json.loads((Path(__file__).resolve().parents[1] / "config" / "markdownlint.json").read_text(encoding="utf-8"))
        for rule, content in cases.items():
            with self.subTest(rule=rule):
                result = self._bridge_fix(content, default_config, rule)
                self.assertNotEqual(content, result["content"]); self.assertIn(rule, {item["rule"] for item in result["applied"]})
        md054 = self._bridge_fix("[target]\n\n[target]: target.md\n", {"default": False, "MD054": {"shortcut": False, "inline": True}}, "MD054")
        self.assertIn("[target](target.md)", md054["content"]); self.assertIn("MD054", {item["rule"] for item in md054["applied"]})

    @staticmethod
    def _bridge_fix(content: str, config: dict, rule: str) -> dict:
        request = {"action": "fix-content", "content": content, "config": config, "rules": [rule], "ignored": []}
        completed = subprocess.run([str(NODE), str(BRIDGE)], input=json.dumps(request), capture_output=True, text=True, encoding="utf-8", check=True)
        return json.loads(completed.stdout)

    def test_modified_content_skips_rule_with_active_ignore(self) -> None:
        page = self.locale / "Main" / "Page.md"; page.write_text("# Page\nText  \n", encoding="utf-8")
        state = {"size": page.stat().st_size, "mtime_ns": str(page.stat().st_mtime_ns)}
        ignored = [{"rule": "markdown.md009", "native_rule": "MD009", "line": 2, "column": 5, "anchor": "Text  ", "ignore_id": "x"}]
        with patch("mdoc_check.autofix.vale", return_value=[]), patch("mdoc_check.autofix.cspell", return_value=[]):
            result = run(self.root, self.report, "en/Main/Page.md", page, "# Changed\nText  \n", state, state, ignored)
        self.assertIn("MD009", result["skipped_rules"]); self.assertIn("Text  ", result["content"])

    def test_unchanged_content_preserves_exact_ignored_finding(self) -> None:
        page = self.locale / "Main" / "Page.md"; content = "# Page\n\nFirst \nSecond \n"; page.write_text(content, encoding="utf-8")
        state = {"size": page.stat().st_size, "mtime_ns": str(page.stat().st_mtime_ns)}
        ignored = [{"rule": "markdown.md009", "native_rule": "MD009", "line": 3, "column": 6, "anchor": "First ", "ignore_id": "x"}]
        with patch("mdoc_check.autofix.vale", return_value=[]), patch("mdoc_check.autofix.cspell", return_value=[]):
            result = run(self.root, self.report, "en/Main/Page.md", page, content, state, state, ignored)
        self.assertIn("First \n", result["content"]); self.assertIn("Second\n", result["content"]); self.assertIn("MD009", result["skipped_rules"])

    def test_external_change_and_fixer_failure_leave_input_untouched(self) -> None:
        page = self.locale / "Main" / "Page.md"; page.write_text("#Title", encoding="utf-8")
        state = {"size": 1, "mtime_ns": "1"}; current = {"size": 2, "mtime_ns": "2"}
        with self.assertRaisesRegex(ValueError, "外部程序"):
            run(self.root, self.report, "en/Main/Page.md", page, "#Title", state, current, [])
        real = {"size": page.stat().st_size, "mtime_ns": str(page.stat().st_mtime_ns)}
        with patch("mdoc_check.autofix._markdown", side_effect=RuntimeError("failed")):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                run(self.root, self.report, "en/Main/Page.md", page, "#Title", real, real, [])
        self.assertEqual("#Title", page.read_text(encoding="utf-8"))

    def test_cycle_and_ten_unstable_rounds_are_rejected(self) -> None:
        page = self.locale / "Main" / "Page.md"; page.write_text("a", encoding="utf-8")
        state = {"size": page.stat().st_size, "mtime_ns": str(page.stat().st_mtime_ns)}
        values = iter(["b", "a"]); response = lambda *_args, **_kwargs: {"content": next(values), "errors": [], "applied": []}
        with patch("mdoc_check.autofix._markdown", side_effect=response), patch("mdoc_check.autofix._custom", side_effect=lambda content, *_args: (content, [], [])):
            with self.assertRaisesRegex(ValueError, "循环"):
                run(self.root, self.report, "en/Main/Page.md", page, "a", state, state, [])
        counter = iter(range(20)); response = lambda content, *_args, **_kwargs: {"content": content + str(next(counter)), "errors": [], "applied": []}
        with patch("mdoc_check.autofix._markdown", side_effect=response), patch("mdoc_check.autofix._custom", side_effect=lambda content, *_args: (content, [], [])):
            with self.assertRaisesRegex(ValueError, "10 轮"):
                run(self.root, self.report, "en/Main/Page.md", page, "a", state, state, [])

    def test_total_timeout_rejects_partial_result(self) -> None:
        page = self.locale / "Main" / "Page.md"; page.write_text("a", encoding="utf-8")
        state = {"size": page.stat().st_size, "mtime_ns": str(page.stat().st_mtime_ns)}
        (self.root / ".mdoc" / "check.yaml").write_text("auto_fix:\n  timeout_seconds: 1\n", encoding="utf-8")
        with patch("mdoc_check.autofix.time.monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(ValueError, "超时"):
                run(self.root, self.report, "en/Main/Page.md", page, "a", state, state, [])

    def test_task_case_fix_uses_staging_locale_root(self) -> None:
        staging = self.root / ".mdoc" / "tasks" / "edit" / "staging" / "en"
        (staging / "Main").mkdir(parents=True); (staging / "Main" / "Staged.md").write_text("# Staged\n", encoding="utf-8")
        page = staging / "Main" / "Page.md"; content = "# Page\n\n[Staged](staged.md)\n"; page.write_text(content, encoding="utf-8")
        state = {"size": page.stat().st_size, "mtime_ns": str(page.stat().st_mtime_ns)}
        report = {"context": "task", "scope": {"kind": "task", "task": "edit", "book": "guide"}}
        with patch("mdoc_check.autofix.vale", return_value=[]), patch("mdoc_check.autofix.cspell", return_value=[]):
            result = run(self.root, report, "en/Main/Page.md", page, content, state, state, [])
        self.assertIn("[Staged](Staged.md)", result["content"])

    def test_brand_fix_uses_existing_report_finding_without_saving(self) -> None:
        page = self.locale / "Main" / "Page.md"; content = "# Page\n\nUse Lidar360MLS.\n"; page.write_text(content, encoding="utf-8")
        state = {"size": page.stat().st_size, "mtime_ns": str(page.stat().st_mtime_ns)}
        findings = [{"rule": "terminology.brand-name", "line": 3, "column": 5, "details": {"actual": "Lidar360MLS", "expected": "LiDAR360MLS"}}]
        with patch("mdoc_check.autofix.vale", return_value=[]), patch("mdoc_check.autofix.cspell", return_value=[]):
            result = run(self.root, self.report, "en/Main/Page.md", page, content, state, state, [], findings)
        self.assertIn("LiDAR360MLS", result["content"]); self.assertEqual(1, result["rules"]["terminology.brand-name"]); self.assertEqual(content, page.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
