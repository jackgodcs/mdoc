#!/usr/bin/env python3
"""Render only PDF pages needed by the PDF Check viewer."""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path


def render_page(pdf: Path, page: int, output: Path, dpi: int = 144):
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(pdf)
        try:
            if page < 1 or page > len(document):
                raise RuntimeError(f"PDF page is out of range: {page}")
            pdf_page = document[page - 1]
            try:
                bitmap = pdf_page.render(scale=dpi / 72)
                try:
                    bitmap.to_pil().save(output, format="PNG")
                finally:
                    bitmap.close()
            finally:
                pdf_page.close()
        finally:
            document.close()
        return
    except ImportError as exc:
        raise RuntimeError("pypdfium2 is unavailable") from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"pypdfium2 did not produce a page image: {exc}") from exc


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
