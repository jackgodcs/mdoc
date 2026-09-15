from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from ruamel.yaml import YAML

from .core import book_context, load_workspace


MAX_MARKDOWN_BYTES = 10 * 1024 * 1024
DEFAULT_CONFIG = {
    "search": {"max_matches": 10000},
    "images": {"max_bytes": 20 * 1024 * 1024, "max_width": 4096, "max_height": 4096},
    "session": {"idle_hours": 8},
    "codex": {"enabled": True, "reasoning": "low", "timeout_seconds": 120},
    "translation": {"timeout_seconds": 120},
    "web_translation": {"google": True, "bing": True},
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
IMAGE_PATTERN = re.compile(r"!\[[^]]*]\(([^)]+)\)|<img\b[^>]*?\bsrc\s*=\s*(['\"])(.*?)\2", re.IGNORECASE)


def _merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in (override or {}).items():
        result[key] = _merge(result.get(key, {}), value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def configuration(workspace: Path) -> dict:
    path = workspace / ".mdoc" / "config" / "book-feedback.json"
    value = DEFAULT_CONFIG
    if path.is_file():
        try:
            value = _merge(DEFAULT_CONFIG, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            value = DEFAULT_CONFIG
    return value


def ensure_configuration(workspace: Path) -> Path:
    path = workspace / ".mdoc" / "config" / "book-feedback.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def feedback_context(workspace: Path) -> dict:
    config = load_workspace(workspace)
    books = []
    for book_id, book in config["books"].items():
        books.append({
            "id": book_id,
            "name": book.get("display_name") or book_id,
            "source_locale": book.get("source_locale"),
            "locales": [{"id": locale, "language": item.get("language", locale)} for locale, item in book["locales"].items()],
        })
    return {"workspace": str(workspace), "workspace_id": config["workspace"]["id"], "books": books, "config": configuration(workspace)}


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def _case_path(root: Path, logical: str) -> tuple[Path | None, list[str]]:
    parts = PurePosixPath(logical.replace("\\", "/")).parts
    if not parts or ".." in parts:
        raise ValueError("文件路径越界。")
    current = root.resolve()
    actual = []
    for part in parts:
        matches = [item for item in current.iterdir() if item.name.casefold() == part.casefold()] if current.is_dir() else []
        if not matches:
            return None, actual + [part]
        if len(matches) > 1:
            raise ValueError("路径存在大小写不唯一的匹配：" + logical)
        current = matches[0]
        actual.append(current.name)
    return _inside(current, root), actual


def locale_file(workspace: Path, book_id: str, locale: str, logical: str) -> tuple[Path | None, str]:
    config = load_workspace(workspace)
    if book_id not in config["books"] or locale not in config["books"][book_id]["locales"]:
        raise ValueError("未知书册或语言。")
    _book, root, _language = book_context(workspace, config, book_id, locale)
    path, actual = _case_path(root, logical)
    return path, PurePosixPath(*actual).as_posix()


def locale_files(workspace: Path, book_id: str, logical: str) -> list[dict]:
    config = load_workspace(workspace); results = []
    if book_id not in config["books"]: raise ValueError("未知书册。")
    for locale in config["books"][book_id]["locales"]:
        try:
            path, actual = locale_file(workspace, book_id, locale, logical)
            results.append({"locale": locale, "exists": bool(path and path.is_file()), "path": actual, "physical_path": str(path) if path else "", "ambiguous": False})
        except ValueError as exc:
            results.append({"locale": locale, "exists": False, "path": logical, "physical_path": "", "ambiguous": True, "reason": str(exc)})
    return results


def _file_state(path: Path, raw: bytes | None = None) -> dict:
    stat = path.stat(); data = raw if raw is not None else path.read_bytes()
    return {"size": stat.st_size, "mtime_ns": str(stat.st_mtime_ns), "digest": hashlib.sha256(data).hexdigest()}


def load_markdown(workspace: Path, book_id: str, locale: str, logical: str) -> dict:
    path, actual = locale_file(workspace, book_id, locale, logical)
    if not path or not path.is_file() or path.suffix.casefold() not in {".md", ".markdown"}:
        raise ValueError("Markdown 文件不存在。")
    raw = path.read_bytes()
    if len(raw) > MAX_MARKDOWN_BYTES:
        raise ValueError("Markdown 文件超过 10 MiB。")
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw[3 if bom else 0:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Markdown 文件不是有效的 UTF-8。") from exc
    newline = "crlf" if "\r\n" in text else "lf"
    return {
        "book": book_id, "locale": locale, "path": actual, "physical_path": str(path),
        "content": text.replace("\r\n", "\n").replace("\r", "\n"),
        "state": _file_state(path, raw), "newline": newline,
        "trailing_newline": text.endswith(("\n", "\r")), "bom": bom,
    }


def save_markdown(workspace: Path, book_id: str, locale: str, logical: str, content: str, state: dict, force: bool = False) -> dict:
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_MARKDOWN_BYTES:
        raise ValueError("Markdown 内容不能超过 10 MiB。")
    original = load_markdown(workspace, book_id, locale, logical); path = Path(original["physical_path"])
    if original["state"] != state and not force:
        return {"saved": False, "conflict": True, "current": original}
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.rstrip("\n") + "\n" if original["trailing_newline"] else normalized.rstrip("\n")
    text = normalized.replace("\n", "\r\n") if original["newline"] == "crlf" else normalized
    data = (b"\xef\xbb\xbf" if original["bom"] else b"") + text.encode("utf-8")
    if data == path.read_bytes():
        return {"saved": False, "conflict": False, "state": original["state"]}
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, path.stat().st_mode); os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"saved": True, "conflict": False, "state": _file_state(path)}


class SearchIndex:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve(); self.lock = threading.RLock(); self.indexes: dict[tuple[str, str], dict] = {}

    def _cache_path(self, book_id: str, locale: str) -> Path:
        name = hashlib.sha256(f"{book_id}\0{locale}".encode()).hexdigest()[:16]
        return self.workspace / ".mdoc" / "cache" / "feedback" / f"{name}.json"

    def _load_cache(self, book_id: str, locale: str) -> dict:
        path = self._cache_path(book_id, locale)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if value.get("schema_version") == 1 and value.get("book") == book_id and value.get("locale") == locale else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cache(self, book_id: str, locale: str, value: dict) -> None:
        path = self._cache_path(book_id, locale); path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "book": book_id, "locale": locale, **value}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _markdown_files(root: Path) -> list[Path]:
        files = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in {".md", ".markdown"}:
                continue
            relative = path.relative_to(root)
            if any(part.startswith(".") or part.casefold() in {"node_modules", "work", "temp", "tmp"} for part in relative.parts[:-1]):
                continue
            try:
                path.resolve().relative_to(root.resolve())
            except ValueError:
                continue
            files.append(path)
        return sorted(files, key=lambda item: item.relative_to(root).as_posix().casefold())

    def refresh(self, book_id: str, locale: str, progress=None, rebuild: bool = False) -> dict:
        config = load_workspace(self.workspace); _book, root, _language = book_context(self.workspace, config, book_id, locale)
        key = (book_id, locale)
        with self.lock:
            cached = {} if rebuild else self.indexes.get(key) or self._load_cache(book_id, locale)
            current = cached.get("files", {})
            files = self._markdown_files(root); discovered = set(); invalid = []; updated = 0
            for index, path in enumerate(files, 1):
                logical = path.relative_to(root).as_posix(); discovered.add(logical); stat = path.stat(); old = current.get(logical)
                if not old or old["mtime_ns"] != stat.st_mtime_ns or old["size"] != stat.st_size:
                    raw = path.read_bytes()
                    try:
                        text = raw[3 if raw.startswith(b"\xef\xbb\xbf") else 0:].decode("utf-8")
                    except UnicodeDecodeError:
                        invalid.append(logical); current.pop(logical, None); continue
                    current[logical] = {"text": text.replace("\r\n", "\n").replace("\r", "\n"), "mtime_ns": stat.st_mtime_ns, "size": stat.st_size}; updated += 1
                if progress:
                    progress(index, len(files), logical)
            for logical in set(current) - discovered:
                current.pop(logical, None)
            self.indexes[key] = {"files": current, "invalid": invalid, "refreshed_at": int(time.time())}
            self._save_cache(book_id, locale, self.indexes[key])
            return {"files": len(current), "updated": updated, "invalid": invalid}

    def update(self, book_id: str, locale: str, logical: str) -> None:
        key = (book_id, locale)
        with self.lock:
            if key not in self.indexes:
                return
        self.refresh(book_id, locale)

    def search(self, book_id: str, locale: str, query: str, limit: int | None = None, rebuild: bool = False) -> dict:
        if not query:
            raise ValueError("请输入要严格匹配的文字。")
        refreshed = self.refresh(book_id, locale, rebuild=rebuild)
        maximum = limit or int(configuration(self.workspace)["search"]["max_matches"]); matches = []; files = []
        with self.lock:
            values = list(self.indexes[(book_id, locale)]["files"].items())
        for logical, record in values:
            text = record["text"]; start = 0; occurrences = []
            while len(matches) < maximum:
                found = text.find(query, start)
                if found < 0:
                    break
                before = text[:found]; line = before.count("\n") + 1; column = found - before.rfind("\n")
                lines = text.splitlines(); first = max(0, line - 3); last = min(len(lines), line + 2)
                item = {"id": hashlib.sha256(f"{logical}\0{found}\0{query}".encode()).hexdigest()[:16], "path": logical, "offset": found, "end_offset": found + len(query), "line": line, "column": column, "context_first_line": first + 1, "context": lines[first:last]}
                matches.append(item); occurrences.append(item); start = found + max(1, len(query))
            if occurrences:
                files.append({"path": logical, "matches": occurrences, "count": len(occurrences)})
            if len(matches) >= maximum:
                break
        return {"book": book_id, "locale": locale, "query": query, "files": files, "file_count": len(files), "match_count": len(matches), "truncated": len(matches) >= maximum, "index": refreshed}


def image_references(workspace: Path, book_id: str, locale: str, logical: str, content: str | None = None) -> list[dict]:
    source = load_markdown(workspace, book_id, locale, logical); text = source["content"] if content is None else content
    _config = load_workspace(workspace); _book, root, _language = book_context(workspace, _config, book_id, locale)
    page = Path(source["physical_path"]); grouped = {}
    for match in IMAGE_PATTERN.finditer(text):
        value = (match.group(1) or match.group(3) or "").strip().split(maxsplit=1)[0].strip("<>")
        parsed = urlsplit(value); line = text[:match.start()].count("\n") + 1
        item = grouped.setdefault(value, {"reference": value, "lines": [], "editable": False, "exists": False, "reason": ""})
        item["lines"].append(line)
        if parsed.scheme in {"http", "https", "data"}:
            item["reason"] = "远程或内嵌图片只读。"; continue
        try:
            candidate = Path(unquote(parsed.path).replace("/", os.sep))
            path = candidate if candidate.is_absolute() else page.parent / candidate
            path = _inside(path, root); item["path"] = str(path); item["exists"] = path.is_file()
            item["editable"] = path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
            if not item["exists"]: item["reason"] = "图片文件不存在，请先修正 Markdown 路径。"
            elif not item["editable"]: item["reason"] = "第一版只允许修改 PNG/JPG/JPEG。"
        except (OSError, ValueError):
            item["reason"] = "图片位于工作区语言目录外，只读。"
    return list(grouped.values())


def resource_path(workspace: Path, book_id: str, locale: str, logical: str, resource: str) -> Path:
    source = load_markdown(workspace, book_id, locale, logical); page = Path(source["physical_path"])
    parsed = urlsplit(resource)
    if parsed.scheme or parsed.netloc or not parsed.path:
        raise ValueError("预览资源必须使用本地相对路径。")
    config = load_workspace(workspace); _book, root, _language = book_context(workspace, config, book_id, locale)
    path = _inside(page.parent / Path(unquote(parsed.path).replace("/", os.sep)), root)
    if not path.is_file() or path.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        raise ValueError("预览资源不存在或格式不受支持。")
    return path
