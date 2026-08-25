from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from . import claims, screenshots
from .adapters import import_generator_outputs
from .authoring import prepare as prepare_authoring, submit as submit_authoring
from .config import load_task, load_workspace
from .errors import MdocError
from .io import canonical_digest, file_digest, write_json_atomic
from .locking import book_publish_lock, task_lock
from .models import thaw
from .paths import changes
from .publishing import baselines, plan as publish_plan
from .quality import task_check
from .state import TERMINAL, load_state, save_state, transition
from .transactions import execute as execute_transaction, recover as recover_transactions


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


def load(workspace_path: Path, task_id: str):
    workspace = load_workspace(workspace_path)
    task = load_task(workspace, task_id)
    state_path = task.directory / "task-state.json"
    return task, state_path, load_state(state_path, task_id)


def launch_screenshot_assistant(task) -> None:
    subprocess.Popen(
        [sys.executable, str(SCRIPT_DIR / "screenshot_assistant.py"), "--workspace", str(task.workspace.repository), "--task", task.task_id],
        cwd=task.workspace.repository,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _current_files(task) -> dict:
    return {
        f"{item['locale']}/{item['path']}": file_digest(staged)
        for item, _formal, staged in changes(task)
        if item["action"] != "delete" and staged.is_file()
    }


def continue_task(task, state: dict, *, no_gui: bool = False, quality_check=task_check) -> dict:
    recover_transactions(task, state)
    if state["status"] in TERMINAL or state["status"] == "ready_for_review":
        return state
    confirmation = state.get("definition_confirmation")
    if not confirmation:
        if state.get("scope_claimed"):
            claims.release(task)
            state["scope_claimed"] = False
        return transition(state, "waiting_for_definition_confirmation", "definition_requires_confirmation", {"kind": "definition_confirmation", "digest": task.digest})
    if confirmation["digest"] != task.digest:
        return transition(state, "waiting_for_resolution", "definition_changed", {"kind": "definition_changed"})
    if confirmation.get("workspace_digest") != task.workspace.digest:
        return transition(state, "waiting_for_resolution", "workspace_changed", {"kind": "workspace_changed", "expected": confirmation.get("workspace_digest"), "actual": task.workspace.digest})
    if not state.get("scope_claimed"):
        claims.claim(task)
        state["scope_claimed"] = True
        state["baselines"] = baselines(task)
    ready, screenshot_manifest = screenshots.readiness(task, state)
    if (state.get("screenshot_acceptance") or {}).get("status") == "stale":
        state["authoring_submission"] = None
        state["quality_gate"] = None
    if task.definition["screenshots"]:
        if not ready:
            waiting = transition(state, "waiting_for_screenshots", "screenshots_incomplete", {"kind": "screenshots", "blockers": screenshot_manifest["blockers"]})
            if not no_gui and task.workspace.config["screenshots"].get("auto_open_assistant"):
                launch_screenshot_assistant(task)
            return waiting
        acceptance = state.get("screenshot_acceptance")
        if not acceptance or acceptance.get("status") != "accepted":
            return transition(state, "waiting_for_screenshot_acceptance", "screenshots_require_acceptance", {"kind": "screenshot_acceptance", "manifest_digest": screenshot_manifest["digest"]})
    request_path = task.directory / "authoring-request.json"
    if not state.get("authoring_submission"):
        if not request_path.is_file():
            prepare_authoring(task)
        try:
            submit_authoring(task, state)
        except MdocError as exc:
            kind = "authoring_incomplete" if exc.code == "MDOC-AUTHORING-INCOMPLETE" else "authoring_scope_blocked"
            status = "waiting_for_authoring" if exc.code == "MDOC-AUTHORING-INCOMPLETE" else "waiting_for_resolution"
            return transition(state, status, kind, {"kind": "authoring", "request": str(request_path), "error": exc.payload()["error"]})
    current_files = _current_files(task)
    expected_count = sum(item["action"] != "delete" for item in task.definition["manifest"])
    if len(current_files) != expected_count:
        return transition(state, "waiting_for_authoring", "authoring_incomplete", {"kind": "authoring", "request": str(request_path)})
    if state["authoring_submission"].get("files") != current_files:
        submit_authoring(task, state)
    gate_input = canonical_digest({"files": current_files, "reviews": state.get("reviews", {}), "profile": task.definition["quality_gate"]})
    previous = state.get("quality_gate") or {}
    if state["status"] == "waiting_for_resolution" and (state.get("waiting_on") or {}).get("kind") == "quality_gate_findings" and previous.get("input_digest") == gate_input:
        return state
    if previous.get("status") != "passed" or previous.get("input_digest") != gate_input:
        transition(state, "verifying", "quality_gate_started")
        report = quality_check(task, state)
        state["quality_gate"] = {"status": report["status"], "digest": report["digest"], "input_digest": gate_input}
        if report["status"] != "passed":
            return transition(state, "waiting_for_resolution", "quality_gate_blocked", {"kind": "quality_gate_findings", "report": report["path"], "blocking_count": report.get("blocking_count", 0)})
    transition(state, "publishing", "quality_gate_passed")
    try:
        plan = publish_plan(task, state)
        write_json_atomic(task.directory / "reports" / f"publish-plan-r{plan['revision']}.json", plan)
        with book_publish_lock(task):
            execute_transaction(task, state, plan, lambda: quality_check(task, state, published=True))
    except MdocError as exc:
        if exc.code == "MDOC-PUBLISH-CONFLICT":
            state["quality_gate"] = None
        return transition(state, "waiting_for_resolution", "publishing_paused", {"kind": "publishing", "error": exc.payload()["error"]})
    except Exception as exc:
        error = MdocError("MDOC-PUBLISH-FAILED", "发布失败，本事务已回滚。", {"cause": str(exc)})
        return transition(state, "waiting_for_resolution", "publishing_paused", {"kind": "publishing", "error": error.payload()["error"]})
    state["revision"] += 1
    return transition(state, "ready_for_review", "published_and_verified", {"kind": "final_acceptance", "revision": state["revision"]})


def act(workspace_path: Path, task_id: str, action: str, *, no_gui: bool = False, item: str | None = None, screenshot_status: str | None = None, review: str | None = None, review_status: str | None = None, target: str | None = None, confirmed: bool = False) -> dict:
    task, state_path, state = load(workspace_path, task_id)
    if action == "status":
        screenshots.synchronize(task, state)
        return state
    with task_lock(task):
        if action == "continue":
            continue_task(task, state, no_gui=no_gui)
        elif action == "confirm-definition":
            if state["status"] != "waiting_for_definition_confirmation" or state.get("definition_confirmation"):
                raise MdocError("MDOC-DEFINITION-NOT-EDITABLE", "当前状态不能确认任务定义。")
            claims.claim(task)
            state["scope_claimed"] = True
            state["definition_confirmation"] = {"digest": task.digest, "workspace_digest": task.workspace.digest, "at": int(time.time())}
            state["definition_snapshot"] = thaw(task.definition)
            state["baselines"] = baselines(task)
            import_generator_outputs(task)
            transition(state, "draft", "definition_confirmed")
            continue_task(task, state, no_gui=no_gui)
        elif action == "accept-screenshots":
            if state["status"] not in {"waiting_for_screenshots", "waiting_for_screenshot_acceptance"}:
                raise MdocError("MDOC-SCREENSHOT-ACCEPTANCE-NOT-READY", "任务尚未进入截图验收。")
            screenshots.accept(task, state)
            continue_task(task, state, no_gui=no_gui)
        elif action == "screenshot-status":
            screenshots.set_status(task, state, item or "", screenshot_status or "")
            continue_task(task, state, no_gui=no_gui)
        elif action == "submit-authoring":
            submit_authoring(task, state)
            state["quality_gate"] = None
            continue_task(task, state, no_gui=no_gui)
        elif action == "review":
            state["reviews"][review] = {"status": review_status, "at": int(time.time()), "input_digest": canonical_digest(_current_files(task))}
            state["quality_gate"] = None
            continue_task(task, state, no_gui=no_gui)
        elif action == "approve-deletion":
            declared = {f"{entry['locale']}/{entry['path']}" for entry in task.definition["manifest"] if entry["action"] == "delete"}
            if target not in declared:
                raise MdocError("MDOC-DELETION-TARGET-INVALID", "删除确认必须精确指向 manifest 中的 delete 对象。", {"target": target})
            state["exception_approvals"][f"delete:{target}"] = True
            continue_task(task, state, no_gui=no_gui)
        elif action == "confirm-final":
            if state["status"] != "ready_for_review":
                raise MdocError("MDOC-FINAL-ACCEPTANCE-NOT-READY", "任务尚未准备好最终验收。")
            transition(state, "accepted", "user_accepted_final_manual")
            if state.get("scope_claimed"):
                claims.release(task)
                state["scope_claimed"] = False
        elif action == "cancel":
            if not confirmed:
                return {"status": "cancellation_planned", "task_id": task_id, "published_revisions": state["revision"], "manifest": thaw(task.definition["manifest"])}
            if state["status"] in TERMINAL:
                raise MdocError("MDOC-TASK-TERMINAL", "终态任务不能取消。")
            transition(state, "cancelled", "user_cancelled_task")
            if state.get("scope_claimed"):
                claims.release(task)
                state["scope_claimed"] = False
        elif action == "revise-output":
            if state["status"] != "ready_for_review":
                raise MdocError("MDOC-REVISION-NOT-READY", "任务尚未准备好输出修订。")
            state["authoring_submission"] = None
            state["quality_gate"] = None
            state["reviews"] = {}
            state["baselines"] = baselines(task)
            prepare_authoring(task)
            transition(state, "waiting_for_authoring", "revision_requested", {"kind": "authoring", "request": str(task.directory / "authoring-request.json")})
        save_state(state_path, state)
    return state
