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
        requirements = {"schema_version": 1, "product": "mdoc", "product_version": "1.2.0", "platform": "windows-x86_64", "python": {"version": ">=3.12,<3.13"}, "capabilities": {"full": {"python_packages": ["jsonschema"], "tools": {}}}}
        requirements_bytes = (json.dumps(requirements, indent=2) + "\n").encode()
        files = {"skill/mdoc/SKILL.md": b"# mdoc\n", "skill/mdoc/scripts/mdoc.py": b"print('new')\n", "runtime/requirements-v1.json": requirements_bytes}
        manifest = {"schema_version": 1, "product": "mdoc", "platform": "windows-x86_64", "version": "1.2.0", "runtime_contract": {"toolchain_version": "2026.08.1", "python": ">=3.12.0,<3.13.0", "profile": "Full", "requirements_sha256": __import__("hashlib").sha256(requirements_bytes).hexdigest(), "dependency_contract_sha256": transaction.dependency_contract_sha256(requirements)}, "files": [{"path": name, "sha256": __import__("hashlib").sha256(data).hexdigest()} for name, data in files.items()]}
        with zipfile.ZipFile(self.package, "w") as archive:
            for name, data in files.items(): archive.writestr(name, data)
            archive.writestr("PACKAGE-MANIFEST.json", json.dumps(manifest))

    def tearDown(self): self.temp.cleanup()

    def test_plan_and_apply_replace_installation_and_remove_detailed_plan(self):
        self.prepare_compatible_runtime()
        self.installation.mkdir(parents=True)
        (self.installation / "old.txt").write_text("old", encoding="utf-8")
        plan = transaction.create_plan(self.package, self.installation, self.runtime, "update", probe=lambda *_: [])
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
        state.write_text(json.dumps({"schema_version": 1, "status": "ready", "toolchain_version": "2026.08.1", "python_contract": ">=3.12.0,<3.13.0", "profile": "Full", "dependency_contract_sha256": self.manifest_contract(), "capability_probe": "ready", "python_source": "system-or-user"}), encoding="utf-8")
        plan = transaction.create_plan(self.package, self.installation, self.runtime, "update", probe=lambda *_: [])
        self.assertFalse(plan["runtime_rebuild"])
        self.assertEqual([], plan["runtime_rebuild_reasons"])
        state.write_text(json.dumps({"schema_version": 1, "status": "ready", "toolchain_version": "2026.07.1", "python_contract": ">=3.12.0,<3.13.0", "profile": "Full", "dependency_contract_sha256": self.manifest_contract(), "capability_probe": "ready", "python_source": "system-or-user"}), encoding="utf-8")
        plan = transaction.create_plan(self.package, self.installation, self.runtime, "update", probe=lambda *_: [])
        self.assertTrue(plan["runtime_rebuild"])
        self.assertIn("toolchain_version_changed", plan["runtime_rebuild_reasons"])

    def test_force_repair_and_failed_probe_require_rebuild(self):
        self.prepare_compatible_runtime()
        forced = transaction.create_plan(self.package, self.installation, self.runtime, "update", probe=lambda *_: [], force=True)
        self.assertIn("runtime_repair_forced", forced["runtime_rebuild_reasons"])
        failed = transaction.create_plan(self.package, self.installation, self.runtime, "update", probe=lambda *_: ["pdf_capability_probe_failed"])
        self.assertIn("pdf_capability_probe_failed", failed["runtime_rebuild_reasons"])

    def test_dependency_change_requires_rebuild(self):
        self.prepare_compatible_runtime()
        state = self.runtime / "state/installed-runtime.json"
        value = json.loads(state.read_text(encoding="utf-8")); value["dependency_contract_sha256"] = "different"
        state.write_text(json.dumps(value), encoding="utf-8")
        plan = transaction.create_plan(self.package, self.installation, self.runtime, "update", probe=lambda *_: [])
        self.assertIn("dependency_contract_changed", plan["runtime_rebuild_reasons"])

    def test_changed_package_invalidates_plan(self):
        self.prepare_compatible_runtime()
        transaction.create_plan(self.package, self.installation, self.runtime, "update", probe=lambda *_: [])
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
            "schema_version": 1,
            "status": "ready",
            "toolchain_version": "2026.08.1",
            "python_contract": ">=3.12.0,<3.13.0",
            "profile": "Full",
            "dependency_contract_sha256": self.manifest_contract(),
            "capability_probe": "ready",
            "python_source": "system-or-user",
        }), encoding="utf-8")

    def manifest_contract(self):
        with zipfile.ZipFile(self.package) as package:
            manifest = json.loads(package.read("PACKAGE-MANIFEST.json"))
        return manifest["runtime_contract"]["dependency_contract_sha256"]

    def test_product_version_does_not_change_dependency_contract(self):
        first = {"product_version": "1.5.6", "python": {"version": "3.12"}}
        second = {"python": {"version": "3.12"}, "product_version": "1.5.7"}
        self.assertEqual(transaction.dependency_contract_sha256(first), transaction.dependency_contract_sha256(second))

    def test_legacy_state_uses_installed_requirements_for_migration(self):
        self.prepare_compatible_runtime()
        state = self.runtime / "state/installed-runtime.json"
        value = json.loads(state.read_text(encoding="utf-8")); value.pop("dependency_contract_sha256")
        state.write_text(json.dumps(value), encoding="utf-8")
        support = self.installation / "runtime-support/runtime"; support.mkdir(parents=True)
        with zipfile.ZipFile(self.package) as package:
            old = json.loads(package.read("runtime/requirements-v1.json")); old["product_version"] = "1.1.0"
        (support / "requirements-v1.json").write_text(json.dumps(old), encoding="utf-8")
        plan = transaction.create_plan(self.package, self.installation, self.runtime, "update", probe=lambda *_: [])
        self.assertFalse(plan["runtime_rebuild"])
        self.assertTrue(plan["legacy_dependency_contract_migrated"])

    def test_apply_refuses_half_upgrade_when_runtime_rebuild_is_required(self):
        plan = transaction.create_plan(self.package, self.installation, self.runtime, "update")
        self.assertTrue(plan["runtime_rebuild"])
        with self.assertRaisesRegex(transaction.TransactionError, "MDOC-RUNTIME-REPAIR-REQUIRED"):
            transaction.apply_plan(self.runtime, True)


if __name__ == "__main__": unittest.main()
