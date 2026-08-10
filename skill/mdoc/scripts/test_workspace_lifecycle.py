from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("mdoc.py")


class WorkspaceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        (self.repo / "Book-A").mkdir(parents=True)
        self.workspace = self.root / "workspace"
        self.env = os.environ | {"LOCALAPPDATA": str(self.root / "LocalAppData")}

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args: str, expected: int = 0):
        result = subprocess.run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True, encoding="utf-8", env=self.env, check=False)
        self.assertEqual(expected, result.returncode, result.stderr or result.stdout)
        return result

    def setup_v2(self):
        self.cli("setup", "--repository", str(self.repo), "--workspace", str(self.workspace), "--book", "Book-A")

    def test_adopt_plan_then_apply_preserves_unknown_configuration(self):
        self.setup_v2()
        config = self.workspace / "workspace.yaml"
        config.write_text(config.read_text(encoding="utf-8") + "product_extension:\n  keep_me: true\n", encoding="utf-8")
        (self.workspace / "open-mdoc.cmd").unlink()
        plan = json.loads(self.cli("workspace", "adopt", "--workspace", str(self.workspace), "--plan", "--json").stdout)
        self.assertEqual("planned", plan["status"])
        self.assertIn("write_stable_launcher", [item["action"] for item in plan["actions"]])
        applied = json.loads(self.cli("workspace", "adopt", "--workspace", str(self.workspace), "--apply", "--confirm", "--json").stdout)
        self.assertEqual("adopted", applied["status"])
        self.assertIn("keep_me: true", config.read_text(encoding="utf-8"))
        self.assertFalse(self.workspace.joinpath(".work/mdoc/plans/adopt-current.json").exists())
        self.assertTrue(self.workspace.joinpath(".work/mdoc/records/latest-adopt.json").is_file())

    def test_adopt_rejects_changed_input_after_plan(self):
        self.setup_v2()
        self.cli("workspace", "adopt", "--workspace", str(self.workspace), "--plan")
        (self.workspace / "workspace.yaml").write_text((self.workspace / "workspace.yaml").read_text(encoding="utf-8") + "changed: true\n", encoding="utf-8")
        result = self.cli("workspace", "adopt", "--workspace", str(self.workspace), "--apply", "--confirm", expected=2)
        self.assertIn("MDOC-PLAN-STALE", result.stderr)

    def test_migrate_v1_to_v2_preserves_tasks_and_unknown_fields(self):
        self.workspace.mkdir()
        (self.workspace / "manual-tasks" / "done").mkdir(parents=True)
        (self.workspace / "manual-tasks" / "done" / "state.yaml").write_text("status: completed\n", encoding="utf-8")
        (self.workspace / "workspace.yaml").write_text("schema_version: 1\nmanual:\n  active_book: Book-A\nlegacy_extra: keep\n", encoding="utf-8")
        (self.workspace / "workspace.local.yaml").write_text(f"schema_version: 1\nlocal:\n  manual_repository: {json.dumps(str(self.repo))}\n", encoding="utf-8")
        self.cli("workspace", "migrate", "--workspace", str(self.workspace), "--plan")
        payload = json.loads(self.cli("workspace", "migrate", "--workspace", str(self.workspace), "--apply", "--confirm", "--json").stdout)
        self.assertEqual(2, payload["schema_version"])
        source = (self.workspace / "workspace.yaml").read_text(encoding="utf-8")
        self.assertIn("schema_version: 2", source)
        self.assertIn("legacy_extra: keep", source)
        self.assertTrue((self.workspace / "manual-tasks" / "done" / "state.yaml").is_file())

    def test_cleanup_deletes_only_planned_regenerable_content(self):
        self.setup_v2()
        cache = self.workspace / ".pdf-check" / "cache.bin"
        cache.parent.mkdir()
        cache.write_bytes(b"cache")
        keep = self.workspace / "manual-tasks" / "keep" / "state.yaml"
        keep.parent.mkdir(parents=True)
        keep.write_text("status: completed\n", encoding="utf-8")
        self.cli("workspace", "cleanup", "--workspace", str(self.workspace), "--plan")
        self.cli("workspace", "cleanup", "--workspace", str(self.workspace), "--apply", "--confirm")
        self.assertFalse(cache.exists())
        self.assertTrue(keep.exists())

    def test_registry_corruption_is_reported_and_repair_requires_plan(self):
        self.setup_v2()
        registry = self.root / "LocalAppData" / "mdoc" / "workspace-registry.json"
        registry.write_text("{broken", encoding="utf-8")
        result = self.cli("workspace", "list", "--json", expected=2)
        self.assertIn("MDOC-REGISTRY-CORRUPT", result.stderr)
        scan = self.root / "scan"
        candidate = scan / "one"
        candidate.mkdir(parents=True)
        (candidate / "workspace.yaml").write_text((self.workspace / "workspace.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        (candidate / "workspace.local.yaml").write_text((self.workspace / "workspace.local.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        self.cli("workspace", "registry", "repair", "--scan-root", str(scan), "--plan")
        self.cli("workspace", "registry", "repair", "--scan-root", str(scan), "--apply", "--confirm")
        payload = json.loads(self.cli("workspace", "list", "--json").stdout)
        self.assertEqual([str(candidate.resolve())], [item["workspace"] for item in payload["workspaces"]])
        self.assertLessEqual(len(list(registry.parent.glob("workspace-registry.corrupt-*.json"))), 1)

    def test_registry_prune_removes_only_missing_local_entry(self):
        self.setup_v2()
        missing = self.root / "missing-workspace"
        self.cli("workspace", "register", "--workspace", str(missing), "--repository", str(self.repo))
        payload = json.loads(self.cli("workspace", "prune", "--json").stdout)
        self.assertEqual(1, payload["removed"])
        listed = json.loads(self.cli("workspace", "list", "--json").stdout)
        self.assertEqual([str(self.workspace.resolve())], [item["workspace"] for item in listed["workspaces"]])


if __name__ == "__main__":
    unittest.main()
