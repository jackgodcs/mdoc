from __future__ import annotations

import json
import subprocess
import sys
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

    def test_out_of_manifest_staging_files_are_preserved_reported_and_not_published(self) -> None:
        directory = self.define_simple_task("preserve-staging")
        cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "preserve-staging", "--no-gui", "--json")
        staging = directory / "staging" / "zh"
        (staging / "Main").mkdir(parents=True, exist_ok=True)
        (staging / "Main" / "Simple.md").write_text("# Simple\n\nComplete content.\n", encoding="utf-8")
        (staging / "Summary.md").write_text("# Guide\n\n- [Simple](Main/Simple.md)\n", encoding="utf-8")
        extra = staging / "notes" / "useful-draft.md"
        extra.parent.mkdir(parents=True)
        extra.write_text("Useful material that belongs to another task.\n", encoding="utf-8")

        ready = cli("task", "submit-authoring", "--workspace", str(self.workspace), "--task", "preserve-staging", "--no-gui", "--json")

        self.assertEqual("ready_for_review", ready["status"])
        self.assertEqual(["zh/notes/useful-draft.md"], ready["preserved_staging_files"])
        self.assertEqual(["zh/notes/useful-draft.md"], ready["authoring_submission"]["preserved_staging_files"])
        self.assertEqual("Useful material that belongs to another task.\n", extra.read_text(encoding="utf-8"))
        self.assertFalse((self.locale / "notes" / "useful-draft.md").exists())

        status = cli("task", "status", "--workspace", str(self.workspace), "--task", "preserve-staging", "--json")
        self.assertEqual(["zh/notes/useful-draft.md"], status["preserved_staging_files"])
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parents[1] / "scripts" / "mdoc.py"), "task", "status", "--workspace", str(self.workspace), "--task", "preserve-staging"],
            cwd=Path(__file__).parents[3], capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("staging 中以下清单外文件已保留", result.stdout)
        self.assertIn("- zh/notes/useful-draft.md", result.stdout)
        extra.unlink()
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parents[1] / "scripts" / "mdoc.py"), "task", "status", "--workspace", str(self.workspace), "--task", "preserve-staging"],
            cwd=Path(__file__).parents[3], capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("staging 中以下清单外文件已保留", result.stdout)

    def test_explicit_publish_conflict_approval_rebases_changed_update_and_publishes(self) -> None:
        directory = self.define_simple_task("changed-update")
        cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "changed-update", "--no-gui", "--json")
        staging = directory / "staging" / "zh"
        (staging / "Main").mkdir(parents=True, exist_ok=True)
        (staging / "Main" / "Simple.md").write_text("# Simple\n\nComplete content.\n", encoding="utf-8")
        (staging / "Summary.md").write_text("# Guide\n\n- [Simple](Main/Simple.md)\n", encoding="utf-8")
        (self.locale / "Summary.md").write_text("# Guide\n\nExternal change.\n", encoding="utf-8")
        paused = cli("task", "submit-authoring", "--workspace", str(self.workspace), "--task", "changed-update", "--no-gui", "--json")
        self.assertEqual("waiting_for_resolution", paused["status"])
        self.assertEqual("MDOC-PUBLISH-CONFLICT", paused["waiting_on"]["error"]["code"])
        self.assertEqual("target_changed", paused["waiting_on"]["error"]["details"]["conflicts"][0]["reason"])

        error = cli("task", "approve-publish-conflict", "--workspace", str(self.workspace), "--task", "changed-update", "--no-gui", "--json", expected=2)
        self.assertEqual("MDOC-PUBLISH-CONFLICT-CONFIRMATION-REQUIRED", error["error"]["code"])

        published = cli("task", "approve-publish-conflict", "--workspace", str(self.workspace), "--task", "changed-update", "--confirm", "--no-gui", "--json")
        self.assertEqual("ready_for_review", published["status"])
        self.assertEqual(1, len(published["publish_conflict_approvals"]))
        self.assertEqual(["zh/Summary.md"], published["publish_conflict_approvals"][0]["targets"])
        self.assertEqual((staging / "Summary.md").read_text(encoding="utf-8"), (self.locale / "Summary.md").read_text(encoding="utf-8"))

    def test_publish_conflict_approval_rejects_create_target_exists(self) -> None:
        directory = self.define_simple_task("existing-create")
        cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "existing-create", "--no-gui", "--json")
        staging = directory / "staging" / "zh"
        (staging / "Main").mkdir(parents=True, exist_ok=True)
        (staging / "Main" / "Simple.md").write_text("# Simple\n\nStaged content.\n", encoding="utf-8")
        (staging / "Summary.md").write_text("# Guide\n\n- [Simple](Main/Simple.md)\n", encoding="utf-8")
        external = self.locale / "Main" / "Simple.md"
        external.write_text("# Existing\n\nExternal content.\n", encoding="utf-8")
        paused = cli("task", "submit-authoring", "--workspace", str(self.workspace), "--task", "existing-create", "--no-gui", "--json")
        self.assertEqual("waiting_for_resolution", paused["status"])
        self.assertEqual("create_target_exists", paused["waiting_on"]["error"]["details"]["conflicts"][0]["reason"])

        error = cli("task", "approve-publish-conflict", "--workspace", str(self.workspace), "--task", "existing-create", "--confirm", "--no-gui", "--json", expected=2)
        self.assertEqual("MDOC-PUBLISH-CONFLICT-NOT-APPROVABLE", error["error"]["code"])
        self.assertEqual("# Existing\n\nExternal content.\n", external.read_text(encoding="utf-8"))

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
        launcher = self.workspace / "Open-Screenshot-Assistant-screenshots.cmd"
        self.assertTrue(launcher.is_file())
        launcher_text = launcher.read_text(encoding="ascii")
        self.assertIn('pushd "%~dp0" >nul 2>&1', launcher_text)
        self.assertIn('"%MDOC_PYTHON%" -B "%MDOC_ASSISTANT%" --workspace "%MDOC_WORKSPACE%" --task "screenshots"', launcher_text)
        self.assertNotIn("--contributor", launcher_text)
        launcher.write_text("旧启动脚本", encoding="utf-8")
        cli("task", "continue", "--workspace", str(self.workspace), "--task", "screenshots", "--no-gui", "--json")
        self.assertTrue(launcher.is_file())
        self.assertEqual(launcher_text, launcher.read_text(encoding="ascii"))

        capture = directory / "captures" / "zh" / "window.png"
        capture.parent.mkdir(parents=True)
        Image.new("RGB", (12, 8), "red").save(capture)
        state = cli("task", "continue", "--workspace", str(self.workspace), "--task", "screenshots", "--no-gui", "--json")
        self.assertEqual("waiting_for_screenshot_acceptance", state["status"])
        self.assertEqual("captured", state["screenshots"]["MAIN-WINDOW:zh"]["status"])
        state = cli("task", "screenshots", "accept", "--workspace", str(self.workspace), "--task", "screenshots", "--no-gui", "--json")
        self.assertEqual("waiting_for_authoring", state["status"])
        self.assertEqual("captured", state["screenshots"]["MAIN-WINDOW:zh"]["status"])
        self.assertEqual(capture.read_bytes(), (directory / "staging" / "zh" / "images" / "window.png").read_bytes())

        Image.new("RGB", (13, 8), "blue").save(capture)
        state = cli("task", "continue", "--workspace", str(self.workspace), "--task", "screenshots", "--no-gui", "--json")
        self.assertEqual("waiting_for_screenshot_acceptance", state["status"])
        self.assertEqual("stale", state["screenshot_acceptance"]["status"])

    def test_manual_jpeg_capture_and_aggregate_acceptance_survive_sessions(self) -> None:
        directory = self.define_simple_task("jpeg-screenshots")
        draft_path = directory / "task-draft.yaml"
        draft = YAML(typ="safe").load(draft_path.read_text(encoding="utf-8"))
        draft["scope"]["assets"]["create"] = [{"locale": "zh", "path": "images/window.jpg", "evidence": []}]
        draft["screenshots"] = [{"id": "MAIN-WINDOW", "filename": "window.jpg", "locales": ["zh"], "required": True, "destinations": {"zh": "images/window.jpg"}}]
        write_yaml(draft_path, draft)
        cli("task", "define", "--workspace", str(self.workspace), "--task", "jpeg-screenshots", "--json")
        waiting = cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "jpeg-screenshots", "--no-gui", "--json")
        self.assertEqual("waiting_for_screenshots", waiting["status"])

        capture = directory / "captures" / "zh" / "window.jpg"
        capture.parent.mkdir(parents=True)
        Image.new("RGB", (12, 8), "red").save(capture, format="JPEG", quality=95, subsampling=0)
        state = cli("task", "continue", "--workspace", str(self.workspace), "--task", "jpeg-screenshots", "--no-gui", "--json")
        self.assertEqual("waiting_for_screenshot_acceptance", state["status"])
        self.assertEqual("JPEG", state["screenshots"]["MAIN-WINDOW:zh"]["file"]["format"])
        state = cli("task", "screenshots", "accept", "--workspace", str(self.workspace), "--task", "jpeg-screenshots", "--no-gui", "--json")
        self.assertEqual("waiting_for_authoring", state["status"])
        self.assertEqual(capture.read_bytes(), (directory / "staging" / "zh" / "images" / "window.jpg").read_bytes())

    def test_contributor_submission_does_not_accept_or_publish_screenshots(self) -> None:
        directory = self.define_simple_task("contributor")
        draft_path = directory / "task-draft.yaml"
        draft = YAML(typ="safe").load(draft_path.read_text(encoding="utf-8"))
        draft["scope"]["assets"]["create"] = [{"locale": "zh", "path": "images/window.png", "evidence": []}]
        draft["screenshots"] = [{"id": "WINDOW", "filename": "window.png", "locales": ["zh"], "required": True, "destinations": {"zh": "images/window.png"}}]
        write_yaml(draft_path, draft)
        cli("task", "define", "--workspace", str(self.workspace), "--task", "contributor", "--json")
        cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "contributor", "--no-gui", "--json")

        capture = directory / "captures" / "zh" / "window.png"
        capture.parent.mkdir(parents=True)
        Image.new("RGB", (12, 8), "green").save(capture)
        submitted = cli("task", "screenshots", "submit", "--workspace", str(self.workspace), "--task", "contributor", "--no-gui", "--json")
        self.assertEqual("submitted", submitted["screenshot_submission"]["status"])
        self.assertIsNone(submitted["screenshot_acceptance"])
        self.assertFalse((directory / "staging" / "zh" / "images" / "window.png").exists())
        self.assertFalse((self.locale / "images" / "window.png").exists())

        state = cli("task", "screenshots", "set-status", "--workspace", str(self.workspace), "--task", "contributor", "--item", "WINDOW:zh", "--status", "needs_retake", "--contributor", "--no-gui", "--json")
        self.assertEqual("waiting_for_screenshots", state["status"])
        self.assertEqual("stale", state["screenshot_submission"]["status"])

    def test_blocked_screenshot_reason_survives_sync_and_clears_when_restored(self) -> None:
        directory = self.define_simple_task("blocked-screenshot")
        draft_path = directory / "task-draft.yaml"
        draft = YAML(typ="safe").load(draft_path.read_text(encoding="utf-8"))
        draft["scope"]["assets"]["create"] = [{"locale": "zh", "path": "images/window.png", "evidence": []}]
        draft["screenshots"] = [{"id": "WINDOW", "filename": "window.png", "locales": ["zh"], "required": True, "destinations": {"zh": "images/window.png"}}]
        write_yaml(draft_path, draft)
        cli("task", "define", "--workspace", str(self.workspace), "--task", "blocked-screenshot", "--json")
        cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "blocked-screenshot", "--no-gui", "--json")

        state = cli(
            "task", "screenshots", "set-status", "--workspace", str(self.workspace), "--task", "blocked-screenshot",
            "--item", "WINDOW:zh", "--status", "blocked", "--reason", "Target application is unavailable.", "--no-gui", "--json",
        )
        self.assertEqual("blocked", state["screenshots"]["WINDOW:zh"]["status"])
        self.assertEqual("Target application is unavailable.", state["screenshots"]["WINDOW:zh"]["reason"])
        state = cli("task", "continue", "--workspace", str(self.workspace), "--task", "blocked-screenshot", "--no-gui", "--json")
        self.assertEqual("blocked", state["screenshots"]["WINDOW:zh"]["status"])
        self.assertEqual("Target application is unavailable.", state["screenshots"]["WINDOW:zh"]["reason"])

        state = cli(
            "task", "screenshots", "set-status", "--workspace", str(self.workspace), "--task", "blocked-screenshot",
            "--item", "WINDOW:zh", "--status", "pending", "--no-gui", "--json",
        )
        self.assertEqual("pending", state["screenshots"]["WINDOW:zh"]["status"])
        self.assertNotIn("reason", state["screenshots"]["WINDOW:zh"])

    def test_contributor_launcher_stays_inside_the_workspace_root(self) -> None:
        self.define_simple_task("contributor-launcher")
        result = cli("task", "create-contributor-launcher", "--workspace", str(self.workspace), "--task", "contributor-launcher", "--output", "Open-Task.cmd", "--json")
        launcher = self.workspace / "Open-Task.cmd"
        # Windows may serialize the same temporary directory with an 8.3
        # short path (for example RUNNER~1) in the subprocess result.
        self.assertEqual(launcher.resolve(), Path(result["path"]).resolve())
        self.assertTrue(launcher.samefile(Path(result["path"])))
        launcher_text = launcher.read_text(encoding="ascii")
        self.assertIn('pushd "%~dp0" >nul 2>&1', launcher_text)
        self.assertIn("set \"MDOC_WORKSPACE=%CD%\\.\"", launcher_text)
        self.assertIn('set "MDOC_PYTHON=%LOCALAPPDATA%\\mdoc\\runtime\\Scripts\\python.exe"', launcher_text)
        self.assertIn('set "MDOC_ASSISTANT=%USERPROFILE%\\.codex\\skills\\mdoc\\scripts\\screenshot_assistant.py"', launcher_text)
        self.assertIn('"%MDOC_PYTHON%" -B "%MDOC_ASSISTANT%" --workspace "%MDOC_WORKSPACE%" --task "contributor-launcher" --contributor', launcher_text)
        self.assertIn('cmd /c exit 2', launcher_text)
        self.assertIn('popd', launcher_text)
        error = cli("task", "create-contributor-launcher", "--workspace", str(self.workspace), "--task", "contributor-launcher", "--output", "nested\\Open-Task.cmd", "--json", expected=2)
        self.assertEqual("MDOC-CONTRIBUTOR-LAUNCHER-INVALID", error["error"]["code"])

    def test_task_without_screenshots_does_not_create_screenshot_launcher(self) -> None:
        self.define_simple_task("no-screenshots")
        cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "no-screenshots", "--no-gui", "--json")

        self.assertFalse((self.workspace / "Open-Screenshot-Assistant-no-screenshots.cmd").exists())

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

    def test_definition_revision_releases_scope_and_requires_confirmation_again(self) -> None:
        directory = self.define_simple_task("revision-owner")
        cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "revision-owner", "--no-gui", "--json")
        revised = cli("task", "revise", "--workspace", str(self.workspace), "--task", "revision-owner", "--no-gui", "--json")
        self.assertEqual("draft", revised["status"])
        self.assertFalse(revised["scope_claimed"])
        draft_path = directory / "task-draft.yaml"
        draft = YAML(typ="safe").load(draft_path.read_text(encoding="utf-8"))
        draft["scope"]["pages"]["create"][0]["path"] = "Main/Revised.md"
        draft["evidence"][0]["supports"] = ["zh/Main/Revised.md"]
        write_yaml(draft_path, draft)
        defined = cli("task", "define", "--workspace", str(self.workspace), "--task", "revision-owner", "--json")
        self.assertEqual("waiting_for_definition_confirmation", defined["status"])
        state = cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "revision-owner", "--no-gui", "--json")
        self.assertEqual("waiting_for_authoring", state["status"])
        self.assertEqual("Main/Revised.md", state["definition_snapshot"]["manifest"][0]["path"])

    def test_cancel_releases_scope_and_terminal_tasks_cannot_reopen(self) -> None:
        self.define_simple_task("cancelled-owner")
        cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "cancelled-owner", "--no-gui", "--json")
        plan = cli("task", "cancel", "--workspace", str(self.workspace), "--task", "cancelled-owner", "--json")
        self.assertEqual("cancellation_planned", plan["status"])
        state = cli("task", "cancel", "--workspace", str(self.workspace), "--task", "cancelled-owner", "--confirm", "--json")
        self.assertEqual("cancelled", state["status"])
        self.assertFalse(state["scope_claimed"])
        error = cli("task", "revise", "--workspace", str(self.workspace), "--task", "cancelled-owner", "--json", expected=2)
        self.assertEqual("MDOC-TASK-TERMINAL", error["error"]["code"])

    def test_screenshot_copy_from_is_cli_owned_and_byte_identical(self) -> None:
        en = self.workspace / "Guide" / "en"
        (en / "Main").mkdir(parents=True)
        (en / "images").mkdir()
        (en / "Summary.md").write_text("# Guide\n", encoding="utf-8")
        cli("workspace", "revise", "--workspace", str(self.workspace), "--json")
        write_yaml(self.workspace / ".mdoc" / "workspace-draft.yaml", valid_workspace())
        cli("workspace", "apply", "--workspace", str(self.workspace), "--json")
        cli("workspace", "confirm", "--workspace", str(self.workspace), "--json")
        directory = self.define_simple_task("screenshot-copy")
        path = directory / "task-draft.yaml"
        draft = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
        draft["scope"]["locales"] = ["zh", "en"]
        draft["scope"]["assets"]["create"] = [
            {"locale": "zh", "path": "images/window.png"},
            {"locale": "en", "path": "images/window.png"},
        ]
        draft["locale_plan"] = {"source": "zh", "targets": {"en": {"content": "not_applicable", "screenshots": {"copy_from": "zh"}}}}
        draft["screenshots"] = [{"id": "WINDOW", "filename": "window.png", "locales": ["zh", "en"], "required": True, "destinations": {"zh": "images/window.png", "en": "images/window.png"}}]
        write_yaml(path, draft)
        cli("task", "define", "--workspace", str(self.workspace), "--task", "screenshot-copy", "--json")
        cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", "screenshot-copy", "--no-gui", "--json")
        source = directory / "captures" / "zh" / "window.png"
        source.parent.mkdir(parents=True)
        Image.new("RGB", (14, 9), "green").save(source)
        state = cli("task", "continue", "--workspace", str(self.workspace), "--task", "screenshot-copy", "--no-gui", "--json")
        self.assertEqual("waiting_for_screenshot_acceptance", state["status"])
        copied = directory / "captures" / "en" / "window.png"
        self.assertEqual(source.read_bytes(), copied.read_bytes())


if __name__ == "__main__":
    unittest.main()
