from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML

from skill.mdoc.tests.test_workspace_cli import valid_workspace


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mdoc.py"
YAML_WRITER = YAML()


class TaskDefinitionCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "manual"
        locale = self.workspace / "Guide" / "zh"
        (locale / "Main").mkdir(parents=True)
        (locale / "images").mkdir()
        (locale / "Summary.md").write_text("# Guide\n", encoding="utf-8")
        config = valid_workspace()
        config["books"]["guide"]["locales"] = {"zh": {"root": "zh", "language": "zh"}}
        self.cli("workspace", "init", "--workspace", str(self.workspace), "--json")
        self.write_yaml(self.workspace / ".mdoc" / "workspace-draft.yaml", config)
        self.cli("workspace", "apply", "--workspace", str(self.workspace), "--json")
        self.cli("workspace", "confirm", "--workspace", str(self.workspace), "--json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *arguments: str, expected: int = 0) -> dict:
        result = subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        self.assertEqual(expected, result.returncode, result.stdout or result.stderr)
        return json.loads(result.stdout)

    @staticmethod
    def write_yaml(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            YAML_WRITER.dump(value, stream)

    def test_task_draft_defines_and_freezes_a_manifest(self) -> None:
        created = self.cli("task", "create", "--workspace", str(self.workspace), "--task", "add-search", "--book", "guide", "--intent", "add_feature", "--json")
        self.assertEqual("task_draft_created", created["status"])
        draft_path = self.workspace / ".mdoc" / "tasks" / "add-search" / "task-draft.yaml"
        draft = YAML(typ="safe").load(draft_path.read_text(encoding="utf-8"))
        draft["task"]["title"] = "Add search"
        draft["scope"] = {
            "locales": ["zh"],
            "pages": {"create": [{"locale": "zh", "path": "Main/Search.md", "evidence": ["spec"]}], "update": [], "delete": []},
            "assets": {"create": [], "update": [], "delete": []},
            "navigation": {"update": [{"locale": "zh", "path": "Summary.md"}]},
        }
        draft["locale_plan"] = {"source": "zh", "targets": {}}
        draft["evidence"] = [{"id": "spec", "kind": "official_document", "location": "local:spec", "supports": ["zh/Main/Search.md"], "required": False, "critical": True}]
        self.write_yaml(draft_path, draft)

        defined = self.cli("task", "define", "--workspace", str(self.workspace), "--task", "add-search", "--json")
        self.assertEqual("waiting_for_definition_confirmation", defined["status"])
        task = YAML(typ="safe").load((draft_path.parent / "task.yaml").read_text(encoding="utf-8"))
        self.assertEqual(["zh/Main/Search.md", "zh/Summary.md"], [f"{item['locale']}/{item['path']}" for item in task["manifest"]])
        self.assertNotIn("changes", task)

        confirmed = self.cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "add-search", "--no-gui", "--json")
        self.assertEqual("waiting_for_authoring", confirmed["status"])
        state = json.loads((draft_path.parent / "task-state.json").read_text(encoding="utf-8"))
        self.assertEqual(task["definition_digest"], state["definition_confirmation"]["digest"])

    def test_locale_plan_rejects_cycles_and_scope_rejects_escape(self) -> None:
        self.cli("task", "create", "--workspace", str(self.workspace), "--task", "bad", "--book", "guide", "--intent", "add_locale", "--json")
        path = self.workspace / ".mdoc" / "tasks" / "bad" / "task-draft.yaml"
        draft = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
        draft["scope"]["locales"] = ["zh"]
        draft["scope"]["pages"]["create"] = [{"locale": "zh", "path": "../escape.md", "evidence": []}]
        draft["locale_plan"] = {"source": "zh", "targets": {}}
        self.write_yaml(path, draft)
        error = self.cli("task", "define", "--workspace", str(self.workspace), "--task", "bad", "--json", expected=2)
        self.assertEqual("MDOC-PATH-UNSAFE", error["error"]["code"])


if __name__ == "__main__":
    unittest.main()
