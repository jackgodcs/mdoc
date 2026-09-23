from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path

from .core import load_workspace
from .model import digest
from .sources import source_path


GLOBAL_RULES = {"navigation.locale-parity"}


def workspace_root(workspace: Path) -> Path:
    return workspace.resolve() / ".mdoc" / "reports" / "check"


def context_directory(workspace: Path, report: dict) -> Path:
    scope = report.get("scope") or {}
    context = report.get("context", report["kind"])
    directory = workspace_root(workspace) / context
    if context == "book":
        directory /= Path(scope["book"]) / scope["locale"] / scope["kind"]
    elif context == "task":
        directory /= Path(scope["task"]) / scope.get("mode", "coordinator")
    return directory


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def source_state(path: Path) -> dict | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def source_changed(workspace: Path, report: dict, record: dict) -> bool:
    physical = _physical(workspace, report, record["path"])
    return source_state(physical) != record.get("source_state") if physical else record.get("source_state") is not None


def _counts(findings: list[dict]) -> dict:
    errors = sum(item["severity"] == "error" and item.get("suppression") != "active" for item in findings)
    warnings = sum(item["severity"] == "warning" and item.get("suppression") != "active" for item in findings)
    return {"effective_errors": errors, "effective_warnings": warnings, "ignored": sum(item.get("suppression") == "active" for item in findings), "findings": len(findings)}


def _physical(workspace: Path, report: dict, display: str) -> Path | None:
    try:
        return source_path(workspace, report, display, report.get("scope", {}).get("book"))
    except (KeyError, OSError, ValueError):
        return None


def _record(workspace: Path, report: dict, display: str, findings: list[dict]) -> dict:
    physical = _physical(workspace, report, display)
    exists = bool(physical and physical.is_file())
    suffix = Path(display).suffix.lower()
    kind = "markdown" if suffix in {".md", ".markdown"} else "image" if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"} else "resource"
    existence = "existing" if exists else "missing"
    if report.get("context") == "task":
        try:
            from .tasks import load_task
            definition, _state_value, _directory = load_task(workspace, report["scope"]["task"])
            item = next((value for value in definition["manifest"] if f"{value['locale']}/{value['path'].replace(chr(92), '/')}" == display), None)
            if item and item["action"] == "create": existence = "added" if exists else "missing"
            elif item and item["action"] == "delete": existence = "deleted" if not exists else "existing"
        except (KeyError, OSError, ValueError): pass
    counts = _counts(findings)
    return {"path": display, "kind": kind, "existence": existence, "status": "blocked" if counts["effective_errors"] else "passed", "source_state": source_state(physical) if physical else None, "checked_at": int(time.time()), "counts": counts, "findings": findings}


def records_from_report(workspace: Path, report: dict) -> list[dict]:
    files = set()
    input_sets = [report.get("inputs") or {}, *(unit.get("inputs") or {} for unit in report.get("units", []))]
    for inputs in input_sets:
        for key, values in inputs.items():
            if key in {"markdown", "navigation", "images", "resources"} and isinstance(values, list):
                files.update(value for value in values if isinstance(value, str) and "/" in value and not Path(value).is_absolute())
    files.update(item["path"] for item in report.get("findings", []) if item.get("rule") not in GLOBAL_RULES)
    if report.get("context") == "task":
        try:
            from .tasks import load_task
            definition, _state_value, _directory = load_task(workspace, report["scope"]["task"])
            files.update(f"{item['locale']}/{item['path'].replace(chr(92), '/')}" for item in definition["manifest"])
        except (KeyError, OSError, ValueError): pass
    grouped = {path: [] for path in files}
    for item in report.get("findings", []):
        if item.get("rule") not in GLOBAL_RULES:
            grouped.setdefault(item["path"], []).append(item)
    return [_record(workspace, report, display, grouped[display]) for display in sorted(grouped)]


