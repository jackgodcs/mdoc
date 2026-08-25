from __future__ import annotations

from .errors import MdocError
from .io import file_digest
from .paths import changes


def baselines(task) -> dict:
    return {
        f"{item['locale']}/{item['path']}": {"exists": formal.is_file(), "sha256": file_digest(formal) if formal.is_file() else None}
        for item, formal, _staged in changes(task)
    }


def plan(task, state: dict) -> dict:
    operations = []
    conflicts = []
    for item, formal, staged in changes(task):
        key = f"{item['locale']}/{item['path']}"
        baseline = state["baselines"].get(key)
        current = file_digest(formal) if formal.is_file() else None
        if baseline is None:
            conflicts.append({"target": key, "reason": "baseline_missing"})
        elif item["action"] == "create" and formal.exists():
            conflicts.append({"target": key, "reason": "create_target_exists"})
        elif item["action"] in {"update", "delete"} and current != baseline["sha256"]:
            conflicts.append({"target": key, "reason": "target_changed"})
        elif item["action"] == "delete" and not state["exception_approvals"].get(f"delete:{key}"):
            conflicts.append({"target": key, "reason": "deletion_not_approved"})
        operations.append({"action": item["action"], "target": key, "formal": str(formal), "staged": str(staged) if item["action"] != "delete" else None})
    if conflicts:
        raise MdocError("MDOC-PUBLISH-CONFLICT", "发布目标需要人工处理。", {"conflicts": conflicts})
    return {"schema_version": 1, "task_id": task.task_id, "revision": state["revision"] + 1, "operations": operations}
