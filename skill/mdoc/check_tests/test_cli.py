from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import hashlib
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from ruamel.yaml import YAML

from mdoc_check.core import store
from mdoc_check.core import run
from mdoc_check.core import run_selected
from mdoc_check.ignores import add as add_ignore
from mdoc_check.model import digest
from mdoc_check.reports import file_record, file_records, global_record


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "mdoc.py"


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "manual"
        locale = self.workspace / "Guide" / "en"
        (locale / "Main" / "Child").mkdir(parents=True)
        (locale / "images").mkdir()
        (self.workspace / ".mdoc").mkdir()
        (locale / "Summary.md").write_text(
            "# Summary\n\n- [First](./Main/First.md)\n  - [Child](./Main/Child/Child.md)\n- [Second](./Main/Second.md)\n",
            encoding="utf-8",
        )
        (locale / "Main" / "First.md").write_text("# First\n\nText.\n", encoding="utf-8")
        (locale / "Main" / "Child" / "Child.md").write_text("# Child\n\nText.\n", encoding="utf-8")
        (locale / "Main" / "Second.md").write_text("# Second\n\nText.\n", encoding="utf-8")
        workspace = {
            "schema_version": 1,
            "workspace": {"id": "test", "formal_vcs": "none"},
            "product": {"id": "test", "display_name": "Test"},
            "books": {
                "guide": {
                    "root": "Guide",
                    "source_locale": "en",
                    "locales": {"en": {"root": "en", "language": "en"}},
                    "content_root": "Main",
                    "assets_root": "images",
                    "navigation": {"summary": "Summary.md"},
                }
            },
        }
        stream = __import__("io").StringIO()
        YAML().dump(workspace, stream)
        (self.workspace / ".mdoc" / "workspace.yaml").write_text(stream.getvalue(), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str, expected: int = 0) -> dict:
        result = subprocess.run(
            [sys.executable, str(CLI), "check", *args, "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        self.assertEqual(expected, result.returncode, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def stored_report(self, result: dict) -> dict:
        self.assertEqual("mdoc_check_result", result["kind"])
        report_path = Path(result["report"]["path"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        records = file_records(report_path)
        report["file_records"] = records
        report["findings"] = [finding for record in records for finding in file_record(report_path, record["path"])[1]]
        report["findings"].extend(global_record(report_path)["findings"])
        return report

    def define_task(self, task_id: str = "check-task") -> Path:
        directory = self.workspace / ".mdoc" / "tasks" / task_id
        staging = directory / "staging" / "en"
        (staging / "Main").mkdir(parents=True)
        definition = {
            "schema_version": 1,
            "task": {"id": task_id, "book": "guide", "intent": "update_content", "title": "Check task"},
            "scope": {}, "locale_plan": {"source": "en", "targets": {}}, "screenshots": [], "evidence": [],
            "quality_gate": {"profile": "full", "required_reviews": []},
            "manifest": [
                {"action": "update", "locale": "en", "path": "Main/First.md", "kind": "page", "evidence": []},
                {"action": "update", "locale": "en", "path": "Summary.md", "kind": "navigation", "evidence": []},
            ],
        }
        encoded = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        definition["definition_digest"] = hashlib.sha256(encoded).hexdigest()
        stream = __import__("io").StringIO()
        YAML().dump(definition, stream)
        (directory / "task.yaml").write_text(stream.getvalue(), encoding="utf-8")
        state = {"schema_version": 1, "task_id": task_id, "status": "waiting_for_authoring", "definition_confirmation": {"digest": definition["definition_digest"]}}
        (directory / "task-state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        return directory

    def test_section_uses_summary_navigation_subtree(self) -> None:
        result = self.run_cli(
            "run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en",
            "--scope", "section", "--target", "Main/First.md", "--internal-only",
        )
        report = self.stored_report(result)
        self.assertEqual("mdoc_check_report", report["kind"])
        self.assertEqual(1, report["schema_version"])
        self.assertEqual(3, result["updated_files"])
        self.assertTrue(Path(result["report"]["path"]).is_file())
        paths = [item["path"] for item in report["file_records"] if item["kind"] == "markdown"]
        self.assertEqual(["en/Main/Child/Child.md", "en/Main/First.md"], [path for path in paths if path != "en/Summary.md"])
        self.assertNotIn("en/Main/Second.md", paths)

    def test_english_han_is_mandatory_error(self) -> None:
        page = self.workspace / "Guide" / "en" / "Main" / "First.md"
        page.write_text("# First\n\nEnglish 中文.\n", encoding="utf-8")
        report = self.stored_report(self.run_cli(
            "run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en",
            "--scope", "page", "--target", "Main/First.md", "--internal-only", expected=3,
        ))
        finding = next(item for item in report["findings"] if item["rule"] == "locale.en-no-han")
        self.assertEqual("error", finding["severity"])
        self.assertTrue(finding["mandatory"])
        self.assertEqual(3, finding["line"])

    def test_workspace_aggregates_registered_locales(self) -> None:
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--scope", "workspace", "--internal-only"))
        self.assertEqual("workspace", report["context"])
        self.assertEqual({"en"}, {item["path"].split("/", 1)[0] for item in report["file_records"]})

    def test_report_inputs_include_summary_resources_and_configuration(self) -> None:
        image = self.workspace / "Guide" / "en" / "images" / "Sample.png"
        Image.new("RGB", (2, 2), "white").save(image)
        page = self.workspace / "Guide" / "en" / "Main" / "First.md"
        page.write_text("# First\n\n![Sample](../images/Sample.png)\n", encoding="utf-8")
        report = self.stored_report(self.run_cli(
            "run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en",
            "--scope", "page", "--target", "Main/First.md", "--internal-only",
        ))
        paths = {item["path"] for item in report["file_records"]}
        self.assertIn("en/Summary.md", paths)
        self.assertIn("en/images/Sample.png", paths)

    def test_brand_rule_runs_in_internal_mode_and_records_configuration(self) -> None:
        page = self.workspace / "Guide" / "en" / "Main" / "First.md"; page.write_text("# First\n\nUse Lidar360MLS.\n", encoding="utf-8")
        report = run(self.workspace, "guide", "en", "page", "Main/First.md", "basic", internal_only=True)
        issue = next(item for item in report["findings"] if item["rule"] == "terminology.brand-name")
        self.assertEqual(".mdoc/check-brands.yaml", issue["details"]["configuration"]); self.assertIn(".mdoc/check-brands.yaml", report["inputs"]["configuration"])

    def test_language_configuration_overrides_workspace_configuration(self) -> None:
        (self.workspace / ".mdoc" / "check.yaml").write_text("images:\n  max_width_px: 10\n", encoding="utf-8")
        (self.workspace / ".mdoc" / "check.en.yaml").write_text("images:\n  max_width_px: 20\n", encoding="utf-8")
        image = self.workspace / "Guide" / "en" / "images" / "Wide.png"
        Image.new("RGB", (15, 2), "white").save(image)
        page = self.workspace / "Guide" / "en" / "Main" / "First.md"
        page.write_text("# First\n\n![Wide](../images/Wide.png)\n", encoding="utf-8")
        report = self.stored_report(self.run_cli(
            "run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en",
            "--scope", "page", "--target", "Main/First.md", "--internal-only",
        ))
        self.assertFalse(any(item["rule"] == "image.max-width" for item in report["findings"]))

    def test_workspace_configuration_overrides_markdownlint_severity(self) -> None:
        (self.workspace / ".mdoc" / "check.yaml").write_text("severity_overrides:\n  md003: warning\n", encoding="utf-8")
        page = self.workspace / "Guide" / "en" / "Main" / "First.md"
        page.write_text("First\n=====\n", encoding="utf-8")
        report = self.stored_report(self.run_cli(
            "run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en",
            "--scope", "page", "--target", "Main/First.md",
        ))
        issue = next(item for item in report["findings"] if item["rule"] == "markdown.md003")
        self.assertEqual("warning", issue["severity"])

    def test_invalid_severity_override_is_rejected(self) -> None:
        (self.workspace / ".mdoc" / "check.yaml").write_text("severity_overrides:\n  md003: ignore\n", encoding="utf-8")
        report = self.run_cli("run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en", "--scope", "page", "--target", "Main/First.md", expected=4)
        self.assertIn("severity_overrides", report["error"])

    def test_rule_configuration_applies_to_mdoc_findings(self) -> None:
        (self.workspace / ".mdoc" / "check.yaml").write_text("severity_overrides:\n  markdown.single-h1: warning\ndisabled_rules:\n  - html.table-structure\n", encoding="utf-8")
        page = self.workspace / "Guide" / "en" / "Main" / "First.md"
        page.write_text("## Section\n\n<table><tr><td>Broken</tr>\n", encoding="utf-8")
        report = self.stored_report(self.run_cli(
            "run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en",
            "--scope", "page", "--target", "Main/First.md", "--internal-only",
        ))
        issue = next(item for item in report["findings"] if item["rule"] == "markdown.single-h1")
        self.assertEqual("warning", issue["severity"])
        self.assertFalse(any(item["rule"] == "html.table-structure" for item in report["findings"]))

    def test_default_non_blocking_rules_are_warnings(self) -> None:
        from mdoc_check.core import _rule_id, apply_rule_config, check_config
        from mdoc_check.model import finding

        config, _paths = check_config(self.workspace, "en")
        rules = {
            "html.block-blank-line", "html.table-structure", "image.extension-matches-format",
            "link.anchor-exists", "markdown.md032", "markdown.md038", "markdown.single-h1", "path.case-exact",
        }
        findings = [finding(rule, "error", "en/Main/First.md", "Example", 1, 1) for rule in rules]
        self.assertEqual({"warning"}, {item["severity"] for item in apply_rule_config(findings, config)})
        self.assertEqual("md032", _rule_id("markdown.md032"))

    def test_mandatory_rule_cannot_be_overridden_or_disabled(self) -> None:
        for config in ("severity_overrides:\n  text.utf8: warning\n", "disabled_rules:\n  - path.inside-locale\n"):
            with self.subTest(config=config):
                (self.workspace / ".mdoc" / "check.yaml").write_text(config, encoding="utf-8")
                report = self.run_cli("run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en", "--scope", "page", "--target", "Main/First.md", expected=4)
                self.assertIn("mandatory", report["error"].lower())

    def test_invalid_disabled_rules_is_rejected(self) -> None:
        (self.workspace / ".mdoc" / "check.yaml").write_text("disabled_rules: html.table-structure\n", encoding="utf-8")
        report = self.run_cli("run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en", "--scope", "page", "--target", "Main/First.md", expected=4)
        self.assertIn("disabled_rules", report["error"])

    def test_legacy_vale_product_rule_configuration_is_rejected(self) -> None:
        for value in ("severity_overrides:\n  vale.MDOC.ProductName: warning\n", "disabled_rules:\n  - vale.MDOC.ProductName\n"):
            with self.subTest(value=value):
                (self.workspace / ".mdoc" / "check.yaml").write_text(value, encoding="utf-8")
                report = self.run_cli("run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en", "--scope", "page", "--target", "Main/First.md", expected=4)
                self.assertIn("terminology.brand-name", report["error"])

    def test_invalid_checker_concurrency_is_rejected(self) -> None:
        (self.workspace / ".mdoc" / "check.yaml").write_text("execution:\n  external_checkers: 4\n", encoding="utf-8")
        report = self.run_cli("run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en", "--scope", "book", expected=4)
        self.assertIn("external_checkers", report["error"])

    def test_explicit_worker_configuration_is_accepted(self) -> None:
        (self.workspace / ".mdoc" / "check.yaml").write_text("execution:\n  external_checkers: 1\n  image_workers: 1\n  image_workers_max: 2\n", encoding="utf-8")
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en", "--scope", "book", "--internal-only"))
        self.assertIn("en/Summary.md", {item["path"] for item in report["file_records"]})

    def japanese_locale(self, content: str) -> None:
        source = self.workspace / ".mdoc" / "workspace.yaml"
        config = YAML(typ="safe").load(source.read_text(encoding="utf-8"))
        config["books"]["guide"]["locales"]["ja"] = {"root": "ja", "language": "ja"}
        stream = __import__("io").StringIO(); YAML().dump(config, stream); source.write_text(stream.getvalue(), encoding="utf-8")
        locale = self.workspace / "Guide" / "ja"
        (locale / "Main").mkdir(parents=True); (locale / "images").mkdir()
        (locale / "Summary.md").write_text("# Summary\n\n- [Page](Main/Page.md)\n", encoding="utf-8")
        (locale / "Main" / "Page.md").write_text(content, encoding="utf-8")

    def test_japanese_page_does_not_run_english_spelling(self) -> None:
        self.japanese_locale("# ページ\n\nExisting English business text with Gvscript.\n")
        with patch("mdoc_check.core.cspell", side_effect=AssertionError("CSpell must not run for Japanese")):
            report = run(self.workspace, "guide", "ja", "page", "Main/Page.md", "basic")
        self.assertEqual("not_applicable", report["checkers"]["cspell"]["status"])
        self.assertFalse(any(item["rule"] == "spelling.unknown-word" for item in report["findings"]))

    def test_english_page_in_japanese_manual_runs_english_spelling(self) -> None:
        self.japanese_locale("# English page\n\nExisting English business text with Gvscript.\n")
        with patch("mdoc_check.core.cspell", return_value=[]) as checker:
            report = run(self.workspace, "guide", "ja", "page", "Main/Page.md", "basic")
        checker.assert_called_once()
        self.assertEqual("passed", report["checkers"]["cspell"]["status"])
        dictionaries = checker.call_args.args[3]
        self.assertEqual([self.workspace / ".mdoc" / "check-words.txt", self.workspace / ".mdoc" / "check-words.en.txt"], dictionaries[:2])
        self.assertFalse(dictionaries[2].exists())

    def test_invalid_language_detection_scan_lines_is_rejected(self) -> None:
        (self.workspace / ".mdoc" / "check.yaml").write_text("language_detection:\n  scan_lines: 101\n", encoding="utf-8")
        report = self.run_cli("run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en", "--scope", "page", "--target", "Main/First.md", expected=4)
        self.assertIn("language_detection.scan_lines", report["error"])

    def test_page_target_cannot_escape_locale_root(self) -> None:
        report = self.run_cli(
            "run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en",
            "--scope", "page", "--target", "../../Outside.md", "--internal-only", expected=4,
        )
        self.assertEqual("incomplete", report["status"])
        self.assertIn("relative Markdown path", report["error"])

    def test_human_output_shows_real_stages_and_summary(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLI), "check", "run", "--workspace", str(self.workspace), "--book", "guide", "--locale", "en", "--scope", "page", "--target", "Main/First.md", "--internal-only"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1"},
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertIn("已选择 1 个 Markdown", result.stderr)
        self.assertIn("状态: passed", result.stdout)
        self.assertIn("报告:", result.stdout)

    def test_workspace_navigation_parity_counts_level_difference(self) -> None:
        source = self.workspace / ".mdoc" / "workspace.yaml"
        config = YAML(typ="safe").load(source.read_text(encoding="utf-8"))
        config["books"]["guide"]["locales"]["zh"] = {"root": "zh", "language": "zh"}
        stream = __import__("io").StringIO()
        YAML().dump(config, stream)
        source.write_text(stream.getvalue(), encoding="utf-8")
        zh = self.workspace / "Guide" / "zh"
        (zh / "Main" / "Child").mkdir(parents=True)
        (zh / "images").mkdir()
        for logical in ("First.md", "Second.md"):
            (zh / "Main" / logical).write_text(f"# {logical[:-3]}\n", encoding="utf-8")
        (zh / "Main" / "Child" / "Child.md").write_text("# Child\n", encoding="utf-8")
        (zh / "Summary.md").write_text(
            "# Summary\n\n- [First](Main/First.md)\n- [Child](Main/Child/Child.md)\n- [Second](Main/Second.md)\n",
            encoding="utf-8",
        )
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--scope", "workspace", "--internal-only"))
        self.assertEqual(1, report["counts"]["detected_warnings"])
        parity = report["findings"][-1]
        self.assertEqual("navigation.locale-parity", parity["rule"])
        self.assertEqual("guide", parity["book"])
        self.assertTrue(parity["finding_id"])

    def test_doctor_runs_locked_smoke_checks(self) -> None:
        report = self.run_cli("doctor")
        self.assertEqual("passed", report["status"])
        self.assertTrue(all(report["smoke"].values()))

    def test_report_context_keeps_only_current_generation(self) -> None:
        first = run(self.workspace, "guide", "en", "page", "Main/First.md", "basic", True)
        store(first, self.workspace)
        first_generation = json.loads(Path(first["path"]).read_text(encoding="utf-8"))["generation"]
        second = run(self.workspace, "guide", "en", "page", "Main/First.md", "basic", True)
        store(second, self.workspace)
        latest = json.loads(Path(second["path"]).read_text(encoding="utf-8"))
        generations = list((Path(second["path"]).parent / "generations").iterdir())
        self.assertNotEqual(first_generation, latest["generation"])
        self.assertEqual([latest["generation"]], [path.name for path in generations])
        self.assertEqual([], list(Path(second["path"]).parent.glob("[0-9]*.json")))

    def test_file_selection_refreshes_only_selected_file_and_marks_full_check_stale(self) -> None:
        report = run(self.workspace, "guide", "en", "book", None, "basic", True)
        store(report, self.workspace)
        report_path = Path(report["path"])
        before = json.loads(report_path.read_text(encoding="utf-8"))
        second_before = file_record(report_path, "en/Main/Second.md")[0]
        (self.workspace / "Guide" / "en" / "Main" / "First.md").write_text("# First\n", encoding="utf-8")
        selection = Path(self.temp.name) / "selection.json"
        selection.write_text(json.dumps({"schema_version": 1, "kind": "mdoc_check_file_selection", "source_report": report_path.relative_to(self.workspace / ".mdoc" / "reports" / "check").as_posix(), "base_revision": before["revision"], "files": ["en/Main/First.md"]}, indent=2), encoding="utf-8")
        result = run_selected(self.workspace, selection, internal_only=True)
        current = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(1, result["updated_files"])
        self.assertEqual("stale", current["full_check"]["status"])
        self.assertEqual(4, current["files"]["count"])
        self.assertEqual(second_before, file_record(report_path, "en/Main/Second.md")[0])
        self.assertEqual({"count": 0, "freshness": "current"}, current["global_findings"])

    def test_task_report_records_added_missing_and_deleted_files(self) -> None:
        directory = self.define_task()
        definition_path = directory / "task.yaml"
        definition = YAML(typ="safe").load(definition_path.read_text(encoding="utf-8"))
        definition["manifest"] = [
            {"action": "create", "locale": "en", "path": "Main/Added.md", "kind": "page", "evidence": []},
            {"action": "create", "locale": "en", "path": "Main/Missing.md", "kind": "page", "evidence": []},
            {"action": "delete", "locale": "en", "path": "Main/Second.md", "kind": "page", "evidence": []},
        ]
        authority = {key: value for key, value in definition.items() if key != "definition_digest"}
        definition["definition_digest"] = hashlib.sha256(json.dumps(authority, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        stream = __import__("io").StringIO(); YAML().dump(definition, stream); definition_path.write_text(stream.getvalue(), encoding="utf-8")
        state_path = directory / "task-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")); state["definition_confirmation"]["digest"] = definition["definition_digest"]
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        (directory / "staging" / "en" / "Main" / "Added.md").write_text("# Added\n", encoding="utf-8")
        report = {"schema_version": 1, "kind": "mdoc_check_report", "context": "task", "scope": {"kind": "task", "task": "check-task", "book": "guide", "mode": "coordinator"}, "status": "passed", "findings": [], "inputs": {}, "counts": {}, "created_at": 1}
        store(report, self.workspace)
        records = {item["path"]: item for item in file_records(Path(report["path"]))}
        self.assertEqual("added", records["en/Main/Added.md"]["existence"])
        self.assertEqual("missing", records["en/Main/Missing.md"]["existence"])
        self.assertEqual("deleted", records["en/Main/Second.md"]["existence"])

    def test_file_selection_rejects_stale_revision(self) -> None:
        report = run(self.workspace, "guide", "en", "page", "Main/First.md", "basic", True); store(report, self.workspace)
        report_path = Path(report["path"]); selection = Path(self.temp.name) / "selection.json"
        selection.write_text(json.dumps({"schema_version": 1, "kind": "mdoc_check_file_selection", "source_report": report_path.relative_to(self.workspace / ".mdoc" / "reports" / "check").as_posix(), "base_revision": 0, "files": ["en/Main/First.md"]}, indent=2), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "revision"):
            run_selected(self.workspace, selection, internal_only=True)

    def test_task_check_suppresses_pre_existing_errors_but_blocks_introduced_errors(self) -> None:
        directory = self.define_task()
        (self.workspace / "Guide" / "en" / "Main" / "Second.md").write_text("# Second\n\nLegacy，text.\n", encoding="utf-8")
        (directory / "staging" / "en" / "Main" / "First.md").write_text("# First\n\nIntroduced，text.\n", encoding="utf-8")
        (directory / "staging" / "en" / "Summary.md").write_text("# Summary\n\n- [First](Main/First.md)\n  - [Child](Main/Child/Child.md)\n- [Second](Main/Second.md)\n", encoding="utf-8")
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--internal-only", expected=3))
        introduced = next(item for item in report["findings"] if item["path"] == "en/Main/First.md" and item["rule"] == "locale.en-punctuation")
        existing = next(item for item in report["findings"] if item["path"] == "en/Main/Second.md" and item["rule"] == "locale.en-punctuation")
        self.assertEqual(("introduced", "inactive"), (introduced["classification"], introduced["suppression"]))
        self.assertEqual(("pre_existing", "active"), (existing["classification"], existing["suppression"]))
        self.assertEqual(1, report["counts"]["effective_errors"])
        self.assertEqual("check-task", report["scope"]["task"])
        self.assertIsInstance(report["baseline"]["findings"], int)

    def test_task_baseline_suppresses_existing_finding_on_changed_page(self) -> None:
        directory = self.define_task()
        formal = self.workspace / "Guide" / "en" / "Main" / "First.md"
        formal.write_text("# First\n\nExisting，text.\n", encoding="utf-8")
        (directory / "staging" / "en" / "Main" / "First.md").write_text("# First\n\nExisting，text.\n", encoding="utf-8")
        (directory / "staging" / "en" / "Summary.md").write_text("# Summary\n\n- [First](Main/First.md)\n  - [Child](Main/Child/Child.md)\n- [Second](Main/Second.md)\n", encoding="utf-8")
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--internal-only"))
        issue = next(item for item in report["findings"] if item["path"] == "en/Main/First.md" and item["rule"] == "locale.en-punctuation")
        self.assertEqual(("pre_existing", "active"), (issue["classification"], issue["suppression"]))
        self.assertEqual(0, report["counts"]["effective_errors"])

    def test_task_internal_checks_read_staged_markdown(self) -> None:
        directory = self.define_task()
        (directory / "staging" / "en" / "Main" / "First.md").write_text("# First\n\nEnglish 中文.\n", encoding="utf-8")
        (directory / "staging" / "en" / "Summary.md").write_text("# Summary\n\n- [First](Main/First.md)\n  - [Child](Main/Child/Child.md)\n- [Second](Main/Second.md)\n", encoding="utf-8")
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--internal-only", expected=3))
        issue = next(item for item in report["findings"] if item["path"] == "en/Main/First.md" and item["rule"] == "locale.en-no-han")
        self.assertTrue(issue["mandatory"])
        self.assertEqual(("introduced", "inactive"), (issue["classification"], issue["suppression"]))

    def test_task_baseline_identity_survives_line_shift(self) -> None:
        directory = self.define_task()
        formal = self.workspace / "Guide" / "en" / "Main" / "First.md"
        formal.write_text("# First\n\nEnglish，text.\n", encoding="utf-8")
        (directory / "staging" / "en" / "Main" / "First.md").write_text("# First\n\nAdded line.\n\nEnglish，text.\n", encoding="utf-8")
        (directory / "staging" / "en" / "Summary.md").write_text("# Summary\n\n- [First](Main/First.md)\n  - [Child](Main/Child/Child.md)\n- [Second](Main/Second.md)\n", encoding="utf-8")
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--internal-only"))
        issue = next(item for item in report["findings"] if item["rule"] == "locale.en-punctuation")
        self.assertEqual(("pre_existing", "active"), (issue["classification"], issue["suppression"]))
        self.assertEqual(0, report["counts"]["effective_errors"])

    def test_pre_existing_task_warning_remains_visible_without_user_ignore(self) -> None:
        directory = self.define_task()
        summary = "# Summary\n\n- [Wrong title](Main/First.md)\n  - [Child](Main/Child/Child.md)\n- [Second](Main/Second.md)\n"
        (self.workspace / "Guide" / "en" / "Summary.md").write_text(summary, encoding="utf-8")
        (directory / "staging" / "en" / "Main" / "First.md").write_text("# First\n", encoding="utf-8")
        (directory / "staging" / "en" / "Summary.md").write_text(summary, encoding="utf-8")
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--internal-only"))
        self.assertGreater(report["counts"]["effective_warnings"], 0)
        warning = next(item for item in report["findings"] if item["severity"] == "warning")
        self.assertEqual(("pre_existing", "inactive"), (warning["classification"], warning["suppression"]))

    def test_user_ignore_suppresses_matching_page_error(self) -> None:
        page = self.workspace / "Guide" / "en" / "Main" / "First.md"
        page.write_text("No H1.\n", encoding="utf-8")
        report = run(self.workspace, "guide", "en", "page", "Main/First.md", "basic", True)
        issue = next(item for item in report["findings"] if item["rule"] == "markdown.single-h1")
        add_ignore(self.workspace, issue)
        checked = run(self.workspace, "guide", "en", "page", "Main/First.md", "basic", True)
        ignored = next(item for item in checked["findings"] if item["rule"] == "markdown.single-h1")
        self.assertEqual("active", ignored["suppression"])
        self.assertEqual(0, checked["counts"]["effective_errors"])

    def test_task_overlay_can_introduce_error_on_unchanged_file(self) -> None:
        directory = self.define_task()
        definition_path = directory / "task.yaml"
        definition = YAML(typ="safe").load(definition_path.read_text(encoding="utf-8"))
        definition["manifest"] = [{"action": "delete", "locale": "en", "path": "Main/Second.md", "kind": "page", "evidence": []}]
        authority = {key: value for key, value in definition.items() if key != "definition_digest"}
        definition["definition_digest"] = hashlib.sha256(json.dumps(authority, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        stream = __import__("io").StringIO(); YAML().dump(definition, stream); definition_path.write_text(stream.getvalue(), encoding="utf-8")
        state_path = directory / "task-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")); state["definition_confirmation"]["digest"] = definition["definition_digest"]
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--internal-only", expected=3))
        issue = next(item for item in report["findings"] if item["path"] == "en/Summary.md" and item["rule"] == "link.target-exists")
        self.assertEqual(("introduced", "inactive"), (issue["classification"], issue["suppression"]))

    def test_task_candidate_excludes_out_of_manifest_staging_files(self) -> None:
        directory = self.define_task()
        (directory / "staging" / "en" / "Main" / "First.md").write_text("# First\n", encoding="utf-8")
        (directory / "staging" / "en" / "Summary.md").write_text("# Summary\n\n- [First](Main/First.md)\n  - [Child](Main/Child/Child.md)\n- [Second](Main/Second.md)\n", encoding="utf-8")
        extra = directory / "staging" / "en" / "Main" / "Extra.md"
        extra.write_text("Extra without H1.\n", encoding="utf-8")
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--internal-only"))
        self.assertNotIn("en/Main/Extra.md", [item["path"] for item in report["file_records"]])
        self.assertFalse(any(item["path"] == "en/Main/Extra.md" for item in report["findings"]))

    def test_skip_check_returns_skipped_without_changing_task_state(self) -> None:
        directory = self.define_task()
        state_path = directory / "task-state.json"
        before = state_path.read_bytes()
        result = self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--skip-check")
        report = json.loads(Path(result["report"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual("skipped", report["status"])
        self.assertEqual("user_requested", report["skip_reason"])
        self.assertEqual(before, state_path.read_bytes())

    def test_task_check_requires_confirmed_definition(self) -> None:
        directory = self.define_task()
        state_path = directory / "task-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")); state["definition_confirmation"] = None
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        report = self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--skip-check", expected=4)
        self.assertIn("not confirmed", report["error"])

    def test_task_manifest_path_cannot_escape_locale(self) -> None:
        directory = self.define_task()
        definition_path = directory / "task.yaml"
        definition = YAML(typ="safe").load(definition_path.read_text(encoding="utf-8"))
        definition["manifest"] = [{"action": "update", "locale": "en", "path": "Main/../Outside.md", "kind": "page", "evidence": []}]
        authority = {key: value for key, value in definition.items() if key != "definition_digest"}
        definition["definition_digest"] = hashlib.sha256(json.dumps(authority, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        stream = __import__("io").StringIO(); YAML().dump(definition, stream); definition_path.write_text(stream.getvalue(), encoding="utf-8")
        state_path = directory / "task-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")); state["definition_confirmation"]["digest"] = definition["definition_digest"]
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        report = self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", expected=4)
        self.assertIn("must stay inside", report["error"])

    def test_task_only_options_are_rejected_for_book_scope(self) -> None:
        report = self.run_cli("run", "--workspace", str(self.workspace), "--scope", "book", "--book", "guide", "--locale", "en", "--skip-check", expected=4)
        self.assertIn("valid only for task scope", report["error"])

    def test_task_checks_declared_asset_even_when_markdown_does_not_reference_it(self) -> None:
        directory = self.define_task()
        definition_path = directory / "task.yaml"
        definition = YAML(typ="safe").load(definition_path.read_text(encoding="utf-8"))
        definition["manifest"].append({"action": "create", "locale": "en", "path": "images/Declared.png", "kind": "asset", "evidence": []})
        authority = {key: value for key, value in definition.items() if key != "definition_digest"}
        definition["definition_digest"] = hashlib.sha256(json.dumps(authority, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        stream = __import__("io").StringIO(); YAML().dump(definition, stream); definition_path.write_text(stream.getvalue(), encoding="utf-8")
        state_path = directory / "task-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")); state["definition_confirmation"]["digest"] = definition["definition_digest"]
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        (directory / "staging" / "en" / "Main" / "First.md").write_text("# First\n", encoding="utf-8")
        (directory / "staging" / "en" / "Summary.md").write_text("# Summary\n\n- [First](Main/First.md)\n  - [Child](Main/Child/Child.md)\n- [Second](Main/Second.md)\n", encoding="utf-8")
        asset = directory / "staging" / "en" / "images" / "Declared.png"
        asset.parent.mkdir(parents=True); asset.write_bytes(b"not an image")
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--internal-only", expected=3))
        issue = next(item for item in report["findings"] if item["path"] == "en/images/Declared.png" and item["rule"] == "image.decodable")
        self.assertEqual("introduced", issue["classification"])
        self.assertIn("en/images/Declared.png", [item["path"] for item in report["file_records"]])

    def test_task_candidate_resolves_formal_resource_from_staged_page(self) -> None:
        directory = self.define_task()
        Image.new("RGB", (2, 2), "white").save(self.workspace / "Guide" / "en" / "images" / "Formal.png")
        (directory / "staging" / "en" / "Main" / "First.md").write_text("# First\n\n![Formal](../images/Formal.png)\n", encoding="utf-8")
        (directory / "staging" / "en" / "Summary.md").write_text("# Summary\n\n- [First](Main/First.md)\n  - [Child](Main/Child/Child.md)\n- [Second](Main/Second.md)\n", encoding="utf-8")
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--internal-only"))
        self.assertFalse(any(item["rule"] == "link.target-exists" and "Formal.png" in item["message"] for item in report["findings"]))
        self.assertIn("en/images/Formal.png", [item["path"] for item in report["file_records"]])

    def test_task_check_is_incomplete_when_declared_staging_file_is_missing(self) -> None:
        directory = self.define_task()
        (directory / "staging" / "en" / "Summary.md").write_text("# Summary\n", encoding="utf-8")
        report = self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--internal-only", expected=4)
        self.assertEqual("incomplete", report["status"])
        self.assertIn("Main/First.md", report["error"])

    def contributor_manifest(self, directory: Path, files: list[str], task_id: str = "check-task") -> Path:
        path = directory / "contributor-check.json"
        path.write_text(json.dumps({"schema_version": 1, "kind": "mdoc_check_contributor_manifest", "task_id": task_id, "files": files}, indent=2), encoding="utf-8")
        return path

    def test_contributor_check_only_blocks_selected_frozen_files(self) -> None:
        directory = self.define_task()
        (directory / "staging" / "en" / "Main" / "First.md").write_text("# First\n\nSelected，text.\n", encoding="utf-8")
        (directory / "staging" / "en" / "Summary.md").write_text("# Summary\n\n- [First](Main/First.md)\n- [Again](Main/First.md)\n", encoding="utf-8")
        selection = self.contributor_manifest(directory, ["en/Main/First.md"])
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--contributor-manifest", str(selection), "--internal-only", expected=3))
        self.assertEqual("contributor", report["scope"]["mode"])
        self.assertEqual(["en/Main/First.md"], report["scope"]["files"])
        summary = next(item for item in report["findings"] if item["path"] == "en/Summary.md" and item["rule"] == "navigation.duplicate-entry")
        self.assertEqual("active", summary["suppression"])
        self.assertEqual(1, report["counts"]["effective_errors"])

    def test_contributor_manifest_cannot_expand_frozen_task_scope(self) -> None:
        directory = self.define_task()
        (directory / "staging" / "en" / "Main" / "First.md").write_text("# First\n", encoding="utf-8")
        (directory / "staging" / "en" / "Summary.md").write_text("# Summary\n", encoding="utf-8")
        selection = self.contributor_manifest(directory, ["en/Main/Second.md"])
        report = self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--contributor-manifest", str(selection), "--internal-only", expected=4)
        self.assertIn("outside the frozen task manifest", report["error"])

    def test_contributor_manifest_is_bound_to_task_id(self) -> None:
        directory = self.define_task()
        (directory / "staging" / "en" / "Main" / "First.md").write_text("# First\n", encoding="utf-8")
        (directory / "staging" / "en" / "Summary.md").write_text("# Summary\n", encoding="utf-8")
        selection = self.contributor_manifest(directory, ["en/Main/First.md"], "another-task")
        report = self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--contributor-manifest", str(selection), "--internal-only", expected=4)
        self.assertIn("task_id does not match", report["error"])

    def test_contributor_check_scans_only_selected_locales(self) -> None:
        directory = self.define_task()
        source = self.workspace / ".mdoc" / "workspace.yaml"
        config = YAML(typ="safe").load(source.read_text(encoding="utf-8")); config["books"]["guide"]["locales"]["zh"] = {"root": "zh", "language": "zh"}
        stream = __import__("io").StringIO(); YAML().dump(config, stream); source.write_text(stream.getvalue(), encoding="utf-8")
        zh = self.workspace / "Guide" / "zh"; (zh / "Main").mkdir(parents=True); (zh / "images").mkdir(); (zh / "Summary.md").write_text("# Summary\n", encoding="utf-8")
        (directory / "staging" / "en" / "Main" / "First.md").write_text("# First\n", encoding="utf-8")
        (directory / "staging" / "en" / "Summary.md").write_text("# Summary\n\n- [First](Main/First.md)\n  - [Child](Main/Child/Child.md)\n- [Second](Main/Second.md)\n", encoding="utf-8")
        selection = self.contributor_manifest(directory, ["en/Main/First.md"])
        report = self.stored_report(self.run_cli("run", "--workspace", str(self.workspace), "--scope", "task", "--task", "check-task", "--contributor-manifest", str(selection), "--internal-only"))
        self.assertEqual({"en"}, {item["path"].split("/", 1)[0] for item in report["file_records"]})


if __name__ == "__main__":
    unittest.main()
