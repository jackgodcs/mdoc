#!/usr/bin/env python3
"""Sync screenshot existence and generate compact locale workbooks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

EXCEPTIONS = {"not-applicable", "waived", "blocked"}
GROUPS = {"ST": "工具箱操作", "SE": "脚本编辑器", "SD": "脚本开发与运行"}
CAPTURE_LABEL = {"pending": "⬜ 待截图", "captured": "✅ PNG 已存在", "approved": "✅ PNG 已存在", "needs-retake": "🔁 需重拍", "not-applicable": "➖ 不适用", "waived": "➖ 已豁免", "blocked": "⛔ 阻塞"}
REVIEW_LABEL = {"pending": "⏳ 未验证", "approved": "✅ 已验证", "needs-retake": "🔁 需重拍", "not-applicable": "➖ 不适用", "blocked": "⛔ 阻塞"}
TICK = chr(96)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def scalar(source: str, key: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(key)}:\s*([^#\n]+?)\s*$", source)
    return match.group(1).strip().strip("\"'") if match else None


def field(block: str, key: str, default: str = "") -> str:
    if key == "id":
        match = re.search(r"(?m)^  - id:[ \t]*([^#\n]+?)[ \t]*$", block)
    else:
        match = re.search(rf"(?m)^    {re.escape(key)}:[ \t]*([^#\n]+?)[ \t]*$", block)
    return match.group(1).strip().strip("\"'") if match else default


def values(block: str, key: str) -> list[str]:
    match = re.search(rf"(?m)^    {re.escape(key)}:[ \t]*\n((?:      - .*\n?)*)", block)
    return [x.strip().strip("\"'") for x in re.findall(r"(?m)^      - (.+?)\s*$", match.group(1))] if match else []


def nested_status(block: str, section: str) -> str:
    match = re.search(rf"(?ms)^    {section}:\s*\n      status:\s*([^#\n]+)", block)
    return match.group(1).strip() if match else "pending"


def parse_shot(block: str) -> dict:
    return {"id": field(block, "id"), "filename": field(block, "filename"), "required": field(block, "required", "false").lower() == "true", "entry_steps": values(block, "entry_steps"), "expected_state": values(block, "expected_state"), "capture_status": nested_status(block, "capture"), "review_status": nested_status(block, "review")}


def shot_blocks(source: str) -> list[tuple[int, int, str]]:
    starts = [m.start() for m in re.finditer(r"(?m)^  - id:\s*", source)]
    result = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(source)
        result.append((start, end, source[start:end]))
    return result


def replace_capture(block: str, status: str, file_map: dict[str, str], locale_statuses: dict[str, str]) -> str:
    block = re.sub(
        r"(?ms)^locales:[ \t]*\n(?:        .*\n?)*^review:[ \t]*\n((?:      .*\n?)*)",
        r"    review:\n\1",
        block,
    )
    block = re.sub(r"(?ms)(^    capture:\s*\n      status:\s*)[^#\n]+", rf"\g<1>{status}", block, count=1)
    match = re.search(r"(?m)^      files:[ \t]*(?:\{\}[ \t]*\n?|\n(?:        .*\n?)*)", block)
    replacement = "      files:\n" + "".join(f"        {locale}: {path}\n" for locale, path in file_map.items()) if file_map else "      files: {}\n"
    block = block[:match.start()] + replacement + block[match.end():] if match else block
    locale_block = "      locales:\n" + "".join(f"        {locale}: {value}\n" for locale, value in locale_statuses.items())
    locale_match = re.search(r"(?m)^      locales:[ \t]*\n(?:        .*\n?)*", block)
    if locale_match:
        block = block[:locale_match.start()] + locale_block + block[locale_match.end():]
    else:
        files_match = re.search(r"(?m)^      files:[ \t]*(?:\{\}[ \t]*\n?|\n(?:        .*\n?)*)", block)
        if files_match:
            block = block[:files_match.end()] + locale_block + block[files_match.end():]
    return block


def repository_root(workspace: Path) -> Path:
    value = scalar(read(workspace / "workspace.local.yaml"), "manual_repository")
    if not value:
        raise SystemExit("manual_repository is missing")
    return Path(value)


def independent_locales(workspace: Path) -> list[str]:
    source = read(workspace / "product-profile.yaml")
    result = [scalar(source, "source")]
    match = re.search(r"(?ms)^  targets:\s*\n(.*?)(?=^[a-zA-Z_]+:|\Z)", source)
    if match:
        for entry in re.split(r"(?m)^    - locale:\s*", match.group(1))[1:]:
            locale = entry.splitlines()[0].strip()
            if scalar(entry, "strategy") != "copy":
                result.append(locale)
    return [x for x in result if x]


def preserved_notes(source: str) -> dict[str, str]:
    pattern = r'(?ms)<!-- user-notes:begin id="([^"]+)" -->(.*?)<!-- user-notes:end -->'
    return {shot_id: body for shot_id, body in re.findall(pattern, source)}


def shot_title(shot: dict) -> str:
    text = shot["expected_state"][0] if shot["expected_state"] else Path(shot["filename"]).stem
    return text[:28] + ("…" if len(text) > 28 else "")


def render(task_id: str, locale: str, shots: list[dict], notes: dict[str, str]) -> str:
    required = [x for x in shots if x["required"]]
    done = [x for x in required if x["capture_status"] in {"captured", "approved", "not-applicable", "waived"}]
    pending = [x for x in shots if x["required"] and x["capture_status"] in {"pending", "needs-retake", "blocked"}]
    optional = [x for x in shots if not x["required"]]
    lines = [f"# {task_id} {locale} 截图工作单", "", "## 通用规则", "", f"- 保存到 {TICK}captures/{locale}/original/{TICK}，使用卡片指定的 PNG 文件名。", "- 重拍直接覆盖目标文件；采集完成仅表示 PNG 存在。", "- 内容验证独立执行；使用测试数据并避免敏感信息。", "", "## 进度", "", f"- 必需截图：{len(done)}/{len(required)}", f"- 可选截图已存在：{sum(x['capture_status'] in {'captured', 'approved'} for x in optional)}/{len(optional)}", f"- 内容已验证：{sum(x['review_status'] == 'approved' for x in shots)}/{len(shots)}", "", "## 待截图", "", " · ".join(f"[{x['id']}](#{x['id'].lower()})" for x in pending) if pending else "必需截图已齐全。", ""]
    grouped: dict[str, list[dict]] = {}
    for shot in shots:
        grouped.setdefault(shot["id"].split("-", 1)[0], []).append(shot)
    for prefix, group in grouped.items():
        lines += [f"# {GROUPS.get(prefix, prefix)}", ""]
        for shot in group:
            target = f"captures/{locale}/original/{shot['filename']}"
            labels = f"{TICK}{'必需' if shot['required'] else '可选'}{TICK} · {TICK}{CAPTURE_LABEL.get(shot['capture_status'], shot['capture_status'])}{TICK} · {TICK}{REVIEW_LABEL.get(shot['review_status'], shot['review_status'])}{TICK}"
            lines += [f"## {shot['id']}｜{shot_title(shot)}", "", labels, "", f"**目标文件：** {TICK}{target}{TICK}", ""]
            if shot["entry_steps"]:
                lines += ["**操作**", ""] + [f"{i}. {step}" for i, step in enumerate(shot["entry_steps"], 1)] + [""]
            if shot["expected_state"]:
                lines += ["**截图重点：** " + "；".join(shot["expected_state"]), ""]
            if "删除" in " ".join(shot["entry_steps"] + shot["expected_state"]):
                lines += ["**注意：** 只截确认窗口，不要确认删除。", ""]
            lines += [f"![{shot['id']} 截图]({target})", "", f'<!-- user-notes:begin id="{shot["id"]}" -->', notes.get(shot["id"], "\n**备注：**\n").strip("\n"), "<!-- user-notes:end -->", ""]
    return "\n".join(lines).rstrip() + "\n"


def acceptance(source: str) -> tuple[str | None, tuple[int, int] | None]:
    match = re.search(r"(?ms)^screenshot_acceptance:\s*\n.*?(?=^[a-zA-Z_][a-zA-Z0-9_-]*:|\Z)", source)
    return (match.group(0), (match.start(), match.end())) if match else (None, None)


def manifest_digest(work_root: Path, locales: list[str], shots: list[dict]) -> str:
    records = []
    for locale in locales:
        for shot in shots:
            if not shot["required"]:
                continue
            path = work_root / "captures" / locale / "original" / shot["filename"]
            if path.is_file():
                stat = path.stat()
                records.append({"locale": locale, "id": shot["id"], "path": path.relative_to(work_root).as_posix(), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            elif shot["capture_status"] in {"not-applicable", "waived"}:
                records.append({"locale": locale, "id": shot["id"], "decision": shot["capture_status"]})
            else:
                records.append({"locale": locale, "id": shot["id"], "missing": True})
    raw = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def update_state(path: Path, complete: bool, digest: str, accept_now: bool) -> None:
    source = read(path)
    value = "complete" if complete else "pending"
    if re.search(r"(?m)^  screenshot_capture:\s*", source):
        source = re.sub(r"(?m)^  screenshot_capture:\s*.*$", f"  screenshot_capture: {value}", source)
    else:
        source = re.sub(r"(?m)^(checks:\s*)$", rf"\1\n  screenshot_capture: {value}", source, count=1)
    block, span = acceptance(source)
    if accept_now:
        new = "screenshot_acceptance:\n  status: user_accepted\n  method: user_visual_review\n" + f"  accepted_at: {datetime.now(timezone.utc).isoformat()}\n" + "  scope: all_required_captures\n  review_performed: false\n" + f"  manifest_digest: {digest}\n"
        source = source[:span[0]] + new + source[span[1]:] if span else source.rstrip() + "\n" + new
    elif block and scalar(block, "status") == "user_accepted" and scalar(block, "manifest_digest") != digest:
        new = re.sub(r"(?m)^  status:\s*.*$", "  status: stale", block, count=1).rstrip() + f"\n  changed_at: {datetime.now(timezone.utc).isoformat()}\n"
        source = source[:span[0]] + new + source[span[1]:]
    write(path, source)


def sync(workspace: Path, task_id: str, accept_now: bool = False) -> int:
    task_dir = workspace / "manual-tasks" / task_id
    screenshot_path = task_dir / "screenshots.yaml"
    work_root = repository_root(workspace) / ".work" / "greenvalley-manual" / task_id
    locales = independent_locales(workspace)
    source = read(screenshot_path)
    parts, cursor, shots = [], 0, []
    for start, end, block in shot_blocks(source):
        parts.append(source[cursor:start])
        shot = parse_shot(block)
        file_map = {}
        for locale in locales:
            target = work_root / "captures" / locale / "original" / shot["filename"]
            if target.is_file() and target.suffix.lower() == ".png":
                file_map[locale] = target.relative_to(work_root).as_posix()
        locale_statuses = {locale: ("captured" if locale in file_map else "pending") for locale in locales}
        if len(file_map) == len(locales):
            status = "captured"
        elif shot["capture_status"] in EXCEPTIONS:
            status = shot["capture_status"]
        else:
            status = "pending"
        updated = replace_capture(block, status, file_map, locale_statuses)
        parts.append(updated)
        shots.append(parse_shot(updated))
        cursor = end
    parts.append(source[cursor:])
    write(screenshot_path, "".join(parts))
    for locale in locales:
        workbook = work_root / f"screenshot-workbook.{locale}.md"
        notes = preserved_notes(read(workbook)) if workbook.exists() else {}
        localized = []
        for shot in shots:
            clone = dict(shot)
            target = work_root / "captures" / locale / "original" / shot["filename"]
            if target.is_file() and target.suffix.lower() == ".png":
                clone["capture_status"] = "captured"
            elif clone["capture_status"] not in EXCEPTIONS:
                clone["capture_status"] = "pending"
            localized.append(clone)
        write(workbook, render(task_id, locale, localized, notes))
    complete = all((work_root / "captures" / locale / "original" / shot["filename"]).is_file() or shot["capture_status"] in {"not-applicable", "waived"} for locale in locales for shot in shots if shot["required"])
    if accept_now and not complete:
        print("ERROR: required captures are incomplete; acceptance was not recorded")
        return 1
    digest = manifest_digest(work_root, locales, shots)
    update_state(task_dir / "state.yaml", complete, digest, accept_now)
    print(f"SYNCED: {task_id}; locales={','.join(locales)}; required_complete={str(complete).lower()}")
    for locale in locales:
        print(work_root / f"screenshot-workbook.{locale}.md")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("sync", "accept"):
        command = commands.add_parser(name)
        command.add_argument("workspace", type=Path)
        command.add_argument("task_id")
    args = parser.parse_args()
    return sync(args.workspace.resolve(), args.task_id, args.command == "accept")


if __name__ == "__main__":
    sys.exit(main())
