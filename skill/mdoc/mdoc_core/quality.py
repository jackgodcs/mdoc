from __future__ import annotations

import time
from pathlib import Path

from .adapters import run_build
from .io import canonical_digest, file_digest
from .virtual_book import VirtualBook


def _build_blocking_count(build: dict) -> int:
    if build["status"] in {"not_requested", "passed"}:
        return 0
    return int(((build.get("pdf_check") or {}).get("counts") or {}).get("effective_errors") or 1)


def _task_file_digest(task) -> str:
    values = {}
    for item in task.definition["manifest"]:
        key = f"{item['locale']}/{item['path']}"
        if item["action"] == "delete":
            values[key] = "delete"
        else:
            path = task.directory / "staging" / item["locale"] / Path(*item["path"].replace(chr(92), "/").split("/"))
            values[key] = file_digest(path) if path.is_file() else None
    return canonical_digest(values)


def _review_states(task, state: dict, input_digest: str) -> dict:
    required = sorted(set(task.workspace.config["quality_gate"].get("required_reviews", ())) | set(task.definition["quality_gate"].get("required_reviews", ())))
    result = {}
    for name in required:
        review = state.get("reviews", {}).get(name)
        if not review:
            status = "waiting_for_review"
        elif review.get("status") != "human_accepted":
            status = review.get("status", "waiting_for_review")
        elif review.get("input_digest") != input_digest:
            status = "stale"
        else:
            status = "human_accepted"
        result[name] = {"status": status, "input_digest": review.get("input_digest") if review else None}
    return result


def _build(task, view: VirtualBook) -> dict:
    adapter_name = task.definition["quality_gate"].get("build_adapter") or view.book.get("release_build_adapter")
    if not task.workspace.config.get("build_adapters", {}).get(adapter_name or ""):
        return {"status": "not_requested", "adapter": adapter_name}
    return run_build(task.workspace, view, adapter_name, task.directory / "builds")


def task_check(task, state: dict, *, published: bool = False, skip_check: bool = False) -> dict:
    from mdoc_check.core import run as run_check, store as store_check

    profile = task.definition["quality_gate"]["profile"]
    if profile not in {"basic", "full"}:
        raise ValueError("旧任务使用 standard/release Quality Gate profile，必须重新创建任务。")
    automated = run_check(task.workspace.repository, None, None, "task", None, profile, False, task_id=task.task_id, skip_check=skip_check)
    store_check(automated, task.workspace.repository)
    input_digest = _task_file_digest(task)
    reviews = _review_states(task, state, input_digest)
    pending = [name for name, value in reviews.items() if value["status"] != "human_accepted"]
    view = VirtualBook.task(task, published=published)
    build = _build(task, view)
    blocking_count = int((automated.get("counts") or {}).get("effective_errors", 0)) + _build_blocking_count(build)
    if automated["status"] == "incomplete":
        blocking_count += 1
    status = "passed" if automated["status"] in {"passed", "skipped"} and blocking_count == 0 and not pending else "blocked"
    report = {
        "schema_version": 1, "context": "published-task" if published else "task", "task_id": task.task_id,
        "book": task.definition["task"]["book"], "profile": profile, "status": status, "input_digest": input_digest,
        "candidate_digest": view.digest(), "files_scanned": sum(len(unit.get("inputs", {}).get("markdown", [])) for unit in automated.get("units", [])),
        "findings": automated.get("findings", []), "blocking_count": blocking_count, "fixes": [], "reviews": reviews,
        "pending_reviews": pending, "build": build, "automated_check": {"status": automated["status"], "counts": automated.get("counts", {}), "path": automated.get("path")},
        "created_at": int(time.time()),
    }
    report["digest"] = canonical_digest({key: value for key, value in report.items() if key not in {"created_at", "path"}})
    report["path"] = automated.get("path")
    return report
