from __future__ import annotations

import subprocess
import sys
import time
import copy
import os
from pathlib import Path

from . import claims, screenshots
from .adapters import import_generator_outputs
from .authoring import prepare as prepare_authoring, preserved_staging_files, submit as submit_authoring
from .config import load_task, load_workspace
from .errors import MdocError
from .io import canonical_digest, file_digest, relative_path, write_json_atomic, write_yaml_atomic
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


def launch_screenshot_assistant(task, *, contributor: bool = False) -> None:
    command = [sys.executable, str(SCRIPT_DIR / "screenshot_assistant.py"), "--workspace", str(task.workspace.repository), "--task", task.task_id]
    if contributor:
        command.append("--contributor")
    subprocess.Popen(
        command,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _current_files(task) -> dict:
    return {
        f"{item['locale']}/{item['path']}": file_digest(staged)
        for item, _formal, staged in changes(task)
        if item["action"] != "delete" and staged.is_file()
    }


def _quality_check_needed(previous: dict, gate_input: str) -> bool:
    return previous.get("status") != "passed" or previous.get("input_digest") != gate_input


def _create_screenshot_launcher(task, name: str, *, contributor: bool) -> Path:
    relative = relative_path(name, "screenshot launcher output")
    if len(relative.parts) != 1 or relative.suffix.lower() != ".cmd":
        raise MdocError("MDOC-SCREENSHOT-LAUNCHER-INVALID", "截图启动器必须是工作区根目录下的 .cmd 文件。")
    target = task.workspace.repository / relative
    contributor_argument = " --contributor" if contributor else ""
    content = (
        "@echo off\r\n"
        "setlocal\r\n"
        "pushd \"%~dp0\" >nul 2>&1\r\n"
        "if errorlevel 1 (\r\n"
        "  echo Unable to access the shared mdoc workspace.\r\n"
        "  pause\r\n"
        "  exit /b 2\r\n"
        ")\r\n"
        "set \"MDOC_WORKSPACE=%CD%\\.\"\r\n"
        "set \"MDOC_PYTHON=%LOCALAPPDATA%\\mdoc\\runtime\\Scripts\\python.exe\"\r\n"
        "set \"MDOC_ASSISTANT=%USERPROFILE%\\.codex\\skills\\mdoc\\scripts\\screenshot_assistant.py\"\r\n"
        "if exist \"%MDOC_PYTHON%\" if exist \"%MDOC_ASSISTANT%\" (\r\n"
        f"  \"%MDOC_PYTHON%\" -B \"%MDOC_ASSISTANT%\" --workspace \"%MDOC_WORKSPACE%\" --task \"{task.task_id}\"{contributor_argument}\r\n"
        ") else (\r\n"
        "  echo mdoc screenshot assistant is not installed for this user.\r\n"
        "  echo Install mdoc, then open this launcher again.\r\n"
        "  cmd /c exit 2\r\n"
        ")\r\n"
        "set \"EXIT_CODE=%ERRORLEVEL%\"\r\n"
        "popd\r\n"
        "if not \"%EXIT_CODE%\"==\"0\" pause\r\n"
        "exit /b %EXIT_CODE%\r\n"
    )
    encoded = content.encode("ascii")
    if target.is_file() and target.read_bytes() == encoded:
        return target
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_bytes(encoded)
        os.replace(temporary, target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise MdocError("MDOC-SCREENSHOT-LAUNCHER-WRITE-FAILED", "无法写入截图启动器。", {"path": str(target), "cause": str(exc)}) from exc
    return target


def create_screenshot_launcher(task) -> dict:
    target = _create_screenshot_launcher(task, f"Open-Screenshot-Assistant-{task.task_id}.cmd", contributor=False)
    return {"status": "screenshot_launcher_created", "task_id": task.task_id, "path": str(target)}


def create_contributor_launcher(task, output: str | None = None) -> dict:
    try:
        target = _create_screenshot_launcher(task, output or f"Open-Screenshot-Task-{task.task_id}.cmd", contributor=True)
    except MdocError as exc:
        if exc.code == "MDOC-SCREENSHOT-LAUNCHER-INVALID":
            raise MdocError("MDOC-CONTRIBUTOR-LAUNCHER-INVALID", "协作者启动器必须是工作区根目录下的 .cmd 文件。") from exc
        if exc.code == "MDOC-SCREENSHOT-LAUNCHER-WRITE-FAILED":
            raise MdocError("MDOC-CONTRIBUTOR-LAUNCHER-WRITE-FAILED", "无法写入协作者截图启动器。", exc.details) from exc
        raise
    return {"status": "contributor_launcher_created", "task_id": task.task_id, "path": str(target)}


def continue_task(task, state: dict, *, no_gui: bool = False, quality_check=task_check) -> dict:
    recover_transactions(task, state)
    state["preserved_staging_files"] = preserved_staging_files(task)
    if task.definition["screenshots"] and state.get("definition_confirmation"):
        create_screenshot_launcher(task)
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
    if _quality_check_needed(previous, gate_input):
        transition(state, "verifying", "quality_gate_started")
        report = quality_check(task, state)
        state["quality_gate"] = {
            "status": report["status"], "digest": report["digest"], "input_digest": gate_input,
            "reviews": report.get("reviews", {}), "build": report.get("build", {}),
        }
        if report["status"] != "passed":
            return transition(state, "waiting_for_resolution", "quality_gate_blocked", {"kind": "quality_gate_findings", "report": report["path"], "blocking_count": report.get("blocking_count", 0)})
    transition(state, "publishing", "quality_gate_passed")
    try:
        with book_publish_lock(task):
            plan = publish_plan(task, state)
            write_json_atomic(task.directory / "reports" / f"publish-plan-r{plan['revision']}.json", plan)
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


def act(workspace_path: Path, task_id: str, action: str, *, no_gui: bool = False, item: str | None = None, screenshot_status: str | None = None, screenshot_reason: str | None = None, contributor: bool = False, review: str | None = None, review_status: str | None = None, target: str | None = None, confirmed: bool = False) -> dict:
    task, state_path, state = load(workspace_path, task_id)
    if action == "status":
        screenshots.synchronize(task, state)
        state["preserved_staging_files"] = preserved_staging_files(task)
        return state
    with task_lock(task):
        if state["status"] in TERMINAL and action != "continue":
            raise MdocError("MDOC-TASK-TERMINAL", "终态任务不能再次修改。")
        if action == "contribute":
            launch_screenshot_assistant(task, contributor=True)
            return {"status": "contributor_assistant_opened", "task_id": task.task_id}
        if action == "create-contributor-launcher":
            return create_contributor_launcher(task, target)
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
        elif action == "submit-screenshots":
            screenshots.submit(task, state)
        elif action == "screenshot-status":
            screenshots.set_status(task, state, item or "", screenshot_status or "", screenshot_reason or "")
            if not contributor:
                continue_task(task, state, no_gui=no_gui)
        elif action == "screenshots-open":
            launch_screenshot_assistant(task)
            return {"status": "screenshot_assistant_opened", "task_id": task.task_id}
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
        elif action == "approve-publish-conflict":
            if not confirmed:
                raise MdocError("MDOC-PUBLISH-CONFLICT-CONFIRMATION-REQUIRED", "必须使用 --confirm 明确批准以当前正式目标为新基线，再发布已确认的 staging 输出。")
            waiting = state.get("waiting_on") or {}
            error = waiting.get("error") or {}
            if state["status"] != "waiting_for_resolution" or waiting.get("kind") != "publishing" or error.get("code") != "MDOC-PUBLISH-CONFLICT":
                raise MdocError("MDOC-PUBLISH-CONFLICT-NOT-READY", "当前任务没有可批准的发布目标冲突。")
            conflicts = error.get("details", {}).get("conflicts", [])
            actions = {f"{entry['locale']}/{entry['path']}": entry["action"] for entry in task.definition["manifest"]}
            if not conflicts or any(conflict.get("reason") != "target_changed" or actions.get(conflict.get("target")) != "update" for conflict in conflicts):
                raise MdocError("MDOC-PUBLISH-CONFLICT-NOT-APPROVABLE", "只能批准 update 目标的 target_changed 发布冲突；新建、删除或其他冲突必须单独处理。", {"conflicts": conflicts})
            state.setdefault("publish_conflict_approvals", []).append({
                "at": int(time.time()),
                "reason": "target_changed",
                "targets": [conflict["target"] for conflict in conflicts],
                "previous_baselines": {conflict["target"]: state["baselines"].get(conflict["target"]) for conflict in conflicts},
            })
            state["baselines"] = baselines(task)
            state["quality_gate"] = None
            transition(state, "waiting_for_authoring", "publish_conflict_baseline_approved")
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
            state["baselines"] = baselines(task)
            prepare_authoring(task)
            transition(state, "waiting_for_authoring", "revision_requested", {"kind": "authoring", "request": str(task.directory / "authoring-request.json")})
        elif action == "revise":
            if state["status"] in TERMINAL:
                raise MdocError("MDOC-TASK-TERMINAL", "终态任务不能修订。")
            draft = copy.deepcopy(thaw(task.definition))
            draft.pop("manifest", None)
            draft.pop("definition_digest", None)
            if "generator" in draft:
                draft["generator"] = {"id": draft["generator"]["id"], "inputs": draft["generator"].get("inputs", {})}
            write_yaml_atomic(task.directory / "task-draft.yaml", draft)
            if state.get("scope_claimed"):
                claims.release(task)
                state["scope_claimed"] = False
            state["definition_confirmation"] = None
            state["definition_snapshot"] = None
            state["screenshot_acceptance"] = None
            state["screenshot_submission"] = None
            state["authoring_submission"] = None
            state["quality_gate"] = None
            state["reviews"] = {}
            transition(state, "draft", "definition_revision_requested", {"kind": "definition_draft", "draft": str(task.directory / "task-draft.yaml")})
        save_state(state_path, state)
    return state
