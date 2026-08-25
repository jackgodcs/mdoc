from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mdoc.py"
YAML_WRITER = YAML()


def valid_workspace() -> dict:
    return {
        "schema_version": 1,
        "workspace": {"id": "test-workspace", "formal_vcs": "none"},
        "product": {"id": "test-product", "display_name": "Test Product"},
        "books": {
            "guide": {
                "root": "Guide",
                "source_locale": "zh",
                "locales": {
                    "zh": {"root": "zh", "language": "zh"},
                    "en": {"root": "en", "language": "en"},
                },
                "content_root": "Main",
                "assets_root": "images",
                "navigation": {"summary": "Summary.md"},
            }
        },
        "locales": {},
        "writing": {},
        "screenshots": {"auto_open_assistant": False},
        "quality_gate": {
            "default_profile": "standard",
            "required_reviews": [],
            "safe_fixes": True,
            "rules": [],
        },
        "publishing": {"allow_deletions": True},
        "generators": {},
        "build_adapters": {},
        "retention": {},
    }


class WorkspaceCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = Path(self.temp.name) / "manual"
        self.repository.mkdir()
        for locale in ("zh", "en"):
            root = self.repository / "Guide" / locale
            (root / "Main").mkdir(parents=True)
            (root / "images").mkdir()
            (root / "Summary.md").write_text("# Guide\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *arguments: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(expected, result.returncode, result.stdout or result.stderr)
        return result

    def write_draft(self, value: dict) -> None:
        path = self.repository / ".mdoc" / "workspace-draft.yaml"
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            YAML_WRITER.dump(value, stream)

    def test_workspace_candidate_requires_fresh_explicit_confirmation(self) -> None:
        human = self.run_cli("workspace", "init", "--workspace", str(self.repository))
        self.assertIn("工作区草稿", human.stdout)
        draft = self.repository / ".mdoc" / "workspace-draft.yaml"
        self.assertTrue(draft.is_file())
        self.assertFalse((self.repository / ".mdoc" / "workspace.yaml").exists())

        self.write_draft(valid_workspace())
        applied = self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json")
        payload = json.loads(applied.stdout)
        self.assertEqual("waiting_for_workspace_confirmation", payload["status"])
        self.assertTrue((self.repository / ".mdoc" / "cache" / "workspace-candidate.json").is_file())
        self.assertFalse((self.repository / ".mdoc" / "workspace.yaml").exists())

        changed = valid_workspace()
        changed["product"]["display_name"] = "Changed after apply"
        self.write_draft(changed)
        stale = self.run_cli("workspace", "confirm", "--workspace", str(self.repository), "--json", expected=2)
        self.assertEqual("MDOC-WORKSPACE-CANDIDATE-STALE", json.loads(stale.stdout)["error"]["code"])
        self.assertFalse((self.repository / ".mdoc" / "workspace.yaml").exists())

        self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json")
        confirmed = self.run_cli("workspace", "confirm", "--workspace", str(self.repository), "--json")
        self.assertEqual("workspace_ready", json.loads(confirmed.stdout)["status"])
        self.assertEqual("Changed after apply", self.read_yaml(self.repository / ".mdoc" / "workspace.yaml")["product"]["display_name"])
        self.assertFalse((self.repository / ".mdoc" / "cache" / "workspace-candidate.json").exists())

    def test_workspace_revise_and_local_configuration_are_separate(self) -> None:
        self.run_cli("workspace", "init", "--workspace", str(self.repository))
        self.write_draft(valid_workspace())
        self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json")
        self.run_cli("workspace", "confirm", "--workspace", str(self.repository), "--json")

        revised = self.run_cli("workspace", "revise", "--workspace", str(self.repository), "--json")
        self.assertEqual("workspace_draft_created", json.loads(revised.stdout)["status"])
        self.assertEqual(valid_workspace(), self.read_yaml(self.repository / ".mdoc" / "workspace-draft.yaml"))

        (self.repository / ".mdoc" / "workspace-draft.yaml").unlink()
        self.run_cli("workspace", "local", "init", "--workspace", str(self.repository), "--json")
        local_draft = self.repository / ".mdoc" / "workspace.local-draft.yaml"
        with local_draft.open("w", encoding="utf-8", newline="\n") as stream:
            YAML_WRITER.dump({"schema_version": 1, "resources": {"spec": {"path": "C:\\evidence\\spec.md"}}}, stream)
        self.run_cli("workspace", "local", "apply", "--workspace", str(self.repository), "--json")
        self.assertFalse((self.repository / ".mdoc" / "workspace.local.yaml").exists())
        confirmed = self.run_cli("workspace", "local", "confirm", "--workspace", str(self.repository), "--json")
        self.assertEqual("workspace_local_ready", json.loads(confirmed.stdout)["status"])
        self.assertEqual(r"C:\evidence\spec.md", self.read_yaml(self.repository / ".mdoc" / "workspace.local.yaml")["resources"]["spec"]["path"])

    def test_portable_paths_and_local_override_authority_are_strict(self) -> None:
        self.run_cli("workspace", "init", "--workspace", str(self.repository))
        invalid = valid_workspace()
        invalid["books"]["guide"]["root"] = "C:\\manual"
        self.write_draft(invalid)
        error = self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json", expected=2)
        self.assertEqual("MDOC-PATH-UNSAFE", json.loads(error.stdout)["error"]["code"])

        invalid = valid_workspace()
        invalid["unknown"] = True
        self.write_draft(invalid)
        error = self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json", expected=2)
        self.assertEqual("MDOC-CONFIG-SCHEMA-INVALID", json.loads(error.stdout)["error"]["code"])

        self.write_draft(valid_workspace())
        self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json")
        self.run_cli("workspace", "confirm", "--workspace", str(self.repository), "--json")
        self.run_cli("workspace", "local", "init", "--workspace", str(self.repository), "--json")
        with (self.repository / ".mdoc" / "workspace.local-draft.yaml").open("w", encoding="utf-8", newline="\n") as stream:
            YAML_WRITER.dump({"schema_version": 1, "books": {}}, stream)
        error = self.run_cli("workspace", "local", "apply", "--workspace", str(self.repository), "--json", expected=2)
        self.assertEqual("MDOC-CONFIG-SCHEMA-INVALID", json.loads(error.stdout)["error"]["code"])

    @staticmethod
    def read_yaml(path: Path) -> dict:
        parser = YAML(typ="safe")
        with path.open("r", encoding="utf-8") as stream:
            return parser.load(stream)


if __name__ == "__main__":
    unittest.main()
