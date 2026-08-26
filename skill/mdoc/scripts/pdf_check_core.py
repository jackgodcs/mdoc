#!/usr/bin/env python3
"""Core report model and deterministic checks for mdoc PDF Check."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

RULE_VERSION = "1"


class PdfCheckError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def fingerprint(finding: dict) -> str:
    stable = [finding.get("rule_id"), finding.get("artifact_id"), finding.get("source_locations", []), finding.get("regions", []), finding.get("evidence", {}), RULE_VERSION]
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def count_findings(findings: list[dict]) -> dict:
    active = [item for item in findings if item.get("status") != "ignored-by-user"]
    return {
        "effective_errors": sum(item.get("severity") == "error" and item.get("effective_blocking", False) for item in active),
        "ignored_errors": sum(item.get("severity") == "error" and item.get("status") == "ignored-by-user" for item in findings),
        "task_errors": sum(item.get("severity") == "error" and item.get("scope") == "task" for item in active),
        "artifact_errors": sum(item.get("severity") == "error" and item.get("scope") == "artifact" for item in active),
        "book_existing_errors": sum(item.get("severity") == "error" and item.get("scope") == "book-existing" for item in active),
        "warnings": sum(item.get("severity") == "warning" for item in active),
        "suggestions": sum(item.get("severity") == "suggestion" for item in active),
        "not_evaluable": sum(item.get("status") == "not-evaluable" for item in active),
    }


def load_overrides(path: Path | None) -> dict[str, dict]:
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["finding_fingerprint"]: item for item in data.get("overrides", [])}


def finding(rule_id: str, severity: str, confidence: str, artifact_id: str, pages: list[int], message: str, evidence: dict | None = None, regions: list[dict] | None = None, scope: str = "artifact", blocking: bool | None = None) -> dict:
    item = {
        "id": "", "rule_id": rule_id, "severity": severity, "confidence": confidence, "scope": scope,
        "effective_blocking": severity == "error" if blocking is None else blocking, "status": "new",
        "artifact_id": artifact_id, "pdf_pages": pages, "regions": regions or [], "source_locations": [],
        "message": message, "suggested_fix": None, "evidence": evidence or {},
    }
    item["fingerprint"] = fingerprint(item)
    return item


def inspect_pdf(pdf_path: Path, artifact_id: str) -> tuple[list[dict], list[dict]]:
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                raise PdfCheckError("PDF has no pages")
            pages, findings = [], []
            common_size = None
            for number, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                words = page.extract_words() or []
                images = page.images or []
                pages.append({"page": number, "width_pt": page.width, "height_pt": page.height, "text_length": len(text), "image_count": len(images)})
                size = (round(page.width, 1), round(page.height, 1))
                common_size = common_size or size
                if number > 1 and size != common_size:
                    findings.append(finding("MDOC-PDF-PAGE-GEOMETRY-CHANGED", "warning", "probable", artifact_id, [number], "Page size or orientation differs from the first content page", {"page_size": size, "reference_size": common_size}, scope="book-existing", blocking=False))
                if "MDOC-MAP:" in text:
                    item = finding("MDOC-PDF-MARKER-LEAK", "error", "exact", artifact_id, [number], "Check PDF contains a mdoc mapping marker", {"marker": "MDOC-MAP:"})
                    marker = re.search(r"MDOC-MAP:([^:]+):(\d+):\S+", text)
                    if marker:
                        item["source_locations"] = [{"file": marker.group(1), "start_line": int(marker.group(2)), "end_line": int(marker.group(2)), "mapping_method": "embedded-marker", "confidence": "exact"}]
                    findings.append(item)
                if not text.strip() and not images and not page.rects and not page.lines and not page.curves:
                    findings.append(finding("MDOC-PDF-UNEXPECTED-BLANK-PAGE", "warning", "probable", artifact_id, [number], "Page has no visible text, images, or drawing objects", {"page": number}, scope="book-existing", blocking=False))
                elif len(text.strip()) < 30 and not images:
                    findings.append(finding("MDOC-PDF-PAGE-SPARSE", "suggestion", "review", artifact_id, [number], "Page contains very little visible content", {"characters": len(text.strip())}, scope="book-existing", blocking=False))
                small_chars = []
                for char in page.chars or []:
                    if char.get("text") == "�":
                        region = {"page": number, "bbox_pt": [char.get("x0"), char.get("top"), char.get("x1"), char.get("bottom")]}
                        findings.append(finding("MDOC-PDF-FONT-GLYPH-MISSING", "error", "exact", artifact_id, [number], "PDF text contains a replacement character", {"character": "�"}, [region]))
                    if float(char.get("size", 99)) < 7:
                        small_chars.append(char)
                if small_chars:
                    region = {"page": number, "bbox_pt": [min(char.get("x0", 0) for char in small_chars), min(char.get("top", 0) for char in small_chars), max(char.get("x1", 0) for char in small_chars), max(char.get("bottom", 0) for char in small_chars)]}
                    findings.append(finding("MDOC-PDF-TEXT-TOO-SMALL", "suggestion", "review", artifact_id, [number], "Page contains text smaller than the readable-size threshold", {"minimum_font_size_pt": min(float(char.get("size", 99)) for char in small_chars), "character_count": len(small_chars)}, [region], scope="book-existing", blocking=False))
                for word in words:
                    if word.get("x0", 0) < -1 or word.get("x1", 0) > page.width + 1 or word.get("top", 0) < -1 or word.get("bottom", 0) > page.height + 1:
                        region = {"page": number, "bbox_pt": [word.get("x0"), word.get("top"), word.get("x1"), word.get("bottom")]}
                        findings.append(finding("MDOC-PDF-CONTENT-OUTSIDE-PAGE", "error", "exact", artifact_id, [number], "Text object extends outside the PDF page", {"text": word.get("text")}, [region]))
            return pages, findings
    except PdfCheckError:
        raise
    except Exception as exc:
        raise PdfCheckError(f"PDF cannot be parsed: {exc}") from exc


def apply_overrides(findings: list[dict], overrides: dict[str, dict], input_digest: str):
    for item in findings:
        override = overrides.get(item["fingerprint"])
        if override and override.get("input_digest") == input_digest and override.get("rule_version") == RULE_VERSION:
            item["status"] = "ignored-by-user"
            item["effective_blocking"] = False
            item["ignore_reason"] = override.get("reason")


def write_report(data: dict, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    (output / "pdf-check.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = data["counts"]
    lines = ["# PDF Check", "", f"Status: {data['status']}", f"Effective errors: {counts['effective_errors']}", f"Ignored errors: {counts['ignored_errors']}", f"Warnings: {counts['warnings']}", f"Suggestions: {counts['suggestions']}", ""]
    lines.extend(f"- {item['severity']}/{item['confidence']} {item['rule_id']} page {','.join(map(str, item['pdf_pages']))}: {item['message']}" for item in data["findings"][:200])
    (output / "pdf-check-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def aggregate_reports(reports: list[dict], output: Path) -> dict:
    findings, artifacts, pages = [], [], []
    for report in reports:
        offset = len(findings)
        for index, item in enumerate(report.get("findings", []), offset + 1):
            item = dict(item)
            item["id"] = f"PDF-{index:04d}"
            findings.append(item)
        artifacts.extend(report.get("artifacts", []))
        pages.extend({**page, "artifact_id": report["artifacts"][0]["id"]} for page in report.get("pages", []))
    digest = hashlib.sha256("".join(item["sha256"] for item in artifacts).encode()).hexdigest()
    data = {"schema_version": 1, "status": "completed", "generated_at": int(time.time()), "rule_version": RULE_VERSION, "input_digest": digest, "counts": count_findings(findings), "artifacts": artifacts, "pages": pages, "findings": findings}
    write_report(data, output)
    return data


def check_existing_pdf(book_root: Path, pdf_path: Path, output: Path, options: dict | None = None) -> dict:
    options = options or {}
    if not pdf_path.is_file() or pdf_path.read_bytes()[:5] != b"%PDF-":
        raise PdfCheckError(f"Invalid PDF artifact: {pdf_path}")
    digest = sha256(pdf_path)
    pages, findings = inspect_pdf(pdf_path, options.get("artifact_id", "pdf"))
    if options.get("source_map"):
        page_map = options["source_map"]
        for item in findings:
            locations = []
            for page in item.get("pdf_pages", []):
                locations.extend(page_map.get(str(page), page_map.get(page, [])))
            if locations and not item.get("source_locations"):
                item["source_locations"] = locations
    task_files = set(options.get("task_files", []))
    whole_book = options.get("task_scope_policy") == "whole-book" or not task_files
    for item in findings:
        sources = {entry.get("file") for entry in item.get("source_locations", [])}
        if sources & task_files:
            item["scope"], item["effective_blocking"] = "task", item["severity"] == "error"
        elif item["scope"] != "artifact" and whole_book:
            item["scope"], item["effective_blocking"] = "book", item["severity"] == "error"
        elif item["scope"] != "artifact":
            item["scope"], item["effective_blocking"] = "book-existing", False
    for index, item in enumerate(findings, 1):
        item["id"] = f"PDF-{index:04d}"
    apply_overrides(findings, load_overrides(options.get("overrides")), digest)
    data = {
        "schema_version": 1, "status": "completed", "generated_at": int(time.time()), "rule_version": RULE_VERSION,
        "input_digest": digest, "book_root": book_root.as_posix(), "counts": count_findings(findings),
        "artifacts": [{"id": options.get("artifact_id", "pdf"), "locale": options.get("locale", "unknown"), "required": options.get("required", True), "file_name": pdf_path.name, "sha256": digest, "page_count": len(pages), "status": "completed"}],
        "pages": pages, "findings": findings,
    }
    write_report(data, output)
    return data


def confirm_ignore(report: dict, finding_id: str, overrides_path: Path, reason: str):
    item = next((entry for entry in report.get("findings", []) if entry.get("id") == finding_id), None)
    if not item:
        raise PdfCheckError(f"Unknown finding: {finding_id}")
    artifact = next((entry for entry in report.get("artifacts", []) if entry.get("id") == item.get("artifact_id")), None)
    input_digest = artifact.get("sha256") if artifact else report["input_digest"]
    existing = json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.exists() else {"schema_version": 1, "overrides": []}
    existing["overrides"] = [entry for entry in existing.get("overrides", []) if entry.get("finding_fingerprint") != item["fingerprint"]]
    existing["overrides"].append({"finding_fingerprint": item["fingerprint"], "rule_id": item["rule_id"], "action": "ignore", "reason": reason, "input_digest": input_digest, "rule_version": RULE_VERSION})
    overrides_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = overrides_path.with_suffix(overrides_path.suffix + ".tmp")
    temporary.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(overrides_path)
