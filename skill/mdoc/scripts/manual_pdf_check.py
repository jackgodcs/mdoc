#!/usr/bin/env python3
"""One-command PDF visual problem checker for mdoc manuals."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

from pdf_check_core import PdfCheckError, aggregate_reports, check_existing_pdf
from pdf_check_mapping import content_map
from pdf_check_render import cleanup_runs, finalize, prepare_viewer_resources
from pdf_check_server import serve

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from mdoc_core.adapters import run_build
from mdoc_core.config import load_task, load_workspace
from mdoc_core.io import relative_path
from mdoc_core.state import load_state
from mdoc_core.virtual_book import VirtualBook


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("check", "open"):
        item = commands.add_parser(name)
        group = item.add_mutually_exclusive_group(required=True)
        group.add_argument("--book-root", type=Path)
        group.add_argument("--workspace", type=Path)
        item.add_argument("--task")
        item.add_argument("--pdf", type=Path)
        item.add_argument("--output", type=Path)
        item.add_argument("--artifact-id", default="pdf")
        item.add_argument("--locale", default="unknown")
        item.add_argument("--overrides", type=Path)
        item.add_argument("--work-root", type=Path)
    for name in ("serve", "verify", "finalize", "clean"):
        item = commands.add_parser(name)
        group = item.add_mutually_exclusive_group(required=True)
        group.add_argument("--book-root", type=Path)
        group.add_argument("--workspace", type=Path)
        item.add_argument("--task")
        item.add_argument("--output", type=Path)
        item.add_argument("--work-root", type=Path)
    commands.add_parser("doctor")
    return result


def selected_pdf_adapter(task) -> tuple[str, dict]:
    book = task.workspace.config["books"][task.definition["task"]["book"]]
    adapter_id = task.definition.get("quality_gate", {}).get("build_adapter") or book.get("release_build_adapter")
    if not adapter_id:
        raise PdfCheckError("No PDF build adapter is selected. Set task quality_gate.build_adapter or book.release_build_adapter.")
    adapter = task.workspace.config.get("build_adapters", {}).get(adapter_id)
    if not isinstance(adapter, Mapping):
        raise PdfCheckError(f"Unknown build adapter: {adapter_id}")
    if adapter.get("artifact_kind", "generic") != "pdf":
        raise PdfCheckError(f"Build adapter is not a PDF adapter: {adapter_id}")
    return adapter_id, dict(adapter)


def changed_markdown_files(view: VirtualBook) -> set[str]:
    result: set[str] = set()
    for locale in sorted(view.book["locales"]):
        locale_root = relative_path(view.book["locales"][locale]["root"], f"locales.{locale}.root").as_posix()
        for item in view.files(locale):
            if item.changed and item.path.lower().endswith(".md"):
                result.add((Path(locale_root) / Path(*item.path.split("/"))).as_posix())
    return result


def task_context(workspace: Path, task_id: str) -> dict:
    workspace_context = load_workspace(workspace.resolve())
    task = load_task(workspace_context, task_id)
    state = load_state(task.directory / "task-state.json", task.task_id)
    published = state.get("status") in {"ready_for_review", "accepted"}
    view = VirtualBook.task(task, published=published)
    adapter_id, adapter = selected_pdf_adapter(task)
    work = task.directory / "temporary" / "pdf-check"
    source = work / "source"
    view.materialize(source)
    return {
        "workspace": workspace_context,
        "task": task,
        "state": state,
        "view": view,
        "adapter_id": adapter_id,
        "adapter": adapter,
        "book": source,
        "task_files": changed_markdown_files(view),
        "output": task.directory / "reports" / "pdf-check",
        "work": work,
        "overrides": task.directory / "reports" / "pdf-check-overrides.json",
        "launcher": work / "open-pdf-check.cmd",
    }


def write_task_launcher(path: Path, workspace: Path, task_id: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "@echo off\r\n"
        f'"{sys.executable}" -B "{Path(__file__).resolve()}" open --workspace "{workspace.resolve()}" --task "{task_id}"\r\n'
        "if errorlevel 2 pause\r\n"
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="")
    temporary.replace(path)


def run_task(workspace: Path, task_id: str) -> tuple[dict, dict, dict[str, Path]]:
    context = task_context(workspace, task_id)
    record = run_build(context["workspace"], context["view"], context["adapter_id"], context["work"] / "builds")
    artifact = Path(record.get("artifact") or "")
    if record.get("exit_code") != 0 or not artifact.is_file():
        pdf_check = record.get("pdf_check") or {}
        raise PdfCheckError(pdf_check.get("error") or f"PDF build failed: {context['adapter_id']}")
    stable_pdf = context["work"] / "artifacts" / context["adapter_id"] / "check.pdf"
    stable_pdf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact, stable_pdf)
    mapping = content_map(context["book"], stable_pdf, context["adapter"].get("locale"))
    report = check_existing_pdf(
        context["book"], stable_pdf, context["work"] / "artifacts" / context["adapter_id"] / "report",
        {
            "artifact_id": context["adapter_id"],
            "locale": context["adapter"].get("locale", "unknown"),
            "required": True,
            "overrides": context["overrides"],
            "task_files": context["task_files"],
            "task_scope_policy": "task-only",
            "source_map": mapping,
        },
    )
    aggregate = aggregate_reports([report], context["output"])
    return aggregate, context, {context["adapter_id"]: stable_pdf.resolve()}


def task_pdf_paths(workspace: Path, context: dict) -> dict[str, Path]:
    pdf = context["work"] / "artifacts" / context["adapter_id"] / "check.pdf"
    return {context["adapter_id"]: pdf.resolve()} if pdf.is_file() else {}


def verify_viewer_resources(report: dict, work_root: Path) -> bool:
    from pdf_check_core import sha256
    for artifact in report.get("artifacts", []):
        current = work_root.resolve() / "current" / "artifacts" / artifact["id"] / "check.pdf"
        if not current.is_file() or sha256(current) != artifact.get("sha256"):
            return False
    return bool(report.get("artifacts"))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "doctor":
        modules = {}
        for name in ("pdfplumber", "pypdf", "pypdfium2", "PIL"):
            try:
                __import__(name); modules[name] = "available"
            except ImportError:
                modules[name] = "unavailable"
        tools = {}
        print(json.dumps({"modules": modules, "tools": tools, "platform": sys.platform}, ensure_ascii=False, indent=2))
        return 0 if modules["pdfplumber"] == "available" else 4
    if getattr(args, "workspace", None) and not args.task:
        print("PDF CHECK FAILED: --task is required with --workspace")
        return 2
    try:
        context = None
        if getattr(args, "workspace", None) and args.command in {"serve", "verify", "finalize", "clean"}:
            context = task_context(args.workspace.resolve(), args.task)
            args.book_root, args.output, args.work_root = context["book"], context["output"], context["work"]
        elif args.command in {"serve", "verify", "finalize", "clean"} and (not args.output or not args.work_root):
            raise PdfCheckError("--output and --work-root are required with --book-root")
        report_path = args.output.resolve() / "pdf-check.json" if getattr(args, "output", None) else None
        if args.command in {"finalize", "clean"}:
            finalize(args.work_root.resolve()); print(f"CLEANED: {args.work_root.resolve()}"); return 0
        if args.command == "verify":
            if not report_path.exists(): return 3
            data = json.loads(report_path.read_text(encoding="utf-8"))
            return 0 if verify_viewer_resources(data, args.work_root.resolve()) else 3
        if args.command == "serve":
            if not report_path.exists(): return 3
            viewer = Path(__file__).resolve().parent.parent / "assets" / "pdf-check-viewer"
            recheck = None
            preview_pdfs = None
            if context:
                recheck = [sys.executable, str(Path(__file__).resolve()), "check", "--workspace", str(args.workspace.resolve()), "--task", args.task]
                preview_pdfs = task_pdf_paths(args.workspace.resolve(), context)
            server, url = serve(report_path, args.work_root.resolve(), args.book_root.resolve(), viewer, context.get("overrides") if context else None, recheck_command=recheck, preview_pdfs=preview_pdfs)
            print(f"VIEWER: {url}"); server.serve_forever(); return 0
        if args.workspace:
            data, context, preview_pdf = run_task(args.workspace.resolve(), args.task)
            args.book_root, args.output, args.work_root, args.overrides = context["book"], context["output"], context["work"], context["overrides"]
            write_task_launcher(context["launcher"], args.workspace.resolve(), args.task)
        else:
            if not args.pdf or not args.output:
                raise PdfCheckError("--pdf and --output are required with --book-root")
            if not args.pdf.is_file() or args.pdf.read_bytes()[:5] != b"%PDF-":
                raise PdfCheckError(f"Invalid PDF artifact: {args.pdf.resolve()}")
            source_map = content_map(args.book_root.resolve(), args.pdf.resolve(), args.locale)
            data = check_existing_pdf(args.book_root.resolve(), args.pdf.resolve(), args.output.resolve(), {"artifact_id": args.artifact_id, "locale": args.locale, "overrides": args.overrides, "source_map": source_map})
            preview_pdf = {args.artifact_id: args.pdf.resolve()}
    except PdfCheckError as exc:
        print(f"PDF CHECK FAILED: {exc}")
        return 2
    print(json.dumps(data["counts"], ensure_ascii=False))
    print(f"REPORT: {args.output.resolve() / 'pdf-check.json'}")
    if args.command == "open":
        work_root = (args.work_root or args.output / ".pdf-check").resolve()
        cleanup_runs(work_root)
        prepare_viewer_resources(preview_pdf, data, work_root)
        viewer = Path(__file__).resolve().parent.parent / "assets" / "pdf-check-viewer"
        if args.workspace:
            recheck = [sys.executable, str(Path(__file__).resolve()), "check", "--workspace", str(args.workspace.resolve()), "--task", args.task]
        else:
            recheck = [sys.executable, str(Path(__file__).resolve()), "check", "--book-root", str(args.book_root.resolve()), "--pdf", str(args.pdf.resolve()), "--output", str(args.output.resolve()), "--artifact-id", args.artifact_id, "--locale", args.locale]
            if args.overrides: recheck.extend(["--overrides", str(args.overrides.resolve())])
        server, url = serve(args.output.resolve() / "pdf-check.json", work_root, args.book_root.resolve(), viewer, args.overrides, recheck_command=recheck, preview_pdfs=preview_pdf)
        print(f"VIEWER: {url}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
    return 1 if data["counts"]["effective_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
