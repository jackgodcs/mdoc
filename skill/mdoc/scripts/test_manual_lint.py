from __future__ import annotations

import json
import struct
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

import manual_lint


def png(path: Path, width: int, height: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00")


class ManualLintTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = json.loads(json.dumps(manual_lint.DEFAULTS))
        validation = self.config["validation"]
        validation["markdown"].update({"require_blank_line_after_html_block": True, "ordered_list_style": "compact", "paragraph_max_characters": 40})
        validation["images"].update({"require_width_for_block_images": True, "width_steps": [300, 600], "max_pixel_count": 100000})
        validation["terminology"] = {"product_name": "AtlasControl", "forbidden_variants": ["Atlascontrol"]}

    def tearDown(self):
        self.temp.cleanup()

    def lint(self):
        linter = manual_lint.Linter(self.root, self.config, "full")
        linter.scan()
        return linter

    def test_full_rule_profiles_cover_faq_catalog(self):
        expected = {"MDOC-PATH-CASE", "MDOC-HTML-BLANK-LINE", "MDOC-LIST-COMPAT", "MDOC-PARAGRAPH-LONG", "MDOC-IMAGE-WIDTH", "MDOC-HTML-IMG-SYNTAX", "MDOC-FILENAME-SPACE", "MDOC-IMAGE-DIMENSION", "MDOC-LINK-LEVEL", "MDOC-BARE-URL", "MDOC-PRODUCT-NAME", "MDOC-LOCALE-PUNCT", "MDOC-TERM-CASE", "MDOC-SPELLING", "MDOC-PATH-ABSOLUTE", "MDOC-TABLE-SYNTAX"}
        self.assertTrue(expected <= manual_lint.PROFILES["full"])

    def test_exact_and_review_findings(self):
        png(self.root / "en" / "images" / "Shot.PNG", 800, 400)
        (self.root / "en" / "Target.md").write_text("# Target\n", encoding="utf-8")
        (self.root / "en" / "Page.md").write_text("#Title#\n\n[target](target.md)\n\n<div align=center>\n<img src=\"images/shot.png\" \" width=\"555\">\n</div>\n\n1. Step\n\nAtlascontrol，text http://example.com " + "long " * 20 + "\n\n|a|b|\n|---|---|\n|1|\n", encoding="utf-8")
        rules = {finding.rule_id for finding in self.lint().findings}
        expected = {"MDOC-HEADING-SYNTAX", "MDOC-PATH-CASE", "MDOC-HTML-IMG-SYNTAX", "MDOC-HTML-BLANK-LINE", "MDOC-LIST-COMPAT", "MDOC-PRODUCT-NAME", "MDOC-LOCALE-PUNCT", "MDOC-BARE-URL", "MDOC-PARAGRAPH-LONG", "MDOC-TABLE-SYNTAX", "MDOC-IMAGE-DIMENSION", "MDOC-IMAGE-WIDTH-STEP"}
        self.assertTrue(expected <= rules, expected - rules)

    def test_code_and_inline_suppression(self):
        (self.root / "en").mkdir()
        (self.root / "en" / "Page.md").write_text("# Page\n\n<!-- mdoc-lint-disable-next-line MDOC-PRODUCT-NAME reason=\"UI source\" -->\nAtlascontrol\n\n~~~text\nAtlascontrol，http://example.com\n~~~\n", encoding="utf-8")
        product = [item for item in self.lint().findings if item.rule_id == "MDOC-PRODUCT-NAME"]
        self.assertEqual(1, len(product))
        self.assertTrue(product[0].suppressed)

    def test_baseline_and_safe_fix_preserve_crlf_and_bom(self):
        (self.root / "en").mkdir()
        (self.root / "en" / "Target.md").write_text("# Target\n", encoding="utf-8")
        page = self.root / "en" / "Page.md"
        page.write_bytes(b"\xef\xbb\xbf#Title#\r\n\r\n[target](target.md)\r\n")
        linter = self.lint()
        report = self.root / "report.json"
        manual_lint.write_report(linter, report, "full")
        manual_lint.safe_fix(self.root, report, True)
        result = page.read_bytes()
        self.assertTrue(result.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"\r\n", result)
        self.assertIn(b"# Title", result)
        self.assertIn(b"Target.md", result)
        baseline = self.root / "baseline.json"
        baseline.write_text(json.dumps({"fingerprints": [item.fingerprint for item in linter.findings]}), encoding="utf-8")
        rerun = manual_lint.Linter(self.root, self.config, "full", baseline)
        rerun.scan()
        self.assertTrue(any(item.status in {"existing", "resolved"} for item in rerun.findings))

    def test_normal_parent_relative_image_is_not_reported_as_case_error(self):
        png(self.root / "en" / "images" / "Shot.png", 100, 100)
        page = self.root / "en" / "Main" / "Page.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Page\n\n![shot](../images/Shot.png)\n", encoding="utf-8")
        findings = self.lint().findings
        self.assertFalse(any(item.rule_id == "MDOC-PATH-CASE" for item in findings))

    def test_terminology_emphasis_table_spelling_and_locale_image_width(self):
        for locale, width in (("en", 300), ("zh", 600)):
            png(self.root / locale / "images" / "Shot.png", 800, 400)
            (self.root / locale / "Page.md").write_text(
                f"# Page\n\n<img src=\"images/Shot.png\" width=\"{width}\">\n\n**broken\n\n|a|b|\n|---|---|\n" + ("example module wrd\n" if locale == "en" else ""),
                encoding="utf-8",
            )
        terminology = self.root / "terminology.csv"
        terminology.write_text("term_id,source,en,ja,ui_label,keep_original,forbidden_variants,notes\nmodule,示例模块,Example Module,Example Module,,false,example module,\n", encoding="utf-8")
        dictionary = self.root / "words.txt"
        dictionary.write_text("page\nexample\nmodule\n", encoding="utf-8")
        self.config["validation"]["markdown"]["table_style"] = "html"
        self.config["validation"]["spelling"] = {"enabled": True, "required_locales": ["en"], "engine": "wordlist", "dictionary_file": "words.txt"}
        linter = manual_lint.Linter(self.root, self.config, "full", terminology=terminology)
        linter.scan()
        rules = {finding.rule_id for finding in linter.findings}
        self.assertTrue({"MDOC-TERM-CASE", "MDOC-TERM-FORBIDDEN", "MDOC-EMPHASIS-SYNTAX", "MDOC-TABLE-STYLE", "MDOC-SPELLING", "MDOC-IMAGE-LOCALE-WIDTH"} <= rules)

    def test_zero_dependency_yaml_build_adapter_and_advisory_policy(self):
        workspace = self.root / "workspace"
        task_dir = workspace / "manual-tasks" / "task-1"
        task_dir.mkdir(parents=True)
        repo = self.root / "repo" / "V1" / "en" / "Main" / "Module"
        repo.mkdir(parents=True)
        (repo / "Page.md").write_text("#Bad\n", encoding="utf-8")
        (workspace / "workspace.yaml").write_text(f"schema_version: 2\nproduct:\n  profile: product-profile.yaml\nrepository:\n  root: {self.root.as_posix()}/repo\nmanual:\n  active_version: V1\ntasks:\n  root: manual-tasks\nvalidation:\n  build_adapters:\n    - id: smoke\n      command: [cmd, /c, exit, '0']\n      required: true\n", encoding="utf-8")
        (workspace / "product-profile.yaml").write_text("schema_version: 2\nmanual_layout:\n  content_directory: Main\nvalidation:\n  mode: advisory\n  publish_policy:\n    required_before_publish: false\n", encoding="utf-8")
        (task_dir / "task.yaml").write_text("schema_version: 2\ntarget:\n  document_path: Module\nvalidation:\n  mode: inherit\n  profile: release\n", encoding="utf-8")
        (task_dir / "structure.yaml").write_text("pages:\n  - id: page\n    file: Page.md\n", encoding="utf-8")
        args = SimpleNamespace(book_root=None, workspace=workspace, task="task-1", phase="formal", product_profile=None, output=None)
        context = manual_lint.resolve_context(args)
        self.assertEqual("smoke", context["adapters"][0]["id"])
        self.assertEqual({"en/Main/Module/Page.md"}, context["include_files"])
        policy = manual_lint.effective_policy(context["config"], context["task"])
        linter = manual_lint.Linter(context["root"], context["config"], "release", include_files=context["include_files"])
        linter.scan()
        data = manual_lint.write_report(linter, self.root / "report.json", "release", policy=policy)
        self.assertGreater(data["counts"]["error"], 0)
        self.assertFalse(data["publish_blocked"])
        required = json.loads(json.dumps(context["config"]))
        required["validation"]["mode"] = "required"
        required["validation"]["publish_policy"]["required_before_publish"] = True
        required_policy = manual_lint.effective_policy(required, {"validation": {"mode": "advisory"}})
        required_data = manual_lint.write_report(linter, self.root / "required.json", "release", policy=required_policy)
        self.assertEqual("required", required_policy["mode"])
        self.assertTrue(required_data["publish_blocked"])


if __name__ == "__main__":
    unittest.main()
