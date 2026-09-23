from __future__ import annotations

import copy
import html
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .errors import MdocError
from .io import read_yaml, write_yaml_atomic
from .models import thaw

try:
    from PIL import Image
except ImportError:  # pragma: no cover - reported by doctor in incomplete runtimes
    Image = None


SUMMARY_LINK = re.compile(r"^(?P<indent>\s*)[*+-]\s+\[(?P<title>[^]]+)]\((?P<target>[^)]+)\)")
HTML_RESOURCE = re.compile(r"(?P<prefix>\b(?:src|href)\s*=\s*[\"'])(?P<target>[^\"']+)(?P<suffix>[\"'])", re.I)
CSS_RESOURCE = re.compile(r"(?P<prefix>url\(\s*[\"']?)(?P<target>[^)\"']+)(?P<suffix>[\"']?\s*\))", re.I)
TOOL_VERSIONS = {"node": "24.18.0", "honkit": "6.2.2", "calibre": "9.14.0", "qpdf": "12.4.1"}


DEFAULTS = {
    "defaults": {
        "paper_size": "a4",
        "margins_pt": {"left": 67, "right": 67, "top": 36, "bottom": 36},
        "image_optimization": {
            "enabled": True,
            "target_dpi": 180,
            "max_width_px": 1048,
            "min_bytes": 20480,
            "jpeg_quality": 75,
            "jpeg_subsampling": "4:4:4",
            "transparent_background": "white",
            "never_upscale": True,
        },
        "optimization": {
            "enabled": True,
            "recompress_flate": True,
            "compression_level": 9,
            "object_streams": "generate",
        },
        "toc": {"right_value": "page", "show_left_number": True},
        "bookmarks": {"levels": 5, "show_left_number": True},
        "cover": {"enabled": True, "preserve_aspect_ratio": True},
        "page_numbers": {"enabled": True, "position": "right"},
        "concurrency": {"builds": 3, "images": "auto"},
    },
    "retention": {"failed_work_days": 7, "batch_reports": 20, "keep_successful_book_work": False},
}


def init(workspace: Path) -> dict:
    control = workspace.resolve() / ".mdoc"
    authority = control / "workspace.yaml"
    draft = control / "workspace-draft.yaml"
    candidate = control / "cache" / "workspace-candidate.json"
    if draft.exists() or candidate.exists():
        raise MdocError("MDOC-WORKSPACE-DRAFT-EXISTS", "工作区草稿或候选配置已经存在。")
    value = read_yaml(authority)
    value["pdf"] = copy.deepcopy(DEFAULTS)
    write_yaml_atomic(draft, value)
    return {"status": "pdf_workspace_draft_created", "draft": str(draft), "next": ["workspace apply", "workspace confirm"]}


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def effective_settings(config: dict, book: dict) -> dict:
    defaults = _deep_merge(DEFAULTS["defaults"], thaw(config.get("pdf", {}).get("defaults", {})))
    return _deep_merge(defaults, thaw(book.get("pdf", {})))


def normalized_target(target: str) -> tuple[str, str]:
    split = urlsplit(target.strip().split(maxsplit=1)[0])
    return unquote(split.path).replace("\\", "/").removeprefix("./"), split.fragment


def summary_entries(summary: Path) -> list[dict]:
    counters: list[int] = []
    entries: list[dict] = []
    indents: list[int] = []
    try:
        lines = summary.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise MdocError("MDOC-PDF-SUMMARY-INVALID", f"无法读取 Summary：{summary}", {"cause": str(exc)}) from exc
    for line_number, line in enumerate(lines, 1):
        match = SUMMARY_LINK.match(line)
        if not match:
            continue
        indent = len(match.group("indent").expandtabs(4))
        while indents and indent < indents[-1]:
            indents.pop()
        if not indents or indent > indents[-1]:
            indents.append(indent)
        level = len(indents) - 1
        while len(counters) <= level:
            counters.append(0)
        counters = counters[: level + 1]
        counters[level] += 1
        path, anchor = normalized_target(match.group("target"))
        entries.append({"line": line_number, "level": level, "number": ".".join(str(value) for value in counters), "title": match.group("title"), "path": path, "anchor": anchor})
    if not entries:
        raise MdocError("MDOC-PDF-SUMMARY-EMPTY", f"Summary 没有可构建的 Markdown 条目：{summary}")
    return entries


def select_entries(entries: list[dict], target: str, mode: str, summary_line: int | None = None, include_notices: bool = False):
    target_path, target_anchor = normalized_target(target)
    matches = [entry for entry in entries if entry["path"].casefold() == target_path.casefold() and entry["anchor"] == target_anchor]
    if summary_line is not None:
        selected = next((entry for entry in matches if entry.get("line") == summary_line), None)
        if not selected: raise MdocError("MDOC-PDF-SUMMARY-LINE-MISMATCH", f"Summary 行号与 PDF 目标不匹配：{summary_line}", {"target": target, "matches": [entry.get("line") for entry in matches]})
    elif matches:
        selected = matches[0]
    else:
        raise MdocError("MDOC-PDF-TARGET-NOT-IN-SUMMARY", f"PDF 目标未在 Summary 中引用：{target}")
    notices = [] if len(matches) < 2 or summary_line is not None else [{"kind": "summary_target_multiple_matches", "target": target, "matches": len(matches), "selected_line": selected.get("line")}]
    if mode == "page":
        result = [selected]
        return (result, notices) if include_notices else result
    start = entries.index(selected)
    end = start + 1
    while end < len(entries) and entries[end]["level"] > selected["level"]:
        end += 1
    result = entries[start:end]
    return (result, notices) if include_notices else result


def scoped_entries(entries: list[dict]) -> list[dict]:
    base = entries[0]["level"] if entries else 0
    return [{**entry, "level": entry["level"] - base} for entry in entries]


def _content_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines(); result = []; front_matter = bool(lines and lines[0].strip() == "---"); fenced = False; comment = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if front_matter:
            if index and stripped == "---": front_matter = False
            continue
        if "<!--" in stripped: comment = True
        if comment:
            if "-->" in stripped: comment = False
            continue
        if stripped.startswith(("```", "~~~")): fenced = not fenced; continue
        if fenced or not stripped or re.fullmatch(r"!?\[[^]]*]\([^)]+\)", stripped) or re.fullmatch(r"<[^>]+>", stripped): continue
        result.append(stripped)
        if len(result) == 5: break
    return result


def standalone_title(path: Path) -> str:
    for line in _content_lines(path):
        match = re.match(r"^#(?!#)\s+(.+?)\s*#*\s*$", line)
        if match:
            title = re.sub(r"[*_~`]", "", re.sub(r"!?\[([^]]+)]\([^)]+\)", r"\1", match.group(1))).strip()
            if title: return title
    return path.stem


def standalone_language(path: Path) -> str:
    text = "\n".join(_content_lines(path))
    if re.search(r"[\u3040-\u30ff]", text): return "ja"
    if re.search(r"[\u3400-\u9fff]", text): return "zh-hans"
    return "en"


