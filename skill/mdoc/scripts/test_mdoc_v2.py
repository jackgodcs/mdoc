from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from reportlab.pdfgen import canvas
from ruamel.yaml import YAML

import mdoc as mdoc_module
from mdoc_core import quality
from mdoc_core.config import load_task, load_workspace
from mdoc_core.state import load_state


SCRIPT = Path(__file__).with_name("mdoc.py")
YAML_WRITER = YAML()


def write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        YAML_WRITER.dump(value, stream)


class MdocV2IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = Path(self.temp.name) / "manual-repository"
        self.locale = self.repository / "Guide" / "en"
        (self.locale / "Main").mkdir(parents=True)
        (self.locale / "images").mkdir()
        (self.locale / "Summary.md").write_text("# Guide\n", encoding="utf-8")
        workspace = {
            "schema_version": 1,
            "workspace": {"id": "test-workspace", "formal_vcs": "none"},
            "product": {"id": "test-product", "display_name": "Test Product"},
            "books": {
                "guide": {
                    "root": "Guide",
                    "source_locale": "en",
                    "locales": {"en": {"root": "en", "language": "en"}},
                    "content_root": "Main",
                    "assets_root": "images",
                    "navigation": {"summary": "Summary.md"},
                }
            },
            "quality_gate": {
                "default_profile": "standard",
                "required_reviews": [],
                "safe_fixes": True,
                "require_summary_links": True,
                "forbidden_terms": [],
                "rules": [],
                "build_adapters": {},
            },
            "screenshots": {"auto_open_assistant": False},
            "publishing": {"atomic": True, "deletion_requires_confirmation": True, "allow_deletions": True},
        }
        draft = self.repository / "workspace-draft.yaml"
        write_yaml(draft, workspace)
        self.assertEqual("waiting_for_workspace_confirmation", self.cli("workspace", "init", "--repository", str(self.repository), "--draft", str(draft))[1]["status"])
        self.assertEqual("workspace_ready", self.cli("workspace", "apply", "--repository", str(self.repository), "--confirm")[1]["status"])
        write_yaml(self.repository / ".mdoc" / "workspace.local.yaml", {"schema_version": 1, "resources": {"spec": {"path": "spec.md"}}})
        self.workspace_draft = json.loads(json.dumps(workspace))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *arguments: str, expected: int = 0) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        self.assertEqual(expected, result.returncode, result.stdout or result.stderr)
        return result.returncode, json.loads(result.stdout)

    def task_definition(self, task_id: str, page: str, *, screenshots: bool = False, reviews: list[str] | None = None) -> dict:
        changes = [
            {"action": "create", "locale": "en", "path": page, "kind": "page", "evidence": ["spec"]},
            {"action": "update", "locale": "en", "path": "Summary.md", "kind": "navigation"},
        ]
        screenshot_items = []
        if screenshots:
            changes.append({"action": "create", "locale": "en", "path": "images/shot.png", "kind": "asset"})
            screenshot_items.append({"id": "MAIN-WINDOW", "filename": "shot.png", "locales": ["en"], "required": True, "destinations": {"en": "images/shot.png"}})
        return {
            "schema_version": 1,
            "task": {"id": task_id, "book": "guide", "intent": "add_feature", "title": task_id},
            "changes": changes,
            "locales": {"en": {"content": "rewrite", "screenshots": "rewrite" if screenshots else "not_applicable"}},
            "screenshots": screenshot_items,
            "evidence": [{"id": "spec", "kind": "official_document", "binding": "resources.spec", "supports": [f"en/{page}"], "required": True, "critical": True}],
            "quality_gate": {"profile": "full", "required_reviews": reviews or []},
        }

    def create(self, definition: dict) -> dict:
        draft = self.repository / f"{definition['task']['id']}-draft.yaml"
        write_yaml(draft, definition)
        return self.cli("task", "create", "--repository", str(self.repository), "--draft", str(draft), "--no-gui")[1]

    def action(self, task_id: str, action: str, *extra: str, expected: int = 0) -> dict:
        return self.cli("task", action, "--task", task_id, "--repository", str(self.repository), "--no-gui", *extra, expected=expected)[1]

    def stage_page_and_summary(self, task_id: str, page: str, text: str = "# New Page\n\nComplete content.\n") -> None:
        staging = self.repository / ".mdoc" / "tasks" / task_id / "staging" / "en"
        target = staging / page
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        (staging / "Summary.md").write_text(f"# Guide\n\n- [New page]({page})\n", encoding="utf-8")

    def test_task_continue_runs_one_gate_and_publishes_once(self) -> None:
        task_id, page = "new-page", "Main/NewPage.md"
        self.assertEqual("waiting_for_definition_confirmation", self.create(self.task_definition(task_id, page))["status"])
        self.assertEqual("waiting_for_authoring", self.action(task_id, "confirm-definition")["status"])
        self.stage_page_and_summary(task_id, page)
        state = self.action(task_id, "continue")
        self.assertEqual("ready_for_review", state["status"])
        self.assertTrue((self.locale / page).is_file())
        transactions = len(state["publish"]["transactions"])
        state = self.action(task_id, "continue")
        self.assertEqual(transactions, len(state["publish"]["transactions"]))
        self.assertEqual("accepted", self.action(task_id, "accept")["status"])

    def test_bad_gate_is_idempotent_and_reruns_after_staging_changes(self) -> None:
        task_id, page = "gate-retry", "Main/GateRetry.md"
        self.create(self.task_definition(task_id, page))
        self.action(task_id, "confirm-definition")
        self.stage_page_and_summary(task_id, page, "# Gate Retry\n\nTODO\n")
        first = self.action(task_id, "continue")
        self.assertEqual("quality_gate", first["waiting_on"]["kind"])
        report_root = self.repository / ".mdoc" / "quality-reports" / task_id
        reports_before = {path.name for path in report_root.glob("*.json")}
        second = self.action(task_id, "continue")
        self.assertEqual(first["quality_gate"]["digest"], second["quality_gate"]["digest"])
        self.assertEqual(reports_before, {path.name for path in report_root.glob("*.json")})
        self.stage_page_and_summary(task_id, page, "# Gate Retry\n\nResolved.\n")
        self.assertEqual("ready_for_review", self.action(task_id, "continue")["status"])

    def test_target_png_is_automatically_captured_and_acceptance_can_become_stale(self) -> None:
        task_id, page = "screenshot-task", "Main/Screenshot.md"
        self.create(self.task_definition(task_id, page, screenshots=True))
        self.assertEqual("waiting_for_screenshots", self.action(task_id, "confirm-definition")["status"])
        capture = self.repository / ".mdoc" / "tasks" / task_id / "captures" / "en" / "shot.png"
        capture.parent.mkdir(parents=True)
        Image.new("RGB", (8, 6), "red").save(capture)
        state = self.action(task_id, "continue")
        self.assertEqual("waiting_for_screenshot_acceptance", state["status"])
        self.assertEqual("captured", state["screenshots"]["MAIN-WINDOW:en"]["status"])
        self.assertEqual("waiting_for_authoring", self.action(task_id, "accept-screenshots")["status"])
        Image.new("RGB", (9, 6), "blue").save(capture)
        state = self.action(task_id, "continue")
        self.assertEqual("waiting_for_screenshot_acceptance", state["status"])
        self.assertEqual("stale", state["screenshot_acceptance"]["status"])

    def test_scope_claims_and_publish_baselines_pause_safely(self) -> None:
        page = "Main/Shared.md"
        self.create(self.task_definition("owner", page))
        self.action("owner", "confirm-definition")
        self.create(self.task_definition("contender", page))
        error = self.action("contender", "confirm-definition", expected=2)
        self.assertEqual("MDOC-SCOPE-CONFLICT", error["error"]["code"])
        self.action("owner", "cancel")
        self.action("contender", "cancel")

        task_id, delayed_page = "baseline", "Main/Baseline.md"
        self.create(self.task_definition(task_id, delayed_page, reviews=["factual_accuracy"]))
        self.action(task_id, "confirm-definition")
        self.stage_page_and_summary(task_id, delayed_page)
        self.assertEqual("quality_gate", self.action(task_id, "continue")["waiting_on"]["kind"])
        (self.locale / "Summary.md").write_text("# Externally changed\n", encoding="utf-8")
        state = self.action(task_id, "review", "--review", "factual_accuracy", "--status", "passed")
        self.assertEqual("publishing", state["waiting_on"]["kind"])
        self.assertEqual("waiting_for_resolution", state["status"])

    def test_standalone_quality_gate_is_advisory_unless_enforced(self) -> None:
        page = self.locale / "Main" / "Existing.md"
        page.write_text("# Existing\n\nTODO\n", encoding="utf-8")
        code, report = self.cli("quality", "check", "--repository", str(self.repository), "--book", "guide", "--path", "Main")
        self.assertEqual(0, code)
        self.assertEqual("blocked", report["status"])
        code, report = self.cli("quality", "check", "--repository", str(self.repository), "--book", "guide", "--path", "Main", "--enforce", expected=3)
        self.assertEqual(3, code)
        self.assertEqual(3, report["exit_code"])
        error = self.cli("quality", "check", "--repository", str(self.repository), "--book", "guide", "--changed", expected=2)[1]
        self.assertEqual("MDOC-QUALITY-SCOPE-INVALID", error["error"]["code"])

    def test_configuration_drift_pauses_task(self) -> None:
        task_id, page = "drift", "Main/Drift.md"
        self.create(self.task_definition(task_id, page))
        self.action(task_id, "confirm-definition")
        write_yaml(self.repository / ".mdoc" / "workspace.local.yaml", {"schema_version": 1, "resources": {"spec": {"path": "changed.md"}}})
        state = self.action(task_id, "continue")
        self.assertEqual("waiting_for_resolution", state["status"])
        self.assertEqual("workspace_changed", state["waiting_on"]["kind"])

    def test_out_of_scope_staging_file_pauses_for_resolution(self) -> None:
        task_id, page = "scope-extra", "Main/Scoped.md"
        self.create(self.task_definition(task_id, page))
        self.action(task_id, "confirm-definition")
        self.stage_page_and_summary(task_id, page)
        extra = self.repository / ".mdoc" / "tasks" / task_id / "staging" / "en" / "Main" / "Extra.md"
        extra.write_text("# Extra\n", encoding="utf-8")
        state = self.action(task_id, "continue")
        self.assertEqual("waiting_for_resolution", state["status"])
        self.assertEqual("authoring", state["waiting_on"]["kind"])
        self.assertEqual("MDOC-AUTHORING-OUT-OF-SCOPE", state["waiting_on"]["error"]["code"])

    def test_task_definition_drift_pauses_without_rewriting_snapshot(self) -> None:
        task_id, page = "definition-drift", "Main/DefinitionDrift.md"
        self.create(self.task_definition(task_id, page))
        self.action(task_id, "confirm-definition")
        task_path = self.repository / ".mdoc" / "tasks" / task_id / "task.yaml"
        changed = self.task_definition(task_id, page)
        changed["task"]["title"] = "Changed after confirmation"
        write_yaml(task_path, changed)
        state = self.action(task_id, "continue")
        self.assertEqual("waiting_for_resolution", state["status"])
        self.assertEqual("definition_changed", state["waiting_on"]["kind"])
        self.assertEqual(task_id, state["definition_snapshot"]["task"]["title"])

    def test_deletion_requires_exact_approval_then_publishes(self) -> None:
        task_id, page = "delete-page", "Main/DeleteMe.md"
        target = self.locale / page
        target.write_text("# Delete Me\n\nOld content.\n", encoding="utf-8")
        definition = {
            "schema_version": 1,
            "task": {"id": task_id, "book": "guide", "intent": "update_content", "title": "Delete a page"},
            "changes": [{"action": "delete", "locale": "en", "path": page, "kind": "page", "evidence": ["spec"]}],
            "locales": {"en": {"content": "rewrite", "screenshots": "not_applicable"}},
            "screenshots": [],
            "evidence": [{"id": "spec", "kind": "official_document", "binding": "resources.spec", "supports": [f"en/{page}"], "required": True, "critical": True}],
            "quality_gate": {"profile": "standard", "required_reviews": []},
        }
        self.create(definition)
        state = self.action(task_id, "confirm-definition")
        self.assertEqual("waiting_for_resolution", state["status"])
        self.assertEqual("publishing", state["waiting_on"]["kind"])
        error = self.action(task_id, "approve-deletion", "--target", "en/Main/Other.md", expected=2)
        self.assertEqual("MDOC-DELETION-TARGET-INVALID", error["error"]["code"])
        state = self.action(task_id, "approve-deletion", "--target", f"en/{page}")
        self.assertEqual("ready_for_review", state["status"])
        self.assertFalse(target.exists())

    def test_transaction_rolls_back_only_task_files_when_post_gate_fails(self) -> None:
        task_id, page = "rollback", "Main/Rollback.md"
        self.create(self.task_definition(task_id, page))
        self.action(task_id, "confirm-definition")
        self.stage_page_and_summary(task_id, page)
        original_summary = (self.locale / "Summary.md").read_text(encoding="utf-8")
        workspace = load_workspace(self.repository)
        task = load_task(workspace, task_id)
        state = load_state(task.directory / "task-state.json", task_id)

        def forced_gate(current_task, current_state, *, published=False):
            if published:
                return {"status": "blocked", "digest": "forced-post-publish-failure"}
            return quality.task_check(current_task, current_state, published=False)

        with mock.patch.object(mdoc_module, "quality_task", side_effect=forced_gate):
            result = mdoc_module.continue_task(task, state, no_gui=True)
        self.assertEqual("waiting_for_resolution", result["status"])
        self.assertEqual("publishing", result["waiting_on"]["kind"])
        self.assertFalse((self.locale / page).exists())
        self.assertEqual(original_summary, (self.locale / "Summary.md").read_text(encoding="utf-8"))
        transactions = list((task.directory / "publish-transactions").glob("*/transaction.json"))
        self.assertEqual(1, len(transactions))
        self.assertEqual("rolled_back", json.loads(transactions[0].read_text(encoding="utf-8"))["status"])

    def test_screenshot_task_accepts_and_publishes_captured_png(self) -> None:
        task_id, page = "screenshot-publish", "Main/ScreenshotPublish.md"
        self.create(self.task_definition(task_id, page, screenshots=True))
        self.action(task_id, "confirm-definition")
        capture = self.repository / ".mdoc" / "tasks" / task_id / "captures" / "en" / "shot.png"
        capture.parent.mkdir(parents=True)
        Image.new("RGB", (12, 7), "green").save(capture)
        self.action(task_id, "continue")
        self.action(task_id, "accept-screenshots")
        self.stage_page_and_summary(task_id, page, "# Screenshot Publish\n\n![Window](../images/shot.png)\n")
        state = self.action(task_id, "continue")
        self.assertEqual("ready_for_review", state["status"])
        self.assertEqual(capture.read_bytes(), (self.locale / "images" / "shot.png").read_bytes())

    def test_required_manual_review_blocks_until_passed(self) -> None:
        task_id, page = "manual-review", "Main/ManualReview.md"
        self.create(self.task_definition(task_id, page, reviews=["factual_accuracy"]))
        self.action(task_id, "confirm-definition")
        self.stage_page_and_summary(task_id, page)
        state = self.action(task_id, "continue")
        self.assertEqual("waiting_for_resolution", state["status"])
        self.assertEqual(["factual_accuracy"], json.loads((self.repository / ".mdoc" / "quality-reports" / task_id / "latest.json").read_text(encoding="utf-8"))["pending_reviews"])
        state = self.action(task_id, "review", "--review", "factual_accuracy", "--status", "failed")
        self.assertEqual("waiting_for_resolution", state["status"])
        state = self.action(task_id, "review", "--review", "factual_accuracy", "--status", "passed")
        self.assertEqual("ready_for_review", state["status"])

    def test_standalone_quality_locale_and_missing_path_are_explicit(self) -> None:
        page = self.locale / "Main" / "Locale.md"
        page.write_text("# Locale\n\nComplete.\n", encoding="utf-8")
        report = self.cli("quality", "check", "--repository", str(self.repository), "--book", "guide", "--locale", "en", "--path", "Main")[1]
        self.assertEqual("passed", report["status"])
        error = self.cli("quality", "check", "--repository", str(self.repository), "--book", "guide", "--locale", "en", "--path", "Missing", expected=2)[1]
        self.assertEqual("MDOC-QUALITY-SCOPE-INVALID", error["error"]["code"])

    def test_invalid_png_is_not_automatically_captured(self) -> None:
        task_id, page = "invalid-png", "Main/InvalidPng.md"
        self.create(self.task_definition(task_id, page, screenshots=True))
        self.action(task_id, "confirm-definition")
        capture = self.repository / ".mdoc" / "tasks" / task_id / "captures" / "en" / "shot.png"
        capture.parent.mkdir(parents=True)
        capture.write_bytes(b"not a png")
        state = self.action(task_id, "continue")
        self.assertEqual("waiting_for_screenshots", state["status"])
        self.assertEqual("pending", state["screenshots"]["MAIN-WINDOW:en"]["status"])

    def test_terminal_task_is_idempotent_but_cannot_be_reopened(self) -> None:
        task_id, page = "terminal", "Main/Terminal.md"
        self.create(self.task_definition(task_id, page))
        self.action(task_id, "confirm-definition")
        self.stage_page_and_summary(task_id, page)
        self.action(task_id, "continue")
        accepted = self.action(task_id, "accept")
        self.assertEqual("accepted", accepted["status"])
        self.assertEqual("accepted", self.action(task_id, "continue")["status"])
        error = self.action(task_id, "revise", expected=2)
        self.assertEqual("MDOC-REVISION-NOT-READY", error["error"]["code"])
        error = self.action(task_id, "cancel", expected=2)
        self.assertEqual("MDOC-TASK-TERMINAL", error["error"]["code"])

    def test_release_profile_builds_and_checks_a_real_pdf(self) -> None:
        source_pdf = self.repository / "source-manual.pdf"
        writer = canvas.Canvas(str(source_pdf))
        writer.setFont("Helvetica", 10)
        writer.drawString(40, 780, "A complete rendered manual page with readable text.")
        writer.drawString(40, 760, "The artifact includes content, layout, navigation, and release checks.")
        writer.drawString(40, 740, "It also contains reliable rendering, review evidence, and final validation.")
        writer.showPage()
        writer.save()
        build_script = self.repository / "build-manual.py"
        build_script.write_text(
            "import os, shutil\n"
            "root = os.path.dirname(__file__)\n"
            "shutil.copy2(os.path.join(root, 'source-manual.pdf'), os.path.join(root, 'Guide', 'manual.pdf'))\n",
            encoding="utf-8",
        )
        config = json.loads(json.dumps(self.workspace_draft))
        config["books"]["guide"]["release_build_adapter"] = "render-pdf"
        config["quality_gate"]["build_adapters"] = {
            "render-pdf": {"command": [sys.executable, str(build_script)], "artifact": "manual.pdf", "timeout_seconds": 60}
        }
        write_yaml(self.repository / ".mdoc" / "workspace.yaml", config)
        write_yaml(self.repository / ".mdoc" / "workspace.local.yaml", {"schema_version": 1, "resources": {"spec": {"path": "spec.md"}}})
        task_id, page = "release", "Main/Release.md"
        definition = self.task_definition(task_id, page)
        definition["quality_gate"] = {"profile": "release", "required_reviews": [], "build_adapter": "render-pdf"}
        self.create(definition)
        self.action(task_id, "confirm-definition")
        self.stage_page_and_summary(task_id, page)
        state = self.action(task_id, "continue")
        task_report = json.loads((self.repository / ".mdoc" / "quality-reports" / task_id / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual("ready_for_review", state["status"])
        self.assertTrue((self.repository / "Guide" / "manual.pdf").is_file())
        self.assertEqual("passed", task_report["build"]["pdf_check"]["status"])
        report = self.cli("quality", "check", "--repository", str(self.repository), "--book", "guide", "--profile", "release")[1]
        self.assertEqual("passed", report["status"])
        self.assertEqual("passed", report["build"]["pdf_check"]["status"])

if __name__ == "__main__":
    unittest.main()
