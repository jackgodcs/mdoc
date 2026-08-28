#!/usr/bin/env python3
"""mdoc 1.3.8 command line entry point."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from mdoc_core import VERSION, screenshots, workspace as workspace_lifecycle
from mdoc_core.config import load_task, load_workspace
from mdoc_core.errors import MdocError
from mdoc_core.quality import book_check, task_check
from mdoc_core.state import load_state
from mdoc_core.task_definition import create as create_task_draft, define as define_task
from mdoc_core.task import act as task_action


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
    "contributor_assistant_opened": "协作者截图助手已打开。",
    "contributor_launcher_created": "协作者截图启动器已生成。",
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


def cmd_quality(args):
    workspace = context(args)
    if args.task:
        if args.profile:
            raise MdocError("MDOC-QUALITY-PROFILE-FROZEN", "Task Quality Gate profile is frozen in task.yaml.")
        task = load_task(workspace, args.task)
        state = load_state(task.directory / "task-state.json", task.task_id)
        if args.locale or args.path or args.changed:
            raise MdocError("MDOC-QUALITY-SCOPE-INVALID", "--locale, --path, and --changed apply only to --book.")
        report = task_check(task, state, published=args.published)
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
    for name in ("status", "continue", "contribute", "confirm-definition", "submit-authoring", "confirm-final", "revise", "revise-output"):
        item = ts.add_parser(name); item.add_argument("--task", required=True); item.add_argument("--workspace", type=Path); item.add_argument("--no-gui", action="store_true")
    launcher = ts.add_parser("create-contributor-launcher"); launcher.add_argument("--task", required=True); launcher.add_argument("--workspace", type=Path); launcher.add_argument("--output")
    cancel = ts.add_parser("cancel"); cancel.add_argument("--task", required=True); cancel.add_argument("--workspace", type=Path); cancel.add_argument("--confirm", action="store_true"); cancel.add_argument("--no-gui", action="store_true")
    screenshot_group = ts.add_parser("screenshots"); screenshot_actions = screenshot_group.add_subparsers(dest="screenshot_action", required=True)
    for name in ("open", "accept", "submit"):
        item = screenshot_actions.add_parser(name); item.add_argument("--task", required=True); item.add_argument("--workspace", type=Path); item.add_argument("--no-gui", action="store_true")
    status = screenshot_actions.add_parser("set-status"); status.add_argument("--task", required=True); status.add_argument("--workspace", type=Path); status.add_argument("--item", required=True); status.add_argument("--status", choices=sorted(screenshots.USER_SETTABLE), required=True); status.add_argument("--contributor", action="store_true"); status.add_argument("--no-gui", action="store_true")
    review = ts.add_parser("review"); review.add_argument("--task", required=True); review.add_argument("--workspace", type=Path); review.add_argument("--review", choices=("factual_accuracy", "language_quality", "visual_accuracy", "pdf_visual_quality"), required=True); review.add_argument("--status", choices=("human_accepted", "failed"), required=True); review.add_argument("--no-gui", action="store_true")
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
                action = args.task_action
                if action == "screenshots":
                    action = {"open": "screenshots-open", "accept": "accept-screenshots", "submit": "submit-screenshots", "set-status": "screenshot-status"}[args.screenshot_action]
                result = task_action(
                    args.workspace or Path.cwd(), args.task, action,
                    no_gui=getattr(args, "no_gui", False), item=getattr(args, "item", None),
                    screenshot_status=getattr(args, "status", None), contributor=getattr(args, "contributor", False), review=getattr(args, "review", None),
                    review_status=getattr(args, "status", None), target=getattr(args, "target", None) or getattr(args, "output", None),
                    confirmed=getattr(args, "confirm", False),
                )
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