def standalone_settings(path: Path | None) -> tuple[dict, list[dict]]:
    settings = copy.deepcopy(DEFAULTS["defaults"]); notices = []
    if not path: return settings, notices
    try: override = read_yaml(path) or {}
    except Exception as exc: raise MdocError("MDOC-PDF-CONFIG-INVALID", f"PDF 配置无法读取：{path}", {"cause": str(exc)}) from exc
    if not isinstance(override, dict): raise MdocError("MDOC-PDF-CONFIG-INVALID", "PDF 配置顶层必须是对象。")
    allowed = {"paper_size", "margins_pt", "image_optimization", "optimization", "bookmarks"}
    filtered = {}
    for key, value in override.items():
        if key not in allowed: notices.append({"kind": "pdf_config_unknown_field", "field": key}); continue
        if isinstance(value, dict) and isinstance(settings.get(key), dict):
            filtered[key] = {}
            for child, child_value in value.items():
                if child in settings[key]: filtered[key][child] = child_value
                else: notices.append({"kind": "pdf_config_unknown_field", "field": f"{key}.{child}"})
        else: filtered[key] = value
    settings = _deep_merge(settings, filtered)
    validators = {
        "paper_size": lambda value: isinstance(value, str) and bool(value.strip()),
        "margins_pt": lambda value: isinstance(value, dict) and all(isinstance(value.get(name), (int, float)) and not isinstance(value.get(name), bool) and value[name] >= 0 for name in ("left", "right", "top", "bottom")),
        "image_optimization": lambda value: isinstance(value, dict) and isinstance(value.get("enabled"), bool) and isinstance(value.get("target_dpi"), int) and value["target_dpi"] >= 72 and isinstance(value.get("max_width_px"), int) and value["max_width_px"] >= 1 and isinstance(value.get("min_bytes"), int) and value["min_bytes"] >= 0 and isinstance(value.get("jpeg_quality"), int) and 1 <= value["jpeg_quality"] <= 100 and value.get("jpeg_subsampling") in {"4:4:4", "4:2:2", "4:2:0"} and isinstance(value.get("transparent_background"), str) and bool(value["transparent_background"].strip()) and isinstance(value.get("never_upscale"), bool),
        "optimization": lambda value: isinstance(value, dict) and isinstance(value.get("enabled"), bool) and isinstance(value.get("recompress_flate"), bool) and isinstance(value.get("compression_level"), int) and 0 <= value["compression_level"] <= 9 and value.get("object_streams") in {"disable", "preserve", "generate"},
        "bookmarks": lambda value: isinstance(value, dict) and (value.get("levels") == "all" or isinstance(value.get("levels"), int) and value["levels"] >= 1),
    }
    invalid = [name for name, validate in validators.items() if not validate(settings.get(name))]
    if invalid: raise MdocError("MDOC-PDF-CONFIG-INVALID", "PDF 配置包含无效字段值。", {"fields": invalid})
    return settings, notices


def standalone_output(source: Path, output: Path | None) -> Path:
    return output.resolve() if output else Path(tempfile.gettempdir()) / "mdoc" / source.with_suffix(".pdf").name


def _local_reference(document: Path, target: str, root: Path) -> Path | None:
    path, _ = normalized_target(target)
    if not path or urlsplit(target).scheme or target.startswith(("//", "\\\\", "/", "\\")):
        return None
    candidate = (document.parent / path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def optimize_generated_images(root: Path, settings: dict) -> dict:
    stats = {"seen": 0, "optimized": 0, "resized": 0, "flattened_alpha": 0, "before_bytes": 0, "after_bytes": 0, "findings": []}
    if not settings.get("enabled", True):
        return stats
    if Image is None:
        raise MdocError("MDOC-PDF-IMAGE-RUNTIME-MISSING", "PDF 图片优化需要 Pillow。")
    references: dict[Path, list[tuple[Path, re.Pattern, str]]] = {}
    for document in root.rglob("*"):
        if not document.is_file() or document.suffix.lower() not in {".html", ".htm", ".css"}:
            continue
        pattern = CSS_RESOURCE if document.suffix.lower() == ".css" else HTML_RESOURCE
        try:
            text = document.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for match in pattern.finditer(text):
            source = _local_reference(document, match.group("target"), root)
            if source and source.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                references.setdefault(source, []).append((document, pattern, match.group("target")))
    for source, usages in references.items():
        if not source.is_file():
            stats["findings"].append({"kind": "missing_resource", "path": str(source)})
            continue
        stats["seen"] += 1
        source_bytes = source.stat().st_size
        stats["before_bytes"] += source_bytes
        stats["after_bytes"] += source_bytes
        if source_bytes < settings["min_bytes"]:
            continue
        destination = source.with_name(f"{source.stem}.mdoc.jpg")
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            with Image.open(source) as opened:
                opened.load()
                image = opened
                if image.width > settings["max_width_px"]:
                    height = max(1, round(image.height * settings["max_width_px"] / image.width))
                    image = image.resize((settings["max_width_px"], height), Image.Resampling.LANCZOS)
                    stats["resized"] += 1
                if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, settings["transparent_background"])
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                    stats["flattened_alpha"] += 1
                else:
                    image = image.convert("RGB")
                subsampling = {"4:4:4": 0, "4:2:2": 1, "4:2:0": 2}[settings["jpeg_subsampling"]]
                image.save(temporary, format="JPEG", quality=settings["jpeg_quality"], optimize=True, progressive=True, subsampling=subsampling, dpi=(settings["target_dpi"], settings["target_dpi"]))
            optimized_bytes = temporary.stat().st_size
            if optimized_bytes >= source_bytes:
                temporary.unlink(missing_ok=True)
                continue
            os.replace(temporary, destination)
            for document, pattern, old_target in usages:
                text = document.read_text(encoding="utf-8")
                new_target = Path(os.path.relpath(destination, document.parent)).as_posix()
                text = pattern.sub(lambda match: f"{match.group('prefix')}{new_target}{match.group('suffix')}" if match.group("target") == old_target else match.group(0), text)
                document.write_text(text, encoding="utf-8", newline="\n")
            stats["optimized"] += 1
            stats["after_bytes"] += optimized_bytes - source_bytes
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            stats["findings"].append({"kind": "image_optimization_failed", "path": str(source), "error": str(exc)})
    return stats


def validate_book_configs(workspace: Path, config: dict) -> None:
    if "pdf" not in config:
        return
    for book_id, book in config["books"].items():
        book_root = (workspace / book["root"]).resolve()
        for locale_id, locale in book["locales"].items():
            path = book_root / locale["root"] / "book.json"
            if not path.is_file():
                raise MdocError("MDOC-PDF-BOOK-CONFIG-MISSING", f"PDF 书册缺少 book.json：{book_id}/{locale_id}", {"path": str(path)})
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise MdocError("MDOC-PDF-BOOK-CONFIG-INVALID", f"PDF 书册的 book.json 无效：{book_id}/{locale_id}", {"path": str(path), "cause": str(exc)}) from exc
            if not isinstance(value, dict) or not isinstance(value.get("title"), str) or not value["title"].strip() or not isinstance(value.get("language"), str) or not value["language"].strip():
                raise MdocError("MDOC-PDF-BOOK-CONFIG-INVALID", f"PDF 书册的 book.json 必须包含 title 和 language：{book_id}/{locale_id}", {"path": str(path)})


def toolchain_root() -> Path:
    configured = os.environ.get("MDOC_TOOLCHAIN_ROOT")
    if configured:
        return Path(configured)
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise MdocError("MDOC-PDF-TOOLCHAIN-MISSING", "无法定位 mdoc Toolchain 安装目录。")
    return Path(local) / "mdoc" / "toolchain"


def tool_paths(root: Path | None = None) -> dict[str, Path]:
    root = root or toolchain_root()
    return {
        "node": root / "node" / "node.exe",
        "honkit": root / "honkit" / "node_modules" / "honkit" / "bin" / "honkit.js",
        "calibre": root / "calibre" / "ebook-convert.exe",
        "qpdf": root / "qpdf" / "bin" / "qpdf.exe",
    }


def _version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
        return (result.stdout or result.stderr).strip().splitlines()[0] if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def doctor(workspace) -> dict:
    tools = tool_paths()
    probes = {
        "node": _version([str(tools["node"]), "--version"]) if tools["node"].is_file() else None,
        "honkit": _version([str(tools["node"]), str(tools["honkit"]), "--version"]) if tools["node"].is_file() and tools["honkit"].is_file() else None,
        "calibre": _version([str(tools["calibre"]), "--version"]) if tools["calibre"].is_file() else None,
        "qpdf": _version([str(tools["qpdf"]), "--version"]) if tools["qpdf"].is_file() else None,
    }
    invalid = [name for name, value in probes.items() if not value or TOOL_VERSIONS[name] not in value]
    return {"status": "passed" if not invalid else "failed", "toolchain": str(toolchain_root()), "required_versions": TOOL_VERSIONS, "tools": {name: {"path": str(tools[name]), "version": probes[name], "available": bool(probes[name]), "version_matches": bool(probes[name] and TOOL_VERSIONS[name] in probes[name])} for name in tools}, "invalid": invalid, "exit_code": 0 if not invalid else 3}


def _cancelled(cancel: threading.Event | None) -> None:
    if cancel and cancel.is_set():
        raise MdocError("MDOC-PDF-BUILD-CANCELLED", "PDF 构建已因其他任务失败而取消。")


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait()


