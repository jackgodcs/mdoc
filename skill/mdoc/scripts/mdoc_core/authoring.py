from __future__ import annotations

import shutil
import time

from .errors import MdocError
from .io import canonical_digest, file_digest, write_json_atomic
from .models import thaw
from .paths import changes


def prepare(task) -> dict:
    files = []
    for change, formal, staged in changes(task):
        if change["action"] == "update" and not staged.exists():
            if not formal.is_file():
                raise MdocError("MDOC-UPDATE-TARGET-MISSING", f"Update target is missing: {change['locale']}/{change['path']}")
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(formal, staged)
        files.append({
            "action": change["action"], "locale": change["locale"], "path": change["path"],
            "kind": change["kind"], "staging_path": str(staged),
        })
    local = task.workspace.local
    request = {"schema_version": 1, "task_id": task.task_id, "definition_digest": task.digest, "files": files, "evidence": [{"id": item["id"], "kind": item["kind"], "binding": item["binding"], "value": thaw(local.get(item["binding"].split(".", 1)[0], {}).get(item["binding"].split(".", 1)[1])), "supports": list(item["supports"]), "critical": item["critical"]} for item in task.definition["evidence"]]}
    write_json_atomic(task.directory / "authoring-request.json", request)
    return request


def submit(task, state: dict) -> dict:
    expected, missing = {}, []
    for change, _formal, staged in changes(task):
        if change["action"] == "delete":
            continue
        if not staged.is_file():
            missing.append(f"{change['locale']}/{change['path']}")
        else:
            expected[f"{change['locale']}/{change['path']}"] = file_digest(staged)
    if missing:
        raise MdocError("MDOC-AUTHORING-INCOMPLETE", "Declared staging files are missing.", {"files": missing})
    staging = task.directory / "staging"
    allowed = {str(staged.resolve()) for change, _formal, staged in changes(task) if change["action"] != "delete"}
    extras = [str(path) for path in staging.rglob("*") if path.is_file() and str(path.resolve()) not in allowed]
    if extras:
        raise MdocError("MDOC-AUTHORING-OUT-OF-SCOPE", "Staging contains files outside the frozen manifest.", {"files": extras})
    submission = {"at": int(time.time()), "files": expected, "digest": canonical_digest(expected)}
    state["authoring_submission"] = submission
    return submission
