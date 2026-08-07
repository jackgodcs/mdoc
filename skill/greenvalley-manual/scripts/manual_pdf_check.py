#!/usr/bin/env python3
"""One-command PDF visual problem checker for GreenValley manuals."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from pdf_check_build import run_adapter
from pdf_check_core import PdfCheckError, aggregate_reports, check_existing_pdf
from pdf_check_mapping import content_map, equivalent_pagination, marker_map, prepare_instrumented_source
from pdf_check_render import cleanup_runs, finalize, prepare_viewer_resources
from pdf_check_server import serve


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


def task_context(workspace: Path, task_id: str) -> dict:
    from manual_lint import deep_merge, simple_yaml
    workspace = workspace.resolve()
    base = simple_yaml(workspace / "workspace.yaml")
    local = simple_yaml(workspace / "workspace.local.yaml") if (workspace / "workspace.local.yaml").exists() else {}
    config = deep_merge(base, local)
    repository = Path(config["repository"]["root"])
    if not repository.is_absolute():
        repository = workspace / repository
    book = (repository / config["manual"]["active_version"]).resolve()
    task_dir = workspace / config.get("tasks", {}).get("root", "manual-tasks") / task_id
    task = simple_yaml(task_dir / "task.yaml")
    structure = simple_yaml(task_dir / "structure.yaml") if (task_dir / "structure.yaml").exists() else {}
    product = simple_yaml(workspace / config["product"]["profile"])
    content = product.get("manual_layout", {}).get("content_directory", "Main")
    document_path = task.get("target", {}).get("document_path", "")
    locales = [path.name for path in book.iterdir() if path.is_dir()]
    task_files = {(Path(locale) / content / document_path / page["file"]).as_posix() for locale in locales for page in structure.get("pages", []) if isinstance(page, dict) and page.get("file")}
    pdf_config = config.get("validation", {}).get("pdf_check", {})
    launcher = repository / ".work" / "greenvalley-manual" / task_id / "open-pdf-check.cmd"
    return {"book": book, "task_dir": task_dir, "task_files": task_files, "artifacts": pdf_config.get("artifacts", []), "scope_policy": pdf_config.get("task_scope_policy", "task-only"), "output": task_dir / "reports" / "quality-gate", "work": task_dir / ".pdf-check", "overrides": task_dir / "pdf-check-overrides.json", "launcher": launcher}


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
    reports, pdfs = [], {}
    if not context["artifacts"]:
        raise PdfCheckError("PDF source is not configured. Add validation.pdf_check.artifacts to workspace.local.yaml.")
    for artifact in context["artifacts"]:
        source = artifact.get("source", {})
        artifact_work = context["work"] / ".artifacts" / artifact["id"]
        artifact_work.mkdir(parents=True, exist_ok=True)
        if source.get("mode") == "existing-pdf":
            pdf = Path(source["path"])
            if not pdf.is_absolute(): pdf = workspace / pdf
        elif source.get("mode") == "build":
            pdf = artifact_work / "check.pdf"
            adapter = {**artifact, **source}
            mapping = None
            try:
                if source.get("protocol") == "pdf-check-v1":
                    instrumented = prepare_instrumented_source(context["book"], artifact_work / "instrumented-source")
                    mapping_pdf = artifact_work / "mapping.pdf"
                    mapping_result = run_adapter(adapter, instrumented, mapping_pdf, "mapping")
                    if mapping_result["status"] != "passed":
                        raise PdfCheckError(f"PDF mapping build failed: {artifact['id']}")
                result = run_adapter(adapter, context["book"], pdf, "check")
                if result["status"] != "passed": raise PdfCheckError(f"PDF build failed: {artifact['id']}")
                if source.get("protocol") == "pdf-check-v1" and equivalent_pagination(mapping_pdf, pdf):
                    mapping = marker_map(mapping_pdf)
            finally:
                if source.get("protocol") == "pdf-check-v1":
                    shutil.rmtree(artifact_work / "instrumented-source", ignore_errors=True)
                    (artifact_work / "mapping.pdf").unlink(missing_ok=True)
        else:
            raise PdfCheckError(f"PDF source is not configured: {artifact.get('id')}")
        pdfs[artifact["id"]] = pdf.resolve()
        if source.get("mode") != "build" or mapping is None:
            mapping = content_map(context["book"], pdf, artifact.get("locale"))
        reports.append(check_existing_pdf(context["book"], pdf, artifact_work / "report", {"artifact_id": artifact["id"], "locale": artifact.get("locale", "unknown"), "required": artifact.get("required", True), "overrides": context["overrides"], "task_files": context["task_files"], "task_scope_policy": context["scope_policy"], "source_map": mapping}))
    return aggregate_reports(reports, context["output"]), context, pdfs


def task_pdf_paths(workspace: Path, context: dict) -> dict[str, Path]:
    result = {}
    for artifact in context["artifacts"]:
        source = artifact.get("source", {})
        if source.get("mode") == "existing-pdf":
            path = Path(source["path"])
            result[artifact["id"]] = (path if path.is_absolute() else workspace / path).resolve()
        elif source.get("mode") == "build":
            result[artifact["id"]] = (context["work"] / ".artifacts" / artifact["id"] / "check.pdf").resolve()
    return result


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
        for name in ("pdfplumber", "pypdf", "PIL"):
            try:
                __import__(name); modules[name] = "available"
            except ImportError:
                modules[name] = "unavailable"
        tools = {name: bool(shutil.which(name) or shutil.which(name + ".cmd")) for name in ("pdfinfo", "pdftoppm")}
        print(json.dumps({"modules": modules, "tools": tools, "platform": sys.platform}, ensure_ascii=False, indent=2))
        return 0 if modules["pdfplumber"] == "available" else 4
    if getattr(args, "workspace", None) and not args.task:
        print("PDF CHECK FAILED: --task is required with --workspace")
        return 2
    context = None
    if getattr(args, "workspace", None):
        context = task_context(args.workspace.resolve(), args.task)
        args.book_root, args.output, args.work_root = context["book"], context["output"], context["work"]
    elif args.command in {"serve", "verify", "finalize", "clean"} and (not args.output or not args.work_root):
        print("PDF CHECK FAILED: --output and --work-root are required with --book-root")
        return 2
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
    try:
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
