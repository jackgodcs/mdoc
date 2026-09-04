from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML

from skill.mdoc.tests.support import cli, write_yaml
from skill.mdoc.tests.test_workspace_cli import valid_workspace


class QualityCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "manual"
        self.locale = self.workspace / "Guide" / "zh"
        (self.locale / "Main").mkdir(parents=True)
        (self.locale / "images").mkdir()
        (self.locale / "Summary.md").write_text("# Guide\n\n- [Existing](Main/Existing.md)\n", encoding="utf-8")
        (self.locale / "Main" / "Existing.md").write_text("# Existing\n\nComplete.\n", encoding="utf-8")
        config = valid_workspace()
        config["books"]["guide"]["locales"] = {"zh": {"root": "zh", "language": "zh"}}
        config["quality_gate"]["require_summary_links"] = True
        cli("workspace", "init", "--workspace", str(self.workspace), "--json")
        write_yaml(self.workspace / ".mdoc" / "workspace-draft.yaml", config)
        cli("workspace", "apply", "--workspace", str(self.workspace), "--json")
        cli("workspace", "confirm", "--workspace", str(self.workspace), "--json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def define_task(self, task_id: str, *, profile: str = "standard", reviews: list[str] | None = None, build_adapter: str | None = None) -> Path:
        cli("task", "create", "--workspace", str(self.workspace), "--task", task_id, "--book", "guide", "--intent", "add_feature", "--json")
        directory = self.workspace / ".mdoc" / "tasks" / task_id
        path = directory / "task-draft.yaml"
        draft = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
        draft["task"]["title"] = "Quality task"
        draft["scope"] = {
            "locales": ["zh"],
            "pages": {"create": [{"locale": "zh", "path": f"Main/{task_id}.md", "evidence": ["spec"]}], "update": [], "delete": []},
            "assets": {"create": [], "update": [], "delete": []},
            "navigation": {"update": [{"locale": "zh", "path": "Summary.md"}]},
        }
        draft["locale_plan"] = {"source": "zh", "targets": {}}
        draft["evidence"] = [{"id": "spec", "kind": "official_document", "location": "local:spec", "supports": [f"zh/Main/{task_id}.md"], "required": False, "critical": True}]
        draft["quality_gate"] = {"profile": profile, "required_reviews": reviews or []}
        if build_adapter:
            draft["quality_gate"]["build_adapter"] = build_adapter
        write_yaml(path, draft)
        cli("task", "define", "--workspace", str(self.workspace), "--task", task_id, "--json")
        cli("task", "confirm-definition", "--workspace", str(self.workspace), "--task", task_id, "--no-gui", "--json")
        return directory

    def stage(self, directory: Path, task_id: str, page: str) -> None:
        staging = directory / "staging" / "zh"
        (staging / "Main").mkdir(parents=True, exist_ok=True)
        (staging / "Main" / f"{task_id}.md").write_text(page, encoding="utf-8")
        (staging / "Summary.md").write_text(
            f"# Guide\n\n- [Existing](Main/Existing.md)\n- [New](Main/{task_id}.md)\n", encoding="utf-8"
        )

    def test_book_check_is_advisory_unless_enforced_and_preserves_history(self) -> None:
        bad = self.locale / "Main" / "Bad.md"
        bad.write_text("TODO\n", encoding="utf-8")
        report = cli("quality", "check", "--workspace", str(self.workspace), "--book", "guide", "--json")
        self.assertEqual("blocked", report["status"])
        self.assertTrue(Path(report["path"]).is_file())
        enforced = cli("quality", "check", "--workspace", str(self.workspace), "--book", "guide", "--enforce", "--json", expected=3)
        self.assertEqual("blocked", enforced["status"])
        history = list((self.workspace / ".mdoc" / "quality-reports" / "books" / "guide").glob("*-book.json"))
        self.assertEqual(2, len(history))

    def test_task_exact_finding_waits_for_resolution_and_recovers(self) -> None:
        directory = self.define_task("blocked")
        self.stage(directory, "blocked", "TODO\n")
        state = cli("task", "submit-authoring", "--workspace", str(self.workspace), "--task", "blocked", "--no-gui", "--json")
        self.assertEqual("waiting_for_resolution", state["status"])
        self.assertEqual("quality_gate_findings", state["waiting_on"]["kind"])
        self.assertTrue(Path(state["waiting_on"]["report"]).is_file())
        self.assertFalse((self.locale / "Main" / "blocked.md").exists())
        self.stage(directory, "blocked", "# Fixed\n\nComplete.\n")
        state = cli("task", "continue", "--workspace", str(self.workspace), "--task", "blocked", "--no-gui", "--json")
        self.assertEqual("ready_for_review", state["status"])

    def test_safe_fix_only_adds_final_newline_to_staging(self) -> None:
        directory = self.define_task("safe-fix")
        self.stage(directory, "safe-fix", "# Safe\n\nLine without newline")
        staged = directory / "staging" / "zh" / "Main" / "safe-fix.md"
        report = cli("quality", "check", "--workspace", str(self.workspace), "--task", "safe-fix", "--json")
        self.assertTrue(staged.read_bytes().endswith(b"\n"))
        self.assertFalse((self.locale / "Main" / "safe-fix.md").exists())
        self.assertEqual("final-newline", report["fixes"][0]["kind"])

    def test_review_acceptance_becomes_stale_when_candidate_changes(self) -> None:
        directory = self.define_task("reviewed", profile="full", reviews=["factual_accuracy"])
        self.stage(directory, "reviewed", "# Reviewed\n\nFirst.\n")
        state = cli("task", "submit-authoring", "--workspace", str(self.workspace), "--task", "reviewed", "--no-gui", "--json")
        self.assertEqual("waiting_for_resolution", state["status"])
        self.assertEqual("waiting_for_review", state["quality_gate"]["reviews"]["factual_accuracy"]["status"])
        state = cli("task", "review", "--workspace", str(self.workspace), "--task", "reviewed", "--review", "factual_accuracy", "--status", "human_accepted", "--no-gui", "--json")
        self.assertEqual("ready_for_review", state["status"])
        cli("task", "revise-output", "--workspace", str(self.workspace), "--task", "reviewed", "--no-gui", "--json")
        self.stage(directory, "reviewed", "# Reviewed\n\nSecond.\n")
        state = cli("task", "submit-authoring", "--workspace", str(self.workspace), "--task", "reviewed", "--no-gui", "--json")
        self.assertEqual("waiting_for_resolution", state["status"])
        self.assertEqual("stale", state["quality_gate"]["reviews"]["factual_accuracy"]["status"])

    def test_release_without_registered_build_is_blocked(self) -> None:
        directory = self.define_task("release", profile="release")
        self.stage(directory, "release", "# Release\n\nComplete.\n")
        state = cli("task", "submit-authoring", "--workspace", str(self.workspace), "--task", "release", "--no-gui", "--json")
        self.assertEqual("waiting_for_resolution", state["status"])
        self.assertEqual("not_configured", state["quality_gate"]["build"]["status"])

    def test_release_build_reads_candidate_and_writes_isolated_artifact(self) -> None:
        script = self.workspace / "tools" / "build.py"
        script.parent.mkdir()
        script.write_text(
            "import os\n"
            "from pathlib import Path\n"
            "candidate = Path('zh/Main/release-build.md').read_text(encoding='utf-8')\n"
            "target = Path(os.environ['MDOC_ARTIFACT_DIR']) / 'build.txt'\n"
            "target.write_text(candidate, encoding='utf-8')\n",
            encoding="utf-8",
        )
        cli("workspace", "revise", "--workspace", str(self.workspace), "--json")
        config_path = self.workspace / ".mdoc" / "workspace-draft.yaml"
        config = YAML(typ="safe").load(config_path.read_text(encoding="utf-8"))
        config["build_adapters"] = {"candidate": {"command": ["runtime:python", "tools/build.py"], "artifact": "build.txt", "timeout_seconds": 30}}
        write_yaml(config_path, config)
        cli("workspace", "apply", "--workspace", str(self.workspace), "--json")
        cli("workspace", "confirm", "--workspace", str(self.workspace), "--json")
        cli("workspace", "local", "init", "--workspace", str(self.workspace), "--json")
        local = {"schema_version": 1, "applications": {}, "resources": {}, "runtimes": {"python": {"executable": str(Path(__import__('sys').executable))}}}
        write_yaml(self.workspace / ".mdoc" / "workspace.local-draft.yaml", local)
        cli("workspace", "local", "apply", "--workspace", str(self.workspace), "--json")
        cli("workspace", "local", "confirm", "--workspace", str(self.workspace), "--json")

        directory = self.define_task("release-build", profile="release", build_adapter="candidate")
        self.stage(directory, "release-build", "# Candidate\n\nOnly in staging.\n")
        self.assertFalse((self.locale / "Main" / "release-build.md").exists())
        state = cli("task", "submit-authoring", "--workspace", str(self.workspace), "--task", "release-build", "--no-gui", "--json")
        self.assertEqual("ready_for_review", state["status"])
        build = state["quality_gate"]["build"]
        self.assertEqual("passed", build["status"])
        self.assertTrue(build["artifact_sha256"])
        self.assertEqual("# Candidate\n\nOnly in staging.\n", Path(build["artifact"]).read_text(encoding="utf-8"))

    def test_release_build_adapter_rejects_the_removed_pdf_artifact_kind(self) -> None:
        cli("workspace", "revise", "--workspace", str(self.workspace), "--json")
        config_path = self.workspace / ".mdoc" / "workspace-draft.yaml"
        config = YAML(typ="safe").load(config_path.read_text(encoding="utf-8"))
        config["build_adapters"] = {
            "pdf": {
                "command": ["runtime:python", "tools/build_pdf.py"],
                "artifact": "manual.pdf",
                "artifact_kind": "pdf",
                "locale": "zh",
                "timeout_seconds": 30,
            }
        }
        write_yaml(config_path, config)
        result = cli("workspace", "apply", "--workspace", str(self.workspace), "--json", expected=2)
        self.assertEqual("MDOC-CONFIG-SCHEMA-INVALID", result["error"]["code"])


if __name__ == "__main__":
    unittest.main()
