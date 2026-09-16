from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name("mdoc_uninstall.py")
SPEC = importlib.util.spec_from_file_location("mdoc_uninstall", MODULE_PATH)
uninstall = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(uninstall)


class UninstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.installation = self.root / "skills" / "mdoc"
        self.start_menu = self.root / "appdata" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "mdoc"
        self.runtime.mkdir(); self.installation.mkdir(parents=True); self.start_menu.mkdir(parents=True)
        (self.installation / "SKILL.md").write_text("# mdoc\n", encoding="utf-8")
        self.state = {"schema_version": 1, "installation_id": "test", "installation": str(self.installation), "runtime_root": str(self.runtime), "path_entry": str(self.runtime / "bin"), "path_added_by_mdoc": True, "start_menu": str(self.start_menu), "uninstall_registry": r"Software\mdoc-tests\test", "python_ownership": "external", "python_root": str(self.runtime / "python"), "python_installer": None}

    def tearDown(self):
        self.temp.cleanup()

    def test_validate_accepts_owned_paths_and_external_python(self):
        with mock.patch.dict(os.environ, {"APPDATA": str(self.root / "appdata"), "LOCALAPPDATA": str(self.root / "local")}, clear=False):
            uninstall.validate(self.state, self.runtime.resolve())

    def test_validate_rejects_runtime_mismatch(self):
        state = dict(self.state); state["runtime_root"] = str(self.root / "other")
        with self.assertRaisesRegex(uninstall.UninstallError, "MDOC-UNINSTALL-PATH-UNSAFE"):
            uninstall.validate(state, self.runtime.resolve())

    def test_validate_rejects_unmarked_installation(self):
        (self.installation / "SKILL.md").unlink()
        with self.assertRaisesRegex(uninstall.UninstallError, "MDOC-UNINSTALL-INSTALLATION-UNTRUSTED"):
            uninstall.validate(self.state, self.runtime.resolve())

    def test_managed_python_must_be_bounded_by_runtime(self):
        state = dict(self.state); state.update({"python_ownership": "managed-by-mdoc", "python_root": str(self.root / "external-python"), "python_installer": str(self.root / "installer.exe")})
        with mock.patch.dict(os.environ, {"APPDATA": str(self.root / "appdata"), "LOCALAPPDATA": str(self.root / "local")}, clear=False):
            with self.assertRaisesRegex(uninstall.UninstallError, "MDOC-UNINSTALL-PYTHON-UNSAFE"):
                uninstall.validate(state, self.runtime.resolve())

    def test_recovery_requires_update_record_and_skill_marker(self):
        state_root = self.runtime / "state"; (state_root / "records").mkdir(parents=True)
        (state_root / "installed-runtime.json").write_text(json.dumps({"path_entry": str(self.runtime / "bin")}), encoding="utf-8")
        with self.assertRaisesRegex(uninstall.UninstallError, "MDOC-UNINSTALL-RECOVERY-UNTRUSTED"):
            uninstall.load_state(self.runtime, self.installation, True)
        (state_root / "records/latest-update.json").write_text(json.dumps({"installation": str(self.installation)}), encoding="utf-8")
        self.assertEqual("recovery", uninstall.load_state(self.runtime, self.installation, True)["installation_id"])

    def test_owned_processes_excludes_current_process_ancestors(self):
        rows = [{"ProcessId": os.getpid(), "ParentProcessId": 10, "ExecutablePath": str(self.runtime / "runtime/python.exe"), "CommandLine": ""}, {"ProcessId": 10, "ParentProcessId": 0, "ExecutablePath": "powershell.exe", "CommandLine": str(self.runtime)}, {"ProcessId": 20, "ParentProcessId": 0, "ExecutablePath": str(self.runtime / "runtime/python.exe"), "CommandLine": "server"}, {"ProcessId": 30, "ParentProcessId": 0, "ExecutablePath": "python.exe", "CommandLine": "unrelated"}]
        with mock.patch.object(uninstall, "process_rows", return_value=rows):
            self.assertEqual([20], [row["ProcessId"] for row in uninstall.owned_processes(self.state)])

    def test_cleanup_script_uses_crlf(self):
        script = self.root / "cleanup.cmd"; uninstall.write_cleanup_script(script, [str(self.runtime)], True)
        content = script.read_bytes()
        self.assertIn(b"\r\n", content); self.assertNotIn(b"\r\r\n", content)

    def test_partial_uninstall_preserves_managed_python_only(self):
        for name in ("python", "installers", "runtime", "toolchain", "state"):
            (self.runtime / name).mkdir()
        targets = {Path(path).name for path in uninstall.runtime_cleanup_targets(self.runtime, True)}
        self.assertEqual({"runtime", "toolchain", "state"}, targets)


if __name__ == "__main__":
    unittest.main()
