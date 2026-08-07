#!/usr/bin/env python3
"""Render only PDF pages needed by the PDF Check viewer."""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path


def render_page(pdf: Path, page: int, output: Path, dpi: int = 144):
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = output.with_suffix("")
    executable = shutil.which("pdftoppm.exe") or shutil.which("pdftoppm") or shutil.which("pdftoppm.cmd")
    if executable and Path(executable).suffix.lower() == ".cmd":
        candidate = (Path(executable).resolve().parent / ".." / ".." / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe").resolve()
        if candidate.is_file():
            executable = str(candidate)
    if not executable:
        raise RuntimeError("pdftoppm is unavailable")
    arguments = ["-f", str(page), "-singlefile", "-r", str(dpi), "-png", str(pdf), str(prefix)]
    command = (["cmd.exe", "/d", "/c", executable] if Path(executable).suffix.lower() in {".cmd", ".bat"} else [executable]) + arguments
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    generated = prefix.with_suffix(".png")
    if completed.returncode != 0 or not generated.exists():
        raise RuntimeError(completed.stderr or "pdftoppm did not produce a page image")


def prepare_viewer_resources(pdf: Path | dict[str, Path], report: dict, work_root: Path) -> Path:
    work_root.mkdir(parents=True, exist_ok=True)
    run = work_root / f".run-{uuid.uuid4().hex}"
    run.mkdir()
    pdfs = pdf if isinstance(pdf, dict) else {report.get("artifacts", [{"id": "pdf"}])[0]["id"]: pdf}
    for artifact_id, source_pdf in pdfs.items():
        artifact_root = run / "artifacts" / artifact_id
        artifact_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_pdf, artifact_root / "check.pdf")
        problem_pages = sorted({page for item in report.get("findings", []) if item.get("artifact_id") == artifact_id for page in item.get("pdf_pages", [])})
        for page in problem_pages:
            render_page(artifact_root / "check.pdf", page, run / "problem-pages" / artifact_id / f"page-{page:06d}.png")
    current = work_root / "current"
    old = work_root / ".old-current"
    if old.exists():
        shutil.rmtree(old)
    if current.exists():
        current.replace(old)
    run.replace(current)
    if old.exists():
        shutil.rmtree(old)
    cleanup_runs(work_root)
    return current


def cleanup_runs(work_root: Path, maximum_age_seconds: int = 86400):
    now = time.time()
    if not work_root.exists():
        return
    for path in work_root.glob(".run-*"):
        if maximum_age_seconds == 0 or now - path.stat().st_mtime > maximum_age_seconds:
            shutil.rmtree(path, ignore_errors=True)


def finalize(work_root: Path):
    if work_root.exists():
        shutil.rmtree(work_root)
