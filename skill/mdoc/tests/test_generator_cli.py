from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML

from skill.mdoc.tests.support import cli, write_yaml
from skill.mdoc.tests.test_workspace_cli import valid_workspace


class GeneratorCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "manual"
        locale = self.workspace / "Guide" / "zh"
        (locale / "Main").mkdir(parents=True)
        (locale / "images").mkdir()
        (locale / "Summary.md").write_text("# Guide\n", encoding="utf-8")
        tools = self.workspace / "tools"
        tools.mkdir()
        (tools / "generate.py").write_text(
            "from pathlib import Path\n"
            "root = Path.cwd()\n"
            "for name in ('E001.md', 'E002.md'):\n"
            "    (root / name).write_text('# ' + name + '\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        config = valid_workspace()
        config["books"]["guide"]["locales"] = {"zh": {"root": "zh", "language": "zh"}}
        config["generators"] = {
            "error-pages": {
                "command": ["runtime:python", "tools/generate.py"],
                "inputs": [],
                "outputs": {"root": "Main/Errors", "pattern": "E[0-9][0-9][0-9].md", "kind": "page", "locale": "zh"},
                "timeout_seconds": 30,
            }
        }
        cli("workspace", "init", "--workspace", str(self.workspace), "--json")
        write_yaml(self.workspace / ".mdoc" / "workspace-draft.yaml", config)
        cli("workspace", "apply", "--workspace", str(self.workspace), "--json")
        cli("workspace", "confirm", "--workspace", str(self.workspace), "--json")
        cli("workspace", "local", "init", "--workspace", str(self.workspace), "--json")
        write_yaml(self.workspace / ".mdoc" / "workspace.local-draft.yaml", {"schema_version": 1, "applications": {}, "resources": {}, "runtimes": {"python": {"executable": str(Path(__import__('sys').executable))}}})
        cli("workspace", "local", "apply", "--workspace", str(self.workspace), "--json")
        cli("workspace", "local", "confirm", "--workspace", str(self.workspace), "--json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_generator_expands_to_frozen_files_and_imports_to_staging(self) -> None:
        cli("task", "create", "--workspace", str(self.workspace), "--task", "generated", "--book", "guide", "--intent", "create_module", "--json")
        directory = self.workspace / ".mdoc" / "tasks" / "generated"
        draft_path = directory / "task-draft.yaml"
        draft = YAML(typ="safe").load(draft_path.read_text(encoding="utf-8"))
        draft["task"]["title"] = "Generated pages"
        draft["scope"]["locales"] = ["zh"]
        draft["locale_plan"] = {"source": "zh", "targets": {}}
        draft["generator"] = {"id": "error-pages", "inputs": {}}
        draft["evidence"] = [{"id": "spec", "kind": "official_document", "location": "local:spec", "supports": ["*"], "required": False, "critical": True}]
        write_yaml(draft_path, draft)
        defined = cli("task", "define", "--workspace", str(self.workspace), "--task", "generated", "--json")
        self.assertEqual(["zh/Main/Errors/E001.md", "zh/Main/Errors/E002.md"], [f"{item['locale']}/{item['path']}" for item in defined["manifest"]])
        cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "generated", "--no-gui", "--json")
        self.assertTrue((directory / "staging" / "zh" / "Main" / "Errors" / "E001.md").is_file())

    def test_generator_rejects_output_outside_declared_pattern(self) -> None:
        script = self.workspace / "tools" / "generate.py"
        script.write_text("from pathlib import Path\nPath('unexpected.txt').write_text('bad', encoding='utf-8')\n", encoding="utf-8")
        cli("task", "create", "--workspace", str(self.workspace), "--task", "bad-generator", "--book", "guide", "--intent", "create_module", "--json")
        path = self.workspace / ".mdoc" / "tasks" / "bad-generator" / "task-draft.yaml"
        draft = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
        draft["task"]["title"] = "Bad generator"
        draft["scope"]["locales"] = ["zh"]
        draft["locale_plan"] = {"source": "zh", "targets": {}}
        draft["generator"] = {"id": "error-pages", "inputs": {}}
        write_yaml(path, draft)
        error = cli("task", "define", "--workspace", str(self.workspace), "--task", "bad-generator", "--json", expected=2)
        self.assertEqual("MDOC-GENERATOR-OUTPUT-INVALID", error["error"]["code"])


if __name__ == "__main__":
    unittest.main()
