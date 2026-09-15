#!/usr/bin/env python3
"""mdoc command line entry point."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from mdoc_core import VERSION, pdf, screenshots, workspace as workspace_lifecycle
from mdoc_core.config import load_task, load_workspace
from mdoc_core.errors import MdocError
from mdoc_core.state import load_state
from mdoc_core.task_definition import create as create_task_draft, define as define_task
from mdoc_core.task import act as task_action


def add_check_arguments(item):
    item.add_argument("--workspace", type=Path, required=True); item.add_argument("--book"); item.add_argument("--locale")
    item.add_argument("--scope", choices=("page", "section", "book", "workspace", "task")); item.add_argument("--task")
    item.add_argument("--contributor-manifest", type=Path); item.add_argument("--skip-check", action="store_true"); item.add_argument("--target")
    item.add_argument("--level", choices=("basic", "full"), default="basic"); item.add_argument("--internal-only", action="store_true"); item.add_argument("--files-from", type=Path)


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
    "pdf_workspace_draft_created": "PDF 配置已写入工作区草稿，请执行 workspace apply 和 confirm。",
    "pdf_cache_cleaned": "PDF 构建缓存已清理。",
}


def emit(value, as_json=False):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif value.get("kind") in {"mdoc_check_result", "mdoc_check_error"}:
        print(f"状态: {value.get('status', 'incomplete')}")
        counts = value.get("counts") or {}
        if counts:
            print(f"错误: {counts.get('effective_errors', 0)}，警告: {counts.get('effective_warnings', 0)}")
        report_path = value.get("path") or (value.get("report") or {}).get("path")
        if report_path:
            print(f"报告: {report_path}")
        if value.get("error"):
            print(f"错误信息: {value['error']}")
    else:
        if "error" in value:
            print(value["error"].get("message", "mdoc 命令执行失败。"))
        else:
            print(HUMAN_STATUS.get(value.get("status"), value.get("status", "完成。")))
            preserved = value["preserved_staging_files"] if "preserved_staging_files" in value else (value.get("authoring_submission") or {}).get("preserved_staging_files") or []
            if preserved:
                print("staging 中以下清单外文件已保留，未参与当前任务发布：")
                for path in preserved:
                    print(f"- {path}")


def context(args):
    return load_workspace(args.workspace or Path.cwd())


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
        if name == "continue": item.add_argument("--skip-check", action="store_true")
    launcher = ts.add_parser("create-contributor-launcher"); launcher.add_argument("--task", required=True); launcher.add_argument("--workspace", type=Path); launcher.add_argument("--output")
    cancel = ts.add_parser("cancel"); cancel.add_argument("--task", required=True); cancel.add_argument("--workspace", type=Path); cancel.add_argument("--confirm", action="store_true"); cancel.add_argument("--no-gui", action="store_true")
    screenshot_group = ts.add_parser("screenshots"); screenshot_actions = screenshot_group.add_subparsers(dest="screenshot_action", required=True)
    for name in ("open", "accept", "submit"):
        item = screenshot_actions.add_parser(name); item.add_argument("--task", required=True); item.add_argument("--workspace", type=Path); item.add_argument("--no-gui", action="store_true")
    status = screenshot_actions.add_parser("set-status"); status.add_argument("--task", required=True); status.add_argument("--workspace", type=Path); status.add_argument("--item", required=True); status.add_argument("--status", choices=sorted(screenshots.USER_SETTABLE), required=True); status.add_argument("--reason", default=""); status.add_argument("--contributor", action="store_true"); status.add_argument("--no-gui", action="store_true")
    review = ts.add_parser("review"); review.add_argument("--task", required=True); review.add_argument("--workspace", type=Path); review.add_argument("--review", choices=("factual_accuracy", "language_quality", "visual_accuracy", "pdf_visual_quality"), required=True); review.add_argument("--status", choices=("human_accepted", "failed"), required=True); review.add_argument("--no-gui", action="store_true")
    deletion = ts.add_parser("approve-deletion"); deletion.add_argument("--task", required=True); deletion.add_argument("--workspace", type=Path); deletion.add_argument("--target", required=True); deletion.add_argument("--no-gui", action="store_true")
    conflict = ts.add_parser("approve-publish-conflict"); conflict.add_argument("--task", required=True); conflict.add_argument("--workspace", type=Path); conflict.add_argument("--confirm", action="store_true"); conflict.add_argument("--no-gui", action="store_true")
    check_group = sub.add_parser("check"); check_actions = check_group.add_subparsers(dest="check_action", required=True)
    add_check_arguments(check_actions.add_parser("run"))
    check_report = check_actions.add_parser("report"); check_report.add_argument("--workspace", type=Path, required=True); check_report.add_argument("--port", type=int, default=0); check_report.add_argument("--no-open", action="store_true")
    check_clean = check_actions.add_parser("clean"); check_clean.add_argument("--workspace", type=Path, required=True)
    check_actions.add_parser("doctor")
    feedback = sub.add_parser("feedback"); feedback_actions = feedback.add_subparsers(dest="feedback_action", required=True); feedback_open = feedback_actions.add_parser("open"); feedback_open.add_argument("--workspace", type=Path, required=True); feedback_open.add_argument("--port", type=int, default=0); feedback_open.add_argument("--no-open", action="store_true")
    pdf_group = sub.add_parser("pdf"); pdf_actions = pdf_group.add_subparsers(dest="pdf_action", required=True)
    pdf_init = pdf_actions.add_parser("init"); pdf_init.add_argument("--workspace", type=Path, required=True)
    pdf_doctor = pdf_actions.add_parser("doctor"); pdf_doctor.add_argument("--workspace", type=Path)
    pdf_build = pdf_actions.add_parser("build"); pdf_build.add_argument("--workspace", type=Path); pdf_build.add_argument("--file", type=Path); pdf_build.add_argument("--book"); pdf_build.add_argument("--locale"); pdf_build.add_argument("--scope", choices=("page", "section", "book"), default="book"); pdf_build.add_argument("--target"); pdf_build.add_argument("--summary-line", type=int); pdf_build.add_argument("--all-locales", action="store_true"); pdf_build.add_argument("--all-books", action="store_true"); pdf_build.add_argument("--output", type=Path); pdf_build.add_argument("--pdf-config", type=Path); pdf_build.add_argument("--language", choices=("zh-hans", "en", "ja")); pdf_build.add_argument("--font-family"); pdf_build.add_argument("--jobs", type=int); pdf_build.add_argument("--force-jobs", action="store_true"); pdf_build.add_argument("--yes", action="store_true"); pdf_build.add_argument("--no-overwrite", action="store_true"); work_options = pdf_build.add_mutually_exclusive_group(); work_options.add_argument("--keep-work", action="store_true"); work_options.add_argument("--discard-work", action="store_true"); pdf_build.add_argument("--strict-resources", action="store_true"); pdf_build.add_argument("--verify-pipeline", action="store_true")
    pdf_check = pdf_actions.add_parser("check"); pdf_check.add_argument("--workspace", type=Path); pdf_check.add_argument("--pdf", type=Path, required=True); pdf_check.add_argument("--book"); pdf_check.add_argument("--locale"); pdf_check.add_argument("--scope", choices=("page", "section", "book"), default="book"); pdf_check.add_argument("--target"); pdf_check.add_argument("--summary-line", type=int)
    pdf_clean = pdf_actions.add_parser("clean"); pdf_clean.add_argument("--workspace", type=Path)
    return root


def main():
    configure_utf8_console()
    arguments = sys.argv[1:]
    if "--json" in arguments:
        arguments = ["--json", *[item for item in arguments if item != "--json"]]
    args = parser().parse_args(arguments)
    try:
        workspace_path = getattr(args, "workspace", None)
        if workspace_path and (workspace_path.resolve() / ".mdoc" / "workspace.yaml").is_file():
            from mdoc_check.web import create_launcher
            create_launcher(workspace_path.resolve())
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
                    no_gui=getattr(args, "no_gui", False), skip_check=getattr(args, "skip_check", False), item=getattr(args, "item", None),
                    screenshot_status=getattr(args, "status", None), screenshot_reason=getattr(args, "reason", None), contributor=getattr(args, "contributor", False), review=getattr(args, "review", None),
                    review_status=getattr(args, "status", None), target=getattr(args, "target", None) or getattr(args, "output", None),
                    confirmed=getattr(args, "confirm", False),
                )
        elif args.command == "check":
            from mdoc_check.cli import execute as execute_check
            result = execute_check(args)
        elif args.command == "feedback":
            from mdoc_check.web import create_launcher, serve
            create_launcher(args.workspace.resolve()); serve(args.workspace.resolve(), "127.0.0.1", args.port, not args.no_open, "feedback"); result = {"status": "feedback_closed"}
        else:
            if args.pdf_action == "init":
                result = pdf.init(args.workspace)
            elif args.pdf_action == "build" and args.file:
                incompatible = [name for name in ("workspace", "book", "locale", "target") if getattr(args, name)]
                incompatible.extend(name for name in ("all_locales", "all_books", "force_jobs") if getattr(args, name))
                if args.scope != "book" or args.summary_line is not None or args.jobs is not None or args.yes: incompatible.append("workspace_build_option")
                if incompatible: raise MdocError("MDOC-PDF-FILE-OPTION-CONFLICT", "--file 不能与工作区 PDF 构建参数同时使用。", {"options": incompatible})
                result = pdf.build_file(args.file, args.output, args.pdf_config, args.language, args.font_family, args.no_overwrite, args.keep_work, args.discard_work, args.strict_resources, args.verify_pipeline)
            else:
                if args.pdf_action == "build" and (args.pdf_config or args.language or args.font_family): raise MdocError("MDOC-PDF-WORKSPACE-OPTION-CONFLICT", "--pdf-config、--language 和 --font-family 仅适用于 --file。")
                pdf_workspace = context(args)
                if args.pdf_action == "doctor":
                    result = pdf.doctor(pdf_workspace)
                elif args.pdf_action == "check":
                    if args.scope != "book" and not args.target: raise MdocError("MDOC-PDF-TARGET-REQUIRED", "page 和 section 范围需要 --target。")
                    if args.scope == "book" and args.summary_line is not None: raise MdocError("MDOC-PDF-SUMMARY-LINE-SCOPE-INVALID", "--summary-line 仅适用于 page 和 section 范围。")
                    result = pdf.check(pdf_workspace, args.pdf, args.book, args.locale, args.scope, args.target, args.summary_line)
                elif args.pdf_action == "clean":
                    result = pdf.clean(pdf_workspace)
                else:
                    if args.scope != "book" and not args.target:
                        raise MdocError("MDOC-PDF-TARGET-REQUIRED", "page 和 section 范围需要 --target。")
                    if args.scope == "book" and args.summary_line is not None: raise MdocError("MDOC-PDF-SUMMARY-LINE-SCOPE-INVALID", "--summary-line 仅适用于 page 和 section 范围。")
                    if args.output and (args.all_books or args.all_locales):
                        raise MdocError("MDOC-PDF-OUTPUT-BATCH-INVALID", "批量构建不能指定单一 --output。")
                    if args.yes and args.no_overwrite:
                        raise MdocError("MDOC-PDF-OVERWRITE-OPTION-CONFLICT", "--yes 与 --no-overwrite 不能同时使用。")
                    result = pdf.build(pdf_workspace, args.book, args.locale, args.scope, args.target, args.output.resolve() if args.output else None, args.all_locales, args.all_books, args.jobs, args.force_jobs, args.yes, args.no_overwrite, not args.json and sys.stdin.isatty(), args.keep_work, args.discard_work, args.strict_resources, args.verify_pipeline, args.summary_line)
        emit(result, args.json)
        return result.get("exit_code", 0)
    except MdocError as exc:
        emit(exc.payload(), args.json)
        return 2
    except Exception as exc:
        if args.command == "check":
            emit({"schema_version": 1, "kind": "mdoc_check_error", "status": "incomplete", "error": str(exc)}, args.json)
            return 4
        emit(MdocError("MDOC-INTERNAL-ERROR", "mdoc 遇到内部错误。", {"cause": str(exc)}).payload(), args.json)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