def _run(command: list[str], cwd: Path, log: Path, cancel: threading.Event | None = None) -> float:
    log.parent.mkdir(parents=True, exist_ok=True)
    _cancelled(cancel)
    started = time.monotonic()
    with log.open("w", encoding="utf-8", errors="replace", newline="\n") as stream:
        process = subprocess.Popen(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, text=True)
        while process.poll() is None:
            if cancel and cancel.wait(0.1):
                _terminate_process(process)
                _cancelled(cancel)
            time.sleep(0.1)
    if process.returncode:
        raise MdocError("MDOC-PDF-BUILD-COMMAND-FAILED", f"PDF 构建命令失败：{Path(command[0]).name}", {"exit_code": process.returncode, "log": str(log)})
    return round(time.monotonic() - started, 3)


def _safe_hardlink_tree(source: Path, destination: Path, findings: list[dict], excluded: set[str] | None = None) -> None:
    excluded = {item.casefold() for item in (excluded or set())}
    source_root = source.resolve()
    for current, directories, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative = current_path.relative_to(source)
        target_root = destination / relative
        target_root.mkdir(parents=True, exist_ok=True)
        safe_directories = []
        for name in directories:
            candidate = current_path / name
            try:
                candidate.resolve().relative_to(source_root)
                safe_directories.append(name)
            except ValueError:
                findings.append({"kind": "unsafe_resource", "path": str(candidate)})
        directories[:] = safe_directories
        for name in files:
            item_relative = (relative / name).as_posix().removeprefix("./")
            if item_relative.casefold() in excluded:
                continue
            candidate = current_path / name
            try:
                candidate.resolve().relative_to(source_root)
                os.link(candidate, target_root / name)
            except (ValueError, OSError) as exc:
                findings.append({"kind": "resource_copy_failed", "path": str(candidate), "error": str(exc)})


def _isolated_book_config(locale_root: Path, readme: str | None = None, title: str | None = None) -> dict:
    config = json.loads((locale_root / "book.json").read_text(encoding="utf-8-sig"))
    if title is not None:
        config["title"] = title
    config.setdefault("structure", {}).pop("readme", None)
    if readme is not None:
        config["structure"]["readme"] = readme
    config["plugins"] = []
    config.pop("pluginsConfig", None)
    return config


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _prepare_cover(locale_root: Path, config: dict, settings: dict, work: Path, mode: str) -> tuple[dict, Path | None]:
    behavior = settings["cover"]
    report = {"enabled": behavior["enabled"], "configured": False, "applied": False, "preserve_aspect_ratio": behavior["preserve_aspect_ratio"]}
    if mode != "book":
        return {**report, "reason": "scope_not_book"}, None
    if not behavior["enabled"]:
        return {**report, "reason": "disabled"}, None
    cover = (config.get("pdf") or {}).get("cover")
    if cover is None:
        return {**report, "reason": "not_configured"}, None
    report["configured"] = True
    if not isinstance(cover, dict) or set(cover) != {"title", "path"} or not isinstance(cover.get("title"), str) or not cover["title"].strip() or not isinstance(cover.get("path"), str) or not cover["path"].strip():
        raise MdocError("MDOC-PDF-COVER-CONFIG-INVALID", "book.json 的 pdf.cover 必须且只能包含非空的 title 和 path。")
    raw_path = cover["path"].strip()
    split = urlsplit(raw_path)
    if split.scheme or split.netloc or split.query or split.fragment or Path(raw_path).is_absolute() or raw_path.startswith(("/", "\\")):
        raise MdocError("MDOC-PDF-COVER-PATH-UNSAFE", "PDF 封面路径必须是当前语言目录内的相对路径。", {"path": raw_path})
    source = (locale_root / raw_path).resolve()
    try:
        source.relative_to(locale_root.resolve())
    except ValueError as exc:
        raise MdocError("MDOC-PDF-COVER-PATH-UNSAFE", "PDF 封面路径越出当前语言目录。", {"path": raw_path}) from exc
    if source.suffix.casefold() not in {".png", ".jpg", ".jpeg"}:
        raise MdocError("MDOC-PDF-COVER-FORMAT-UNSUPPORTED", "PDF 封面仅支持 PNG 和 JPEG。", {"path": raw_path})
    if not source.is_file():
        raise MdocError("MDOC-PDF-COVER-MISSING", "PDF 封面文件不存在。", {"path": raw_path})
    if Image is None:
        raise MdocError("MDOC-PDF-IMAGE-RUNTIME-MISSING", "PDF 封面校验需要 Pillow。")
    try:
        with Image.open(source) as opened:
            opened.verify()
        with Image.open(source) as opened:
            width, height, image_format = opened.width, opened.height, opened.format
    except Exception as exc:
        raise MdocError("MDOC-PDF-COVER-INVALID", "PDF 封面图片无法解码。", {"path": raw_path, "cause": str(exc)}) from exc
    if image_format not in {"PNG", "JPEG"}:
        raise MdocError("MDOC-PDF-COVER-FORMAT-UNSUPPORTED", "PDF 封面实际内容必须是 PNG 或 JPEG。", {"path": raw_path, "format": image_format})
    destination = work / "cover" / f"cover{source.suffix.lower()}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {**report, "applied": True, "title": cover["title"].strip(), "source": raw_path.replace("\\", "/"), "format": image_format, "width": width, "height": height}, destination


def _materialize_scope(locale_root: Path, entries: list[dict], work: Path, findings: list[dict]) -> list[tuple[dict, str]]:
    selected = {item["path"].casefold() for item in entries}
    def page_name(index: int) -> str:
        letters = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            letters = chr(65 + remainder) + letters
        return f"Page-{letters}.md"

    names = {item["path"].casefold(): page_name(index) for index, item in enumerate(entries, 1)}
    pages = []
    link = re.compile(r"(!?)\[([^]]*)\]\(([^)]+)\)")
    html_resource = re.compile(r"(?P<prefix>\b(?:src|href)\s*=\s*[\"'])(?P<target>[^\"']+)(?P<suffix>[\"'])", re.I)
    for index, entry in enumerate(entries, 1):
        source = locale_root / entry["path"]
        if not source.is_file():
            raise MdocError("MDOC-PDF-PAGE-MISSING", f"Summary 引用的 Markdown 不存在：{entry['path']}")
        text = source.read_text(encoding="utf-8-sig")

        def rewrite(match: re.Match) -> str:
            marker, label, target = match.groups()
            path, fragment = normalized_target(target)
            if not path or urlsplit(target).scheme or target.startswith(("//", "\\\\", "/", "\\")):
                return match.group(0)
            resolved = (source.parent / path).resolve()
            try:
                relative = resolved.relative_to(locale_root.resolve()).as_posix()
            except ValueError:
                findings.append({"kind": "unsafe_resource", "page": entry["path"], "target": target})
                return label if not marker else match.group(0)
            if not marker and path.lower().endswith((".md", ".markdown")):
                if relative.casefold() not in selected:
                    findings.append({"kind": "out_of_scope_link", "page": entry["path"], "target": target})
                    return label
                rewritten = names[relative.casefold()]
            else:
                rewritten = relative
            return f"{marker}[{label}]({rewritten}{f'#{fragment}' if fragment else ''})"

        text = link.sub(rewrite, text)

        def rewrite_html(match: re.Match) -> str:
            target = match.group("target")
            path, fragment = normalized_target(target)
            if not path or urlsplit(target).scheme or target.startswith(("//", "\\\\", "/", "\\")):
                return match.group(0)
            try:
                relative = (source.parent / path).resolve().relative_to(locale_root.resolve()).as_posix()
            except ValueError:
                findings.append({"kind": "unsafe_resource", "page": entry["path"], "target": target})
                return match.group(0)
            return f"{match.group('prefix')}{relative}{f'#{fragment}' if fragment else ''}{match.group('suffix')}"

        name = names[entry["path"].casefold()]
        (work / name).write_text(html_resource.sub(rewrite_html, text), encoding="utf-8", newline="\n")
        pages.append((entry, name))
    return pages


