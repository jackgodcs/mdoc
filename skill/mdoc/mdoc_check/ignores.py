from __future__ import annotations

import hashlib
import threading
import time
import uuid
from pathlib import Path

from ruamel.yaml import YAML


LOCK = threading.Lock()


def _path(root: Path, language: str | None = None) -> Path:
    return root / ".mdoc" / (f"check-ignores.{language}.yaml" if language else "check-ignores.yaml")


def _load(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    value = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    if value.get("schema_version") != 1 or not isinstance(value.get("ignores", []), list):
        raise ValueError(f"Invalid ignore file: {path}")
    return value.get("ignores", [])


def load(root: Path, language: str) -> tuple[list[dict], list[Path]]:
    paths = [_path(root), _path(root, language)]
    return [item for path in paths for item in _load(path)], [path for path in paths if path.is_file()]


def _write(path: Path, ignores: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = __import__("io").StringIO()
    YAML().dump({"schema_version": 1, "ignores": ignores}, stream)
    path.write_text(stream.getvalue(), encoding="utf-8")


def key(item: dict, anchor: str, occurrence: int = 0) -> str:
    value = "\0".join(str(item.get(name, "")) for name in ("book", "checker", "native_rule", "rule", "severity", "mandatory", "path", "message"))
    return hashlib.sha256(f"{value}\0{anchor.strip()}\0{occurrence}".encode("utf-8")).hexdigest()


def annotate(findings: list[dict], physical_files: dict[str, Path]) -> None:
    cache = {}
    occurrences = {}
    for item in findings:
        logical = item["path"].split("/", 1)[-1]
        path = physical_files.get(logical)
        anchor = ""
        if path and path.suffix.lower() in {".md", ".markdown"}:
            try:
                lines = cache.setdefault(path, path.read_text(encoding="utf-8").splitlines())
                if 0 < item["line"] <= len(lines):
                    anchor = lines[item["line"] - 1]
            except (OSError, UnicodeDecodeError):
                pass
        identity = key(item, anchor)
        occurrence = occurrences.get(identity, 0)
        occurrences[identity] = occurrence + 1
        item["finding_id"] = key(item, anchor, occurrence)
        item["anchor"] = anchor


def apply(findings: list[dict], rules: list[dict], locale: str | None = None) -> list[dict]:
    if locale:
        rules = [item for item in rules if str(item.get("path", "")).startswith(locale + "/")]
    active = {item.get("finding_id"): item for item in rules}
    matched = set()
    for item in findings:
        rule = active.get(item.get("finding_id"))
        if rule and not item.get("mandatory"):
            item["ignore_id"] = rule["id"]
            item["suppression"] = "active"
            matched.add(rule["id"])
    return [{**item, "status": "active" if item.get("id") in matched else "stale"} for item in rules]


def add(root: Path, finding: dict, language: str | None = None, reason: str | None = None) -> dict:
    if finding.get("mandatory"):
        raise ValueError("Mandatory findings cannot be ignored.")
    path = _path(root, language)
    with LOCK:
        items = _load(path)
        finding_id = finding.get("finding_id")
        if not finding_id:
            raise ValueError("Finding cannot be ignored without a stable id.")
        existing = next((item for item in items if item.get("finding_id") == finding_id), None)
        if existing:
            return existing
        item = {
            "id": uuid.uuid4().hex,
            "finding_id": finding_id,
            "path": finding["path"],
            "rule": finding["rule"],
            "reason": (reason or "用户通过 mdoc 检查报告忽略此问题").strip(),
            "created_at": int(time.time()),
        }
        items.append(item)
        _write(path, items)
        return item


def remove(root: Path, ignore_id: str) -> None:
    with LOCK:
        for path in [_path(root), *root.joinpath(".mdoc").glob("check-ignores.*.yaml")]:
            items = _load(path)
            kept = [item for item in items if item.get("id") != ignore_id]
            if len(kept) != len(items):
                _write(path, kept)
                return
    raise ValueError("Unknown ignore id.")
