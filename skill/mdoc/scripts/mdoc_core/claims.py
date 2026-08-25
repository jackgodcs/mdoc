from __future__ import annotations

import os
from contextlib import contextmanager

from .errors import MdocError
from .io import read_json, write_json_atomic


@contextmanager
def _lock(workspace):
    path = workspace.control / "cache" / "scope-claims.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise MdocError("MDOC-SCOPE-LOCKED", "Scope claims are being updated. Retry the command.") from exc
    os.close(handle)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _targets(task) -> list[str]:
    book = task.definition["task"]["book"]
    return sorted(f"{book}/{item['locale']}/{item['path'].replace(chr(92), '/')}" for item in task.definition["changes"])


def claim(task) -> None:
    path = task.workspace.control / "cache" / "scope-claims.json"
    with _lock(task.workspace):
        data = read_json(path) if path.is_file() else {"schema_version": 1, "claims": {}}
        claims = data.setdefault("claims", {})
        conflicts = [{"target": target, "task": claims[target]} for target in _targets(task) if target in claims and claims[target] != task.task_id]
        if conflicts:
            raise MdocError("MDOC-SCOPE-CONFLICT", "Another active task owns part of this file scope.", {"conflicts": conflicts})
        for target in _targets(task):
            claims[target] = task.task_id
        write_json_atomic(path, data)


def release(task) -> None:
    path = task.workspace.control / "cache" / "scope-claims.json"
    with _lock(task.workspace):
        data = read_json(path) if path.is_file() else {"schema_version": 1, "claims": {}}
        data["claims"] = {key: value for key, value in data.get("claims", {}).items() if value != task.task_id}
        write_json_atomic(path, data)
