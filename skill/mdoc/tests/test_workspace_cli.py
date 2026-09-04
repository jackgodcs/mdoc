from __future__ import annotations

import json
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML

from skill.mdoc.mdoc_core.errors import MdocError
from skill.mdoc.mdoc_core.io import relative_path
from skill.mdoc.mdoc_core import pdf
from skill.mdoc.tests.support import write_yaml


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

    def test_workspace_pdf_defaults_are_optional_and_use_twenty_kibibytes(self) -> None:
        self.run_cli("workspace", "init", "--workspace", str(self.repository), "--json")
        draft = self.read_yaml(self.repository / ".mdoc" / "workspace-draft.yaml")
        self.assertEqual(20480, draft["pdf"]["defaults"]["image_optimization"]["min_bytes"])
        self.assertEqual(70, draft["pdf"]["defaults"]["image_optimization"]["jpeg_quality"])
        self.assertEqual(3, draft["pdf"]["defaults"]["concurrency"]["builds"])

        self.write_draft(valid_workspace())
        self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json")
        self.run_cli("workspace", "confirm", "--workspace", str(self.repository), "--json")
        self.assertNotIn("pdf", self.read_yaml(self.repository / ".mdoc" / "workspace.yaml"))

    def test_pdf_init_revises_a_draft_without_changing_authority(self) -> None:
        self.run_cli("workspace", "init", "--workspace", str(self.repository), "--json")
        self.write_draft(valid_workspace())
        self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json")
        self.run_cli("workspace", "confirm", "--workspace", str(self.repository), "--json")
        authority = (self.repository / ".mdoc" / "workspace.yaml").read_bytes()

        result = self.run_cli("pdf", "init", "--workspace", str(self.repository), "--json")
        self.assertEqual("pdf_workspace_draft_created", json.loads(result.stdout)["status"])
        draft = self.read_yaml(self.repository / ".mdoc" / "workspace-draft.yaml")
        self.assertEqual(pdf.DEFAULTS, draft["pdf"])
        self.assertEqual(authority, (self.repository / ".mdoc" / "workspace.yaml").read_bytes())

    def test_pdf_enabled_workspace_requires_book_json_for_each_locale(self) -> None:
        self.run_cli("workspace", "init", "--workspace", str(self.repository), "--json")
        workspace = valid_workspace()
        workspace["pdf"] = copy.deepcopy(pdf.DEFAULTS)
        self.write_draft(workspace)
        error = self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json", expected=2)
        self.assertEqual("MDOC-PDF-BOOK-CONFIG-MISSING", json.loads(error.stdout)["error"]["code"])

        for locale in ("zh", "en"):
            (self.repository / "Guide" / locale / "book.json").write_text('{"title":"Guide","language":"' + locale + '"}\n', encoding="utf-8")
        self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json")

    def test_generic_build_adapter_cannot_declare_pdf_artifacts(self) -> None:
        self.run_cli("workspace", "init", "--workspace", str(self.repository), "--json")
        workspace = valid_workspace()
        workspace["build_adapters"] = {
            "old-pdf": {"command": ["runtime:python", "tools/build.py"], "artifact": "manual.pdf", "artifact_kind": "pdf"}
        }
        self.write_draft(workspace)
        error = self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json", expected=2)
        self.assertEqual("MDOC-CONFIG-SCHEMA-INVALID", json.loads(error.stdout)["error"]["code"])

    def test_pdf_global_defaults_must_be_complete_but_book_override_may_be_partial(self) -> None:
        for locale in ("zh", "en"):
            (self.repository / "Guide" / locale / "book.json").write_text('{"title":"Guide","language":"' + locale + '"}\n', encoding="utf-8")
        self.run_cli("workspace", "init", "--workspace", str(self.repository), "--json")
        workspace = valid_workspace()
        workspace["pdf"] = copy.deepcopy(pdf.DEFAULTS)
        workspace["pdf"]["defaults"] = {"paper_size": "a4"}
        self.write_draft(workspace)
        error = self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json", expected=2)
        self.assertEqual("MDOC-CONFIG-SCHEMA-INVALID", json.loads(error.stdout)["error"]["code"])

        workspace["pdf"] = copy.deepcopy(pdf.DEFAULTS)
        workspace["books"]["guide"]["pdf"] = {"margins_pt": {"top": 80}}
        self.write_draft(workspace)
        self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json")

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

    def test_workspace_book_may_use_the_workspace_as_its_root(self) -> None:
        for locale in ("zh", "en"):
            root = self.repository / locale
            (root / "Main").mkdir(parents=True)
            (root / "images").mkdir()
            (root / "Summary.md").write_text("# Guide\n", encoding="utf-8")

        self.run_cli("workspace", "init", "--workspace", str(self.repository), "--json")
        workspace = valid_workspace()
        workspace["books"]["guide"]["root"] = "."
        self.write_draft(workspace)
        applied = self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json")

        self.assertEqual("waiting_for_workspace_confirmation", json.loads(applied.stdout)["status"])
        self.assertEqual(Path("."), relative_path(".", "books.guide.root"))
        for unsafe in ("", "../escape", "C:/manual"):
            with self.assertRaises(MdocError):
                relative_path(unsafe, "books.guide.root")

    def test_workspace_revise_rejects_removing_references_used_by_unfinished_tasks(self) -> None:
        self.run_cli("workspace", "init", "--workspace", str(self.repository), "--json")
        self.write_draft(valid_workspace())
        self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json")
        self.run_cli("workspace", "confirm", "--workspace", str(self.repository), "--json")

        self.run_cli("task", "create", "--workspace", str(self.repository), "--task", "active", "--book", "guide", "--intent", "add_feature", "--json")
        task_draft = self.read_yaml(self.repository / ".mdoc" / "tasks" / "active" / "task-draft.yaml")
        task_draft["task"]["title"] = "Active task"
        task_draft["scope"] = {
            "locales": ["zh"],
            "pages": {"create": [{"locale": "zh", "path": "Main/Active.md", "evidence": ["spec"]}], "update": [], "delete": []},
            "assets": {"create": [], "update": [], "delete": []},
            "navigation": {"update": [{"locale": "zh", "path": "Summary.md"}]},
        }
        task_draft["locale_plan"] = {"source": "zh", "targets": {}}
        task_draft["evidence"] = [{"id": "spec", "kind": "official_document", "location": "local:spec", "supports": ["zh/Main/Active.md"], "required": False, "critical": True}]
        write_yaml(self.repository / ".mdoc" / "tasks" / "active" / "task-draft.yaml", task_draft)
        self.run_cli("task", "define", "--workspace", str(self.repository), "--task", "active", "--json")

        self.run_cli("workspace", "revise", "--workspace", str(self.repository), "--json")
        changed = valid_workspace()
        changed["books"]["guide"]["source_locale"] = "en"
        changed["books"]["guide"]["locales"] = {"en": {"root": "en", "language": "en"}}
        self.write_draft(changed)
        error = self.run_cli("workspace", "apply", "--workspace", str(self.repository), "--json", expected=2)
        self.assertEqual("MDOC-WORKSPACE-ACTIVE-TASK-REFERENCE", json.loads(error.stdout)["error"]["code"])

    @staticmethod
    def read_yaml(path: Path) -> dict:
        parser = YAML(typ="safe")
        with path.open("r", encoding="utf-8") as stream:
            return parser.load(stream)


if __name__ == "__main__":
    unittest.main()
