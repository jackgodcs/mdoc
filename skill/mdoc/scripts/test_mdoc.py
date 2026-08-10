from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("mdoc.py")


class MdocCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "manual-repo"
        self.repo.mkdir()
        (self.repo / "Book-A" / "zh").mkdir(parents=True)
        (self.repo / "Book-A" / "en").mkdir(parents=True)
        self.workspace = self.root / "manual-repo-manual-workspace"
        self.env = os.environ | {"LOCALAPPDATA": str(self.root / "LocalAppData")}

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args: str, expected: int = 0):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True,
            capture_output=True,
            encoding="utf-8",
            env=self.env,
            check=False,
        )
        self.assertEqual(expected, result.returncode, result.stderr or result.stdout)
        return result

    @staticmethod
    def module_available(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ModuleNotFoundError):
            return False

    def test_version_is_stable_product_version(self):
        result = self.run_cli("--version")
        self.assertEqual("mdoc 1.2.0-rc.1", result.stdout.strip())

    def test_human_output_uses_utf8_when_windows_locale_is_not_chinese(self):
        self.run_cli("setup", "--repository", str(self.repo), "--workspace", str(self.workspace), "--book", "Book-A")
        environment = self.env | {"PYTHONIOENCODING": "cp1252"}
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "status", "--workspace", str(self.workspace)],
            text=True, capture_output=True, env=environment, check=False, encoding="utf-8"
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("活动书册：Book-A", result.stdout)

    def test_setup_and_status_report_active_book(self):
        setup = self.run_cli(
            "setup", "--repository", str(self.repo), "--workspace", str(self.workspace),
            "--book", "Book-A", "--json"
        )
        payload = json.loads(setup.stdout)
        self.assertEqual("Book-A", payload["active_book"])
        self.assertEqual("Book-A", payload["operation_book"])
        self.assertTrue((self.workspace / "workspace.yaml").is_file())
        self.assertTrue((self.workspace / "workspace.local.yaml").is_file())
        self.assertTrue((self.workspace / "open-mdoc.cmd").is_file())
        launcher = (self.workspace / "open-mdoc.cmd").read_text(encoding="utf-8")
        self.assertIn("%LOCALAPPDATA%\\mdoc\\bin\\mdoc.cmd", launcher)
        self.assertNotIn(sys.executable, launcher)
        profile = (self.workspace / "product-profile.yaml").read_text(encoding="utf-8")
        self.assertIn("source: zh-CN", profile)
        self.assertIn("targets: [en]", profile)

        status = self.run_cli("status", "--workspace", str(self.workspace), "--json")
        payload = json.loads(status.stdout)
        self.assertEqual(2, payload["schema_version"])
        self.assertEqual("Book-A", payload["active_book"])
        self.assertEqual(str(self.repo.resolve()), payload["repository"])

    def test_setup_refuses_book_outside_repository(self):
        result = self.run_cli(
            "setup", "--repository", str(self.repo), "--workspace", str(self.workspace),
            "--book", "Missing", expected=2
        )
        self.assertIn("MDOC-WORKSPACE-BOOK-MISSING", result.stderr)

    def test_setup_refuses_to_overwrite_initialized_workspace(self):
        self.run_cli("setup", "--repository", str(self.repo), "--workspace", str(self.workspace), "--book", "Book-A")
        result = self.run_cli("setup", "--repository", str(self.repo), "--workspace", str(self.workspace), "--book", "Book-A", expected=2)
        self.assertIn("MDOC-WORKSPACE-ALREADY-INITIALIZED", result.stderr)

    def test_task_target_book_is_fixed_when_active_book_changes(self):
        (self.repo / "Book-B" / "zh").mkdir(parents=True)
        self.run_cli(
            "setup", "--repository", str(self.repo), "--workspace", str(self.workspace),
            "--book", "Book-A"
        )
        created = json.loads(self.run_cli(
            "new-task", "--workspace", str(self.workspace), "--id", "add-search",
            "--operation", "add_feature", "--title", "Search", "--json"
        ).stdout)
        self.assertEqual("Book-A", created["target_book"])
        self.run_cli("switch-book", "--workspace", str(self.workspace), "--book", "Book-B")
        resumed = json.loads(self.run_cli(
            "resume", "--workspace", str(self.workspace), "--task", "add-search", "--json"
        ).stdout)
        self.assertEqual("Book-B", resumed["active_book"])
        self.assertEqual("Book-A", resumed["operation_book"])
        self.assertEqual("Book-A", resumed["target_book"])

    def test_tasks_lists_created_tasks(self):
        self.run_cli(
            "setup", "--repository", str(self.repo), "--workspace", str(self.workspace),
            "--book", "Book-A"
        )
        self.run_cli("new-task", "--workspace", str(self.workspace), "--id", "module-one",
                     "--operation", "create_module", "--title", "Module One")
        payload = json.loads(self.run_cli("tasks", "--workspace", str(self.workspace), "--json").stdout)
        self.assertEqual(["module-one"], [item["id"] for item in payload["tasks"]])

    def test_doctor_reports_runtime_and_active_book(self):
        self.run_cli(
            "setup", "--repository", str(self.repo), "--workspace", str(self.workspace),
            "--book", "Book-A"
        )
        core_ready = self.module_available("ruamel.yaml") and self.module_available("jsonschema")
        payload = json.loads(self.run_cli("doctor", "--workspace", str(self.workspace), "--json", expected=0 if core_ready else 4).stdout)
        self.assertEqual("Book-A", payload["active_book"])
        self.assertEqual("available", payload["runtime"]["python"]["status"])
        self.assertIn("pdf_check", payload["capabilities"])
        self.assertIn("screenshot_assistant", payload["capabilities"])
        self.assertIn("venv", payload["runtime"]["python"]["features"])
        self.assertIn("tkinter", payload["runtime"]["python"]["features"])

    def test_doctor_json_runs_without_workspace(self):
        core_ready = self.module_available("ruamel.yaml") and self.module_available("jsonschema")
        payload = json.loads(self.run_cli("doctor", "--json", expected=0 if core_ready else 4).stdout)
        self.assertIsNone(payload["active_book"])
        self.assertEqual("unbound", payload["workspace_status"])
        self.assertIn(payload["status"], {"ready", "ready_with_warnings", "repair_required"})

    def test_doctor_uses_dedicated_exit_codes_for_workspace_migration_and_repair(self):
        self.workspace.mkdir()
        (self.workspace / "workspace.yaml").write_text(
            "schema_version: 1\nmanual:\n  active_book: Book-A\n", encoding="utf-8"
        )
        (self.workspace / "workspace.local.yaml").write_text(
            f"schema_version: 1\nlocal:\n  manual_repository: {json.dumps(str(self.repo))}\n",
            encoding="utf-8",
        )
        migration = self.run_cli("doctor", "--workspace", str(self.workspace), "--json", expected=3)
        self.assertEqual("migration_required", json.loads(migration.stdout)["status"])

        state = self.root / "LocalAppData" / "mdoc" / "state" / "installed-runtime.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"runtime_python": str(self.root / "missing-python.exe")}), encoding="utf-8")
        repair = self.run_cli("doctor", "--json", expected=4)
        self.assertEqual("repair_required", json.loads(repair.stdout)["status"])

    def test_check_delegates_to_quality_gate_for_fixed_task_book(self):
        (self.repo / "Book-A" / "zh" / "Page.md").write_text("# Page\n", encoding="utf-8")
        self.run_cli(
            "setup", "--repository", str(self.repo), "--workspace", str(self.workspace),
            "--book", "Book-A"
        )
        self.run_cli("new-task", "--workspace", str(self.workspace), "--id", "check-book",
                     "--operation", "update_feature", "--title", "Check")
        result = self.run_cli("check", "--workspace", str(self.workspace), "--task", "check-book", "--profile", "quick")
        self.assertIn("活动书册：Book-A", result.stdout)
        self.assertIn("COMPLETE:", result.stdout)

    def test_diagnose_keeps_only_latest_sanitized_record(self):
        self.run_cli("setup", "--repository", str(self.repo), "--workspace", str(self.workspace), "--book", "Book-A")
        payload = json.loads(self.run_cli("diagnose", "--workspace", str(self.workspace), "--json").stdout)
        report = Path(payload["diagnostic_report"])
        self.assertTrue(report.is_file())
        source = report.read_text(encoding="utf-8")
        self.assertNotIn(str(self.repo), source)
        self.assertEqual(["latest.json"], [item.name for item in report.parent.iterdir()])

    def test_uninstall_requires_explicit_confirmation(self):
        target = self.root / "skills" / "mdoc"
        target.mkdir(parents=True)
        result = self.run_cli("uninstall", "--installation", str(target), expected=2)
        self.assertIn("MDOC-UNINSTALL-CONFIRMATION-REQUIRED", result.stderr)
        self.run_cli("uninstall", "--installation", str(target), "--confirm")
        self.assertFalse(target.exists())

    def test_uninstall_removes_only_managed_runtime_unless_keep_tools(self):
        installation = self.root / "skills" / "mdoc"
        installation.mkdir(parents=True)
        runtime_root = self.root / "LocalAppData" / "mdoc"
        (runtime_root / "tools" / "poppler").mkdir(parents=True)
        (runtime_root / "state").mkdir(parents=True)
        (runtime_root / "state" / "installed-tools.json").write_text(json.dumps({
            "schema_version": 1, "tools": [{"id": "poppler", "ownership": "managed-by-mdoc"}]
        }), encoding="utf-8")
        self.run_cli("uninstall", "--installation", str(installation), "--runtime-root", str(runtime_root), "--keep-tools", "--confirm")
        self.assertTrue((runtime_root / "tools").exists())
        installation.mkdir(parents=True)
        self.run_cli("uninstall", "--installation", str(installation), "--runtime-root", str(runtime_root), "--confirm")
        self.assertFalse(runtime_root.exists())

    def test_help_exposes_all_stable_commands(self):
        result = self.run_cli("--help")
        for command in ("setup", "status", "new-task", "tasks", "resume", "switch-book",
                        "configure", "bind-local", "doctor", "check", "pdf-check",
                        "screenshots", "diagnose", "update", "uninstall"):
            self.assertIn(command, result.stdout)
        self.assertIn("workspace", result.stdout)

    def test_workspace_inspect_reports_existing_schema_v2_without_mutation(self):
        self.run_cli("setup", "--repository", str(self.repo), "--workspace", str(self.workspace), "--book", "Book-A")
        before = (self.workspace / "workspace.yaml").read_bytes()
        payload = json.loads(self.run_cli("workspace", "inspect", "--workspace", str(self.workspace), "--json").stdout)
        self.assertEqual(2, payload["detected_schema_version"])
        self.assertEqual("current", payload["migration_status"])
        self.assertEqual("Book-A", payload["active_book"])
        self.assertEqual(before, (self.workspace / "workspace.yaml").read_bytes())

    def test_bind_local_changes_only_machine_binding(self):
        other = self.root / "other-repo"
        (other / "Book-A").mkdir(parents=True)
        self.run_cli("setup", "--repository", str(self.repo), "--workspace", str(self.workspace), "--book", "Book-A")
        payload = json.loads(self.run_cli("bind-local", "--workspace", str(self.workspace), "--repository", str(other), "--json").stdout)
        self.assertEqual(str(other.resolve()), payload["repository"])
        self.assertIn(str(other.resolve()), (self.workspace / "workspace.local.yaml").read_text(encoding="utf-8"))

    def test_update_rejects_zip_path_traversal(self):
        import hashlib
        import zipfile
        package = self.root / "bad.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("../escape.txt", "bad")
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"version": "1.0.0", "sha256": hashlib.sha256(package.read_bytes()).hexdigest()}), encoding="utf-8")
        installation = self.root / "installed" / "mdoc"
        result = self.run_cli("update", "--package", str(package), "--manifest", str(manifest), "--installation", str(installation), expected=2)
        self.assertIn("MDOC-UPDATE-PACKAGE-UNSAFE", result.stderr)
        self.assertFalse((self.root / "escape.txt").exists())

    def test_update_uses_embedded_manifest_plan_then_apply(self):
        import hashlib
        import zipfile
        package = self.root / "release.zip"
        files = {"skill/mdoc/SKILL.md": b"# mdoc\n", "skill/mdoc/scripts/mdoc.py": b"print('new')\n"}
        manifest = {"schema_version": 2, "product": "mdoc", "platform": "windows-x86_64", "version": "1.2.0-rc.1", "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()]}
        with zipfile.ZipFile(package, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr("PACKAGE-MANIFEST.json", json.dumps(manifest))
        installation = self.root / "installed" / "mdoc"
        installation.mkdir(parents=True)
        (installation / "old.txt").write_text("old", encoding="utf-8")
        runtime = self.root / "LocalAppData" / "mdoc"
        state = runtime / "state" / "installed-runtime.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({
            "toolchain_version": None, "python_contract": None, "profile": None,
            "requirements_sha256": None, "capability_probe": "ready",
            "python_source": "system-or-user"
        }), encoding="utf-8")
        planned = json.loads(self.run_cli("update", "--package", str(package), "--installation", str(installation), "--runtime-root", str(runtime), "--plan", "--json").stdout)
        self.assertEqual("planned", planned["status"])
        applied = json.loads(self.run_cli("update", "--runtime-root", str(runtime), "--apply", "--confirm", "--json").stdout)
        self.assertEqual("updated", applied["status"])
        self.assertTrue((installation / "SKILL.md").is_file())

    def test_runtime_cancel_requires_confirmation(self):
        runtime = self.root / "LocalAppData" / "mdoc"
        result = self.run_cli("runtime", "cancel", "--runtime-root", str(runtime), expected=2)
        self.assertIn("MDOC-CONFIRMATION-REQUIRED", result.stderr)


if __name__ == "__main__":
    unittest.main()
