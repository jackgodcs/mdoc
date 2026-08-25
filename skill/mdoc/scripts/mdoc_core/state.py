from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import MdocError
from .io import read_json, write_json_atomic
from .config import validate_schema


TERMINAL = {"accepted", "cancelled"}
STATUSES = {
    "draft", "waiting_for_definition_confirmation", "waiting_for_screenshots",
    "waiting_for_screenshot_acceptance", "waiting_for_authoring", "verifying",
    "waiting_for_resolution", "publishing", "ready_for_review", "accepted", "cancelled",
}


def initial_state(task_id: str) -> dict:
    return {
        "schema_version": 1,
        "task_id": task_id,
        "status": "draft",
        "revision": 0,
        "definition_confirmation": None,
        "definition_snapshot": None,
        "screenshots": {},
        "screenshot_acceptance": None,
        "authoring_submission": None,
        "quality_gate": None,
        "baselines": {},
        "exception_approvals": {},
        "scope_claimed": False,
        "publish": {"transactions": []},
        "waiting_on": None,
        "reviews": {},
        "history": [],
    }


def load_state(path: Path, task_id: str) -> dict:
    if not path.is_file():
        return initial_state(task_id)
    state = read_json(path)
    if state.get("task_id") != task_id:
        raise MdocError("MDOC-TASK-STATE-INVALID", f"Invalid machine state: {path}")
    validate_schema(state, "state.schema.json", "task-state.json")
    return state


def save_state(path: Path, state: dict) -> None:
    validate_schema(state, "state.schema.json", "task-state.json")
    write_json_atomic(path, state)


def transition(state: dict, status: str, reason: str, waiting_on: dict | None = None) -> dict:
    if status not in STATUSES:
        raise MdocError("MDOC-TASK-STATE-INVALID", f"Unknown task status: {status}")
    previous = state["status"]
    if previous == status and state.get("waiting_on") == waiting_on:
        return state
    state["status"] = status
    state["waiting_on"] = waiting_on
    state["history"].append({"at": int(time.time()), "from": previous, "to": status, "reason": reason})
    return state


@contextmanager
def task_lock(task_dir: Path):
    lock = task_dir / ".task.lock"
    task_dir.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise MdocError("MDOC-TASK-LOCKED", f"Task is already being modified: {task_dir.name}") from exc
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def book_publish_lock(control: Path, book_id: str):
    lock = control / "cache" / f"publish-{book_id}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise MdocError("MDOC-BOOK-PUBLISH-LOCKED", f"Another task is publishing book: {book_id}") from exc
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)
