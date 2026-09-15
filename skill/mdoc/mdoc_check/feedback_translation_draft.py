from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from .checkers import BRIDGE, NODE, _run
from .core import load_workspace
from .feedback import _file_state, load_markdown
from .feedback_translation import structure_differences
from .preview import _sanitize

_preferred_methods: dict[str, str] = {}


def blocks(content: str) -> list[dict]:
    items = json.loads(_run([str(NODE), str(BRIDGE)], input_text=json.dumps({"action": "blocks", "content": content})).stdout)["blocks"]
    for item in items: item["html"] = _sanitize({"html": item["html"]})["html"]
    return items


def capabilities(workspace: Path) -> dict:
    from .feedback import configuration
    from .feedback_translation import load_providers

    config = configuration(workspace)
    ai = any(item.get("key_configured") and item.get("default_model") and item.get("base_url") for item in load_providers(workspace).get("providers", []))
    codex = bool(config.get("codex", {}).get("enabled") and (shutil.which("codex") or shutil.which("codex.cmd")))
    google = bool(config.get("web_translation", {}).get("google"))
    bing = bool(config.get("web_translation", {}).get("bing"))
    methods = {"ai": {"available": ai, "reason": "" if ai else "未配置完整的 AI 接口。"}, "codex": {"available": codex, "reason": "" if codex else "未检测到 Codex CLI。"}, "google": {"available": google, "reason": "" if google else "Google 翻译网页已禁用。"}, "bing": {"available": bing, "reason": "" if bing else "必应翻译网页已禁用。"}}
    default = next((name for name in ("ai", "codex", "google", "bing") if methods[name]["available"]), "")
    preferred = _preferred_methods.get(str(workspace.resolve()), "")
    return {"methods": methods, "default": preferred if methods.get(preferred, {}).get("available") else default}


def remember_method(workspace: Path, method: str) -> dict:
    available = capabilities(workspace)
    if method not in available["methods"] or not available["methods"][method]["available"]: raise ValueError("所选翻译方式当前不可用。")
    _preferred_methods[str(workspace.resolve())] = method
    return {"saved": True, "method": method}


def _selection_group(items: list[dict], start: int, end: int) -> tuple[list[dict], bool]:
    selected = [item for item in items if item["to"] > start and item["from"] < end]
    if not selected: raise ValueError("所选文字不属于可映射的 Markdown 块。")
    first, last = selected[0], selected[-1]
    complete = start <= first["from"] + len(first["text"]) - len(first["text"].lstrip()) and end >= last["to"] - len(last["text"]) + len(last["text"].rstrip())
    return selected, complete


def _context(items: list[dict], start: int, count: int) -> dict:
    return {"before": items[start - 1] if start else None, "target": items[start:start + count], "after": items[start + count] if start + count < len(items) else None}


def prepare(workspace: Path, book_id: str, source_locale: str, logical: str, start: int, end: int) -> dict:
    config = load_workspace(workspace); book = config["books"].get(book_id)
    if not book or source_locale not in book["locales"]: raise ValueError("未知书册或语言。")
    source = load_markdown(workspace, book_id, source_locale, logical); source_blocks = blocks(source["content"]); group, complete = _selection_group(source_blocks, start, end); first = group[0]["index"]
    targets = []
    for locale, locale_config in book["locales"].items():
        if locale == source_locale: continue
        try:
            target = load_markdown(workspace, book_id, locale, logical); target_blocks = blocks(target["content"]); mapped = target_blocks[first:first + len(group)]; reliable = len(mapped) == len(group) and all(a["type"] == b["type"] and a["structure"] == b["structure"] for a, b in zip(group, mapped))
            targets.append({"locale": locale, "language": locale_config.get("language", locale), "exists": True, "path": target["path"], "state": target["state"], "content": target["content"], "context": _context(target_blocks, first, len(group)), "reliable": reliable, "reason": "" if reliable else "目标块数量、顺序或 Markdown 结构与源语言不一致。"})
        except ValueError as exc:
            targets.append({"locale": locale, "language": locale_config.get("language", locale), "exists": False, "path": logical, "reliable": False, "reason": str(exc)})
    return {"source": {"locale": source_locale, "path": source["path"], "state": source["state"], "selection": {"from": start, "to": end, "text": source["content"][start:end], "complete_blocks": complete}, "scope": {"from": group[0]["from"], "to": group[-1]["to"], "text": source["content"][group[0]["from"]:group[-1]["to"]]}, "context": _context(source_blocks, first, len(group))}, "targets": targets, "capabilities": capabilities(workspace)}


def validate(source: str, candidate: str) -> dict:
    differences = structure_differences(source, candidate)
    return {"valid": not differences, "differences": differences}


def _encoded(original: dict, content: str) -> bytes:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.rstrip("\n") + "\n" if original["trailing_newline"] else normalized.rstrip("\n")
    text = normalized.replace("\n", "\r\n") if original["newline"] == "crlf" else normalized
    return (b"\xef\xbb\xbf" if original["bom"] else b"") + text.encode("utf-8")


def commit(workspace: Path, book_id: str, source: dict, targets: list[dict]) -> dict:
    current_source = load_markdown(workspace, book_id, source["locale"], source["path"])
    if current_source["state"] != source["state"]: raise ValueError("源语言文件已变化，请重新发起翻译。")
    prepared = []
    for item in targets:
        if item["status"] in {"ignored", "manual"}:
            if item["status"] == "manual" and load_markdown(workspace, book_id, item["locale"], item["path"])["state"] != item["state"]: raise ValueError(item["locale"] + " 人工处理文件在确认后发生变化。")
            continue
        if item["status"] != "confirmed": raise ValueError(item["locale"] + " 尚未完成处理。")
        original = load_markdown(workspace, book_id, item["locale"], item["path"])
        if original["state"] != item["state"]: raise ValueError(item["locale"] + " 目标文件已变化，请重新读取并核对。")
        prepared.append((item, original, Path(original["physical_path"]), _encoded(original, item["content"])))
    runtime = workspace / ".mdoc" / "runtime"; runtime.mkdir(parents=True, exist_ok=True); transaction = Path(tempfile.mkdtemp(prefix="feedback-translation-", dir=runtime)); backups = []
    try:
        staged = []
        for index, (_item, _original, path, data) in enumerate(prepared):
            candidate = transaction / f"{index}.new"; candidate.write_bytes(data); backup = transaction / f"{index}.bak"; backup.write_bytes(path.read_bytes()); staged.append((path, candidate, backup))
        for path, candidate, backup in staged: os.replace(candidate, path); backups.append((path, backup))
    except Exception as exc:
        failed = []
        for path, backup in reversed(backups):
            try:
                if backup.is_file(): os.replace(backup, path)
            except OSError: failed.append(str(path))
        if failed: raise RuntimeError("多语言保存失败且以下文件无法自动恢复：" + "、".join(failed) + "；事务目录：" + str(transaction)) from exc
        shutil.rmtree(transaction, ignore_errors=True); raise
    shutil.rmtree(transaction, ignore_errors=True)
    return {"saved": True, "files": [{"locale": item["locale"], "path": item["path"]} for item, _original, _path, _data in prepared]}
