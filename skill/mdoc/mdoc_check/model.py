from __future__ import annotations

import hashlib


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def finding(rule: str, severity: str, path: str, message: str, line: int, column: int, *, checker: str = "mdoc", native_rule: str | None = None, mandatory: bool = False, end_line: int | None = None, end_column: int | None = None, suggestions: list[str] | None = None, details: dict | None = None) -> dict:
    value = {
        "checker": checker,
        "native_rule": native_rule or rule,
        "rule": rule,
        "severity": severity,
        "mandatory": mandatory,
        "path": path,
        "line": line,
        "column": column,
        "message": message,
        "native_message": message,
        "suggestions": suggestions or [],
    }
    if end_line is not None:
        value["end_line"] = end_line
    if end_column is not None:
        value["end_column"] = end_column
    if details is not None:
        value["details"] = details
    value["fingerprint"] = digest(f"{rule}\0{path}\0{line}\0{column}\0{message}")
    return value
