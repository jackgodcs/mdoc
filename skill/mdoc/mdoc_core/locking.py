from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .errors import MdocError


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _acquire(path: Path, code: str, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {"schema_version": 1, "pid": os.getpid(), "created_at": int(time.time())}
    for _attempt in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
                stale = not _alive(int(current.get("pid", 0)))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                stale = False
            if stale:
                path.unlink(missing_ok=True)
                continue
            raise MdocError(code, message, {"lock": str(path)})
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(metadata, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        return
    raise MdocError(code, message, {"lock": str(path)})


def _metadata(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not _alive(int(value.get("pid", 0))):
            path.unlink(missing_ok=True)
            return None
        return value
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


@contextmanager
def operation_lock(workspace: Path, operation: str, target: dict, conflicts):
    root = workspace.resolve() / ".mdoc" / "cache" / "locks"
    coordinator = root / ".coordinator.lock"
    _acquire(coordinator, "MDOC-OPERATION-LOCK-COORDINATOR", "另一个 mdoc 操作正在登记目标，请立即重试。")
    lock = root / f"operation-{uuid.uuid4().hex}.lock"
    try:
        running = []
        for path in root.glob("operation-*.lock"):
            current = _metadata(path)
            if current and conflicts(current.get("operation"), current.get("target") or {}):
                running.append({"operation": current.get("operation"), "target": current.get("target"), "started_at": current.get("started_at"), "pid": current.get("pid")})
        if running:
            raise MdocError("MDOC-OPERATION-TARGET-LOCKED", "相同或上级目标正在执行，当前命令未启动。", {"requested": {"operation": operation, "target": target}, "running": running})
        lock.parent.mkdir(parents=True, exist_ok=True)
        metadata = {"schema_version": 1, "pid": os.getpid(), "started_at": int(time.time()), "operation": operation, "target": target}
        with lock.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(metadata, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
    finally:
        coordinator.unlink(missing_ok=True)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


@contextmanager
def task_lock(task):
    path = task.workspace.control / "locks" / f"task-{task.task_id}.lock"
    _acquire(path, "MDOC-TASK-LOCKED", f"任务正在被另一个进程修改：{task.task_id}")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


@contextmanager
def book_publish_lock(task):
    book_id = task.definition["task"]["book"]
    path = task.workspace.control / "locks" / f"book-{book_id}.lock"
    _acquire(path, "MDOC-BOOK-PUBLISH-LOCKED", f"书册正在由另一个任务发布：{book_id}")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
