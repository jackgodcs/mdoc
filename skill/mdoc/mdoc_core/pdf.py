from __future__ import annotations

import copy
import html
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        "bookmarks": {"levels": 3},
        "concurrency": {"builds": 3, "images": "auto"},
    },
    "retention": {"failed_work_days": 7, "batch_reports": 20},
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
    defaults = config.get("pdf", {}).get("defaults", DEFAULTS["defaults"])
    return _deep_merge(thaw(defaults), thaw(book.get("pdf", {})))


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


def select_entries(entries: list[dict], target: str, mode: str) -> list[dict]:
    target_path, target_anchor = normalized_target(target)
    matches = [entry for entry in entries if entry["path"].casefold() == target_path.casefold() and entry["anchor"] == target_anchor]
    if len(matches) != 1:
        raise MdocError("MDOC-PDF-SCOPE-AMBIGUOUS", f"PDF 范围必须在 Summary 中唯一匹配：{target}", {"matches": len(matches)})
    selected = matches[0]
    if mode == "page":
        return [selected]
    start = entries.index(selected)
    end = start + 1
    while end < len(entries) and entries[end]["level"] > selected["level"]:
        end += 1
    return entries[start:end]


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


def _run(command: list[str], cwd: Path, log: Path) -> float:
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log.open("w", encoding="utf-8", errors="replace", newline="\n") as stream:
        process = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, text=True, check=False)
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
    if readme is not None:
        config.setdefault("structure", {})["readme"] = readme
    config["plugins"] = []
    config.pop("pluginsConfig", None)
    return config


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


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


def _patch_html(intermediate: Path, pages: list[tuple[dict, str]] | None, entries: list[dict]) -> None:
    if pages is None:
        targets = [(entry, Path(entry["path"]).with_suffix(".html").as_posix()) for entry in entries]
    else:
        targets = [(entry, "index.html" if index == 0 else Path(name).with_suffix(".html").as_posix()) for index, (entry, name) in enumerate(pages)]
    href_numbers = {}
    for entry, href in targets:
        page = intermediate / href
        if not page.is_file():
            continue
        text = page.read_text(encoding="utf-8")
        display = html.escape(f"{entry['number']} {entry['title']}")
        text = re.sub(r"<title>.*?</title>", f"<title>{display}</title>", text, count=1, flags=re.S)
        text = re.sub(r'(<h1 class="book-chapter[^>]*">).*?(</h1>)', lambda match: f"{match.group(1)}{display}{match.group(2)}", text, count=1, flags=re.S)
        page.write_text(text, encoding="utf-8", newline="\n")
        href_numbers[href.casefold()] = entry["number"]
    summary = intermediate / "SUMMARY.html"
    text = summary.read_text(encoding="utf-8")

    def number_link(match: re.Match) -> str:
        href, label = match.group(1), match.group(2)
        number = href_numbers.get(unquote(urlsplit(href).path).removeprefix("./").casefold())
        return match.group(0) if not number or label.lstrip().startswith(number + " ") else f'<a href="{href}">{number} {label}</a>'

    text = re.sub(r'<a href="([^"]+)">(.*?)</a>', number_link, text, flags=re.S)
    summary.write_text(text, encoding="utf-8", newline="\n")
    pdf_css = intermediate / "gitbook" / "pdf.css"
    if pdf_css.is_file():
        with pdf_css.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n.page .section table,.page .section pre{page-break-inside:auto;break-inside:auto}.page .section tr{page-break-inside:avoid;break-inside:avoid}.page .section thead{display:table-header-group}\n")


def _calibre_options(config: dict, settings: dict) -> list[str]:
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
    if source_pdf.get("pageNumbers"):
        options.append("--pdf-page-numbers")
    if source_pdf.get("embedFonts", True):
        options.append("--embed-all-fonts")
    if config.get("author"):
        options.extend(["--authors", str(config["author"])])
    return options


def _flatten_outline(items, level=0):
    for item in items:
        if isinstance(item, list):
            yield from _flatten_outline(item, level + 1)
        else:
            yield item, level


def _toc_pages(reader, expected: int) -> list[int]:
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
                if len(pages) == expected:
                    return pages
    return pages


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


def _repair_outline(source: Path, output: Path, entries: list[dict], levels, qpdf: Path, work: Path) -> dict:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(source))
    toc_pages = _toc_pages(reader, len(entries))
    if len(toc_pages) < len(entries) - 1:
        raise MdocError("MDOC-PDF-TOC-TARGETS-INVALID", "可点击目录目标数量不足。", {"expected": len(entries), "actual": len(toc_pages)})
    interpolated = _enable_image_interpolation(reader)
    writer = PdfWriter(clone_from=reader)
    writer._root_object.pop("/Outlines", None)
    parents = {}
    count = 0
    for index, entry in enumerate(entries):
        if levels != "all" and entry["level"] >= levels:
            continue
        parent = parents.get(entry["level"] - 1)
        parents[entry["level"]] = writer.add_outline_item(f"{entry['number']} {entry['title']}", min(toc_pages[index], len(reader.pages) - 1), parent=parent)
        parents = {level: item for level, item in parents.items() if level <= entry["level"]}
        count += 1
    with output.open("wb") as stream:
        writer.write(stream)
    report = _structural_check(output, entries, levels)
    if report["status"] == "failed":
        raise MdocError("MDOC-PDF-BOOKMARK-REPAIR-FAILED", "重建书签后结构检查失败。", {"findings": report["findings"]})
    return {"toc_targets": len(toc_pages), "bookmarks": count, "interpolated_images": interpolated}


