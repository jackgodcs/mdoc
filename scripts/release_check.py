#!/usr/bin/env python3
"""Fail a release when public-source hygiene or version invariants are broken."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".html", ".css", ".js", ".cmd", ".ps1", ""}
FORBIDDEN_NAMES = {"workspace.local.yaml", "task.local.yaml", "manual_visual_validation.py", "test_manual_visual_validation.py"}
FORBIDDEN_TEXT = [
    ("legacy brand", re.compile(r"greenvalley-manual|GreenValley")),
    ("legacy rule prefix", re.compile(r"GVMD-|GVVR:")),
    ("removed visual review", re.compile(r"visual_review|manual_visual_validation")),
    ("absolute Windows path", re.compile(r"(?i)(?<![A-Za-z])[A-Z]:[\\/]")),
]
ALLOW_ABSOLUTE_IN = {ROOT / "README.md"}
SCANNER = Path(__file__).resolve()


def main() -> int:
    problems = []
    for path in sorted(ROOT.rglob("*")):
        if ".git" in path.parts or not path.is_file():
            continue
        if path.name in FORBIDDEN_NAMES:
            problems.append(f"forbidden file: {path.relative_to(ROOT)}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path == SCANNER:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in FORBIDDEN_TEXT:
            if label == "absolute Windows path" and path in ALLOW_ABSOLUTE_IN:
                continue
            if pattern.search(source):
                problems.append(f"{label}: {path.relative_to(ROOT)}")
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if version != "1.0.0":
        problems.append(f"unexpected VERSION: {version}")
    if problems:
        print("RELEASE CHECK FAILED")
        print("\n".join(f"- {item}" for item in problems))
        return 1
    print("RELEASE CHECK PASSED")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
