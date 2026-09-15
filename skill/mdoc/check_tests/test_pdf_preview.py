from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ruamel.yaml import YAML

from mdoc_check.model import digest
from mdoc_check.pdf_preview import PdfPreviewManager, _prune_empty, _result_root, configuration, target_context


class PdfPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "manual"
        locale = self.workspace / "Guide" / "en"
        (locale / "Main").mkdir(parents=True)
        (locale / "Summary.md").write_text("# Summary\n\n* [Chapter](Main/Chapter.md)\n  * [Page](Main/Page.md)\n  * [Repeated](Main/Page.md)\n", encoding="utf-8")
        (locale / "Main" / "Chapter.md").write_text("# Chapter\n", encoding="utf-8")
        (locale / "Main" / "Page.md").write_text("# Page\n", encoding="utf-8")
        (locale / "Main" / "Loose.md").write_text("# Loose\n", encoding="utf-8")
        (self.workspace / ".mdoc").mkdir()
        config = {"schema_version": 1, "workspace": {"id": f"pdf-test-{Path(self.temp.name).name}"}, "books": {"guide": {"root": "Guide", "source_locale": "en", "locales": {"en": {"root": "en", "language": "en"}}, "content_root": "Main", "assets_root": "images", "navigation": {"summary": "Summary.md"}}}}
        stream = __import__("io").StringIO(); YAML().dump(config, stream)
        (self.workspace / ".mdoc" / "workspace.yaml").write_text(stream.getvalue(), encoding="utf-8")
        self.report = {"scope": {"kind": "book", "book": "guide", "locale": "en"}}

    def tearDown(self) -> None:
        shutil.rmtree(_result_root(self.workspace).parent, ignore_errors=True)
        self.temp.cleanup()

    def test_configuration_defaults_and_workspace_override(self) -> None:
        self.assertEqual({"enabled": True, "retention_days": 7, "max_bytes": 1073741824, "orphan_grace_minutes": 60}, configuration(self.workspace))
        (self.workspace / ".mdoc" / "check.yaml").write_text("pdf_preview:\n  enabled: false\nretention:\n  pdf_preview_days: 3\n  pdf_preview_max_bytes: 1234\n", encoding="utf-8")
        self.assertEqual({"enabled": False, "retention_days": 3, "max_bytes": 1234, "orphan_grace_minutes": 60}, configuration(self.workspace))

    def test_summary_occurrences_and_section_root_are_resolved(self) -> None:
        context = target_context(self.workspace, self.report, "en/Main/Page.md")
        self.assertEqual(2, len(context["occurrences"]))
        self.assertEqual({"Main/Chapter.md"}, {item["section_target"] for item in context["occurrences"]})
        loose = target_context(self.workspace, self.report, "en/Main/Loose.md")
        self.assertTrue(loose["page_available"]); self.assertTrue(loose["standalone"]); self.assertIn("独立文件", loose["reason"])

    def test_doctor_parses_json_and_wraps_cmd_on_windows(self) -> None:
        manager = PdfPreviewManager(self.workspace)
        completed = __import__("subprocess").CompletedProcess([], 0, json.dumps({"status": "passed"}), "")
        with patch("mdoc_check.pdf_preview.shutil.which", return_value=r"C:\mdoc.cmd"), patch("mdoc_check.pdf_preview.subprocess.run", return_value=completed) as run:
            result = manager.doctor(True)
        self.assertEqual("passed", result["status"]); self.assertIn("/c", run.call_args.args[0])

    def test_single_active_job_is_rejected(self) -> None:
        manager = PdfPreviewManager(self.workspace); manager.doctor_cache = {"status": "passed", "command": "mdoc.exe"}; manager.job = {"status": "honkit"}
        with self.assertRaisesRegex(ValueError, "已有 PDF"):
            manager.start("report", self.report, "en/Main/Page.md", "page", "1", {})

    def test_success_replaces_preview_and_failure_keeps_it(self) -> None:
        manager = PdfPreviewManager(self.workspace); manager.doctor_cache = {"status": "passed", "command": "mdoc.exe"}
        def successful(command, **_kwargs):
            output = Path(command[command.index("--output") + 1]) if "--output" in command else None
            if output: output.write_bytes(b"%PDF-1.4\nnew")
            return __import__("subprocess").CompletedProcess(command, 0, json.dumps({"status": "passed", "results": [{"status": "passed", "findings": []}]}), "")
        context = {"enabled": True, "standalone": False, "occurrences": [{"id": "1", "target": "Main/Page.md", "section_target": "Main/Chapter.md", "breadcrumb": "Chapter / Page"}]}
        with patch("mdoc_check.pdf_preview.target_context", return_value=context), patch("mdoc_check.pdf_preview.subprocess.run", side_effect=successful):
            job = manager.start("report", self.report, "en/Main/Page.md", "page", "1", {"path": "en/Main/Page.md"})
            self._wait(manager, job["id"])
        self.assertEqual("completed", manager.status()["status"], manager.status())
        final = _result_root(self.workspace) / "files" / digest("en/Main/Page.md")[:16] / "preview.pdf"
        self.assertEqual(b"%PDF-1.4\nnew", final.read_bytes())
        old = final.read_bytes()
        with patch("mdoc_check.pdf_preview.target_context", return_value=context), patch("mdoc_check.pdf_preview.subprocess.run", return_value=__import__("subprocess").CompletedProcess([], 1, "", "failed")):
            job = manager.start("report", self.report, "en/Main/Page.md", "page", "1", {})
            self._wait(manager, job["id"]); self.assertEqual("failed", manager.status()["status"])
        self.assertEqual(old, final.read_bytes())

    def test_workspace_page_passes_exact_summary_line(self) -> None:
        manager = PdfPreviewManager(self.workspace); manager.doctor_cache = {"status": "passed", "command": "mdoc.exe"}; commands = []
        context = {"enabled": True, "standalone": False, "occurrences": [{"id": "1", "target": "Main/Page.md", "summary_line": 5, "section_target": "Main/Chapter.md", "section_summary_line": 3, "breadcrumb": "Chapter / Page"}]}
        def successful(command, **_kwargs):
            commands.append(command); output = Path(command[command.index("--output") + 1]); output.write_bytes(b"%PDF")
            return __import__("subprocess").CompletedProcess(command, 0, json.dumps({"status": "passed", "results": [{"status": "passed"}]}), "")
        with patch("mdoc_check.pdf_preview.target_context", return_value=context), patch("mdoc_check.pdf_preview.subprocess.run", side_effect=successful):
            job = manager.start("report", self.report, "en/Main/Page.md", "page", "1", {}); self._wait(manager, job["id"])
        self.assertEqual("5", commands[0][commands[0].index("--summary-line") + 1]); self.assertIn("--workspace", commands[0])

    def test_unreferenced_page_uses_standalone_file_and_workspace_pdf_defaults(self) -> None:
        manager = PdfPreviewManager(self.workspace); manager.doctor_cache = {"status": "passed", "command": "mdoc.exe"}; commands = []
        config = YAML(typ="safe").load((self.workspace / ".mdoc" / "workspace.yaml").read_text(encoding="utf-8")); config["pdf"] = {"defaults": {"paper_size": "a4", "margins_pt": {"left": 67, "right": 67, "top": 36, "bottom": 36}}}
        stream = __import__("io").StringIO(); YAML().dump(config, stream); (self.workspace / ".mdoc" / "workspace.yaml").write_text(stream.getvalue(), encoding="utf-8")
        def successful(command, **_kwargs):
            commands.append(command); output = Path(command[command.index("--output") + 1]); output.write_bytes(b"%PDF")
            return __import__("subprocess").CompletedProcess(command, 0, json.dumps({"status": "passed"}), "")
        standalone = target_context(self.workspace, self.report, "en/Main/Loose.md")
        with patch("mdoc_check.pdf_preview.target_context", return_value=standalone), patch("mdoc_check.pdf_preview.subprocess.run", side_effect=successful):
            job = manager.start("report", self.report, "en/Main/Loose.md", "page", None, {}); self._wait(manager, job["id"])
        command = commands[0]; self.assertIn("--file", command); self.assertNotIn("--workspace", command); pdf_config = Path(command[command.index("--pdf-config") + 1]); self.assertFalse(pdf_config.exists())
        result = manager.context("report", self.report, "en/Main/Loose.md")["result"]; self.assertEqual("file", result["scope"])

    def test_stale_cleanup_and_safe_keys(self) -> None:
        manager = PdfPreviewManager(self.workspace); root = _result_root(self.workspace) / "files" / digest("en/Main/Page.md")[:16]; root.mkdir(parents=True); (root / "preview.pdf").write_bytes(b"pdf")
        meta = root / "result.json"; meta.write_text(json.dumps({"pdf": str(root / "preview.pdf"), "stale": False}), encoding="utf-8")
        manager.mark_stale("en/Main/Page.md"); self.assertTrue(json.loads(meta.read_text(encoding="utf-8"))["stale"]); self.assertEqual(root / "preview.pdf", manager.pdf_path("file", root.name)); self.assertIn("last_accessed_at", json.loads(meta.read_text(encoding="utf-8")))
        with self.assertRaises(ValueError): manager.pdf_path("file", "../escape")
        (self.workspace / ".mdoc" / "check.yaml").write_text("retention:\n  pdf_preview_days: 0\n  pdf_preview_max_bytes: 0\n", encoding="utf-8"); manager.cleanup(); self.assertFalse(root.exists())

    def test_cleanup_removes_orphan_pending_and_empty_preview_directories(self) -> None:
        root = _result_root(self.workspace); pending = root / "files" / ("a" * 16) / "pending-orphan"; pending.mkdir(parents=True); (pending / "work.tmp").write_bytes(b"x"); old = time.time() - 3700; os.utime(pending, (old, old))
        PdfPreviewManager(self.workspace)
        self.assertFalse(root.exists())

    def test_cleanup_keeps_completed_preview_and_never_removes_reports_boundary(self) -> None:
        root = _result_root(self.workspace); result = root / "files" / ("b" * 16); result.mkdir(parents=True); pdf = result / "preview.pdf"; pdf.write_bytes(b"%PDF")
        (result / "result.json").write_text(json.dumps({"pdf": str(pdf)}), encoding="utf-8")
        PdfPreviewManager(self.workspace)
        self.assertTrue(pdf.is_file())
        cache = self.workspace / ".mdoc" / "cache"; outside = Path(self.temp.name) / "outside"; outside.mkdir()
        _prune_empty(outside)
        self.assertFalse(outside.exists()); self.assertTrue(cache.is_dir())

    def test_failed_build_without_previous_preview_leaves_no_result_directories(self) -> None:
        manager = PdfPreviewManager(self.workspace); manager.doctor_cache = {"status": "passed", "command": "mdoc.exe"}
        context = {"enabled": True, "standalone": False, "occurrences": [{"id": "1", "target": "Main/Page.md", "section_target": "Main/Chapter.md", "breadcrumb": "Chapter / Page"}]}
        with patch("mdoc_check.pdf_preview.target_context", return_value=context), patch("mdoc_check.pdf_preview.subprocess.run", return_value=__import__("subprocess").CompletedProcess([], 1, "", "failed")):
            job = manager.start("report", self.report, "en/Main/Page.md", "page", "1", {}); self._wait(manager, job["id"])
        self.assertEqual("failed", manager.status()["status"]); self.assertFalse(_result_root(self.workspace).exists())

    def test_task_reports_use_standalone_staging_preview(self) -> None:
        task = {"context": "task", "scope": {"kind": "task", "task": "editing", "book": "guide"}}
        context = target_context(self.workspace, task, "en/Main/Page.md")
        self.assertTrue(context["enabled"]); self.assertTrue(context["standalone"]); self.assertFalse(context["section_available"])

    @staticmethod
    def _wait(manager: PdfPreviewManager, job_id: str) -> None:
        for _ in range(100):
            if manager.status().get("id") == job_id and manager.status()["status"] in {"completed", "failed"}: return
            time.sleep(.02)
        raise AssertionError("PDF job did not finish")


if __name__ == "__main__":
    unittest.main()