def _structural_check(path: Path, entries: list[dict] | None = None, bookmark_levels=3) -> dict:
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
    toc_pages = _toc_pages(reader, len(entries)) if entries else []
    outline = []
    for item, level in _flatten_outline(reader.outline):
        try:
            outline.append({"title": item.title, "page": reader.get_destination_page_number(item), "level": level})
        except Exception as exc:
            findings.append({"severity": "error", "kind": "bookmark_target", "title": getattr(item, "title", ""), "error": str(exc)})
    expected_bookmarks = [entry for entry in entries or [] if bookmark_levels == "all" or entry["level"] < bookmark_levels]
    if entries and len(toc_pages) != len(entries):
        findings.append({"severity": "error", "kind": "toc_target_count", "expected": len(entries), "actual": len(toc_pages)})
    if expected_bookmarks and len(outline) != len(expected_bookmarks):
        findings.append({"severity": "error", "kind": "bookmark_count", "expected": len(expected_bookmarks), "actual": len(outline)})
    for item, expected in zip(outline, [(toc_pages[index], entry) for index, entry in enumerate(entries or []) if bookmark_levels == "all" or entry["level"] < bookmark_levels]):
        target, entry = expected
        if item["page"] != target:
            findings.append({"severity": "error", "kind": "bookmark_toc_mismatch", "title": entry["title"], "bookmark_page": item["page"], "toc_page": target})
        if item["level"] != entry["level"]:
            findings.append({"severity": "error", "kind": "bookmark_level_mismatch", "title": entry["title"], "bookmark_level": item["level"], "summary_level": entry["level"]})
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