def store_full(report: dict, workspace: Path, replace_latest: bool = False) -> dict:
    from .maintenance import activity, cleanup

    cleanup(workspace, automatic=True)
    directory = context_directory(workspace, report)
    if replace_latest:
        generations = directory / "generations"
        latest = None
        try:
            latest = json.loads((directory / "latest.json").read_text(encoding="utf-8")).get("generation")
        except (OSError, json.JSONDecodeError):
            pass
        if generations.is_dir():
            for old in generations.iterdir():
                if old.is_dir() and old.name != latest:
                    shutil.rmtree(old, ignore_errors=True)
    generation = directory / "generations" / uuid.uuid4().hex
    global_findings = []
    for item in report.get("findings", []):
        if item.get("rule") in GLOBAL_RULES:
            global_findings.append(item)
    index = []
    with activity(generation / ".active.json", "report-generation"):
        for record in records_from_report(workspace, report):
            display = record["path"]
            file_id = digest(display)[:12]
            findings = record.pop("findings")
            record["id"] = file_id
            record["finding_count"] = len(findings)
            _write(generation / "files" / f"{file_id}.meta.json", record)
            if findings:
                (generation / "files" / f"{file_id}.findings.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in findings), encoding="utf-8")
            index.append({key: record[key] for key in ("id", "path", "kind", "existence", "status", "source_state", "checked_at", "counts", "finding_count")})
        _write(generation / "files.json", {"files": index})
        _write(generation / "global.json", {"freshness": "current", "findings": global_findings})
    previous = None
    latest_path = directory / "latest.json"
    if latest_path.is_file():
        try:
            previous = json.loads(latest_path.read_text(encoding="utf-8")).get("generation")
        except json.JSONDecodeError:
            pass
    summary = {key: value for key, value in report.items() if key not in {"findings", "inputs", "ignores", "units"}}
    current_counts = _summary(index, global_findings)
    summary.update({"counts": {**report.get("counts", {}), **current_counts}, "generation": generation.name, "revision": int(time.time_ns()), "full_check": {"status": report["status"]}, "files": {"count": len(index), "problem_files": current_counts["problem_files"]}, "global_findings": {"count": len(global_findings), "freshness": "current"}})
    _write(latest_path, summary)
    _write(directory / "latest.meta.json", {key: summary.get(key) for key in ("schema_version", "kind", "context", "status", "created_at", "scope", "counts", "revision", "files", "global_findings")})
    if previous and previous != generation.name:
        shutil.rmtree(directory / "generations" / previous, ignore_errors=True)
    report["path"] = str(latest_path)
    report["revision"] = summary["revision"]
    return report


def load_generation(report_path: Path) -> tuple[dict, Path]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    generation = report_path.parent / "generations" / report["generation"]
    if not generation.is_dir():
        raise ValueError("Report generation is missing.")
    return report, generation


def file_records(report_path: Path) -> list[dict]:
    _report, generation = load_generation(report_path)
    return json.loads((generation / "files.json").read_text(encoding="utf-8"))["files"]


def record_findings(generation: Path, record: dict) -> list[dict]:
    findings_path = generation / "files" / f"{record['id']}.findings.jsonl"
    return [json.loads(line) for line in findings_path.read_text(encoding="utf-8").splitlines() if line] if findings_path.is_file() else []


def file_record(report_path: Path, display: str) -> tuple[dict, list[dict]]:
    records = file_records(report_path)
    record = next(item for item in records if item["path"] == display)
    _report, generation = load_generation(report_path)
    return record, record_findings(generation, record)


def global_record(report_path: Path) -> dict:
    _report, generation = load_generation(report_path)
    return json.loads((generation / "global.json").read_text(encoding="utf-8"))


def _summary(records: list[dict], global_findings: list[dict]) -> dict:
    findings = [item for record in records for item in [record["counts"]]]
    global_counts = _counts(global_findings)
    errors = sum(item["effective_errors"] for item in findings) + global_counts["effective_errors"]
    warnings = sum(item["effective_warnings"] for item in findings) + global_counts["effective_warnings"]
    return {"effective_errors": errors, "effective_warnings": warnings, "ignored": sum(item["ignored"] for item in findings) + global_counts["ignored"], "files": len(records), "problem_files": sum(bool(item["effective_errors"] or item["effective_warnings"]) for item in findings), "global_findings": len(global_findings)}


def replace_file_records(report_path: Path, replacements: list[dict], *, full_check_stale: bool = True) -> dict:
    report, generation = load_generation(report_path)
    current = {item["path"]: item for item in file_records(report_path)}
    for replacement in replacements:
        display = replacement["path"]
        file_id = current.get(display, {}).get("id") or digest(display)[:12]
        findings = replacement.pop("findings", [])
        replacement.update({"id": file_id, "finding_count": len(findings)})
        _write(generation / "files" / f"{file_id}.meta.json", replacement)
        findings_path = generation / "files" / f"{file_id}.findings.jsonl"
        if findings:
            temporary = findings_path.with_name(findings_path.name + ".tmp")
            temporary.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in findings), encoding="utf-8")
            os.replace(temporary, findings_path)
        else:
            findings_path.unlink(missing_ok=True)
        current[display] = {key: replacement[key] for key in ("id", "path", "kind", "existence", "status", "source_state", "checked_at", "counts", "finding_count")}
    records = sorted(current.values(), key=lambda item: item["path"])
    _write(generation / "files.json", {"files": records})
    global_data = global_record(report_path)
    if full_check_stale:
        report["full_check"] = {"status": "stale"}
        if global_data["findings"]:
            global_data["freshness"] = "stale"
            _write(generation / "global.json", global_data)
    report["revision"] = int(time.time_ns())
    report["counts"] = _summary(records, global_data["findings"])
    report["status"] = "blocked" if report["counts"]["effective_errors"] else "passed"
    report["files"] = {"count": len(records), "problem_files": report["counts"]["problem_files"]}
    report["global_findings"] = {"count": len(global_data["findings"]), "freshness": global_data["freshness"]}
    _write(report_path, report)
    _write(report_path.with_name("latest.meta.json"), {key: report.get(key) for key in ("schema_version", "kind", "context", "status", "created_at", "scope", "counts", "revision", "files", "global_findings")})
    return report


def set_suppression(report_path: Path, finding_id: str, active: bool, ignore_id: str | None = None) -> dict:
    for record in file_records(report_path):
        meta, findings = file_record(report_path, record["path"])
        finding = next((item for item in findings if item.get("finding_id") == finding_id), None)
        if not finding:
            continue
        if active:
            finding["suppression"] = "active"; finding["ignore_id"] = ignore_id
        else:
            finding["suppression"] = "inactive"; finding.pop("ignore_id", None)
        meta["findings"] = findings; meta["counts"] = _counts(findings); meta["status"] = "blocked" if meta["counts"]["effective_errors"] else "passed"
        return replace_file_records(report_path, [meta], full_check_stale=False)
    raise ValueError("问题结果已更新，请重新选择。")
