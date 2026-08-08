from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reportlab.pdfgen import canvas

import manual_pdf_check
import pdf_check_core
import pdf_check_render
import pdf_check_server
import pdf_check_source_opener
import pdf_check_build
import pdf_check_mapping


def make_pdf(path: Path, pages: list[dict]):
    writer = canvas.Canvas(str(path), pagesize=(300, 400))
    for page in pages:
        for text, x, y, size in page.get("texts", []):
            writer.setFont("Helvetica", size)
            writer.drawString(x, y, text)
        for x, y, width, height in page.get("rects", []):
            writer.rect(x, y, width, height)
        writer.showPage()
    writer.save()


class ManualPdfCheckTests(unittest.TestCase):
    def test_problem_page_renderer_works_without_poppler(self):
        make_pdf(self.pdf, [{"texts": [("Preview", 20, 350, 10)]}])
        output = self.root / "rendered-page.png"
        pdf_check_render.render_page(self.pdf, 1, output, dpi=72)
        self.assertTrue(output.is_file())
        self.assertTrue(output.read_bytes().startswith(b"\x89PNG"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.book = self.root / "book"
        self.book.mkdir()
        (self.book / "en").mkdir()
        (self.book / "en" / "Page.md").write_text("# Example Page\n\nContent.\n", encoding="utf-8")
        self.pdf = self.root / "manual.pdf"

    def tearDown(self):
        self.temp.cleanup()

    def test_existing_pdf_check_reports_blank_page_and_marker_leak(self):
        make_pdf(self.pdf, [{"texts": [("MDOC-MAP:en/Page.md:1:test", 20, 350, 10)]}, {}])
        output = self.root / "output"
        result = pdf_check_core.check_existing_pdf(self.book, self.pdf, output, {"artifact_id": "pdf-en", "locale": "en"})
        rules = {item["rule_id"] for item in result["findings"]}
        self.assertIn("MDOC-PDF-MARKER-LEAK", rules)
        self.assertIn("MDOC-PDF-UNEXPECTED-BLANK-PAGE", rules)
        self.assertEqual(1, result["counts"]["effective_errors"])
        self.assertTrue((output / "pdf-check.json").exists())
        self.assertTrue((output / "pdf-check-summary.md").exists())

    def test_user_ignore_removes_only_matching_effective_error(self):
        make_pdf(self.pdf, [{"texts": [("MDOC-MAP:en/Page.md:1:test", 20, 350, 10)]}])
        output = self.root / "output"
        first = pdf_check_core.check_existing_pdf(self.book, self.pdf, output, {"artifact_id": "pdf-en", "locale": "en"})
        finding = next(item for item in first["findings"] if item["rule_id"] == "MDOC-PDF-MARKER-LEAK")
        overrides = self.root / "overrides.json"
        pdf_check_core.confirm_ignore(first, finding["id"], overrides, "机器误判")
        second = pdf_check_core.check_existing_pdf(self.book, self.pdf, output, {"artifact_id": "pdf-en", "locale": "en", "overrides": overrides})
        ignored = next(item for item in second["findings"] if item["rule_id"] == "MDOC-PDF-MARKER-LEAK")
        self.assertEqual("ignored-by-user", ignored["status"])
        self.assertEqual(0, second["counts"]["effective_errors"])
        self.assertEqual(1, second["counts"]["ignored_errors"])

    def test_ignore_from_aggregate_report_uses_owning_artifact_digest(self):
        make_pdf(self.pdf, [{"texts": [("MDOC-MAP:en/Page.md:1:test", 20, 350, 10)]}])
        second_pdf = self.root / "other.pdf"
        make_pdf(second_pdf, [{"texts": [("Other", 20, 350, 10)]}])
        first = pdf_check_core.check_existing_pdf(self.book, self.pdf, self.root / "first", {"artifact_id": "en"})
        second = pdf_check_core.check_existing_pdf(self.book, second_pdf, self.root / "second", {"artifact_id": "zh"})
        aggregate = pdf_check_core.aggregate_reports([first, second], self.root / "aggregate")
        finding = next(item for item in aggregate["findings"] if item["artifact_id"] == "en" and item["severity"] == "error")
        overrides = self.root / "overrides.json"
        pdf_check_core.confirm_ignore(aggregate, finding["id"], overrides, "聚合报告误报")
        rechecked = pdf_check_core.check_existing_pdf(self.book, self.pdf, self.root / "rechecked", {"artifact_id": "en", "overrides": overrides})
        ignored = next(item for item in rechecked["findings"] if item["fingerprint"] == finding["fingerprint"])
        self.assertEqual("ignored-by-user", ignored["status"])

    def test_task_scope_only_blocks_declared_source(self):
        findings = [
            {"severity": "error", "scope": "task", "status": "new", "effective_blocking": True},
            {"severity": "error", "scope": "book-existing", "status": "new", "effective_blocking": False},
            {"severity": "error", "scope": "artifact", "status": "new", "effective_blocking": True},
        ]
        counts = pdf_check_core.count_findings(findings)
        self.assertEqual(2, counts["effective_errors"])
        self.assertEqual(1, counts["book_existing_errors"])

    def test_cli_check_exit_codes(self):
        make_pdf(self.pdf, [{}])
        output = self.root / "report"
        code = manual_pdf_check.main(["check", "--book-root", str(self.book), "--pdf", str(self.pdf), "--output", str(output)])
        self.assertEqual(0, code)
        invalid = self.root / "invalid.pdf"
        invalid.write_text("not a pdf", encoding="utf-8")
        code = manual_pdf_check.main(["check", "--book-root", str(self.book), "--pdf", str(invalid), "--output", str(output)])
        self.assertEqual(2, code)

    def test_problem_pages_render_and_cleanup_keeps_only_current(self):
        make_pdf(self.pdf, [{"texts": [("MDOC-MAP:en/Page.md:1:test", 20, 350, 10)]}, {}])
        report_dir = self.root / "report"
        work = self.root / "work"
        data = pdf_check_core.check_existing_pdf(self.book, self.pdf, report_dir, {"artifact_id": "pdf-en", "locale": "en"})

        def render_test_page(_pdf: Path, _page: int, output: Path, dpi: int = 144):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"test preview")

        # Resource lifecycle is independent of the optional Poppler runtime.
        # Actual pdftoppm integration is covered by release/E2E verification on
        # hosts where PDF Check dependencies are installed.
        with mock.patch.object(pdf_check_render, "render_page", side_effect=render_test_page):
            current = pdf_check_render.prepare_viewer_resources(self.pdf, data, work)
        self.assertTrue((current / "artifacts" / "pdf-en" / "check.pdf").exists())
        self.assertTrue(any((current / "problem-pages" / "pdf-en").glob("*.png")))
        stale = work / ".run-old"
        stale.mkdir(parents=True)
        pdf_check_render.cleanup_runs(work, maximum_age_seconds=0)
        self.assertFalse(stale.exists())
        pdf_check_render.finalize(work)
        self.assertFalse(work.exists())
        self.assertTrue((report_dir / "pdf-check.json").exists())

    def test_server_rejects_unknown_source_path(self):
        report = {"findings": [{"id": "PDF-0001", "source_locations": [{"file": "en/Page.md", "start_line": 1}]}]}
        allowed = pdf_check_server.resolve_source(report, "PDF-0001", self.book)
        self.assertEqual((self.book / "en" / "Page.md").resolve(), allowed[0])
        with self.assertRaises(PermissionError):
            pdf_check_server.resolve_source({"findings": [{"id": "X", "source_locations": [{"file": "../../secret.md", "start_line": 1}]}]}, "X", self.book)

    def test_editor_preferences_are_machine_local_and_argument_array_is_safe(self):
        exe = self.root / "Editor.exe"
        exe.write_bytes(b"MZ")
        preferences = self.root / "preferences.json"
        pdf_check_source_opener.save_preferences(preferences, {"mode": "explicit-exe", "executable": str(exe), "argument_style": "goto"})
        command = pdf_check_source_opener.editor_command(pdf_check_source_opener.load_preferences(preferences), self.book / "en" / "Page.md", 12)
        self.assertEqual([str(exe.resolve()), "--goto", f"{(self.book / 'en' / 'Page.md').resolve()}:12"], command)

    def test_windows_default_preference_uses_normal_default_open(self):
        preferences = {"schema_version": 2, "source_editor": {"mode": "windows-default"}}
        self.assertEqual("windows-default", pdf_check_source_opener.open_mode(preferences))
        self.assertEqual("not-configured", pdf_check_source_opener.open_mode({"schema_version": 2}))

    def test_report_is_reloaded_after_file_changes(self):
        report_path = self.root / "pdf-check.json"
        report_path.write_text(json.dumps({"generated_at": 1}), encoding="utf-8")
        self.assertEqual(1, pdf_check_server.load_report(report_path)["generated_at"])
        report_path.write_text(json.dumps({"generated_at": 2}), encoding="utf-8")
        self.assertEqual(2, pdf_check_server.load_report(report_path)["generated_at"])

    def test_verify_accepts_all_current_artifact_pdfs(self):
        make_pdf(self.pdf, [{"texts": [("Page", 20, 350, 10)]}])
        second = self.root / "second.pdf"
        make_pdf(second, [{"texts": [("Other", 20, 350, 10)]}])
        work = self.root / "work"
        for artifact_id, source in (("en", self.pdf), ("zh", second)):
            target = work / "current" / "artifacts" / artifact_id / "check.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        report = {"artifacts": [
            {"id": "en", "sha256": hashlib.sha256(self.pdf.read_bytes()).hexdigest()},
            {"id": "zh", "sha256": hashlib.sha256(second.read_bytes()).hexdigest()},
        ]}
        self.assertTrue(manual_pdf_check.verify_viewer_resources(report, work))
        (work / "current" / "artifacts" / "zh" / "check.pdf").write_bytes(b"changed")
        self.assertFalse(manual_pdf_check.verify_viewer_resources(report, work))

    def test_task_launcher_is_a_single_click_windows_entry_point(self):
        launcher = self.root / "open-pdf-check.cmd"
        manual_pdf_check.write_task_launcher(launcher, self.root / "workspace", "task-one")
        text = launcher.read_text(encoding="utf-8")
        self.assertIn("manual_pdf_check.py", text)
        self.assertIn("open --workspace", text)
        self.assertIn("--task \"task-one\"", text)

    def test_marker_mapping_and_task_scope_are_attached_to_findings(self):
        make_pdf(self.pdf, [{"texts": [("MDOC-MAP:en/Page.md:1:test", 20, 350, 10)]}])
        output = self.root / "report"
        data = pdf_check_core.check_existing_pdf(self.book, self.pdf, output, {"artifact_id": "pdf-en", "locale": "en", "task_files": {"en/Page.md"}})
        marker = next(item for item in data["findings"] if item["rule_id"] == "MDOC-PDF-MARKER-LEAK")
        self.assertEqual("en/Page.md", marker["source_locations"][0]["file"])
        self.assertEqual("task", marker["scope"])

    def test_existing_pdf_content_mapping_finds_markdown_heading(self):
        (self.book / "en" / "Page.md").write_text("## Example Page ##\n\nContent.\n", encoding="utf-8")
        make_pdf(self.pdf, [{"texts": [("Example Page", 20, 350, 12)]}])
        (self.book / "ja").mkdir()
        (self.book / "ja" / "Page.md").write_text("# Example Page\n", encoding="utf-8")
        mapping = pdf_check_mapping.content_map(self.book, self.pdf, "en")
        self.assertEqual("en/Page.md", mapping["1"][0]["file"])
        self.assertEqual(1, len(mapping["1"]))
        self.assertEqual(1, mapping["1"][0]["start_line"])

    def test_small_text_is_grouped_as_one_page_finding(self):
        make_pdf(self.pdf, [{"texts": [("many tiny characters", 20, 350, 5)]}])
        data = pdf_check_core.check_existing_pdf(self.book, self.pdf, self.root / "small")
        findings = [item for item in data["findings"] if item["rule_id"] == "MDOC-PDF-TEXT-TOO-SMALL"]
        self.assertEqual(1, len(findings))

    def test_standard_build_adapter_receives_safe_environment_and_output(self):
        launcher = self.root / "build.py"
        launcher.write_text("import os,shutil; shutil.copy2(os.environ['TEST_PDF'], os.environ['MDOC_OUTPUT_PATH'])", encoding="utf-8")
        make_pdf(self.pdf, [{"texts": [("Page", 20, 350, 10)]}])
        output = self.root / "built.pdf"
        result = pdf_check_build.run_adapter({"id": "pdf-en", "protocol": "pdf-check-v1", "command": [str(Path(__import__('sys').executable)), str(launcher)], "locale": "en"}, self.book, output, "check", {"TEST_PDF": str(self.pdf)})
        self.assertEqual("passed", result["status"])
        self.assertTrue(output.exists())

    def test_quality_gate_pdf_check_blocks_only_when_required(self):
        import manual_lint
        report = {"status": "completed", "counts": {"effective_errors": 1}}
        advisory = manual_lint.pdf_check_blocks({"mode": "advisory", "required_before_publish": False, "required_components": ["pdf_check"]}, report)
        required = manual_lint.pdf_check_blocks({"mode": "required", "required_before_publish": True, "required_components": ["pdf_check"]}, report)
        self.assertFalse(advisory)
        self.assertTrue(required)


if __name__ == "__main__":
    unittest.main()