def _materialize_scope_resources(locale_root: Path, work: Path, pages: list[tuple[dict, str]], findings: list[dict], extra: list[str] | None = None) -> None:
    root = locale_root.resolve(); documents = [(work / name, root) for _entry, name in pages]; pending = [(target, root) for target in extra or []]; copied = set()
    link = re.compile(r"!?\[[^]]*]\(([^)]+)\)")
    while documents:
        document, base = documents.pop()
        try: text = document.read_text(encoding="utf-8")
        except (OSError, UnicodeError): continue
        pattern = CSS_RESOURCE if document.suffix.casefold() == ".css" else HTML_RESOURCE
        pending.extend((match.group("target"), base) for match in pattern.finditer(text))
        if document.suffix.casefold() in {".md", ".markdown"}: pending.extend((match.group(1), base) for match in link.finditer(text))
    while pending:
        target, base = pending.pop(); path, _fragment = normalized_target(target)
        if not path or urlsplit(target).scheme or target.startswith(("//", "\\", "/")) or path.casefold().endswith((".md", ".markdown")): continue
        candidate = (base / path).resolve()
        try: relative = candidate.relative_to(root)
        except ValueError:
            findings.append({"kind": "unsafe_resource", "path": str(candidate)}); continue
        if relative in copied: continue
        if not candidate.is_file():
            findings.append({"kind": "missing_resource", "path": str(candidate)}); continue
        destination = work / relative; destination.parent.mkdir(parents=True, exist_ok=True)
        try: os.link(candidate, destination)
        except OSError as exc:
            findings.append({"kind": "resource_copy_failed", "path": str(candidate), "error": str(exc)}); continue
        copied.add(relative)
        if candidate.suffix.casefold() == ".css":
            try: pending.extend((match.group("target"), candidate.parent) for match in CSS_RESOURCE.finditer(candidate.read_text(encoding="utf-8")))
            except (OSError, UnicodeError): pass


def _standalone_resource(source: Path, target: str, work: Path, findings: list[dict], copied: dict[Path, Path], destination_parent: Path) -> str | None:
    split = urlsplit(target); raw = unquote(split.path)
    if split.scheme in {"http", "https", "data"} or target.startswith("#"): return target
    if split.scheme == "file": candidate = Path(unquote(split.path.lstrip("/")) if split.netloc in {"", "localhost"} else f"//{split.netloc}{unquote(split.path)}")
    elif re.match(r"^[A-Za-z]:[\\/]", target) or target.startswith(("\\", "//")): candidate = Path(target)
    elif split.scheme: return target
    else: candidate = (source.parent / raw).resolve()
    candidate = candidate.resolve()
    if candidate.suffix.casefold() in {".md", ".markdown"}:
        findings.append({"kind": "out_of_scope_link", "page": str(source), "target": target}); return None
    if not candidate.is_file():
        findings.append({"kind": "missing_resource", "page": str(source), "target": target, "path": str(candidate)}); return target
    if candidate in copied: return os.path.relpath(copied[candidate], destination_parent).replace("\\", "/") + (f"#{split.fragment}" if split.fragment else "")
    import hashlib
    name = f"{hashlib.sha256(str(candidate).casefold().encode('utf-8')).hexdigest()[:12]}-{candidate.name}"; destination = work / "resources" / name; destination.parent.mkdir(parents=True, exist_ok=True)
    try: os.link(candidate, destination)
    except OSError: shutil.copy2(candidate, destination)
    copied[candidate] = destination
    if candidate.suffix.casefold() == ".css":
        try:
            text = candidate.read_text(encoding="utf-8")
            text = CSS_RESOURCE.sub(lambda match: f"{match.group('prefix')}{_standalone_resource(candidate, match.group('target'), work, findings, copied, destination.parent) or match.group('target')}{match.group('suffix')}", text)
            destination.write_text(text, encoding="utf-8", newline="\n")
        except (OSError, UnicodeError) as exc: findings.append({"kind": "resource_copy_failed", "path": str(candidate), "error": str(exc)})
    return os.path.relpath(destination, destination_parent).replace("\\", "/") + (f"#{split.fragment}" if split.fragment else "")


def _materialize_standalone(path: Path, work: Path, findings: list[dict]) -> Path:
    try: text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc: raise MdocError("MDOC-PDF-FILE-INVALID", f"Markdown 文件无法读取：{path}", {"cause": str(exc)}) from exc
    copied = {}
    link = re.compile(r"(!?)\[([^]]*)]\(([^)]+)\)")
    def rewrite(match: re.Match) -> str:
        marker, label, target = match.groups(); rewritten = _standalone_resource(path, target, work, findings, copied, work)
        return label if rewritten is None and not marker else match.group(0) if rewritten is None else f"{marker}[{label}]({rewritten})"
    text = link.sub(rewrite, text)
    text = HTML_RESOURCE.sub(lambda match: f"{match.group('prefix')}{_standalone_resource(path, match.group('target'), work, findings, copied, work) or '#'}{match.group('suffix')}", text)
    page = work / "Page.md"; page.write_text(text, encoding="utf-8", newline="\n"); return page


def _patch_html(intermediate: Path, pages: list[tuple[dict, str]] | None, entries: list[dict], toc: dict, right_values: list[int] | None = None, readme: str = "README.md") -> dict:
    if pages is None:
        readme_path = normalized_target(readme)[0].casefold()
        targets = [(entry, "index.html" if entry["path"].casefold() == readme_path else Path(entry["path"]).with_suffix(".html").as_posix()) for entry in entries]
    else:
        targets = [(entry, "index.html" if index == 0 else Path(name).with_suffix(".html").as_posix()) for index, (entry, name) in enumerate(pages)]
    href_entries = {}
    patched_pages = set()
    for entry, href in targets:
        page = intermediate / href
        normalized_href = posixpath.normpath(unquote(urlsplit(href).path).removeprefix("./")).casefold()
        if page.is_file() and normalized_href not in patched_pages:
            text = page.read_text(encoding="utf-8")
            display = html.escape(f"{entry['number']} {entry['title']}")
            text = re.sub(r"<title>.*?</title>", f"<title>{display}</title>", text, count=1, flags=re.S)
            text = re.sub(r'(<h1 class="book-chapter[^>]*">).*?(</h1>)', lambda match: f"{match.group(1)}{display}{match.group(2)}", text, count=1, flags=re.S)
            page.write_text(text, encoding="utf-8", newline="\n")
            patched_pages.add(normalized_href)
        if page.is_file() and entry.get("anchor"):
            text = page.read_text(encoding="utf-8")
            display = html.escape(f"{entry['number']} {entry['title']}")
            if entry["anchor"].isdigit():
                heading = 0
                def number_heading(match: re.Match) -> str:
                    nonlocal heading
                    heading += 1
                    return f"{match.group(1)}{display}{match.group(3)}" if heading == int(entry["anchor"]) else match.group(0)
                text = re.sub(r'(<h1 id="[^"]+">)(.*?)(</h1>)', number_heading, text, flags=re.S)
            else:
                anchor = re.escape(html.escape(entry["anchor"], quote=True))
                text = re.sub(rf'(<h1 id="{anchor}">).*?(</h1>)', lambda match: f"{match.group(1)}{display}{match.group(2)}", text, count=1, flags=re.S)
            page.write_text(text, encoding="utf-8", newline="\n")
        key = (normalized_href, entry.get("anchor", ""))
        href_entries.setdefault(key, []).append(entry)
    summary = intermediate / "SUMMARY.html"
    text = summary.read_text(encoding="utf-8")
    item_pattern = re.compile(r'(<a href="([^"]+)">)(.*?)(</a>)(.*?<span class="page">)(.*?)(</span>)', re.S)
    item_count = 0
    explicit_count = 0
    href_counts = {}

    def patch_item(match: re.Match) -> str:
        nonlocal item_count, explicit_count
        href = match.group(2); split = urlsplit(href)
        key = (posixpath.normpath(unquote(split.path).removeprefix("./")).casefold(), unquote(split.fragment))
        matches = href_entries.get(key, [])
        index = href_counts.get(key, 0)
        entry = matches[index] if index < len(matches) else None
        href_counts[key] = index + 1
        label = match.group(3) if not entry else html.escape(f"{entry['number']} {entry['title']}" if toc["show_left_number"] else entry["title"])
        if entry: explicit_count += 1
        if right_values is not None:
            right = str(right_values[item_count])
        elif toc["right_value"] == "hierarchy" and entry:
            right = entry["number"]
        else:
            right = match.group(6)
        item_count += 1
        return f"{match.group(1)}{label}{match.group(4)}{match.group(5)}{html.escape(right)}{match.group(7)}"

    text = item_pattern.sub(patch_item, text)
    summary.write_text(text, encoding="utf-8", newline="\n")
    pdf_css = intermediate / "gitbook" / "pdf.css"
    if pdf_css.is_file():
        with pdf_css.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n.page .section table,.page .section pre{page-break-inside:auto;break-inside:auto}.page .section tr{page-break-inside:avoid;break-inside:avoid}.page .section thead{display:table-header-group}\n")
    return {"items": item_count, "explicit_items": explicit_count, "implicit_items": item_count - explicit_count}


