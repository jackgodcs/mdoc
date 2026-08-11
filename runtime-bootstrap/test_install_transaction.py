from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("mdoc_install_transaction.py")
SPEC = importlib.util.spec_from_file_location("mdoc_install_transaction", MODULE_PATH)
transaction = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(transaction)


class InstallTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.installation = self.root / "skills" / "mdoc"
        self.package = self.root / "mdoc.zip"
        files = {"skill/mdoc/SKILL.md": b"# mdoc\n", "skill/mdoc/scripts/mdoc.py": b"print('new')\n"}
        manifest = {"schema_version": 2, "product": "mdoc", "platform": "windows-x86_64", "version": "1.2.0", "runtime_contract": {"toolchain_version": "2026.08.1", "python": ">=3.12.0,<3.13.0", "profile": "Full", "requirements_sha256": "new-lock"}, "files": [{"path": name, "sha256": __import__("hashlib").sha256(data).hexdigest()} for name, data in files.items()]}
        with zipfile.ZipFile(self.package, "w") as archive:
            for name, data in files.items(): archive.writestr(name, data)
            archive.writestr("PACKAGE-MANIFEST.json", json.dumps(manifest))

    def tearDown(self): self.temp.cleanup()

    def test_plan_and_apply_replace_installation_and_remove_detailed_plan(self):
        self.prepare_compatible_runtime()
        self.installation.mkdir(parents=True)
        (self.installation / "old.txt").write_text("old", encoding="utf-8")
        plan = transaction.create_plan(self.package, self.installation, self.runtime, "update")
        self.assertEqual("1.2.0", plan["version"])
        result = transaction.apply_plan(self.runtime, True)
        self.assertEqual("updated", result["status"])
        self.assertTrue((self.installation / "SKILL.md").is_file())
        self.assertFalse((self.installation / "old.txt").exists())
        self.assertFalse(transaction.plan_path(self.runtime).exists())
        self.assertTrue((self.runtime / "state/records/latest-update.json").is_file())

    def test_runtime_rebuild_is_decided_from_installed_contract(self):
        state = self.runtime / "state/installed-runtime.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"toolchain_version": "2026.08.1", "python_contract": ">=3.12.0,<3.13.0", "profile": "Full", "requirements_sha256": "new-lock", "capability_probe": "ready", "python_source": "system-or-user"}), encoding="utf-8")
        plan = transaction.create_plan(self.package, self.installation, self.runtime, "update")
        self.assertFalse(plan["runtime_rebuild"])
        self.assertEqual([], plan["runtime_rebuild_reasons"])
        state.write_text(json.dumps({"toolchain_version": "2026.07.1"}), encoding="utf-8")
        plan = transaction.create_plan(self.package, self.installation, self.runtime, "update")
        self.assertTrue(plan["runtime_rebuild"])
        self.assertIn("toolchain_version_changed", plan["runtime_rebuild_reasons"])

    def test_changed_package_invalidates_plan(self):
        self.prepare_compatible_runtime()
        transaction.create_plan(self.package, self.installation, self.runtime, "update")
        self.package.write_bytes(self.package.read_bytes() + b"changed")
        with self.assertRaisesRegex(transaction.TransactionError, "MDOC-PLAN-STALE"):
            transaction.apply_plan(self.runtime, True)

    def test_active_lock_is_refused_and_stale_lock_is_removed(self):
        self.assertTrue(transaction.pid_is_running(os.getpid()))
        self.assertFalse(transaction.pid_is_running(99999999))
        lock = self.runtime / ".repair/install.lock"; lock.parent.mkdir(parents=True)
        lock.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        with self.assertRaisesRegex(transaction.TransactionError, "MDOC-TRANSACTION-LOCKED"):
            transaction.acquire_lock(self.runtime, "update")
        lock.write_text(json.dumps({"pid": 99999999}), encoding="utf-8")
        acquired = transaction.acquire_lock(self.runtime, "update")
        self.assertTrue(acquired.is_file())

    def test_python_source_classification(self):
        codex = self.root / "codex-runtimes/dependencies/python/python.exe"
        managed = self.runtime / "python/python.exe"
        temporary = self.root / "temp/e2e/python.exe"
        self.assertEqual("codex-runtime", transaction.source_kind(codex, self.runtime))
        self.assertEqual("mdoc-managed", transaction.source_kind(managed, self.runtime))
        self.assertEqual("ineligible-temporary", transaction.source_kind(temporary, self.runtime))

    def test_cancel_only_cleans_stale_run(self):
        run = self.runtime / ".repair/runs/stale"; run.mkdir(parents=True)
        active = self.runtime / ".repair/active-run.json"
        active.write_text(json.dumps({"run": str(run), "pid": 99999999}), encoding="utf-8")
        self.assertEqual("cancelled", transaction.cancel(self.runtime, True)["status"])
        self.assertFalse(run.exists())

    def prepare_compatible_runtime(self):
        state = self.runtime / "state/installed-runtime.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({
            "toolchain_version": "2026.08.1",
            "python_contract": ">=3.12.0,<3.13.0",
            "profile": "Full",
            "requirements_sha256": "new-lock",
            "capability_probe": "ready",
            "python_source": "system-or-user",
        }), encoding="utf-8")

    def test_apply_refuses_half_upgrade_when_runtime_rebuild_is_required(self):
        plan = transaction.create_plan(self.package, self.installation, self.runtime, "update")
        self.assertTrue(plan["runtime_rebuild"])
        with self.assertRaisesRegex(transaction.TransactionError, "MDOC-RUNTIME-REPAIR-REQUIRED"):
            transaction.apply_plan(self.runtime, True)


if __name__ == "__main__": unittest.main()
