from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from ruamel.yaml import YAML

from skill.mdoc.tests.support import cli, write_yaml
from skill.mdoc.tests.test_workspace_cli import valid_workspace


class TaskLifecycleCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "manual"
        self.locale = self.workspace / "Guide" / "zh"
        (self.locale / "Main").mkdir(parents=True)
        (self.locale / "images").mkdir()
        (self.locale / "Summary.md").write_text("# Guide\n", encoding="utf-8")
        config = valid_workspace()
        config["books"]["guide"]["locales"] = {"zh": {"root": "zh", "language": "zh"}}
        cli("workspace", "init", "--workspace", str(self.workspace), "--json")
        write_yaml(self.workspace / ".mdoc" / "workspace-draft.yaml", config)
        cli("workspace", "apply", "--workspace", str(self.workspace), "--json")
        cli("workspace", "confirm", "--workspace", str(self.workspace), "--json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def define_simple_task(self, task_id: str = "simple") -> Path:
        cli("task", "create", "--workspace", str(self.workspace), "--task", task_id, "--book", "guide", "--intent", "add_feature", "--json")
        directory = self.workspace / ".mdoc" / "tasks" / task_id
        draft = YAML(typ="safe").load((directory / "task-draft.yaml").read_text(encoding="utf-8"))
        draft["task"]["title"] = "Simple page"
        draft["scope"] = {
            "locales": ["zh"],
            "pages": {"create": [{"locale": "zh", "path": "Main/Simple.md", "evidence": ["spec"]}], "update": [], "delete": []},
            "assets": {"create": [], "update": [], "delete": []},
            "navigation": {"update": [{"locale": "zh", "path": "Summary.md"}]},
        }
        draft["locale_plan"] = {"source": "zh", "targets": {}}
        draft["evidence"] = [{"id": "spec", "kind": "official_document", "location": "local:spec", "supports": ["zh/Main/Simple.md"], "required": False, "critical": True}]
        write_yaml(directory / "task-draft.yaml", draft)
        cli("task", "define", "--workspace", str(self.workspace), "--task", task_id, "--json")
        return directory

    def test_continue_publishes_once_and_final_confirmation_is_terminal(self) -> None:
        directory = self.define_simple_task()
        waiting = cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "simple", "--no-gui", "--json")
        self.assertEqual("waiting_for_authoring", waiting["status"])
        request = json.loads((directory / "authoring-request.json").read_text(encoding="utf-8"))
        self.assertEqual(["zh/Main/Simple.md", "zh/Summary.md"], [f"{item['locale']}/{item['path']}" for item in request["files"]])

        staging = directory / "staging" / "zh"
        (staging / "Main").mkdir(parents=True, exist_ok=True)
        (staging / "Main" / "Simple.md").write_text("# Simple\n\nComplete content.\n", encoding="utf-8")
        (staging / "Summary.md").write_text("# Guide\n\n- [Simple](Main/Simple.md)\n", encoding="utf-8")
        ready = cli("task", "submit-authoring", "--workspace", str(self.workspace), "--task", "simple", "--no-gui", "--json")
        self.assertEqual("ready_for_review", ready["status"])
        self.assertTrue((self.locale / "Main" / "Simple.md").is_file())
        count = len(ready["publish"]["transactions"])
        self.assertEqual(count, len(cli("task", "continue", "--workspace", str(self.workspace), "--task", "simple", "--no-gui", "--json")["publish"]["transactions"]))
        accepted = cli("task", "confirm-final", "--workspace", str(self.workspace), "--task", "simple", "--json")
        self.assertEqual("accepted", accepted["status"])
        self.assertEqual("accepted", cli("task", "continue", "--workspace", str(self.workspace), "--task", "simple", "--no-gui", "--json")["status"])

    def test_manual_png_capture_and_aggregate_acceptance_survive_sessions(self) -> None:
        directory = self.define_simple_task("screenshots")
        draft_path = directory / "task-draft.yaml"
        draft = YAML(typ="safe").load(draft_path.read_text(encoding="utf-8"))
        draft["scope"]["assets"]["create"] = [{"locale": "zh", "path": "images/window.png", "evidence": []}]
        draft["screenshots"] = [{"id": "MAIN-WINDOW", "filename": "window.png", "locales": ["zh"], "required": True, "destinations": {"zh": "images/window.png"}}]
        write_yaml(draft_path, draft)
        cli("task", "define", "--workspace", str(self.workspace), "--task", "screenshots", "--json")
        waiting = cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "screenshots", "--no-gui", "--json")
        self.assertEqual("waiting_for_screenshots", waiting["status"])

        capture = directory / "captures" / "zh" / "window.png"
        capture.parent.mkdir(parents=True)
        Image.new("RGB", (12, 8), "red").save(capture)
        state = cli("task", "continue", "--workspace", str(self.workspace), "--task", "screenshots", "--no-gui", "--json")
        self.assertEqual("waiting_for_screenshot_acceptance", state["status"])
        self.assertEqual("captured", state["screenshots"]["MAIN-WINDOW:zh"]["status"])
        state = cli("task", "accept-screenshots", "--workspace", str(self.workspace), "--task", "screenshots", "--no-gui", "--json")
        self.assertEqual("waiting_for_authoring", state["status"])
        self.assertEqual("captured", state["screenshots"]["MAIN-WINDOW:zh"]["status"])
        self.assertEqual(capture.read_bytes(), (directory / "staging" / "zh" / "images" / "window.png").read_bytes())

        Image.new("RGB", (13, 8), "blue").save(capture)
        state = cli("task", "continue", "--workspace", str(self.workspace), "--task", "screenshots", "--no-gui", "--json")
        self.assertEqual("waiting_for_screenshot_acceptance", state["status"])
        self.assertEqual("stale", state["screenshot_acceptance"]["status"])

    def test_copy_from_locale_is_cli_owned_and_byte_identical(self) -> None:
        en = self.workspace / "Guide" / "en"
        (en / "Main").mkdir(parents=True)
        (en / "images").mkdir()
        (en / "Summary.md").write_text("# Guide\n", encoding="utf-8")
        config = valid_workspace()
        cli("workspace", "revise", "--workspace", str(self.workspace), "--json")
        write_yaml(self.workspace / ".mdoc" / "workspace-draft.yaml", config)
        cli("workspace", "apply", "--workspace", str(self.workspace), "--json")
        cli("workspace", "confirm", "--workspace", str(self.workspace), "--json")

        cli("task", "create", "--workspace", str(self.workspace), "--task", "copy-locale", "--book", "guide", "--intent", "add_locale", "--json")
        directory = self.workspace / ".mdoc" / "tasks" / "copy-locale"
        draft_path = directory / "task-draft.yaml"
        draft = YAML(typ="safe").load(draft_path.read_text(encoding="utf-8"))
        draft["task"]["title"] = "Copy locale"
        draft["scope"] = {
            "locales": ["zh", "en"],
            "pages": {"create": [
                {"locale": "zh", "path": "Main/Copied.md", "evidence": ["spec"]},
                {"locale": "en", "path": "Main/Copied.md", "evidence": ["spec"]},
            ], "update": [], "delete": []},
            "assets": {"create": [], "update": [], "delete": []},
            "navigation": {"update": [
                {"locale": "zh", "path": "Summary.md"},
                {"locale": "en", "path": "Summary.md"},
            ]},
        }
        draft["locale_plan"] = {"source": "zh", "targets": {"en": {"content": {"copy_from": "zh"}, "screenshots": "not_applicable"}}}
        draft["evidence"] = [{"id": "spec", "kind": "official_document", "location": "local:spec", "supports": ["zh/Main/Copied.md", "en/Main/Copied.md"], "required": False, "critical": True}]
        write_yaml(draft_path, draft)
        cli("task", "define", "--workspace", str(self.workspace), "--task", "copy-locale", "--json")
        cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "copy-locale", "--no-gui", "--json")
        request = json.loads((directory / "authoring-request.json").read_text(encoding="utf-8"))
        self.assertEqual(
            ["zh/Main/Copied.md", "en/Summary.md", "zh/Summary.md"],
            [f"{item['locale']}/{item['path']}" for item in request["files"]],
        )
        source = directory / "staging" / "zh" / "Main" / "Copied.md"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"# Exact bytes\r\n\r\nCopied by CLI.\r\n")
        (directory / "staging" / "zh" / "Summary.md").write_text("# Guide\n\n- [Copied](Main/Copied.md)\n", encoding="utf-8")
        (directory / "staging" / "en" / "Summary.md").write_text("# Guide\n\n- [Copied](Main/Copied.md)\n", encoding="utf-8")
        state = cli("task", "submit-authoring", "--workspace", str(self.workspace), "--task", "copy-locale", "--no-gui", "--json")
        self.assertEqual("ready_for_review", state["status"])
        self.assertEqual(source.read_bytes(), (en / "Main" / "Copied.md").read_bytes())


if __name__ == "__main__":
    unittest.main()
