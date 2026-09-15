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

from mdoc_check.maintenance import cleanup, configuration, report_root, touch_access
from mdoc_check.cli import main


class MaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.workspace = Path(self.temp.name) / "manual"; (self.workspace / ".mdoc").mkdir(parents=True)
        config = {"schema_version": 1, "workspace": {"id": f"maintenance-test-{Path(self.temp.name).name}"}, "books": {}}
        stream = __import__("io").StringIO(); YAML().dump(config, stream); (self.workspace / ".mdoc" / "workspace.yaml").write_text(stream.getvalue(), encoding="utf-8")
        self.root = report_root(self.workspace); self.now = time.time()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True); self.temp.cleanup()

    def configure(self, text: str) -> None:
        (self.workspace / ".mdoc" / "check.yaml").write_text(text, encoding="utf-8")

    def age(self, path: Path, seconds: int = 7200) -> None:
        stamp = self.now - seconds; os.utime(path, (stamp, stamp))

    def test_defaults_and_validation(self) -> None:
        value = configuration(self.workspace); self.assertEqual(60, value["orphan_grace_minutes"]); self.assertEqual(1073741824, value["pdf_preview_max_bytes"])
        self.configure("retention:\n  orphan_grace_minutes: -1\n")
        with self.assertRaises(ValueError): configuration(self.workspace)

    def test_pdf_build_retention_comes_from_workspace_configuration(self) -> None:
        value = YAML(typ="safe").load((self.workspace / ".mdoc" / "workspace.yaml").read_text(encoding="utf-8")); value["pdf"] = {"retention": {"failed_work_days": 3, "failed_work_max_bytes": 123}}
        stream = __import__("io").StringIO(); YAML().dump(value, stream); (self.workspace / ".mdoc" / "workspace.yaml").write_text(stream.getvalue(), encoding="utf-8")
        config = configuration(self.workspace); self.assertEqual(3, config["pdf_build_failed_days"]); self.assertEqual(123, config["pdf_build_max_bytes"])

    def test_orphan_generation_and_temporary_files_are_removed_after_grace(self) -> None:
        report = self.root / "book" / "guide" / "en" / "book"; current = report / "generations" / "current"; orphan = report / "generations" / "orphan"; current.mkdir(parents=True); orphan.mkdir(); (orphan / "data").write_bytes(b"x"); temporary = report / "latest.json.tmp"; temporary.write_text("x")
        (report / "latest.json").write_text(json.dumps({"generation": "current", "context": "book", "scope": {"kind": "book"}, "created_at": int(self.now)}), encoding="utf-8")
        self.age(orphan); self.age(temporary); result = cleanup(self.workspace, automatic=False, now=self.now)
        self.assertTrue(current.is_dir()); self.assertFalse(orphan.exists()); self.assertFalse(temporary.exists()); self.assertGreaterEqual(result["files"], 2)

    def test_tmp_prefixed_report_directory_is_not_treated_as_temporary(self) -> None:
        report = self.root / "tmp-valid-report"; report.mkdir(parents=True); (report / "content.json").write_text("{}", encoding="utf-8"); self.age(report)
        cleanup(self.workspace, automatic=False, now=self.now); self.assertTrue(report.is_dir())

    def test_active_orphan_generation_is_preserved(self) -> None:
        report = self.root / "book" / "guide" / "en" / "book"; current = report / "generations" / "current"; orphan = report / "generations" / "orphan"; current.mkdir(parents=True); orphan.mkdir(); (orphan / ".active.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        (report / "latest.json").write_text(json.dumps({"generation": "current", "context": "book", "scope": {"kind": "book"}, "created_at": int(self.now)}), encoding="utf-8"); self.age(orphan)
        cleanup(self.workspace, automatic=False, now=self.now); self.assertTrue(orphan.is_dir())

    def test_active_check_skips_report_tree_cleanup(self) -> None:
        temporary = self.root / "old.tmp"; temporary.parent.mkdir(parents=True); temporary.write_text("x"); self.age(temporary); (self.root / ".check.active.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        cleanup(self.workspace, automatic=False, now=self.now); self.assertTrue(temporary.is_file())

    def test_old_protocol_files_do_not_remove_nested_current_reports(self) -> None:
        parent = self.root / "book"; parent.mkdir(parents=True); legacy = parent / "latest.json"; legacy.write_text(json.dumps({"status": "passed"}), encoding="utf-8"); historical = parent / "123.json"; historical.write_text("{}", encoding="utf-8"); findings = parent / "123.findings.jsonl"; findings.write_text("{}\n", encoding="utf-8")
        nested = parent / "guide" / "en" / "book"; generation = nested / "generations" / "current"; generation.mkdir(parents=True); (nested / "latest.json").write_text(json.dumps({"generation": "current", "context": "book", "scope": {"kind": "book"}, "created_at": int(self.now)}), encoding="utf-8")
        self.age(legacy); self.age(historical); cleanup(self.workspace, automatic=False, now=self.now)
        self.assertFalse(legacy.exists()); self.assertFalse(historical.exists()); self.assertFalse(findings.exists()); self.assertTrue(generation.is_dir())

    def test_inactive_page_is_removed_but_book_report_is_kept(self) -> None:
        for kind in ("page", "book"):
            report = self.root / kind; generation = report / "generations" / "current"; generation.mkdir(parents=True); latest = report / "latest.json"; latest.write_text(json.dumps({"generation": "current", "context": "book", "scope": {"kind": kind}, "created_at": int(self.now - 40 * 86400)}), encoding="utf-8"); self.age(latest, 40 * 86400)
        cleanup(self.workspace, automatic=False, now=self.now); self.assertFalse((self.root / "page").exists()); self.assertTrue((self.root / "book").exists())

    def test_pdf_preview_capacity_removes_oldest_result(self) -> None:
        self.configure("retention:\n  pdf_preview_max_bytes: 15\n  pdf_preview_days: 0\n")
        for index, key in enumerate(("old", "new")):
            directory = self.workspace / ".mdoc" / "cache" / "pdf-preview" / "files" / key; directory.mkdir(parents=True); pdf = directory / "preview.pdf"; pdf.write_bytes(b"x" * 10); meta = directory / "result.json"; meta.write_text(json.dumps({"pdf": str(pdf), "generated_at": int(self.now - (100 if index == 0 else 10))}), encoding="utf-8"); self.age(meta, 100 if index == 0 else 10)
        preview = self.workspace / ".mdoc" / "cache" / "pdf-preview"; cleanup(self.workspace, automatic=False, now=self.now); self.assertFalse((preview / "files" / "old").exists()); self.assertTrue((preview / "files" / "new").exists())

    def test_pdf_build_cache_obeys_age_and_capacity_and_active_lock(self) -> None:
        self.configure("retention:\n  pdf_build_failed_days: 7\n  pdf_build_max_bytes: 15\n")
        builds = self.workspace / ".mdoc" / "cache" / "pdf-builds"
        for name, age in (("expired", 8 * 86400), ("old", 100), ("new", 10)):
            path = builds / name; path.mkdir(parents=True); (path / "data").write_bytes(b"x" * 10); self.age(path, age)
        cleanup(self.workspace, automatic=False, now=self.now); self.assertFalse((builds / "expired").exists()); self.assertFalse((builds / "old").exists()); self.assertTrue((builds / "new").exists())
        locked = builds / "locked"; locked.mkdir(); (locked / "data").write_bytes(b"x" * 20); lock = self.workspace / ".mdoc" / "locks" / "pdf-build.lock"; lock.parent.mkdir(); lock.write_text(str(os.getpid()), encoding="ascii")
        cleanup(self.workspace, automatic=False, now=self.now + 1); self.assertTrue(locked.is_dir())

    def test_successful_kept_pdf_work_is_not_treated_as_failed_cache(self) -> None:
        self.configure("retention:\n  pdf_build_failed_days: 1\n  pdf_build_max_bytes: 1\n")
        work = self.workspace / ".mdoc" / "cache" / "pdf-builds" / "kept-success"; work.mkdir(parents=True); (work / "data").write_bytes(b"x" * 10); self.age(work, 8 * 86400)
        report = self.workspace / ".mdoc" / "artifacts" / "pdf" / "book" / "en" / "manual.build.json"; report.parent.mkdir(parents=True); report.write_text(json.dumps({"status": "passed", "work": str(work)}), encoding="utf-8")
        cleanup(self.workspace, automatic=False, now=self.now); self.assertTrue(work.is_dir())

    def test_throttling_access_and_manual_cleanup(self) -> None:
        first = cleanup(self.workspace, automatic=True, now=self.now); second = cleanup(self.workspace, automatic=True, now=self.now + 10); self.assertEqual("completed", first["status"]); self.assertEqual("throttled", second["reason"])
        report = self.root / "report"; report.mkdir(); touch_access(report, self.now); before = (report / "access.json").read_text(); touch_access(report, self.now + 60); self.assertEqual(before, (report / "access.json").read_text())

    def test_clean_command_returns_stable_json_summary(self) -> None:
        with patch("sys.argv", ["mdoc-check-prototype", "clean", "--workspace", str(self.workspace), "--json"]), patch("builtins.print") as printed:
            self.assertEqual(0, main())
        value = json.loads(printed.call_args.args[0]); self.assertEqual("completed", value["status"]); self.assertIn("bytes", value); self.assertIn("warnings", value)


if __name__ == "__main__": unittest.main()
