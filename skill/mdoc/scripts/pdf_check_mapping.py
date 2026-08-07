#!/usr/bin/env python3
"""Build best-effort Markdown-to-PDF mappings for existing and instrumented PDFs."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path


HEADING = re.compile(r"^#{1,6}\s+(.+?)(?:\s+#+)?\s*$")


def content_map(book_root: Path, pdf_path: Path, locale: str | None = None) -> dict[str, list[dict]]:
    import pdfplumber
    page_text = []
    with pdfplumber.open(pdf_path) as pdf:
        page_text = [(index, (page.extract_text() or "").casefold()) for index, page in enumerate(pdf.pages, 1)]
    result: dict[str, list[dict]] = {}
    search_root = book_root / locale if locale and locale != "unknown" and (book_root / locale).is_dir() else book_root
    for path in sorted(search_root.rglob("*.md")):
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError):
            continue
        title = next((HEADING.match(line).group(1).strip() for line in lines if HEADING.match(line)), "")
        if len(title) < 3:
            continue
        matches = [page for page, text in page_text if title.casefold() in text]
        if len(matches) == 1:
            result.setdefault(str(matches[0]), []).append({"file": path.relative_to(book_root).as_posix(), "start_line": next(index for index, line in enumerate(lines, 1) if HEADING.match(line)), "end_line": next(index for index, line in enumerate(lines, 1) if HEADING.match(line)), "mapping_method": "content-match", "confidence": "probable"})
    return result


def prepare_instrumented_source(book_root: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(book_root, destination)
    for path in destination.rglob("*.md"):
        relative = path.relative_to(destination).as_posix()
        source = path.read_text(encoding="utf-8-sig")
        marker = f'<span style="font-size:1px;color:white">MDOC-MAP:{relative}:1:{uuid.uuid4().hex[:8]}</span>'
        path.write_text(marker + "\n\n" + source, encoding="utf-8")
    return destination


def marker_map(pdf_path: Path) -> dict[str, list[dict]]:
    import pdfplumber
    result = {}
    pattern = re.compile(r"MDOC-MAP:([^:]+):(\d+):\S+")
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            for match in pattern.finditer(page.extract_text() or ""):
                result.setdefault(str(page_number), []).append({"file": match.group(1), "start_line": int(match.group(2)), "end_line": int(match.group(2)), "mapping_method": "embedded-marker", "confidence": "exact"})
    return result


def equivalent_pagination(mapping_pdf: Path, check_pdf: Path) -> bool:
    from pypdf import PdfReader
    left, right = PdfReader(str(mapping_pdf)), PdfReader(str(check_pdf))
    if len(left.pages) != len(right.pages):
        return False
    return all(tuple(round(float(value), 1) for value in page.mediabox[2:]) == tuple(round(float(value), 1) for value in other.mediabox[2:]) for page, other in zip(left.pages, right.pages))
