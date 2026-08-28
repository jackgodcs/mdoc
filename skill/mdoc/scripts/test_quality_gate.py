"""Regression checks for task Quality Gate baseline suppression."""
from __future__ import annotations

import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))

from mdoc_core.quality import _classify, _task_blocking, finding
from mdoc_core.task import _quality_check_needed


def test_pre_existing_active_findings_do_not_block_a_task() -> None:
    findings = [
        finding("markdown.single-h1", "error", "exact", "en/Main/legacy.md", "Missing H1."),
        finding("content.no-placeholder", "error", "exact", "en/Main/changed.md", "Placeholder found."),
        finding("markdown.heading-order", "warning", "exact", "en/Main/legacy.md", "Heading skipped.", required=False),
    ]

    _classify(findings, {"en/Main/changed.md"})

    assert findings[0]["classification"] == "pre_existing"
    assert findings[0]["suppression"] == "active"
    assert findings[1]["classification"] == "introduced"
    assert findings[1]["suppression"] == "inactive"
    assert _task_blocking(findings) == [findings[1]]


def test_pre_existing_findings_without_active_suppression_still_block() -> None:
    item = finding("content.no-placeholder", "error", "exact", "en/Main/legacy.md", "Placeholder found.")
    item.update({"classification": "pre_existing", "suppression": "inactive"})

    assert _task_blocking([item]) == [item]


def test_continue_retries_a_previously_blocked_quality_gate() -> None:
    assert _quality_check_needed({"status": "blocked", "input_digest": "same"}, "same")
    assert _quality_check_needed({"status": "passed", "input_digest": "old"}, "new")
    assert not _quality_check_needed({"status": "passed", "input_digest": "same"}, "same")


if __name__ == "__main__":
    test_pre_existing_active_findings_do_not_block_a_task()
    test_pre_existing_findings_without_active_suppression_still_block()
    test_continue_retries_a_previously_blocked_quality_gate()
    print("quality gate regression checks passed")
