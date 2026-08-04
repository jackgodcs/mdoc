#!/usr/bin/env python3
"""Screenshot task state, manifest, exception, and acceptance management.

The GreenValley manual workspace uses a deliberately small YAML subset. This
module preserves task definitions while rewriting only each screenshot's
capture section. It has no third-party dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CAPTURE_STATUSES = {"pending", "captured", "needs-retake", "not-applicable", "waived", "blocked"}
COMPLETE_STATUSES = {"captured", "not-applicable", "waived"}
EXCEPTION_STATUSES = {"not-applicable", "waived", "blocked"}
LOCALE_LABELS = {"zh": "中文", "en": "English", "ja": "日本語"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def atomic_json(path: Path, data: dict) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def scalar(source: str, key: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(key)}:[ \t]*([^#\n]+?)[ \t]*$", source)
    return match.group(1).strip().strip("\"'") if match else None


def item_field(block: str, key: str, default: str = "") -> str:
    pattern = r"(?m)^  - id:[ \t]*([^#\n]+?)[ \t]*$" if key == "id" else rf"(?m)^    {re.escape(key)}:[ \t]*([^#\n]+?)[ \t]*$"
    match = re.search(pattern, block)
    return match.group(1).strip().strip("\"'") if match else default


def item_list(block: str, key: str) -> list[str]:
    match = re.search(rf"(?m)^    {re.escape(key)}:[ \t]*\n((?:      - .*\n?)*)", block)
    return [value.strip().strip("\"'") for value in re.findall(r"(?m)^      - (.+?)[ \t]*$", match.group(1))] if match else []


def screenshot_blocks(source: str) -> list[tuple[int, int, str]]:
    starts = [match.start() for match in re.finditer(r"(?m)^  - id:[ \t]*", source)]
    return [(start, starts[index + 1] if index + 1 < len(starts) else len(source), source[start:starts[index + 1] if index + 1 < len(starts) else len(source)]) for index, start in enumerate(starts)]


def section(block: str, name: str, next_name: str | None = None) -> str:
    end = rf"(?=^    {re.escape(next_name)}:|\Z)" if next_name else r"\Z"
    match = re.search(rf"(?ms)^    {re.escape(name)}:[ \t]*\n(.*?){end}", block)
    return match.group(1) if match else ""


def parse_locale_states(capture: str, locales: list[str]) -> dict[str, dict]:
    states: dict[str, dict] = {}
    match = re.search(r"(?ms)^      locales:[ \t]*\n(.*?)(?=^      [a-zA-Z_-]+:|\Z)", capture)
    body = match.group(1) if match else ""
    lines = body.splitlines()
    index = 0
    while index < len(lines):
        flat = re.match(r"^        ([a-zA-Z0-9_-]+):[ \t]*([^\s#]+)[ \t]*$", lines[index])
        nested = re.match(r"^        ([a-zA-Z0-9_-]+):[ \t]*$", lines[index])
        if flat:
            states[flat.group(1)] = {"status": flat.group(2)}
            index += 1
            continue
        if nested:
            locale = nested.group(1)
            data: dict[str, str] = {}
            index += 1
            while index < len(lines):
                value = re.match(r"^          ([a-zA-Z0-9_-]+):[ \t]*(.*?)[ \t]*$", lines[index])
                if not value:
                    break
                data[value.group(1)] = value.group(2).strip().strip("\"'")
                index += 1
            states[locale] = data or {"status": "pending"}
            continue
        index += 1
    for locale in locales:
        states.setdefault(locale, {"status": "pending"})
        states[locale].setdefault("status", "pending")
    return states


def parse_files(capture: str) -> dict[str, str]:
    match = re.search(r"(?ms)^      files:[ \t]*(?:\{\}[ \t]*\n?|\n(.*?))(?=^      [a-zA-Z_-]+:|\Z)", capture)
    if not match or not match.group(1):
        return {}
    return {locale: path.strip().strip("\"'") for locale, path in re.findall(r"(?m)^        ([a-zA-Z0-9_-]+):[ \t]*(.+?)[ \t]*$", match.group(1))}


def parse_history(capture: str) -> list[dict]:
    match = re.search(r"(?ms)^      history:[ \t]*\n(.*?)(?=^      [a-zA-Z_-]+:|\Z)", capture)
    if not match:
        return []
    result = []
    for chunk in re.split(r"(?m)^        - locale:[ \t]*", match.group(1))[1:]:
        lines = chunk.splitlines()
        item = {"locale": lines[0].strip()}
        for key, value in re.findall(r"(?m)^          ([a-zA-Z0-9_-]+):[ \t]*(.*?)[ \t]*$", "\n".join(lines[1:])):
            item[key] = value.strip().strip("\"'")
        result.append(item)
    return result


def parse_shot(block: str, locales: list[str]) -> dict:
    capture = section(block, "capture", "review")
    review = section(block, "review")
    return {
        "id": item_field(block, "id"),
        "page_ids": item_list(block, "page_ids"),
        "filename": item_field(block, "filename"),
        "locale_policy": item_field(block, "locale_policy", "per_locale"),
        "required": item_field(block, "required", "false").lower() == "true",
        "entry_steps": item_list(block, "entry_steps"),
        "preconditions": item_list(block, "preconditions"),
        "expected_state": item_list(block, "expected_state"),
        "capture_status": scalar(capture, "status") or "pending",
        "files": parse_files(capture),
        "locales": parse_locale_states(capture, locales),
        "history": parse_history(capture),
        "review_status": scalar(review, "status") or "pending",
        "review_notes": item_list("    notes:\n" + review.split("      notes:", 1)[1] if "      notes:" in review else "", "notes"),
    }


def quote(value: str) -> str:
    if not value:
        return "''"
    if re.search(r"[:#\[\]{}]|^[-?]|\s$|^\s", value):
        return "'" + value.replace("'", "''") + "'"
    return value


def aggregate(states: dict[str, dict]) -> str:
    values = [data.get("status", "pending") for data in states.values()]
    if values and all(value == "captured" for value in values):
        return "captured"
    if values and all(value in COMPLETE_STATUSES for value in values):
        return "approved"
    if "blocked" in values:
        return "blocked"
    if "needs-retake" in values:
        return "needs-retake"
    return "pending"


def render_capture(shot: dict, locales: list[str]) -> str:
    lines = ["    capture:", f"      status: {aggregate(shot['locales'])}"]
    files = {locale: shot["files"][locale] for locale in locales if locale in shot["files"]}
    if files:
        lines.append("      files:")
        lines.extend(f"        {locale}: {path}" for locale, path in files.items())
    else:
        lines.append("      files: {}")
    lines.append("      locales:")
    for locale in locales:
        data = shot["locales"].get(locale, {"status": "pending"})
        lines.extend([f"        {locale}:", f"          status: {data.get('status', 'pending')}"])
        for key in ("reason", "updated_at"):
            if data.get(key):
                lines.append(f"          {key}: {quote(str(data[key]))}")
    if shot.get("history"):
        lines.append("      history:")
        for item in shot["history"]:
            lines.append(f"        - locale: {item.get('locale', '')}")
            for key in ("from", "to", "reason", "changed_at"):
                if item.get(key):
                    lines.append(f"          {key}: {quote(str(item[key]))}")
    return "\n".join(lines) + "\n"


def replace_capture(block: str, shot: dict, locales: list[str]) -> str:
    match = re.search(r"(?ms)^    capture:[ \t]*\n.*?(?=^    review:|\Z)", block)
    rendered = render_capture(shot, locales)
    if not match:
        raise ValueError(f"Screenshot {shot['id']} has no capture section")
    result = block[:match.start()] + rendered + block[match.end():]
    if not re.search(r"(?m)^    review:[ \t]*$", result):
        result = result.rstrip() + "\n    review:\n      status: pending\n      notes: []\n"
    return result


def workspace_repository(workspace: Path) -> Path:
    value = scalar(read(workspace / "workspace.local.yaml"), "manual_repository")
    if not value:
        raise ValueError(f"manual_repository is missing in {workspace / 'workspace.local.yaml'}")
    return Path(value)


def independent_locales(workspace: Path) -> list[dict]:
    source = read(workspace / "product-profile.yaml")
    source_locale = scalar(source, "source")
    result = [{"id": source_locale, "label": LOCALE_LABELS.get(source_locale, source_locale)}]
    match = re.search(r"(?ms)^  targets:[ \t]*\n(.*?)(?=^[a-zA-Z_]+:|\Z)", source)
    if match:
        for entry in re.split(r"(?m)^    - locale:[ \t]*", match.group(1))[1:]:
            locale = entry.splitlines()[0].strip()
            if scalar(entry, "strategy") != "copy":
                result.append({"id": locale, "label": LOCALE_LABELS.get(locale, locale)})
    return [item for item in result if item["id"]]


def task_paths(workspace: Path, task_id: str) -> dict[str, Path]:
    task_dir = workspace / "manual-tasks" / task_id
    work_root = workspace_repository(workspace) / ".work" / "greenvalley-manual" / task_id
    return {
        "task_dir": task_dir,
        "screenshots": task_dir / "screenshots.yaml",
        "state": task_dir / "state.yaml",
        "work_root": work_root,
        "manifest": work_root / "screenshot-assistant.json",
        "local": work_root / "screenshot-assistant.local.json",
        "lock": work_root / "screenshot-assistant.lock",
        "launcher": work_root / "open-screenshot-assistant.cmd",
    }


def load_task(workspace: Path, task_id: str) -> tuple[str, list[dict], list[dict], dict[str, Path]]:
    paths = task_paths(workspace, task_id)
    locales = independent_locales(workspace)
    locale_ids = [item["id"] for item in locales]
    source = read(paths["screenshots"])
    shots = [parse_shot(block, locale_ids) for _, _, block in screenshot_blocks(source)]
    return source, shots, locales, paths


def update_screenshot_yaml(source: str, shots: list[dict], locales: list[str]) -> str:
    parts, cursor = [], 0
    blocks = screenshot_blocks(source)
    by_id = {shot["id"]: shot for shot in shots}
    for start, end, block in blocks:
        parts.append(source[cursor:start])
        shot_id = item_field(block, "id")
        parts.append(replace_capture(block, by_id[shot_id], locales))
        cursor = end
    parts.append(source[cursor:])
    return "".join(parts)


def acceptance_block(source: str) -> tuple[str | None, tuple[int, int] | None]:
    match = re.search(r"(?ms)^screenshot_acceptance:[ \t]*\n.*?(?=^[a-zA-Z_][a-zA-Z0-9_-]*:|\Z)", source)
    return (match.group(0), (match.start(), match.end())) if match else (None, None)


def manifest_records(work_root: Path, shots: list[dict], locale_ids: list[str]) -> list[dict]:
    records = []
    for shot in shots:
        if not shot["required"]:
            continue
        for locale in locale_ids:
            state = shot["locales"][locale]
            target = work_root / "captures" / locale / "original" / shot["filename"]
            if target.is_file():
                stat = target.stat()
                records.append({"id": shot["id"], "locale": locale, "path": target.relative_to(work_root).as_posix(), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
            elif state["status"] in {"not-applicable", "waived"}:
                records.append({"id": shot["id"], "locale": locale, "decision": state["status"], "reason": state.get("reason", "")})
            else:
                records.append({"id": shot["id"], "locale": locale, "missing": True, "status": state["status"]})
    return records


def manifest_digest(records: list[dict]) -> str:
    raw = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def update_state(path: Path, complete: bool, digest: str, accept_now: bool = False) -> None:
    source = read(path)
    value = "complete" if complete else "pending"
    if re.search(r"(?m)^  screenshot_capture:[ \t]*", source):
        source = re.sub(r"(?m)^  screenshot_capture:[ \t]*.*$", f"  screenshot_capture: {value}", source)
    else:
        source = re.sub(r"(?m)^(checks:[ \t]*)$", rf"\1\n  screenshot_capture: {value}", source, count=1)
    block, span = acceptance_block(source)
    if accept_now:
        new = "screenshot_acceptance:\n  status: user_accepted\n  method: user_visual_review\n" + f"  accepted_at: {now()}\n  scope: all_required_captures\n  review_performed: false\n" + f"  manifest_digest: {digest}\n"
        source = source[:span[0]] + new + source[span[1]:] if span else source.rstrip() + "\n" + new
    elif block and scalar(block, "status") == "user_accepted" and scalar(block, "manifest_digest") != digest:
        new = re.sub(r"(?m)^  status:[ \t]*.*$", "  status: stale", block, count=1).rstrip()
        new += f"\n  changed_at: {now()}\n"
        source = source[:span[0]] + new + source[span[1]:]
    atomic_write(path, source)


def build_manifest(workspace: Path, task_id: str, shots: list[dict], locales: list[dict], paths: dict[str, Path]) -> dict:
    state_source = read(paths["state"])
    acceptance, _ = acceptance_block(state_source)
    manifest_shots = []
    for shot in shots:
        locale_data = {}
        for locale in locales:
            locale_id = locale["id"]
            target = paths["work_root"] / "captures" / locale_id / "original" / shot["filename"]
            state = shot["locales"][locale_id]
            locale_data[locale_id] = {"status": state["status"], "reason": state.get("reason", ""), "target": target.relative_to(paths["work_root"]).as_posix(), "absolute_target": str(target), "exists": target.is_file()}
        manifest_shots.append({"id": shot["id"], "page_ids": shot["page_ids"], "filename": shot["filename"], "required": shot["required"], "entry_steps": shot["entry_steps"], "preconditions": shot["preconditions"], "expected_state": shot["expected_state"], "review_status": shot["review_status"], "locales": locale_data})
    return {
        "schema_version": 1,
        "generated_at": now(),
        "workspace": str(workspace),
        "repository": str(workspace_repository(workspace)),
        "task_id": task_id,
        "phase": scalar(state_source, "phase") or "",
        "acceptance": {"status": scalar(acceptance or "", "status") or "pending", "accepted_at": scalar(acceptance or "", "accepted_at") or ""},
        "locales": locales,
        "screenshots": manifest_shots,
    }


def ensure_local_files(paths: dict[str, Path], workspace: Path, task_id: str, skill_root: Path | None = None) -> None:
    paths["work_root"].mkdir(parents=True, exist_ok=True)
    for locale in independent_locales(workspace):
        (paths["work_root"] / "captures" / locale["id"] / "original").mkdir(parents=True, exist_ok=True)
    if not paths["local"].exists():
        atomic_json(paths["local"], {"schema_version": 1, "preferences": {"capture_scope": "current_monitor", "auto_advance": True, "selected_locale": independent_locales(workspace)[0]["id"], "viewed_locales": []}, "notes": {}})
    if skill_root:
        python = sys.executable
        assistant = skill_root / "scripts" / "screenshot_assistant.py"
        launcher = "@echo off\r\n" + f'"{python}" -B "{assistant}" --workspace "{workspace}" --task "{task_id}"\r\n' + "if errorlevel 1 pause\r\n"
        atomic_write(paths["launcher"], launcher)


def summary(manifest: dict) -> dict:
    counts = {}
    for locale in manifest["locales"]:
        locale_id = locale["id"]
        required = [shot for shot in manifest["screenshots"] if shot["required"]]
        counts[locale_id] = {
            "complete": sum(shot["locales"][locale_id]["status"] in COMPLETE_STATUSES for shot in required),
            "required": len(required),
        }
    return {
        "task_id": manifest["task_id"],
        "phase": manifest["phase"],
        "counts": counts,
        "required_complete": manifest.get("required_complete", False),
        "acceptance": manifest["acceptance"],
        "manifest": str(Path(manifest["repository"]) / ".work" / "greenvalley-manual" / manifest["task_id"] / "screenshot-assistant.json"),
    }


def synchronize(workspace: Path, task_id: str, skill_root: Path | None = None) -> dict:
    source, shots, locales, paths = load_task(workspace, task_id)
    locale_ids = [item["id"] for item in locales]
    ensure_local_files(paths, workspace, task_id, skill_root)
    for shot in shots:
        for locale in locale_ids:
            target = paths["work_root"] / "captures" / locale / "original" / shot["filename"]
            state = shot["locales"][locale]
            old = state.get("status", "pending")
            if target.is_file() and target.suffix.lower() == ".png":
                if old != "captured":
                    if old in EXCEPTION_STATUSES or old == "needs-retake":
                        shot["history"].append({"locale": locale, "from": old, "to": "captured", "reason": "Target PNG exists", "changed_at": now()})
                    shot["locales"][locale] = {"status": "captured", "updated_at": now()}
                shot["files"][locale] = target.relative_to(paths["work_root"]).as_posix()
            elif old == "captured":
                shot["history"].append({"locale": locale, "from": "captured", "to": "pending", "reason": "Target PNG is missing", "changed_at": now()})
                shot["locales"][locale] = {"status": "pending", "updated_at": now()}
                shot["files"].pop(locale, None)
            elif old not in EXCEPTION_STATUSES and old != "needs-retake":
                shot["locales"][locale] = {"status": "pending"}
                shot["files"].pop(locale, None)
    atomic_write(paths["screenshots"], update_screenshot_yaml(source, shots, locale_ids))
    records = manifest_records(paths["work_root"], shots, locale_ids)
    complete = all(not record.get("missing") for record in records)
    digest = manifest_digest(records)
    update_state(paths["state"], complete, digest)
    manifest = build_manifest(workspace, task_id, shots, locales, paths)
    manifest["required_complete"] = complete
    atomic_json(paths["manifest"], manifest)
    return manifest


def set_locale_status(workspace: Path, task_id: str, shot_id: str, locale: str, status: str, reason: str) -> dict:
    if status not in {"pending", "blocked", "not-applicable", "waived"}:
        raise ValueError(f"Unsupported manual status: {status}")
    if status != "pending" and not reason.strip():
        raise ValueError("A reason is required")
    source, shots, locales, paths = load_task(workspace, task_id)
    locale_ids = [item["id"] for item in locales]
    if locale not in locale_ids:
        raise ValueError(f"Locale is not independently captured: {locale}")
    shot = next((item for item in shots if item["id"] == shot_id), None)
    if not shot:
        raise ValueError(f"Unknown screenshot: {shot_id}")
    old = shot["locales"][locale].get("status", "pending")
    if old != status:
        shot["history"].append({"locale": locale, "from": old, "to": status, "reason": reason or "Restored by user", "changed_at": now()})
    shot["locales"][locale] = {"status": status, "updated_at": now()}
    if reason:
        shot["locales"][locale]["reason"] = reason.strip()
    if status != "captured":
        shot["files"].pop(locale, None)
    atomic_write(paths["screenshots"], update_screenshot_yaml(source, shots, locale_ids))
    return synchronize(workspace, task_id)


def accept(workspace: Path, task_id: str) -> dict:
    manifest = synchronize(workspace, task_id)
    if not manifest["required_complete"]:
        raise ValueError("Required captures are incomplete")
    source, shots, locales, paths = load_task(workspace, task_id)
    locale_ids = [item["id"] for item in locales]
    records = manifest_records(paths["work_root"], shots, locale_ids)
    update_state(paths["state"], True, manifest_digest(records), accept_now=True)
    return synchronize(workspace, task_id)


def discover(repository: Path) -> dict:
    binding = repository / ".work" / "greenvalley-manual" / "workspace-ref.local.yaml"
    if not binding.exists():
        raise ValueError(f"Workspace binding not found: {binding}")
    location = scalar(read(binding), "location")
    if not location:
        raise ValueError(f"Workspace location is missing: {binding}")
    workspace = Path(location)
    tasks = []
    for task_dir in sorted((workspace / "manual-tasks").iterdir()):
        if not task_dir.is_dir() or not (task_dir / "state.yaml").exists():
            continue
        phase = scalar(read(task_dir / "state.yaml"), "phase") or ""
        if phase == "screenshot_capture":
            tasks.append(task_dir.name)
    return {"workspace": str(workspace), "tasks": tasks}


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("sync", "accept", "status"):
        command = commands.add_parser(name)
        command.add_argument("workspace", type=Path)
        command.add_argument("task_id")
    status_command = commands.add_parser("set-status")
    status_command.add_argument("workspace", type=Path)
    status_command.add_argument("task_id")
    status_command.add_argument("shot_id")
    status_command.add_argument("locale")
    status_command.add_argument("status")
    status_command.add_argument("reason", nargs="?", default="")
    discover_command = commands.add_parser("discover")
    discover_command.add_argument("repository", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "discover":
            result = discover(args.repository.resolve())
        elif args.command == "set-status":
            result = set_locale_status(args.workspace.resolve(), args.task_id, args.shot_id, args.locale, args.status, args.reason)
        elif args.command == "accept":
            result = accept(args.workspace.resolve(), args.task_id)
        else:
            result = synchronize(args.workspace.resolve(), args.task_id, Path(__file__).resolve().parent.parent)
        print(json.dumps(summary(result), ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