def check(workspace, path: Path, book_id: str | None = None, locale_id: str | None = None) -> dict:
    entries = None
    levels = 3
    if book_id and locale_id:
        book = workspace.config["books"].get(book_id)
        if not book or locale_id not in book["locales"]:
            raise MdocError("MDOC-PDF-TARGET-INVALID", f"未知书册或语言：{book_id}/{locale_id}")
        locale_root = workspace.repository / book["root"] / book["locales"][locale_id]["root"]
        entries = summary_entries(locale_root / book["navigation"]["summary"])
        levels = effective_settings(workspace.config, book)["bookmarks"]["levels"]
    return _structural_check(path.resolve(), entries, levels)


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
    return max(1, min(requested, int(max(0, available - 4 * 1024**3) // (8 * 1024**3))))


def _build_one(workspace, book_id: str, locale_id: str, mode: str, target: str | None, output: Path, keep_work: bool, discard_work: bool, strict_resources: bool, verify_pipeline: bool) -> dict:
    tools = tool_paths()
    missing = [name for name, path in tools.items() if not path.is_file()]
    if missing:
        raise MdocError("MDOC-PDF-TOOLCHAIN-MISSING", "PDF Toolchain 组件缺失。", {"missing": missing, "root": str(toolchain_root())})
    book = workspace.config["books"][book_id]
    locale_root = (workspace.repository / book["root"] / book["locales"][locale_id]["root"]).resolve()
    entries = summary_entries(locale_root / book["navigation"]["summary"])
    selected = entries if mode == "book" else select_entries(entries, target or "", mode)
    settings = effective_settings(workspace.config, book)
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    work = workspace.control / "cache" / "pdf-builds" / run_id
    source = work / "book"
    intermediate = work / "ebook"
    logs = work / "logs"
    work.mkdir(parents=True)
    findings = []
    status = "failed"
    try:
        pages = None
        if mode == "book":
            _safe_hardlink_tree(locale_root, source, findings, {"book.json"})
            config = _isolated_book_config(locale_root)
        else:
            excluded = {"book.json", book["navigation"]["summary"], *(entry["path"] for entry in selected)}
            _safe_hardlink_tree(locale_root, source, findings, excluded)
            pages = _materialize_scope(locale_root, selected, source, findings)
            first = selected[0]
            config = _isolated_book_config(locale_root, pages[0][1], f"{json.loads((locale_root / 'book.json').read_text(encoding='utf-8-sig'))['title']} - {first['number']} {first['title']}")
            (source / "Summary.md").write_text("\n".join(f"{'    ' * max(0, entry['level'] - first['level'])}* [{entry['title']}]({name})" for entry, name in pages) + "\n", encoding="utf-8", newline="\n")
        _write_json(source / "book.json", config)
        intermediate.mkdir()
        timings = {"honkit": _run([str(tools["node"]), str(tools["honkit"]), "build", str(source), str(intermediate), "--format", "ebook", "--log", "debug", "--timing"], work, logs / "honkit.log")}
        _patch_html(intermediate, pages, selected)
        image_stats = optimize_generated_images(intermediate, settings["image_optimization"])
        findings.extend(image_stats["findings"])
        raw = work / "raw.pdf"
        outlined = work / "outlined.pdf"
        optimized = work / "optimized.pdf"
        timings["calibre"] = _run([str(tools["calibre"]), str(intermediate / "SUMMARY.html"), str(raw), *_calibre_options(config, settings)], work, logs / "calibre.log")
        outline = _repair_outline(raw, outlined, selected, settings["bookmarks"]["levels"], tools["qpdf"], work)
        candidate = outlined
        if settings["optimization"]["enabled"]:
            command = [str(tools["qpdf"]), str(outlined), str(optimized)]
            if settings["optimization"]["recompress_flate"]:
                command.append("--recompress-flate")
            command.extend([f"--compression-level={settings['optimization']['compression_level']}", f"--object-streams={settings['optimization']['object_streams']}"])
            try:
                timings["qpdf"] = _run(command, work, logs / "qpdf-optimize.log")
                candidate = optimized
            except MdocError as exc:
                findings.append({"kind": "qpdf_optimization_failed", "error": exc.message})
        structural = _structural_check(candidate, selected, settings["bookmarks"]["levels"])
        verification = _pipeline_comparison(outlined, candidate) if verify_pipeline and candidate != outlined else None
        if verification and not verification["passed"]:
            structural["findings"].append({"severity": "error", "kind": "pipeline_verification", "details": verification})
            structural["status"] = "failed"
        if structural["status"] == "failed":
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
            shutil.copy2(candidate, temporary)
            os.replace(temporary, output)
            raise MdocError("MDOC-PDF-CHECK-FAILED", "生成的 PDF 未通过结构检查。", {"findings": structural["findings"]})
        resource_findings = [item for item in findings if item["kind"] in {"missing_resource", "unsafe_resource", "resource_copy_failed"}]
        if strict_resources and resource_findings:
            raise MdocError("MDOC-PDF-RESOURCE-STRICT", "严格资源模式下存在资源 finding。", {"findings": resource_findings})
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        shutil.copy2(candidate, temporary)
        os.replace(temporary, output)
        status = "passed_with_findings" if findings else "passed"
        report = {"schema_version": 1, "status": status, "book": book_id, "locale": locale_id, "scope": mode, "target": target, "output": str(output), "work": str(work), "entries": len(selected), "settings": settings, "images": image_stats, "outline": outline, "check": structural, "findings": findings, "timings": timings, "pipeline_verification": verification}
        _write_json(output.with_suffix(".build.json"), report)
        return report
    finally:
        if status.startswith("passed") and not keep_work:
            shutil.rmtree(work, ignore_errors=True)
        elif status == "failed" and discard_work:
            shutil.rmtree(work, ignore_errors=True)


def build(workspace, book_id: str | None, locale_id: str | None, mode: str, target: str | None, output: Path | None, all_locales: bool, all_books: bool, jobs: int | None, force_jobs: bool, overwrite: bool, no_overwrite: bool, interactive: bool, keep_work: bool, discard_work: bool, strict_resources: bool, verify_pipeline: bool) -> dict:
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
            destination = output if output and len(book_ids) == 1 and len(locales) == 1 else workspace.control / "artifacts" / "pdf" / current_book / current_locale / _output_name(current_book, current_locale, mode, target)
            if destination.exists():
                if no_overwrite:
                    targets.append((current_book, current_locale, destination, "skipped"))
                    continue
                if not overwrite:
                    if not interactive or input(f"目标 PDF 已存在，覆盖？{destination} [y/N] ").strip().casefold() not in {"y", "yes"}:
                        raise MdocError("MDOC-PDF-OVERWRITE-CONFIRMATION-REQUIRED", f"目标 PDF 已存在：{destination}")
            targets.append((current_book, current_locale, destination, "build"))
    configured = jobs or workspace.config["pdf"]["defaults"]["concurrency"]["builds"]
    actual = effective_jobs(configured, force_jobs)
    results = []
    with ThreadPoolExecutor(max_workers=actual) as executor:
        futures = {executor.submit(_build_one, workspace, book, locale, mode, target, destination, keep_work, discard_work, strict_resources, verify_pipeline): (book, locale, destination) for book, locale, destination, action in targets if action == "build"}
        results.extend({"status": "skipped", "book": book, "locale": locale, "output": str(destination)} for book, locale, destination, action in targets if action == "skipped")
        for future in as_completed(futures):
            book, locale, destination = futures[future]
            try:
                results.append(future.result())
            except MdocError as exc:
                results.append({"status": "failed", "book": book, "locale": locale, "output": str(destination), "error": exc.payload()["error"]})
    statuses = {item["status"] for item in results}
    return {"status": "failed" if "failed" in statuses else "passed_with_findings" if "passed_with_findings" in statuses else "passed" if "passed" in statuses else "skipped", "configured_jobs": configured, "actual_jobs": actual, "results": results}


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
    return {"status": "pdf_cache_cleaned", "removed": removed}
