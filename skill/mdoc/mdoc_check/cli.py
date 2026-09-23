from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import doctor, run, run_selected, store


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="mdoc-check-prototype")
    actions = root.add_subparsers(dest="action", required=True)
    check = actions.add_parser("run")
    check.add_argument("--workspace", type=Path, required=True)
    check.add_argument("--book")
    check.add_argument("--locale")
    check.add_argument("--scope", choices=("page", "section", "book", "workspace", "task"))
    check.add_argument("--task")
    check.add_argument("--contributor-manifest", type=Path)
    check.add_argument("--skip-check", action="store_true")
    check.add_argument("--target")
    check.add_argument("--level", choices=("basic", "full"), default="basic")
    check.add_argument("--internal-only", action="store_true")
    check.add_argument("--files-from", type=Path)
    check.add_argument("--replace-latest", action="store_true")
    check.add_argument("--open-report", action="store_true")
    check.add_argument("--json", action="store_true")
    diagnosis = actions.add_parser("doctor")
    diagnosis.add_argument("--json", action="store_true")
    report = actions.add_parser("report")
    report.add_argument("--workspace", type=Path, required=True)
    report.add_argument("--port", type=int, default=0)
    report.add_argument("--no-open", action="store_true")
    report.add_argument("--page", choices=("check", "feedback"), default="check")
    clean = actions.add_parser("clean")
    clean.add_argument("--workspace", type=Path, required=True)
    clean.add_argument("--json", action="store_true")
    return root


def execute(args) -> dict:
    if args.check_action == "report":
        from .web import serve
        workspace = args.workspace.resolve(); serve(workspace, "127.0.0.1", args.port, not args.no_open, "check")
        return {"status": "check_report_closed"}
    if args.check_action == "clean":
        from .maintenance import cleanup
        return cleanup(args.workspace.resolve(), automatic=False)
    if args.check_action == "doctor":
        return store(doctor(), None)
    workspace = args.workspace.resolve()
    from .maintenance import activity, cleanup, report_root
    cleanup(workspace, automatic=True)
    progress = None if getattr(args, "json", False) else lambda message: print(message, file=sys.stderr, flush=True)
    if args.files_from:
        if any((args.scope, args.book, args.locale, args.target, args.task, args.contributor_manifest, args.skip_check)):
            raise ValueError("--files-from does not accept scope, book, locale, target or task options.")
        with activity(report_root(workspace) / ".check.active.json", "selected-check"):
            return run_selected(workspace, args.files_from.resolve(), args.level, args.internal_only, progress)
    if not args.scope: raise ValueError("--scope is required without --files-from.")
    with activity(report_root(workspace) / ".check.active.json", "check"):
        full = run(workspace, args.book, args.locale, args.scope, args.target, args.level, args.internal_only, progress, args.task, args.contributor_manifest, args.skip_check)
        from .reports import store_full
        stored = store_full(full, workspace, getattr(args, "replace_latest", False))
    persisted = json.loads(Path(stored["path"]).read_text(encoding="utf-8"))
    return {"schema_version": 1, "kind": "mdoc_check_result", "status": persisted["status"], "context": persisted.get("context"), "revision": persisted.get("revision"), "updated_files": persisted.get("files", {}).get("count", 0), "failed_files": 0, "counts": persisted.get("counts", {}), "report": {"path": stored.get("path")}, "exit_code": 4 if persisted["status"] == "incomplete" else 3 if persisted["status"] == "blocked" else 0}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args()
    if args.action == "report":
        from .web import serve

        workspace = args.workspace.resolve()
        serve(workspace, "127.0.0.1", args.port, not args.no_open, args.page)
        return 0
    if args.action == "clean":
        from .maintenance import cleanup
        report = cleanup(args.workspace.resolve(), automatic=False)
        if args.json: print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"缓存维护完成：删除 {report['directories']} 个目录、{report['files']} 个文件，释放 {report['bytes'] / 1048576:.2f} MiB。")
            for warning in report["warnings"]: print("警告：" + warning)
        return 0
    try:
        workspace = None if args.action == "doctor" else args.workspace.resolve()
        if workspace is not None:
            from .maintenance import cleanup
            cleanup(workspace, automatic=True)
        progress = None if args.json or args.action == "doctor" else lambda message: print(message, file=sys.stderr, flush=True)
        if args.action == "doctor":
            report = store(doctor(), None)
        elif args.files_from:
            if any((args.scope, args.book, args.locale, args.target, args.task, args.contributor_manifest, args.skip_check)):
                raise ValueError("--files-from does not accept scope, book, locale, target or task options.")
            from .maintenance import activity, report_root
            with activity(report_root(workspace) / ".check.active.json", "selected-check"):
                report = run_selected(workspace, args.files_from.resolve(), args.level, args.internal_only, progress)
        else:
            if not args.scope:
                raise ValueError("--scope is required without --files-from.")
            from .maintenance import activity, report_root
            with activity(report_root(workspace) / ".check.active.json", "check"):
                full = run(workspace, args.book, args.locale, args.scope, args.target, args.level, args.internal_only, progress, args.task, args.contributor_manifest, args.skip_check)
                stored = store(full, workspace)
            persisted = json.loads(Path(stored["path"]).read_text(encoding="utf-8"))
            report = {"schema_version": 1, "kind": "mdoc_check_result", "status": persisted["status"], "context": persisted.get("context"), "revision": persisted.get("revision"), "updated_files": persisted.get("files", {}).get("count", 0), "failed_files": 0, "counts": persisted.get("counts", {}), "report": {"path": stored.get("path")}}
    except Exception as exc:
        report = {"schema_version": 1, "kind": "mdoc_check_error", "status": "incomplete", "error": str(exc)}
        code = 4
    else:
        code = 4 if report["status"] == "incomplete" else 3 if report["status"] == "blocked" else 0
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        counts = report.get("counts", {})
        print(f"状态: {report['status']}")
        if counts:
            print(f"错误: {counts.get('effective_errors', 0)}，警告: {counts.get('effective_warnings', 0)}")
        report_path = report.get("path") or (report.get("report") or {}).get("path")
        if report_path:
            print(f"报告: {report_path}")
        if report.get("error"):
            print(f"错误信息: {report['error']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
