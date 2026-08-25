#!/usr/bin/env python3
"""mdoc 1.2.0 command line entry point."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from mdoc_core import VERSION, claims, screenshots, workspace as workspace_lifecycle
from mdoc_core.authoring import prepare as prepare_authoring, submit as submit_authoring
from mdoc_core.config import load_task, load_workspace, task_directory, validate_schema, validate_task_definition
from mdoc_core.errors import MdocError
from mdoc_core.io import canonical_digest, file_digest, read_yaml, write_json_atomic, write_yaml_atomic
from mdoc_core.models import thaw
from mdoc_core.paths import changes
from mdoc_core.quality import book_check, task_check
from mdoc_core.state import TERMINAL, book_publish_lock, load_state, save_state, task_lock, transition
from mdoc_core.task_definition import create as create_task_draft, define as define_task


def configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


HUMAN_STATUS = {
    "workspace_draft_created": "工作区草稿已创建。",
    "waiting_for_workspace_confirmation": "工作区候选配置已生成，等待确认。",
    "workspace_ready": "工作区配置已确认。",
    "workspace_local_draft_created": "本机配置草稿已创建。",
    "waiting_for_workspace_local_confirmation": "本机候选配置已生成，等待确认。",
    "workspace_local_ready": "本机配置已确认。",
    "task_draft_created": "任务草稿已创建。",
    "waiting_for_definition_confirmation": "任务定义已生成，等待确认。",
    "waiting_for_authoring": "等待在受控 staging 中完成编写。",
}


def emit(value, as_json=False):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        if "error" in value:
            print(value["error"].get("message", "mdoc 命令执行失败。"))
        else:
            print(HUMAN_STATUS.get(value.get("status"), value.get("status", "完成。")))


def context(args):
    return load_workspace(args.workspace or Path.cwd())


def task_and_state(args):
    workspace = context(args)
    task = load_task(workspace, args.task)
    state_path = task.directory / "task-state.json"
    return workspace, task, state_path, load_state(state_path, task.task_id)


def baselines(task):
    return {f"{item['locale']}/{item['path']}": {"exists": formal.is_file(), "sha256": file_digest(formal) if formal.is_file() else None} for item, formal, _staged in changes(task)}


quality_task = task_check


def verify_publish(task, state):
    conflicts = []
    for item, formal, _staged in changes(task):
        key = f"{item['locale']}/{item['path']}"
        base = state["baselines"].get(key)
        if base is None:
            conflicts.append({"target": key, "reason": "baseline_missing"})
            continue
        current = file_digest(formal) if formal.is_file() else None
        if item["action"] == "create" and formal.exists():
            conflicts.append({"target": key, "reason": "create_target_exists"})
        elif item["action"] in {"update", "delete"} and current != base["sha256"]:
            conflicts.append({"target": key, "reason": "baseline_changed"})
        if item["action"] == "delete" and not state["exception_approvals"].get(f"delete:{key}"):
            conflicts.append({"target": key, "reason": "deletion_not_approved"})
    if conflicts:
        raise MdocError("MDOC-PUBLISH-CONFLICT", "Publishing requires resolution.", {"conflicts": conflicts})


def _remove_file(path: Path) -> None:
    if path.exists():
        try:
            path.chmod(path.stat().st_mode | stat.S_IWRITE)
        except OSError:
            pass
        path.unlink(missing_ok=True)


def publish(task, state):
    verify_publish(task, state)
    transaction = {"id": f"{time.time_ns()}-{state['revision']}", "status": "started", "files": []}
    root = task.directory / "publish-transactions" / transaction["id"]
    backups = root / "backups"
    write_json_atomic(root / "transaction.json", transaction)
    try:
        for item, formal, staged in changes(task):
            backup = backups / item["locale"] / item["path"]
            existed = formal.is_file()
            if existed:
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(formal, backup)
            transaction["files"].append({"target": str(formal), "backup": str(backup) if existed else None, "existed": existed})
            if item["action"] == "delete":
                _remove_file(formal)
            else:
                formal.parent.mkdir(parents=True, exist_ok=True)
                temporary = formal.with_name(f".{formal.name}.{transaction['id']}.tmp")
                shutil.copy2(staged, temporary)
                os.replace(temporary, formal)
        post = quality_task(task, state, published=True)
        if post["status"] != "passed":
            raise MdocError("MDOC-PUBLISHED-QUALITY-FAILED", "Published files failed transactional validation.", {"report": post["digest"]})
        transaction["status"] = "committed"
        transaction["report_digest"] = post["digest"]
    except Exception:
        for record in reversed(transaction["files"]):
            target = Path(record["target"])
            if record["existed"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(record["backup"], target)
            else:
                _remove_file(target)
        transaction["status"] = "rolled_back"
        write_json_atomic(root / "transaction.json", transaction)
        state["publish"]["transactions"].append(transaction)
        raise
    write_json_atomic(root / "transaction.json", transaction)
    state["publish"]["transactions"].append(transaction)
    return transaction


def launch_screenshot_assistant(task):
    subprocess = __import__("subprocess")
    subprocess.Popen(
        [sys.executable, str(SCRIPT_DIR / "screenshot_assistant.py"), "--workspace", str(task.workspace.repository), "--task", task.task_id],
        cwd=task.workspace.repository,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def continue_task(task, state, no_gui=False):
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
    ready, manifest = screenshots.readiness(task, state)
    if (state.get("screenshot_acceptance") or {}).get("status") == "stale":
        state["authoring_submission"] = None
        state["quality_gate"] = None
    if task.definition["screenshots"]:
        if not ready:
            waiting = transition(state, "waiting_for_screenshots", "screenshots_incomplete", {"kind": "screenshots", "blockers": manifest["blockers"]})
            if not no_gui and task.workspace.config["screenshots"].get("auto_open_assistant"):
                launch_screenshot_assistant(task)
            return waiting
        acceptance = state.get("screenshot_acceptance")
        if not acceptance or acceptance.get("status") != "accepted":
            return transition(state, "waiting_for_screenshot_acceptance", "screenshots_require_acceptance", {"kind": "screenshot_acceptance", "manifest_digest": manifest["digest"]})
    request_path = task.directory / "authoring-request.json"
    if not state.get("authoring_submission"):
        if not request_path.is_file():
            prepare_authoring(task)
        try:
            submit_authoring(task, state)
        except MdocError as exc:
            if exc.code == "MDOC-AUTHORING-INCOMPLETE":
                return transition(state, "waiting_for_authoring", "authoring_incomplete", {"kind": "authoring", "request": str(request_path), "error": exc.payload()["error"]})
            return transition(state, "waiting_for_resolution", "authoring_scope_blocked", {"kind": "authoring", "error": exc.payload()["error"]})
    current_files = {f"{item['locale']}/{item['path']}": file_digest(staged) for item, _formal, staged in changes(task) if item["action"] != "delete" and staged.is_file()}
    expected_count = sum(item["action"] != "delete" for item in task.definition["manifest"])
    if len(current_files) != expected_count:
        return transition(state, "waiting_for_authoring", "authoring_incomplete", {"kind": "authoring", "request": str(request_path)})
    if state["authoring_submission"].get("files") != current_files:
        try:
            submit_authoring(task, state)
        except MdocError as exc:
            return transition(state, "waiting_for_resolution", "authoring_scope_blocked", {"kind": "authoring", "error": exc.payload()["error"]})
    gate_input_digest = canonical_digest({"files": current_files, "reviews": state.get("reviews", {})})
    previous_gate = state.get("quality_gate") or {}
    if state["status"] == "waiting_for_resolution" and (state.get("waiting_on") or {}).get("kind") == "quality_gate" and previous_gate.get("input_digest") == gate_input_digest:
        return state
    gate_passed = previous_gate.get("status") == "passed" and previous_gate.get("input_digest") == gate_input_digest
    if not gate_passed:
        transition(state, "verifying", "quality_gate_started")
        report = quality_task(task, state)
        state["quality_gate"] = {"status": report["status"], "digest": report["digest"], "input_digest": gate_input_digest}
        if report["status"] != "passed":
            return transition(state, "waiting_for_resolution", "quality_gate_blocked", {"kind": "quality_gate", "digest": report["digest"]})
    transition(state, "publishing", "quality_gate_passed")
    try:
        with book_publish_lock(task.workspace.control, task.definition["task"]["book"]):
            publish(task, state)
    except MdocError as exc:
        if exc.code == "MDOC-PUBLISH-CONFLICT":
            state["quality_gate"] = None
        return transition(state, "waiting_for_resolution", "publishing_paused", {"kind": "publishing", "error": exc.payload()["error"]})
    except Exception as exc:
        error = MdocError("MDOC-PUBLISH-FAILED", "Publishing failed and the task transaction was rolled back.", {"cause": str(exc)})
        return transition(state, "waiting_for_resolution", "publishing_paused", {"kind": "publishing", "error": error.payload()["error"]})
    state["revision"] += 1
    return transition(state, "ready_for_review", "published_and_verified", {"kind": "final_acceptance", "revision": state["revision"]})


def cmd_workspace_init(args):
    repository = args.repository.resolve()
    draft = read_yaml(args.draft.resolve())
    validate_schema(draft, "workspace.schema.json", "workspace draft")
    for book in draft["books"].values():
        root = (repository / book["root"]).resolve()
        try:
            root.relative_to(repository)
        except ValueError as exc:
            raise MdocError("MDOC-PATH-UNSAFE", f"Configured book root escapes the repository: {book['root']}") from exc
        if not root.is_dir():
            raise MdocError("MDOC-BOOK-MISSING", f"Configured book root is missing: {book['root']}")
        for locale in book["locales"].values():
            if not (root / locale["root"]).is_dir():
                raise MdocError("MDOC-BOOK-LOCALE-MISSING", f"Configured locale root is missing: {locale['root']}")
    control = repository / ".mdoc"
    control.mkdir(parents=True, exist_ok=True)
    if (control / "workspace.yaml").exists():
        raise MdocError("MDOC-WORKSPACE-EXISTS", "This repository already has an mdoc workspace.")
    write_yaml_atomic(control / "workspace.candidate.yaml", draft)
    return {"status": "waiting_for_workspace_confirmation", "candidate": str(control / "workspace.candidate.yaml"), "digest": canonical_digest(draft)}


def cmd_workspace_apply(args):
    if not args.confirm:
        raise MdocError("MDOC-CONFIRMATION-REQUIRED", "workspace apply requires --confirm.")
    control = args.repository.resolve() / ".mdoc"
    candidate = read_yaml(control / "workspace.candidate.yaml")
    validate_schema(candidate, "workspace.schema.json", "workspace candidate")
    repository = args.repository.resolve()
    for book in candidate["books"].values():
        root = (repository / book["root"]).resolve()
        try:
            root.relative_to(repository)
        except ValueError as exc:
            raise MdocError("MDOC-PATH-UNSAFE", f"Configured book root escapes the repository: {book['root']}") from exc
        if not root.is_dir():
            raise MdocError("MDOC-BOOK-MISSING", f"Configured book root is missing: {book['root']}")
        for locale in book["locales"].values():
            if not (root / locale["root"]).is_dir():
                raise MdocError("MDOC-BOOK-LOCALE-MISSING", f"Configured locale root is missing: {locale['root']}")
    write_yaml_atomic(control / "workspace.yaml", candidate)
    (control / "workspace.candidate.yaml").unlink(missing_ok=True)
    if not (control / "workspace.local.yaml").is_file():
        write_yaml_atomic(control / "workspace.local.yaml", {"schema_version": 1})
    return {"status": "workspace_ready", "repository": str(args.repository.resolve())}


def cmd_task_create(args):
    workspace = context(args)
    draft = read_yaml(args.draft.resolve())
    task_id = draft.get("task", {}).get("id", "")
    task = validate_task_definition(workspace, task_id, draft, "task draft", check_initial_targets=True)
    directory = task_directory(workspace, task_id)
    if directory.exists():
        raise MdocError("MDOC-TASK-EXISTS", f"Task already exists: {task_id}")
    directory.mkdir(parents=True)
    state = None
    try:
        write_yaml_atomic(directory / "task.yaml", thaw(task.definition))
        task = load_task(workspace, task_id)
        state = load_state(directory / "task-state.json", task_id)
        continue_task(task, state, args.no_gui)
        save_state(directory / "task-state.json", state)
        return state
    except Exception:
        if state and state.get("scope_claimed"):
            claims.release(task)
        try:
            shutil.rmtree(directory)
        except OSError:
            pass
        raise


def cmd_task_action(args):
    _workspace, task, state_path, state = task_and_state(args)
    with task_lock(task.directory):
        if args.task_action == "continue":
            continue_task(task, state, args.no_gui)
        elif args.task_action == "confirm-definition":
            if state["status"] != "waiting_for_definition_confirmation":
                raise MdocError("MDOC-DEFINITION-NOT-EDITABLE", "Task definition cannot be changed in the current state.")
            if state.get("definition_confirmation"):
                raise MdocError("MDOC-DEFINITION-ALREADY-CONFIRMED", "Task definition is already frozen.")
            claims.claim(task); state["scope_claimed"] = True
            state["definition_confirmation"] = {"digest": task.digest, "workspace_digest": task.workspace.digest, "at": int(time.time())}
            state["definition_snapshot"] = thaw(task.definition)
            state["baselines"] = baselines(task)
            transition(state, "draft", "definition_confirmed")
            continue_task(task, state, args.no_gui)
        elif args.task_action == "accept-screenshots":
            if state["status"] not in {"waiting_for_screenshots", "waiting_for_screenshot_acceptance"}:
                raise MdocError("MDOC-SCREENSHOT-ACCEPTANCE-NOT-READY", "Task is not waiting for screenshot acceptance.")
            if not task.definition["screenshots"]:
                raise MdocError("MDOC-SCREENSHOTS-NOT-DECLARED", "This task does not declare screenshots.")
            screenshots.accept(task, state)
            continue_task(task, state, args.no_gui)
        elif args.task_action == "screenshot-status":
            if state["status"] not in {"waiting_for_screenshots", "waiting_for_screenshot_acceptance"}:
                raise MdocError("MDOC-SCREENSHOT-STATUS-NOT-EDITABLE", "Screenshots cannot be changed in the current task state.")
            screenshots.set_status(task, state, args.item, args.status)
            continue_task(task, state, args.no_gui)
        elif args.task_action == "submit-authoring":
            if state["status"] not in {"waiting_for_authoring", "waiting_for_resolution"} or (state.get("waiting_on") or {}).get("kind") in {"definition_changed", "workspace_changed"}:
                raise MdocError("MDOC-AUTHORING-NOT-EDITABLE", "Authoring cannot be submitted in the current task state.")
            submit_authoring(task, state)
            state["quality_gate"] = None
            continue_task(task, state, args.no_gui)
        elif args.task_action == "review":
            if state["status"] not in {"waiting_for_authoring", "waiting_for_resolution", "verifying"} or (state.get("waiting_on") or {}).get("kind") in {"definition_changed", "workspace_changed"}:
                raise MdocError("MDOC-REVIEW-NOT-EDITABLE", "Quality review cannot be changed in the current task state.")
            state["reviews"][args.review] = {"status": args.status, "at": int(time.time())}
            state["quality_gate"] = None
            continue_task(task, state, args.no_gui)
        elif args.task_action == "approve-deletion":
            if (state.get("waiting_on") or {}).get("kind") in {"definition_changed", "workspace_changed"}:
                raise MdocError("MDOC-DELETION-APPROVAL-NOT-EDITABLE", "Deletion approval cannot be changed until configuration drift is resolved.")
            declared = {f"{item['locale']}/{item['path']}" for item in task.definition["manifest"] if item["action"] == "delete"}
            if args.target not in declared:
                raise MdocError("MDOC-DELETION-TARGET-INVALID", "Deletion approval must name a declared delete target.", {"target": args.target})
            if state["status"] in TERMINAL or state["status"] == "ready_for_review":
                raise MdocError("MDOC-DELETION-APPROVAL-NOT-EDITABLE", "Deletion approval cannot be changed in the current task state.")
            state["exception_approvals"][f"delete:{args.target}"] = True
            continue_task(task, state, args.no_gui)
        elif args.task_action == "accept":
            if state["status"] != "ready_for_review":
                raise MdocError("MDOC-FINAL-ACCEPTANCE-NOT-READY", "Task is not ready for final acceptance.")
            transition(state, "accepted", "user_accepted_final_manual")
            if state.get("scope_claimed"):
                claims.release(task); state["scope_claimed"] = False
        elif args.task_action == "cancel":
            if state["status"] in TERMINAL:
                raise MdocError("MDOC-TASK-TERMINAL", "Terminal tasks cannot be changed.")
            transition(state, "cancelled", "user_cancelled_task")
            if state.get("scope_claimed"):
                claims.release(task); state["scope_claimed"] = False
        elif args.task_action == "revise":
            if state["status"] != "ready_for_review":
                raise MdocError("MDOC-REVISION-NOT-READY", "Task is not ready for revision.")
            state["authoring_submission"] = None
            state["quality_gate"] = None
            state["reviews"] = {}
            state["baselines"] = baselines(task)
            prepare_authoring(task)
            transition(state, "waiting_for_authoring", "revision_requested", {"kind": "authoring", "request": str(task.directory / "authoring-request.json")})
        save_state(state_path, state)
    return state


def cmd_quality(args):
    workspace = context(args)
    if args.task:
        if args.profile:
            raise MdocError("MDOC-QUALITY-PROFILE-FROZEN", "Task Quality Gate profile is frozen in task.yaml.")
        task = load_task(workspace, args.task)
        state = load_state(task.directory / "task-state.json", task.task_id)
        if args.locale or args.path or args.changed:
            raise MdocError("MDOC-QUALITY-SCOPE-INVALID", "--locale, --path, and --changed apply only to --book.")
        report = quality_task(task, state, published=args.published)
    else:
        if args.published:
            raise MdocError("MDOC-QUALITY-SCOPE-INVALID", "--published applies only to --task.")
        if not args.book:
            raise MdocError("MDOC-QUALITY-TARGET-REQUIRED", "quality check requires --book or --task.")
        if args.book not in workspace.config["books"]:
            raise MdocError("MDOC-BOOK-MISSING", f"Unknown book: {args.book}")
        if args.locale and args.locale not in workspace.config["books"][args.book]["locales"]:
            raise MdocError("MDOC-QUALITY-LOCALE-INVALID", f"Unknown locale for book: {args.locale}")
        try:
            report = book_check(workspace, args.book, profile=args.profile, locale=args.locale, path=args.path, changed=args.changed)
        except ValueError as exc:
            raise MdocError("MDOC-QUALITY-SCOPE-INVALID", str(exc)) from exc
    if args.enforce and report["status"] != "passed":
        report["exit_code"] = 3
    return report


def parser():
    root = argparse.ArgumentParser(prog="mdoc")
    root.add_argument("--version", action="version", version=f"mdoc {VERSION}")
    root.add_argument("--workspace", type=Path)
    root.add_argument("--json", action="store_true")
    sub = root.add_subparsers(dest="command", required=True)
    workspace = sub.add_parser("workspace"); ws = workspace.add_subparsers(dest="workspace_action", required=True)
    init = ws.add_parser("init"); init.add_argument("--workspace", type=Path, required=True)
    apply = ws.add_parser("apply"); apply.add_argument("--workspace", type=Path, required=True)
    confirm = ws.add_parser("confirm"); confirm.add_argument("--workspace", type=Path, required=True)
    revise = ws.add_parser("revise"); revise.add_argument("--workspace", type=Path, required=True)
    local = ws.add_parser("local"); local_actions = local.add_subparsers(dest="workspace_local_action", required=True)
    for name in ("init", "apply", "confirm", "revise"):
        item = local_actions.add_parser(name); item.add_argument("--workspace", type=Path, required=True)
    task = sub.add_parser("task"); ts = task.add_subparsers(dest="task_action", required=True)
    create = ts.add_parser("create"); create.add_argument("--workspace", type=Path, required=True); create.add_argument("--task", required=True); create.add_argument("--book", required=True); create.add_argument("--intent", choices=("create_module", "add_feature", "update_content", "add_locale"), required=True)
    define = ts.add_parser("define"); define.add_argument("--workspace", type=Path, required=True); define.add_argument("--task", required=True)
    for name in ("continue", "confirm-definition", "accept-screenshots", "submit-authoring", "accept", "cancel", "revise"):
        item = ts.add_parser(name); item.add_argument("--task", required=True); item.add_argument("--workspace", type=Path); item.add_argument("--no-gui", action="store_true")
    status = ts.add_parser("screenshot-status"); status.add_argument("--task", required=True); status.add_argument("--workspace", type=Path); status.add_argument("--item", required=True); status.add_argument("--status", choices=sorted(screenshots.USER_SETTABLE), required=True); status.add_argument("--no-gui", action="store_true")
    review = ts.add_parser("review"); review.add_argument("--task", required=True); review.add_argument("--workspace", type=Path); review.add_argument("--review", choices=("factual_accuracy", "language_quality", "visual_accuracy"), required=True); review.add_argument("--status", choices=("passed", "failed"), required=True); review.add_argument("--no-gui", action="store_true")
    deletion = ts.add_parser("approve-deletion"); deletion.add_argument("--task", required=True); deletion.add_argument("--workspace", type=Path); deletion.add_argument("--target", required=True); deletion.add_argument("--no-gui", action="store_true")
    quality = sub.add_parser("quality"); qs = quality.add_subparsers(dest="quality_action", required=True); check = qs.add_parser("check"); check.add_argument("--workspace", type=Path); targets = check.add_mutually_exclusive_group(required=True); targets.add_argument("--book"); targets.add_argument("--task"); check.add_argument("--profile", choices=("standard", "full", "release")); check.add_argument("--locale"); check.add_argument("--path"); check.add_argument("--changed", action="store_true"); check.add_argument("--published", action="store_true"); check.add_argument("--enforce", action="store_true")
    return root


def main():
    configure_utf8_console()
    arguments = sys.argv[1:]
    if "--json" in arguments:
        arguments = ["--json", *[item for item in arguments if item != "--json"]]
    args = parser().parse_args(arguments)
    try:
        if args.command == "workspace":
            if args.workspace_action == "local":
                actions = {"init": workspace_lifecycle.local_init, "apply": workspace_lifecycle.local_apply, "confirm": workspace_lifecycle.local_confirm, "revise": workspace_lifecycle.local_revise}
                result = actions[args.workspace_local_action](args.workspace)
            else:
                actions = {"init": workspace_lifecycle.init, "apply": workspace_lifecycle.apply, "confirm": workspace_lifecycle.confirm, "revise": workspace_lifecycle.revise}
                result = actions[args.workspace_action](args.workspace)
        elif args.command == "task":
            if args.task_action == "create":
                result = create_task_draft(args.workspace, args.task, args.book, args.intent)
            elif args.task_action == "define":
                result = define_task(args.workspace, args.task)
            else:
                result = cmd_task_action(args)
        else:
            result = cmd_quality(args)
        emit(result, args.json)
        return result.get("exit_code", 0)
    except MdocError as exc:
        emit(exc.payload(), args.json)
        return 2
    except Exception as exc:
        emit(MdocError("MDOC-INTERNAL-ERROR", "mdoc 遇到内部错误。", {"cause": str(exc)}).payload(), args.json)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
