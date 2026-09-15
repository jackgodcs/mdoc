from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from ruamel.yaml import YAML

from .core import PROTOTYPE_CONFIG, _merge, load_workspace


DEFAULTS = {
    "orphan_grace_minutes": 60,
    "inactive_report_days": 30,
    "pdf_preview_days": 7,
    "pdf_preview_max_bytes": 1073741824,
    "pdf_build_failed_days": 7,
    "pdf_build_max_bytes": 10737418240,
    "auto_interval_minutes": 30,
    "notify_freed_bytes": 104857600,
}


def configuration(workspace: Path) -> dict:
    value = dict(DEFAULTS)
    for path in (PROTOTYPE_CONFIG, workspace / ".mdoc" / "check.yaml"):
        if path.is_file(): value = _merge(value, (YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}).get("retention", {}))
    pdf_retention = (load_workspace(workspace).get("pdf") or {}).get("retention") or {}
    value["pdf_build_failed_days"] = pdf_retention.get("failed_work_days", value["pdf_build_failed_days"])
    value["pdf_build_max_bytes"] = pdf_retention.get("failed_work_max_bytes", value["pdf_build_max_bytes"])
    for key in ("orphan_grace_minutes", "inactive_report_days", "pdf_preview_days", "auto_interval_minutes"):
        if not isinstance(value.get(key), int) or isinstance(value[key], bool) or value[key] < 0: raise ValueError(f"retention.{key} must be a non-negative integer.")
    for key in ("pdf_preview_max_bytes", "pdf_build_failed_days", "pdf_build_max_bytes", "notify_freed_bytes"):
        if not isinstance(value.get(key), int) or isinstance(value[key], bool) or value[key] < 0: raise ValueError(f"retention.{key} must be a non-negative integer.")
    return value


def report_root(workspace: Path) -> Path:
    return workspace.resolve() / ".mdoc" / "reports" / "check"


def _inside(path: Path, root: Path) -> bool:
    try: path.resolve().relative_to(root.resolve()); return True
    except ValueError: return False


def _pid_alive(pid: int) -> bool:
    try: os.kill(pid, 0); return True
    except (OSError, ValueError): return False


def _active(path: Path) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8")); return _pid_alive(int(value["pid"] if isinstance(value, dict) else value))
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        try: return _pid_alive(int(path.read_text(encoding="ascii").strip()))
        except (OSError, ValueError): return False


@contextmanager
def activity(path: Path, kind: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": os.getpid(), "kind": kind, "started_at": int(time.time())}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try: yield
    finally: path.unlink(missing_ok=True)


def touch_access(directory: Path, now: float | None = None) -> None:
    now = now or time.time(); path = directory / "access.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if now - int(value.get("last_accessed_at", 0)) < 86400: return
    except (OSError, ValueError, json.JSONDecodeError): pass
    temporary = path.with_name(path.name + ".tmp"); temporary.write_text(json.dumps({"last_accessed_at": int(now)}, indent=2) + "\n", encoding="utf-8"); os.replace(temporary, path)


def _remove(path: Path, result: dict) -> None:
    if not path.exists(): return
    files = [path] if path.is_file() else list(path.rglob("*"))
    regular = [item for item in files if item.is_file()]
    result["files"] += len(regular); result["bytes"] += sum(item.stat().st_size for item in regular if item.exists())
    if path.is_dir(): shutil.rmtree(path, ignore_errors=False); result["directories"] += 1
    else: path.unlink(missing_ok=True)


def _report_cleanup(workspace: Path, config: dict, result: dict, now: float) -> None:
    root = report_root(workspace); grace = config["orphan_grace_minutes"] * 60; protected = {root.resolve()}
    if not root.is_dir(): return
    if _active(root / ".check.active.json"): return
    temporary_paths = [*root.rglob("*.tmp"), *root.rglob("selection-*")]
    temporary_paths.extend(path for path in root.rglob("tmp*") if path.is_dir() and (path / "selection.json").is_file())
    for temporary in temporary_paths:
        if temporary.exists() and now - temporary.stat().st_mtime >= grace and _inside(temporary, root): _remove(temporary, result)
    for latest in list(root.rglob("latest.json")):
        try: report = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): result["warnings"].append(f"检查报告元数据损坏，已保留：{latest}"); continue
        generation = report.get("generation"); generations = latest.parent / "generations"
        if not generation:
            if now - latest.stat().st_mtime >= grace:
                for legacy in [latest, latest.with_name("latest.meta.json"), latest.with_name("latest.findings.jsonl")]: _remove(legacy, result)
            continue
        current = (generations / generation).resolve()
        while _inside(current, root):
            protected.add(current)
            if current == root.resolve(): break
            current = current.parent
        if generation and generations.is_dir():
            for candidate in generations.iterdir():
                marker = candidate / ".active.json"
                if candidate.name != generation and now - candidate.stat().st_mtime >= grace and not _active(marker): _remove(candidate, result)
        scope = report.get("scope") or {}; context = report.get("context"); inactive = context == "book" and scope.get("kind") in {"page", "section"}
        if context == "task": inactive = not (workspace / ".mdoc" / "tasks" / str(scope.get("task", ""))).is_dir()
        access = latest.parent / "access.json"
        timestamps = [latest.stat().st_mtime, float(report.get("created_at", 0))]
        try: timestamps.append(float(json.loads(access.read_text(encoding="utf-8")).get("last_accessed_at", 0)))
        except (OSError, ValueError, json.JSONDecodeError): pass
        if inactive and config["inactive_report_days"] and now - max(timestamps) >= config["inactive_report_days"] * 86400: _remove(latest.parent, result)
    for generations in root.rglob("generations"):
        if generations.is_dir() and not any(generations.iterdir()): generations.rmdir()
    for path in list(root.rglob("[0-9]*.json")):
        if path.exists() and path.stem.isdigit() and "generations" not in path.parts and now - path.stat().st_mtime >= grace:
            findings = path.with_name(path.stem + ".findings.jsonl"); _remove(path, result); _remove(findings, result)
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
        if directory.resolve() in protected: continue
        try: directory.rmdir()
        except OSError: pass