def _calibre_options(config: dict, settings: dict, cover: Path | None = None, page_numbers: bool = False) -> list[str]:
    source_pdf = config.get("pdf", {})
    font_family = source_pdf.get("fontFamily")
    if not isinstance(font_family, str) or not font_family.strip():
        raise MdocError("MDOC-PDF-MAIN-FONT-MISSING", "book.json 的 pdf.fontFamily 不能为空。")
    margins = settings["margins_pt"]
    options = [
        "--title", str(config.get("title", "")), "--language", str(config.get("language", "")), "--book-producer", "HonKit",
        "--publisher", str(config.get("publisher", "HonKit")), "--chapter", "descendant-or-self::*[contains(concat(' ', normalize-space(@class), ' '), ' book-chapter ')]",
        "--level1-toc", "descendant-or-self::*[contains(concat(' ', normalize-space(@class), ' '), ' book-chapter-1 ')]",
        "--level2-toc", "descendant-or-self::*[contains(concat(' ', normalize-space(@class), ' '), ' book-chapter-2 ')]",
        "--level3-toc", "descendant-or-self::*[contains(concat(' ', normalize-space(@class), ' '), ' book-chapter-3 ')]",
        "--max-levels", "1", "--no-chapters-in-toc", "--breadth-first", "--chapter-mark", str(source_pdf.get("chapterMark", "pagebreak")),
        "--page-breaks-before", str(source_pdf.get("pageBreaksBefore", "/")), "--pdf-page-margin-left", str(margins["left"]),
        "--pdf-page-margin-right", str(margins["right"]), "--pdf-page-margin-top", str(margins["top"]), "--pdf-page-margin-bottom", str(margins["bottom"]),
        "--pdf-default-font-size", str(source_pdf.get("fontSize", 12)), "--pdf-mono-font-size", str(source_pdf.get("fontSize", 12)),
        "--paper-size", str(settings["paper_size"]), "--pdf-sans-family", font_family,
    ]
    if page_numbers and settings["page_numbers"]["enabled"]:
        position = settings["page_numbers"]["position"]
        offset = "left:50%;transform:translateX(-50%)" if position == "center" else f'{position}:{settings["margins_pt"][position]}pt'
        options.extend(["--pdf-footer-template", f'<span style="position:absolute;{offset}">_PAGENUM_</span>', "--pdf-page-number-map", "n+1" if cover is not None else "n"])
    if source_pdf.get("embedFonts", True):
        options.append("--embed-all-fonts")
    if config.get("author"):
        options.extend(["--authors", str(config["author"])])
    if cover is not None:
        options.extend(["--cover", str(cover)])
        if settings["cover"]["preserve_aspect_ratio"]:
            options.append("--preserve-cover-aspect-ratio")
    return options


def _flatten_outline(items, level=0):
    for item in items:
        if isinstance(item, list):
            yield from _flatten_outline(item, level + 1)
        else:
            yield item, level


def _toc_pages(reader, expected: int, skip: int = 0) -> list[int]:
    from pypdf.generic import ArrayObject, IndirectObject

    pages = []
    for page in reader.pages:
        for reference in page.get("/Annots") or []:
            annotation = reference.get_object()
            if annotation.get("/Subtype") != "/Link":
                continue
            action = annotation.get("/A")
            destination = annotation.get("/Dest") or (action.get("/D") if action else None)
            target = None
            if isinstance(destination, str):
                named = reader.named_destinations.get(destination)
                target = reader.get_destination_page_number(named) if named else None
            elif isinstance(destination, ArrayObject) and destination and isinstance(destination[0], IndirectObject):
                target = reader._get_page_number_by_indirect(destination[0])
            if target is not None:
                pages.append(target)
                if len(pages) == expected + skip:
                    return pages[skip:]
    return pages[skip:]


def _enable_image_interpolation(reader) -> int:
    from pypdf.generic import BooleanObject, IndirectObject, NameObject

    seen = set()
    changed = 0

    def visit(resources) -> None:
        nonlocal changed
        resources = resources.get_object() if isinstance(resources, IndirectObject) else resources
        for reference in (resources.get("/XObject") or {}).values():
            key = (reference.idnum, reference.generation) if isinstance(reference, IndirectObject) else id(reference)
            if key in seen:
                continue
            seen.add(key)
            item = reference.get_object() if isinstance(reference, IndirectObject) else reference
            if item.get("/Subtype") == "/Image":
                if not bool(item.get("/Interpolate")):
                    item[NameObject("/Interpolate")] = BooleanObject(True)
                    changed += 1
            elif item.get("/Subtype") == "/Form" and item.get("/Resources"):
                visit(item["/Resources"])

    for page in reader.pages:
        if page.get("/Resources"):
            visit(page["/Resources"])
    return changed


def _repair_outline(source: Path, output: Path, entries: list[dict], bookmarks: dict, toc_items: int, implicit_items: int) -> dict:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(source))
    toc_pages = _toc_pages(reader, len(entries), implicit_items)
    if len(toc_pages) != len(entries):
        raise MdocError("MDOC-PDF-TOC-TARGETS-INVALID", "可点击目录目标数量不足。", {"expected": len(entries), "actual": len(toc_pages)})
    interpolated = _enable_image_interpolation(reader)
    writer = PdfWriter(clone_from=reader)
    writer._root_object.pop("/Outlines", None)
    parents = {}
    count = 0
    for index, entry in enumerate(entries):
        if bookmarks["levels"] != "all" and entry["level"] >= bookmarks["levels"]:
            continue
        parent = parents.get(entry["level"] - 1)
        title = f"{entry['number']} {entry['title']}" if bookmarks["show_left_number"] else entry["title"]
        parents[entry["level"]] = writer.add_outline_item(title, min(toc_pages[index], len(reader.pages) - 1), parent=parent)
        parents = {level: item for level, item in parents.items() if level <= entry["level"]}
        count += 1
    with output.open("wb") as stream:
        writer.write(stream)
    report = _structural_check(output, entries, bookmarks, implicit_items)
    if report["status"] == "failed":
        raise MdocError("MDOC-PDF-BOOKMARK-REPAIR-FAILED", "重建书签后结构检查失败。", {"findings": report["findings"]})
    return {"toc_targets": len(toc_pages), "toc_items": toc_items, "bookmarks": count, "interpolated_images": interpolated}


def _standalone_outline(source: Path, output: Path, title: str) -> dict:
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(str(source)); interpolated = _enable_image_interpolation(reader); writer = PdfWriter(clone_from=reader); writer._root_object.pop("/Outlines", None); writer.add_outline_item(title, 0)
    with output.open("wb") as stream: writer.write(stream)
    return {"bookmarks": 1, "interpolated_images": interpolated}