def _pdf_preview_cleanup(workspace: Path, config: dict, result: dict, now: float) -> None:
    root = workspace.resolve() / ".mdoc" / "cache" / "pdf-preview"; grace = config["orphan_grace_minutes"] * 60; retained = []
    if not root.is_dir(): return
    for pending in root.rglob("pending-*"):
        if now - pending.stat().st_mtime >= grace: _remove(pending, result)
    for metadata in list(root.rglob("result.json")):
        try:
            value = json.loads(metadata.read_text(encoding="utf-8")); pdf = Path(value["pdf"]); accessed = max(metadata.stat().st_mtime, float(value.get("last_accessed_at", value.get("generated_at", 0))))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            if now - metadata.stat().st_mtime >= grace: _remove(metadata.parent, result)
            continue
        if not pdf.is_file():
            if now - metadata.stat().st_mtime >= grace: _remove(metadata.parent, result)
        elif config["pdf_preview_days"] and now - accessed >= config["pdf_preview_days"] * 86400: _remove(metadata.parent, result)
        else: retained.append((accessed, metadata.parent, pdf.stat().st_size + metadata.stat().st_size))
    total = sum(item[2] for item in retained)
    ordered = sorted(retained)
    for _accessed, directory, size in ordered[:-1]:
        if total <= config["pdf_preview_max_bytes"]: break
        _remove(directory, result); total -= size
    if total > config["pdf_preview_max_bytes"] and ordered: result["warnings"].append("最新 PDF 预览自身超过容量上限，已保留当前有效结果。")


def _pdf_build_cleanup(workspace: Path, config: dict, result: dict, now: float) -> None:
    root = workspace / ".mdoc" / "cache" / "pdf-builds"; lock = workspace / ".mdoc" / "locks" / "pdf-build.lock"
    if not root.is_dir() or _active(lock): return
    protected = set()
    artifacts = workspace / ".mdoc" / "artifacts" / "pdf"
    for report in artifacts.rglob("*.build.json") if artifacts.is_dir() else []:
        try:
            value = json.loads(report.read_text(encoding="utf-8")); work = Path(value.get("work", "")).resolve()
            if str(value.get("status", "")).startswith("passed") and _inside(work, root): protected.add(work)
        except (OSError, ValueError, json.JSONDecodeError): pass
    runs = []
    for directory in root.iterdir():
        if not directory.is_dir() or directory.resolve() in protected: continue
        files = [item for item in directory.rglob("*") if item.is_file()]; size = sum(item.stat().st_size for item in files); runs.append((directory.stat().st_mtime, directory, size))
    retained = []
    for modified, directory, size in sorted(runs):
        if config["pdf_build_failed_days"] and now - modified >= config["pdf_build_failed_days"] * 86400: _remove(directory, result)
        else: retained.append((modified, directory, size))
    total = sum(item[2] for item in retained)
    for _modified, directory, size in retained:
        if total <= config["pdf_build_max_bytes"]: break
        _remove(directory, result); total -= size


def _temp_cleanup(config: dict, result: dict, now: float) -> None:
    grace = config["orphan_grace_minutes"] * 60
    for path in Path(tempfile.gettempdir()).glob("mdoc-check-cspell-*.json"):
        if now - path.stat().st_mtime >= grace: _remove(path, result)


def cleanup(workspace: Path, *, automatic: bool = True, now: float | None = None) -> dict:
    workspace = workspace.resolve(); now = now or time.time(); config = configuration(workspace); root = report_root(workspace); root.mkdir(parents=True, exist_ok=True); state = root / "maintenance.json"; lock = root / ".maintenance.lock"
    if automatic:
        try:
            if now - float(json.loads(state.read_text(encoding="utf-8")).get("last_run_at", 0)) < config["auto_interval_minutes"] * 60: return {"status": "skipped", "reason": "throttled", "files": 0, "directories": 0, "bytes": 0, "warnings": []}
        except (OSError, ValueError, json.JSONDecodeError): pass
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.write(descriptor, json.dumps({"pid": os.getpid(), "started_at": int(now)}).encode()); os.close(descriptor)
    except FileExistsError:
        if _active(lock): return {"status": "skipped", "reason": "locked", "files": 0, "directories": 0, "bytes": 0, "warnings": []}
        if now - lock.stat().st_mtime < config["orphan_grace_minutes"] * 60: return {"status": "skipped", "reason": "stale-lock-grace", "files": 0, "directories": 0, "bytes": 0, "warnings": []}
        lock.unlink(missing_ok=True); return cleanup(workspace, automatic=automatic, now=now)
    result = {"status": "completed", "files": 0, "directories": 0, "bytes": 0, "warnings": []}
    try:
        for operation in (_report_cleanup, _pdf_preview_cleanup, _pdf_build_cleanup):
            try: operation(workspace, config, result, now)
            except OSError as exc: result["warnings"].append(str(exc))
        try: _temp_cleanup(config, result, now)
        except OSError as exc: result["warnings"].append(str(exc))
        state.write_text(json.dumps({"last_run_at": int(now), **result}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result
    finally: lock.unlink(missing_ok=True)