def _structural_check(path: Path, entries: list[dict] | None = None, bookmarks: dict | None = None, implicit_items: int = 0) -> dict:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
    except Exception as exc:
        raise MdocError("MDOC-PDF-INVALID", f"PDF 无法解析：{path}", {"cause": str(exc)}) from exc
    if not reader.pages:
        raise MdocError("MDOC-PDF-EMPTY", f"PDF 没有页面：{path}")
    findings = []
    text = "".join(page.extract_text() or "" for page in reader.pages)
    if "�" in text:
        findings.append({"severity": "error", "kind": "replacement_character"})
    fonts = {}
    for page_number, page in enumerate(reader.pages, 1):
        resources = page.get("/Resources") or {}
        for name, reference in (resources.get("/Font") or {}).items():
            font = reference.get_object()
            descriptor = font.get("/FontDescriptor")
            descendants = font.get("/DescendantFonts") or []
            if descendants:
                descendant = descendants[0].get_object()
                descriptor = descriptor or descendant.get("/FontDescriptor")
            descriptor = descriptor.get_object() if descriptor else {}
            embedded = bool(font.get("/CharProcs")) if font.get("/Subtype") == "/Type3" else any(key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3"))
            to_unicode = "/ToUnicode" in font
            key = str(font.get("/BaseFont") or name)
            fonts[key] = {"embedded": embedded, "to_unicode": to_unicode}
            if not embedded or not to_unicode:
                findings.append({"severity": "error", "kind": "font_mapping", "font": key, "page": page_number, "embedded": embedded, "to_unicode": to_unicode})
    bookmarks = bookmarks or DEFAULTS["defaults"]["bookmarks"]
    toc_pages = _toc_pages(reader, len(entries), implicit_items) if entries else []
    outline = []
    for item, level in _flatten_outline(reader.outline):
        try:
            outline.append({"title": item.title, "page": reader.get_destination_page_number(item), "level": level})
        except Exception as exc:
            findings.append({"severity": "error", "kind": "bookmark_target", "title": getattr(item, "title", ""), "error": str(exc)})
    expected_bookmarks = [entry for entry in entries or [] if bookmarks["levels"] == "all" or entry["level"] < bookmarks["levels"]]
    if entries and len(toc_pages) != len(entries):
        findings.append({"severity": "error", "kind": "toc_target_count", "expected": len(entries), "actual": len(toc_pages)})
    if expected_bookmarks and len(outline) != len(expected_bookmarks):
        findings.append({"severity": "error", "kind": "bookmark_count", "expected": len(expected_bookmarks), "actual": len(outline)})
    for item, expected in zip(outline, [(toc_pages[index], entry) for index, entry in enumerate(entries or []) if bookmarks["levels"] == "all" or entry["level"] < bookmarks["levels"]]):
        target, entry = expected
        if item["page"] != target:
            findings.append({"severity": "error", "kind": "bookmark_toc_mismatch", "title": entry["title"], "bookmark_page": item["page"], "toc_page": target})
        if item["level"] != entry["level"]:
            findings.append({"severity": "error", "kind": "bookmark_level_mismatch", "title": entry["title"], "bookmark_level": item["level"], "summary_level": entry["level"]})
        expected_title = f"{entry['number']} {entry['title']}" if bookmarks["show_left_number"] else entry["title"]
        if item["title"] != expected_title:
            findings.append({"severity": "error", "kind": "bookmark_title_mismatch", "expected": expected_title, "actual": item["title"]})
    status = "failed" if any(item["severity"] == "error" for item in findings) else "passed"
    return {"status": status, "path": str(path), "pages": len(reader.pages), "bytes": path.stat().st_size, "toc_targets": len(toc_pages), "bookmarks": len(outline), "fonts": fonts, "findings": findings}


def _pipeline_comparison(before: Path, after: Path) -> dict:
    from pypdf import PdfReader

    first = PdfReader(str(before))
    second = PdfReader(str(after))
    first_text = re.sub(r"\s+", " ", "".join(page.extract_text() or "" for page in first.pages)).strip()
    second_text = re.sub(r"\s+", " ", "".join(page.extract_text() or "" for page in second.pages)).strip()
    first_boxes = [tuple(round(float(value), 3) for value in page.mediabox) for page in first.pages]
    second_boxes = [tuple(round(float(value), 3) for value in page.mediabox) for page in second.pages]
    result = {"page_count": len(first.pages) == len(second.pages), "media_boxes": first_boxes == second_boxes, "normalized_text": first_text == second_text}
    result["passed"] = all(result.values())
    return result


def check(workspace, path: Path, book_id: str | None = None, locale_id: str | None = None, mode: str = "book", target: str | None = None, summary_line: int | None = None) -> dict:
    entries = None
    bookmarks = DEFAULTS["defaults"]["bookmarks"]
    implicit_items = 0
    if mode != "book" and (not book_id or not locale_id): raise MdocError("MDOC-PDF-CHECK-SCOPE-CONTEXT-REQUIRED", "局部 PDF 检查需要同时指定 --book 和 --locale。")
    if book_id and locale_id:
        book = workspace.config["books"].get(book_id)
        if not book or locale_id not in book["locales"]:
            raise MdocError("MDOC-PDF-TARGET-INVALID", f"未知书册或语言：{book_id}/{locale_id}")
        locale_root = workspace.repository / book["root"] / book["locales"][locale_id]["root"]
        entries = summary_entries(locale_root / book["navigation"]["summary"])
        if mode != "book":
            if not target: raise MdocError("MDOC-PDF-TARGET-REQUIRED", "page 和 section 范围需要 --target。")
            entries = scoped_entries(select_entries(entries, target, mode, summary_line))
        bookmarks = effective_settings(workspace.config, book)["bookmarks"]
    return _structural_check(path.resolve(), entries, bookmarks, implicit_items)


def _output_name(book_id: str, locale_id: str, mode: str, target: str | None) -> str:
    if mode == "book":
        return f"{book_id}-{locale_id}.pdf"
    import hashlib

    path = normalized_target(target or "")[0]
    digest = hashlib.sha256(path.casefold().encode("utf-8")).hexdigest()[:8]
    suffix = "-section" if mode == "section" else ""
    return f"{Path(path).stem}-{digest}{suffix}-{locale_id}.pdf"


def _available_memory() -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong), ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong), ("total_page_file", ctypes.c_ulonglong), ("available_page_file", ctypes.c_ulonglong), ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong), ("available_extended_virtual", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        return status.available_physical if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None
    except Exception:
        return None


def effective_jobs(requested: int, force: bool) -> int:
    if force:
        return requested
    available = _available_memory()
    if available is None:
        return 1
    return max(1, min(requested, int(max(0, available - 2 * 1024**3) // (4 * 1024**3))))


def _build_one(workspace, book_id: str, locale_id: str, mode: str, target: str | None, output: Path, keep_work: bool, discard_work: bool, strict_resources: bool, verify_pipeline: bool, summary_line: int | None = None, cancel: threading.Event | None = None) -> dict:
    started = time.monotonic()
    _cancelled(cancel)
    tools = tool_paths()
    missing = [name for name, path in tools.items() if not path.is_file()]
    if missing:
        raise MdocError("MDOC-PDF-TOOLCHAIN-MISSING", "PDF Toolchain 组件缺失。", {"missing": missing, "root": str(toolchain_root())})
    book = workspace.config["books"][book_id]
    locale_root = (workspace.repository / book["root"] / book["locales"][locale_id]["root"]).resolve()
    entries = summary_entries(locale_root / book["navigation"]["summary"])
    notices = []
    if mode == "book": selected = entries
    else:
        selected, notices = select_entries(entries, target or "", mode, summary_line, True)
        selected = scoped_entries(selected)
    settings = effective_settings(workspace.config, book)
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    work = workspace.control / "cache" / "pdf-builds" / run_id
    source = work / "book"
    intermediate = work / "ebook"
    logs = work / "logs"
    work.mkdir(parents=True)
    findings = []
    status = "failed"; report = None
    try:
        stage = time.monotonic()
        pages = None
        if mode == "book":
            _safe_hardlink_tree(locale_root, source, findings, {"book.json"})
            config = _isolated_book_config(locale_root, selected[0]["path"] if selected else None)
        else:
            source.mkdir()
            pages = _materialize_scope(locale_root, selected, source, findings)
            first = selected[0]
            config = _isolated_book_config(locale_root, pages[0][1], f"{json.loads((locale_root / 'book.json').read_text(encoding='utf-8-sig'))['title']} - {first['number']} {first['title']}")
            _materialize_scope_resources(locale_root, source, pages, findings, [value for value in config.get("styles", {}).values() if isinstance(value, str)])
            (source / "Summary.md").write_text("\n".join(f"{'    ' * max(0, entry['level'] - first['level'])}* [{entry['title']}]({name}{f'#{entry['anchor']}' if entry.get('anchor') else ''})" for entry, name in pages) + "\n", encoding="utf-8", newline="\n")
        cover_report, cover_path = _prepare_cover(locale_root, config, settings, work, mode)
        _write_json(source / "book.json", config)
        intermediate.mkdir()
        timings = {"prepare": round(time.monotonic() - stage, 3), "honkit": _run([str(tools["node"]), str(tools["honkit"]), "build", str(source), str(intermediate), "--format", "ebook", "--log", "debug", "--timing"], work, logs / "honkit.log", cancel)}
        _cancelled(cancel)
        readme = config.get("structure", {}).get("readme", "README.md")
        toc_report = _patch_html(intermediate, pages, selected, settings["toc"], readme=readme)
        if toc_report["explicit_items"] != len(selected):
            raise MdocError("MDOC-PDF-TOC-TARGETS-INVALID", "HTML 目录与 Summary 条目无法一一匹配。", {"expected": len(selected), "actual": toc_report["explicit_items"]})
        if settings["toc"]["right_value"] == "hierarchy" and settings["toc"]["show_left_number"]:
            notices.append({"kind": "toc_hierarchy_number_repeated"})
        stage = time.monotonic()
        image_stats = optimize_generated_images(intermediate, settings["image_optimization"])
        _cancelled(cancel)
        timings["images"] = round(time.monotonic() - stage, 3)
        findings.extend(image_stats["findings"])
        raw = work / "raw.pdf"
        outlined = work / "outlined.pdf"
        optimized = work / "optimized.pdf"
        toc_iterations = []
        if settings["toc"]["right_value"] == "page":
            right_values = None
            for iteration in range(1, 4):
                current = raw if iteration == 1 else work / f"raw-{iteration}.pdf"
                duration = _run([str(tools["calibre"]), str(intermediate / "SUMMARY.html"), str(current), *_calibre_options(config, settings, cover_path, mode == "book")], work, logs / f"calibre-{iteration}.log", cancel)
                from pypdf import PdfReader
                targets = _toc_pages(PdfReader(str(current)), toc_report["items"])
                if len(targets) != toc_report["items"]:
                    raise MdocError("MDOC-PDF-TOC-TARGETS-INVALID", "可点击目录目标数量不足。", {"expected": toc_report["items"], "actual": len(targets)})
                desired = [page + 1 for page in targets]
                changed = len(desired) if right_values is None else sum(left != right for left, right in zip(right_values, desired))
                toc_iterations.append({"iteration": iteration, "changed_targets": changed, "duration": duration})
                raw = current
                if right_values == desired:
                    break
                right_values = desired
                _patch_html(intermediate, pages, selected, settings["toc"], right_values, readme)
            else:
                raise MdocError("MDOC-PDF-TOC-PAGE-NUMBERS-NOT-STABLE", "目录页码在三轮分页后仍未收敛。", {"iterations": toc_iterations})
            timings["calibre"] = round(sum(item["duration"] for item in toc_iterations), 3)
        else:
            timings["calibre"] = _run([str(tools["calibre"]), str(intermediate / "SUMMARY.html"), str(raw), *_calibre_options(config, settings, cover_path, mode == "book")], work, logs / "calibre.log", cancel)
            right_values = None
            toc_iterations.append({"iteration": 1, "changed_targets": 0, "duration": timings["calibre"]})
        stage = time.monotonic(); outline = _repair_outline(raw, outlined, selected, settings["bookmarks"], toc_report["items"], toc_report["implicit_items"]); timings["outline"] = round(time.monotonic() - stage, 3)
        _cancelled(cancel)
        candidate = outlined
        if settings["optimization"]["enabled"]:
            command = [str(tools["qpdf"]), str(outlined), str(optimized)]
            if settings["optimization"]["recompress_flate"]:
                command.append("--recompress-flate")
            command.extend([f"--compression-level={settings['optimization']['compression_level']}", f"--object-streams={settings['optimization']['object_streams']}"])
            try:
                timings["qpdf"] = _run(command, work, logs / "qpdf-optimize.log", cancel)
                candidate = optimized
            except MdocError as exc:
                if exc.code == "MDOC-PDF-BUILD-CANCELLED":
                    raise
                findings.append({"kind": "qpdf_optimization_failed", "error": exc.message})
        stage = time.monotonic(); structural = _structural_check(candidate, selected, settings["bookmarks"], toc_report["implicit_items"]); timings["check"] = round(time.monotonic() - stage, 3)
        if right_values is not None:
            from pypdf import PdfReader
            final_values = [page + 1 for page in _toc_pages(PdfReader(str(candidate)), toc_report["items"])]
            if final_values != right_values:
                structural["findings"].append({"severity": "error", "kind": "toc_value_mismatch", "expected": right_values, "actual": final_values})
                structural["status"] = "failed"
        verification = _pipeline_comparison(outlined, candidate) if verify_pipeline and candidate != outlined else None
        if verification and not verification["passed"]:
            structural["findings"].append({"severity": "error", "kind": "pipeline_verification", "details": verification})
            structural["status"] = "failed"
        if structural["status"] == "failed":
            raise MdocError("MDOC-PDF-CHECK-FAILED", "生成的 PDF 未通过结构检查。", {"findings": structural["findings"]})
        resource_findings = [item for item in findings if item["kind"] in {"missing_resource", "unsafe_resource", "resource_copy_failed"}]
        if strict_resources and resource_findings:
            raise MdocError("MDOC-PDF-RESOURCE-STRICT", "严格资源模式下存在资源 finding。", {"findings": resource_findings})
        _cancelled(cancel)
        stage = time.monotonic(); output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        shutil.copy2(candidate, temporary)
        os.replace(temporary, output)
        timings["output"] = round(time.monotonic() - stage, 3)
        status = "passed_with_findings" if findings else "passed"
        report = {"schema_version": 1, "status": status, "book": book_id, "locale": locale_id, "scope": mode, "target": target, "summary_line": summary_line, "output": str(output), "work": str(work), "entries": len(selected), "settings": settings, "cover": cover_report, "toc": {"mode": settings["toc"]["right_value"], "items": toc_report["items"], "implicit_items": toc_report["implicit_items"], "iterations": len(toc_iterations), "changed_targets_per_iteration": [item["changed_targets"] for item in toc_iterations]}, "images": image_stats, "outline": outline, "check": structural, "findings": findings, "notices": notices, "timings": timings, "pipeline_verification": verification}
        return report
    finally:
        cleanup = time.monotonic()
        if status.startswith("passed") and not keep_work:
            shutil.rmtree(work, ignore_errors=True)
        elif status == "failed" and discard_work:
            shutil.rmtree(work, ignore_errors=True)
        if report:
            report["timings"]["cleanup"] = round(time.monotonic() - cleanup, 3); report["timings"]["total"] = round(time.monotonic() - started, 3)
            _write_json(output.with_suffix(".build.json"), report)


def build(workspace, book_id: str | None, locale_id: str | None, mode: str, target: str | None, output: Path | None, all_locales: bool, all_books: bool, jobs: int | None, force_jobs: bool, overwrite: bool, no_overwrite: bool, interactive: bool, keep_work: bool, discard_work: bool, strict_resources: bool, verify_pipeline: bool, summary_line: int | None = None) -> dict:
    if "pdf" not in workspace.config:
        raise MdocError("MDOC-PDF-NOT-CONFIGURED", "工作区尚未配置 PDF，请先执行 mdoc pdf init。")
    book_ids = list(workspace.config["books"]) if all_books else [book_id]
    if not all_books and (not book_id or book_id not in workspace.config["books"]):
        raise MdocError("MDOC-PDF-BOOK-REQUIRED", "请指定有效的 --book，或使用 --all-books。")
    targets = []
    for current_book in book_ids:
        locales = list(workspace.config["books"][current_book]["locales"]) if all_locales or all_books else [locale_id]
        for current_locale in locales:
            if not current_locale or current_locale not in workspace.config["books"][current_book]["locales"]:
                raise MdocError("MDOC-PDF-LOCALE-REQUIRED", f"请为书册指定有效的 --locale：{current_book}")
            locale = workspace.config["books"][current_book]["locales"][current_locale]
            keep_locale_work = keep_work or mode == "book" and not discard_work and (locale.get("pdf") or {}).get("keep_successful_book_work", workspace.config["pdf"]["retention"].get("keep_successful_book_work", False))
            destination = output if output and len(book_ids) == 1 and len(locales) == 1 else workspace.control / "artifacts" / "pdf" / current_book / current_locale / _output_name(current_book, current_locale, mode, target)
            if destination.exists():
                if no_overwrite:
                    targets.append((current_book, current_locale, destination, "skipped", keep_locale_work))
                    continue
                if not overwrite:
                    if not interactive or input(f"目标 PDF 已存在，覆盖？{destination} [y/N] ").strip().casefold() not in {"y", "yes"}:
                        raise MdocError("MDOC-PDF-OVERWRITE-CONFIRMATION-REQUIRED", f"目标 PDF 已存在：{destination}")
            targets.append((current_book, current_locale, destination, "build", keep_locale_work))
    configured = jobs or workspace.config["pdf"]["defaults"]["concurrency"]["builds"]
    actual = effective_jobs(configured, force_jobs)
    results = []
    cancel = threading.Event()
    with ThreadPoolExecutor(max_workers=actual) as executor:
        futures = {executor.submit(_build_one, workspace, book, locale, mode, target, destination, keep_locale_work, discard_work, strict_resources, verify_pipeline, summary_line, cancel): (book, locale, destination) for book, locale, destination, action, keep_locale_work in targets if action == "build"}
        results.extend({"status": "skipped", "book": book, "locale": locale, "output": str(destination)} for book, locale, destination, action, _keep_locale_work in targets if action == "skipped")
        for future in as_completed(futures):
            book, locale, destination = futures[future]
            try:
                results.append(future.result())
            except CancelledError:
                results.append({"status": "cancelled", "book": book, "locale": locale, "output": str(destination)})
            except MdocError as exc:
                status = "cancelled" if exc.code == "MDOC-PDF-BUILD-CANCELLED" else "failed"
                results.append({"status": status, "book": book, "locale": locale, "output": str(destination), "error": exc.payload()["error"]})
                if status == "failed" and not cancel.is_set():
                    print(f"[mdoc] PDF build failed: {book}/{locale}\n{exc.code}: {exc.message}", file=sys.stderr, flush=True)
                    cancel.set()
                    for pending in futures:
                        if pending is not future:
                            pending.cancel()
            except Exception as exc:
                error = MdocError("MDOC-INTERNAL-ERROR", "mdoc 遇到内部错误。", {"cause": str(exc)})
                results.append({"status": "failed", "book": book, "locale": locale, "output": str(destination), "error": error.payload()["error"]})
                if not cancel.is_set():
                    print(f"[mdoc] PDF build failed: {book}/{locale}\n{error.code}: {error.message}", file=sys.stderr, flush=True)
                    cancel.set()
                    for pending in futures:
                        if pending is not future:
                            pending.cancel()
    statuses = {item["status"] for item in results}
    status = "failed" if "failed" in statuses else "passed_with_findings" if "passed_with_findings" in statuses else "passed" if "passed" in statuses else "skipped"
    return {"status": status, "configured_jobs": configured, "actual_jobs": actual, "results": results, "exit_code": 2 if status == "failed" else 0}


def build_file(path: Path, output: Path | None, pdf_config: Path | None, language: str | None, font_family: str | None, no_overwrite: bool, keep_work: bool, discard_work: bool, strict_resources: bool, verify_pipeline: bool) -> dict:
    source_file = path.resolve()
    if not source_file.is_file() or source_file.suffix.casefold() not in {".md", ".markdown"}: raise MdocError("MDOC-PDF-FILE-INVALID", f"--file 必须指向可读取的 Markdown 文件：{source_file}")
    if keep_work and discard_work: raise MdocError("MDOC-PDF-WORK-OPTION-CONFLICT", "--keep-work 与 --discard-work 不能同时使用。")
    destination = standalone_output(source_file, output); destination.parent.mkdir(parents=True, exist_ok=True)
    if no_overwrite and destination.exists(): return {"status": "skipped", "scope": "file", "file": str(source_file), "output": str(destination)}
    settings, notices = standalone_settings(pdf_config); title = standalone_title(source_file); detected = language or standalone_language(source_file); default_font = "SimSun" if detected == "zh-hans" else "Arial"
    config = {"title": title, "language": detected, "structure": {"readme": "Page.md"}, "plugins": [], "pdf": {"fontSize": 12, "fontFamily": font_family or default_font, "pageNumbers": False, "pageBreaksBefore": "/", "chapterMark": "none", "embedFonts": True}}
    work = Path(tempfile.gettempdir()) / "mdoc" / "work" / f"{int(time.time())}-{uuid.uuid4().hex[:8]}"; book = work / "book"; intermediate = work / "ebook"; logs = work / "logs"; book.mkdir(parents=True); findings = []; status = "failed"
    try:
        page = _materialize_standalone(source_file, book, findings); _write_json(book / "book.json", config); intermediate.mkdir()
        tools = tool_paths(); missing = [name for name, tool in tools.items() if not tool.is_file()]
        if missing: raise MdocError("MDOC-PDF-TOOLCHAIN-MISSING", "PDF Toolchain 组件缺失。", {"missing": missing, "root": str(toolchain_root())})
        timings = {"honkit": _run([str(tools["node"]), str(tools["honkit"]), "build", str(book), str(intermediate), "--format", "ebook", "--log", "debug", "--timing"], work, logs / "honkit.log")}
        image_stats = optimize_generated_images(intermediate, settings["image_optimization"]); findings.extend(image_stats["findings"]); raw = work / "raw.pdf"; outlined = work / "outlined.pdf"; optimized = work / "optimized.pdf"
        html_page = intermediate / "index.html"
        if not html_page.is_file(): html_page = intermediate / page.with_suffix(".html").name
        timings["calibre"] = _run([str(tools["calibre"]), str(html_page), str(raw), *_calibre_options(config, settings)], work, logs / "calibre.log")
        if not raw.is_file() or not raw.stat().st_size: raise MdocError("MDOC-PDF-EMPTY", "Calibre 未生成有效 PDF。")
        outline = _standalone_outline(raw, outlined, title); candidate = outlined
        if settings["optimization"]["enabled"]:
            command = [str(tools["qpdf"]), str(outlined), str(optimized)]
            if settings["optimization"]["recompress_flate"]: command.append("--recompress-flate")
            command.extend([f"--compression-level={settings['optimization']['compression_level']}", f"--object-streams={settings['optimization']['object_streams']}"] )
            try: timings["qpdf"] = _run(command, work, logs / "qpdf-optimize.log"); candidate = optimized
            except MdocError as exc: findings.append({"kind": "qpdf_optimization_failed", "error": exc.message})
        verification = _pipeline_comparison(outlined, candidate) if verify_pipeline and candidate != outlined else None
        if verification and not verification["passed"]: raise MdocError("MDOC-PDF-PIPELINE-VERIFY-FAILED", "PDF 优化前后结构不一致。", {"verification": verification})
        resource_findings = [item for item in findings if item["kind"] in {"missing_resource", "unsafe_resource", "resource_copy_failed"}]
        if strict_resources and resource_findings: raise MdocError("MDOC-PDF-RESOURCE-STRICT", "严格资源模式下存在 resource finding。", {"findings": resource_findings})
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp"); shutil.copy2(candidate, temporary); os.replace(temporary, destination); status = "passed_with_findings" if findings else "passed"
        return {"schema_version": 1, "status": status, "scope": "file", "file": str(source_file), "output": str(destination), "work": str(work), "title": title, "language": detected, "settings": settings, "cover": {"enabled": settings["cover"]["enabled"], "configured": False, "applied": False, "preserve_aspect_ratio": settings["cover"]["preserve_aspect_ratio"], "reason": "scope_not_book"}, "images": image_stats, "outline": outline, "findings": findings, "notices": notices, "timings": timings, "pipeline_verification": verification}
    finally:
        if discard_work or status.startswith("passed") and not keep_work: shutil.rmtree(work, ignore_errors=True)


def clean(workspace) -> dict:
    root = workspace.control / "cache" / "pdf-builds"
    days = workspace.config.get("pdf", DEFAULTS)["retention"]["failed_work_days"]
    threshold = time.time() - days * 86400
    removed = []
    if root.is_dir():
        for path in root.iterdir():
            if path.is_dir() and path.stat().st_mtime < threshold:
                shutil.rmtree(path)
                removed.append(str(path))
    reports = sorted(workspace.control.glob("artifacts/pdf/**/*.build.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    keep = workspace.config.get("pdf", DEFAULTS)["retention"]["batch_reports"]
    for path in reports[keep:]:
        path.unlink(missing_ok=True)
        removed.append(str(path))
    standalone = Path(tempfile.gettempdir()) / "mdoc"
    for path in standalone.glob("*.pdf") if standalone.is_dir() else []:
        path.unlink(missing_ok=True)
        removed.append(str(path))
    return {"status": "pdf_cache_cleaned", "removed": removed}
