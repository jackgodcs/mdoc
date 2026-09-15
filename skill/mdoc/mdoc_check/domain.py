from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from PIL import Image, UnidentifiedImageError

from .checkers import BRIDGE, NODE, _run
from .core import summary_entries
from .model import finding


ALLOWED_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
LOCAL_ABSOLUTE = re.compile(r"(?:file://|(?<![A-Za-z])[A-Za-z]:[\\/])", re.IGNORECASE)
HEADING = re.compile(r"^(#{1,6})\s+\S", re.MULTILINE)
ENGLISH_PUNCTUATION = re.compile(r"[，。；：！？、“”‘’（）]")
HTML_CLOSE_WITHOUT_BLANK = re.compile(r"</(?:div|table)>[ \t]*\n(?=\S)", re.IGNORECASE)
HTML_RESOURCE = re.compile(r"<(?:img|a)\b[^>]*?\b(?:src|href)\s*=\s*(['\"])(.*?)\1", re.IGNORECASE)
HTML_ID = re.compile(r"\bid\s*=\s*(['\"])(.*?)\1", re.IGNORECASE)
HTML_TABLE_TAG = re.compile(r"<\s*(/?)\s*(table|tr|th|td)\b[^>]*>", re.IGNORECASE)
INLINE_DISABLE = re.compile(r"<!--\s*markdownlint-(?:disable|enable|capture|restore|disable-file)", re.IGNORECASE)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
FORMAT_EXTENSIONS = {"JPEG": {".jpg", ".jpeg"}, "PNG": {".png"}, "GIF": {".gif"}, "BMP": {".bmp"}, "WEBP": {".webp"}, "TIFF": {".tif", ".tiff"}}
HONKIT_SLUG_SYMBOLS = set("[]!\"'#$%&()*+,./:;<=>?@^`{|}~©∑®†“”‘’∂ƒ™℠…œŒ˚ºª•∆∞♥")


def _exact_path(root: Path, relative: PurePosixPath) -> tuple[Path | None, bool]:
    current = root
    exact = True
    for part in relative.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            current = current.parent
            continue
        if not current.is_dir():
            return None, exact
        names = {child.name.lower(): child.name for child in current.iterdir()}
        actual = names.get(part.lower())
        if actual is None:
            return None, exact
        exact = exact and actual == part
        current /= actual
    return current, exact


def _parsed(files: list[Path]) -> dict[str, dict]:
    request = {"action": "parse", "files": [str(path.resolve()) for path in files]}
    raw = json.loads(_run([str(NODE), str(BRIDGE)], input_text=json.dumps(request)).stdout)
    results = {}
    for name, tokens in raw.items():
        found = []
        visible = []
        headings = []
        html_ids = set()
        for token in tokens:
            line = (token.get("map") or [0])[0] + 1
            if token["type"] == "heading_open":
                headings.append({"level": int(token["tag"][1:]), "line": line})
            if token["type"] in {"html_block", "html_inline"}:
                for match in HTML_RESOURCE.finditer(token.get("content") or ""):
                    found.append({"kind": "image" if match.group(0).lower().startswith("<img") else "link", "target": match.group(2), "line": line})
                html_ids.update(match.group(2) for match in HTML_ID.finditer(token.get("content") or ""))
            auto_link_depth = 0
            for child in token.get("children") or []:
                if child["type"] == "link_open" and child.get("markup") == "autolink":
                    auto_link_depth += 1
                if child["type"] == "text" and auto_link_depth == 0:
                    visible.append({"text": child["content"], "line": line})
                if child["type"] == "link_close" and child.get("markup") == "autolink":
                    auto_link_depth = max(0, auto_link_depth - 1)
                if child["type"] not in {"link_open", "image"}:
                    continue
                attrs = dict(child.get("attrs") or [])
                target = attrs.get("href") or attrs.get("src")
                if target:
                    found.append({"kind": "image" if child["type"] == "image" else "link", "target": target, "line": line})
        results[name] = {"references": found, "visible": visible, "headings": headings, "html_ids": sorted(html_ids), "tokens": tokens}
    return results


def _slug(value: str) -> str:
    value = "".join(character for character in value if character not in HONKIT_SLUG_SYMBOLS).replace(" ", "-").lower()
    return value[1:] if value.startswith("-") else value


def _anchors(parsed: dict) -> set[str]:
    anchors = set(parsed["html_ids"])
    tokens = parsed["tokens"]
    for index, token in enumerate(tokens):
        if token["type"] == "heading_open" and index + 1 < len(tokens) and tokens[index + 1]["type"] == "inline":
            inline = tokens[index + 1]
            text = "".join(child.get("content") or "" for child in inline.get("children") or [] if child["type"] in {"text", "code_inline", "image"})
            anchors.add(_slug(text))
    return anchors


def _inline_text(token: dict) -> str:
    return "".join(child.get("content") or "" for child in token.get("children") or [] if child["type"] in {"text", "code_inline", "image"})


def _image_findings(path: Path, display: str, rules: dict) -> list[dict]:
    results = []
    try:
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
            image_format = image.format or ""
        byte_size = path.stat().st_size
        if byte_size == 0:
            results.append(finding("image.non-empty", "error", display, "Image file is empty.", 1, 1, mandatory=True))
        if path.suffix.lower() not in FORMAT_EXTENSIONS.get(image_format, {path.suffix.lower()}):
            results.append(finding("image.extension-matches-format", "error", display, f"Image extension does not match decoded format {image_format}.", 1, 1))
        allowed = {str(value).lower() for value in rules.get("allowed_formats") or []}
        normalized_format = "jpeg" if image_format.upper() == "JPEG" else image_format.lower()
        if allowed and normalized_format not in allowed:
            results.append(finding("image.allowed-format", "warning", display, f"Image format {normalized_format or 'unknown'} is not in the configured allowed formats.", 1, 1))
        thresholds = (("min_width_px", width, "image.min-width", "below"), ("min_height_px", height, "image.min-height", "below"), ("max_width_px", width, "image.max-width", "above"), ("max_height_px", height, "image.max-height", "above"), ("min_bytes", byte_size, "image.min-bytes", "below"), ("max_bytes", byte_size, "image.max-bytes", "above"))
        for key, actual, rule, direction in thresholds:
            limit = rules.get(key)
            if limit is not None and (actual < limit if direction == "below" else actual > limit):
                results.append(finding(rule, "warning", display, f"Image {key} threshold is {limit}; actual value is {actual}.", 1, 1))
    except (OSError, UnidentifiedImageError):
        results.append(finding("image.decodable", "error", display, "Image cannot be decoded.", 1, 1, mandatory=True))
    return results


def inspect(locale_root: Path, locale: str, language: str, logical_paths: list[str], content_root: str, assets_root: str, summary_path: str, whole_book: bool, image_rules: dict | None = None, physical_files: dict[str, Path] | None = None, forced_images: set[str] | None = None, image_workers: int = 1, brands: dict | None = None, brand_sources: dict[Path, str] | None = None) -> tuple[list[dict], dict]:
    image_rules = image_rules or {}
    forced_images = forced_images or set()
    findings = []
    images = set()
    image_checks = []
    resources = set()
    physical_files = physical_files or {}
    files = [physical_files.get(path, locale_root / Path(*PurePosixPath(path).parts)) for path in logical_paths]
    exact_logical = {path.lower(): path for path in physical_files}
    parsed = _parsed([path for path in files if path.is_file()])
    for logical, physical in zip(logical_paths, files):
        display = f"{locale}/{logical}"
        try:
            text = physical.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        if brands:
            from .brands import inspect as inspect_brands
            findings.extend(inspect_brands(text, display, brands, brand_sources or {}))
        page_parsed = parsed.get(str(physical.resolve()), {"references": [], "visible": [], "headings": []})
        if logical != summary_path and len([heading for heading in page_parsed["headings"] if heading["level"] == 1]) != 1:
            findings.append(finding("markdown.single-h1", "error", display, "Content page must contain exactly one H1.", 1, 1))
        if language == "en":
            for visible in page_parsed["visible"]:
                punctuation = ENGLISH_PUNCTUATION.search(visible["text"])
                if punctuation:
                    findings.append(finding("locale.en-punctuation", "error", display, f"English Markdown contains non-English punctuation: {punctuation.group(0)}", visible["line"], punctuation.start() + 1))
                    break
        html_spacing = HTML_CLOSE_WITHOUT_BLANK.search(text)
        if html_spacing:
            before = text[:html_spacing.start()]
            findings.append(finding("html.block-blank-line", "error", display, "HTML block must be followed by a blank line.", before.count("\n") + 1, 1))
        inline_disable = INLINE_DISABLE.search(text)
        if inline_disable:
            before = text[:inline_disable.start()]
            findings.append(finding("markdown.inline-disable", "warning", display, "Inline markdownlint control directives are not allowed to suppress workspace checks.", before.count("\n") + 1, 1))
        lines = text.splitlines()
        indented_heading = next((heading for heading in page_parsed["headings"] if heading["line"] <= len(lines) and lines[heading["line"] - 1].startswith(" ")), None)
        if indented_heading:
            findings.append(finding("markdown.md023", "error", display, "Heading must start at the beginning of the line.", indented_heading["line"], 1, checker="mdoc", native_rule="MD023"))
        table_stack = []
        malformed_table = None
        for tag in HTML_TABLE_TAG.finditer(text):
            closing, name = bool(tag.group(1)), tag.group(2).lower()
            if not closing:
                table_stack.append(name)
            elif not table_stack or table_stack.pop() != name:
                malformed_table = tag
                break
        if malformed_table is None and table_stack:
            malformed_table = next(HTML_TABLE_TAG.finditer(text), None)
        if malformed_table is not None:
            before = text[:malformed_table.start()]
            findings.append(finding("html.table-structure", "error", display, "HTML table tags must be balanced and correctly nested.", before.count("\n") + 1, 1))
        for segment in PurePosixPath(logical).parts:
            if not ALLOWED_SEGMENT.fullmatch(segment):
                findings.append(finding("path.ascii-only", "error", display, f"Managed path segment must use only ASCII letters, digits, '.', '-' or '_': {segment}", 1, 1))
                break
        for reference in page_parsed["references"]:
            target = reference["target"]
            if LOCAL_ABSOLUTE.search(target):
                findings.append(finding("path.no-local-absolute", "error", display, f"Local resource reference must not use an absolute path: {target}", reference["line"], 1, mandatory=True))
                continue
            parsed_target = urlsplit(target)
            if parsed_target.scheme or target.startswith("//"):
                continue
            if target.startswith("#"):
                if unquote(parsed_target.fragment) not in _anchors(page_parsed):
                    findings.append(finding("link.anchor-exists", "error", display, f"Linked anchor is missing: {target}", reference["line"], 1))
                continue
            relative_text = unquote(parsed_target.path).replace("\\", "/")
            if "\\" in parsed_target.path:
                findings.append(finding("path.forward-slash", "error", display, f"Local reference must use '/': {target}", reference["line"], 1))
            relative = PurePosixPath(logical).parent / PurePosixPath(relative_text)
            normalized_parts = []
            escaped = False
            for part in relative.parts:
                if part in {"", "."}:
                    continue
                if part == "..":
                    if not normalized_parts:
                        escaped = True
                        break
                    normalized_parts.pop()
                else:
                    normalized_parts.append(part)
            normalized = PurePosixPath(*normalized_parts).as_posix()
            if escaped:
                findings.append(finding("path.inside-locale", "error", display, f"Local reference escapes the locale root: {target}", reference["line"], 1, mandatory=True))
                continue
            if physical_files:
                actual_logical = exact_logical.get(normalized.lower())
                candidate = physical_files.get(actual_logical) if actual_logical else None
                exact = actual_logical == normalized
            else:
                candidate, exact = _exact_path(locale_root, relative)
            try:
                (locale_root / Path(*relative.parts)).resolve().relative_to(locale_root.resolve())
            except ValueError:
                findings.append(finding("path.inside-locale", "error", display, f"Local reference escapes the locale root: {target}", reference["line"], 1, mandatory=True))
                continue
            if candidate is None:
                findings.append(finding("link.target-exists", "error", display, f"Linked target is missing: {target}", reference["line"], 1))
                continue
            if not exact:
                findings.append(finding("path.case-exact", "error", display, f"Reference path casing does not match disk: {target}", reference["line"], 1))
            relative_actual = actual_logical if physical_files else candidate.relative_to(locale_root).as_posix()
            if candidate.suffix.lower() in {".md", ".markdown"}:
                for segment in PurePosixPath(relative_actual).parts:
                    if not ALLOWED_SEGMENT.fullmatch(segment):
                        findings.append(finding("path.ascii-only", "error", f"{locale}/{relative_actual}", f"Managed Markdown path segment must use only ASCII letters, digits, '.', '-' or '_': {segment}", 1, 1))
                        break
            if parsed_target.fragment:
                target_parsed = parsed.get(str(candidate.resolve()))
                if target_parsed is None and candidate.suffix.lower() in {".md", ".markdown"}:
                    target_parsed = _parsed([candidate])[str(candidate.resolve())]
                if target_parsed is not None and unquote(parsed_target.fragment) not in _anchors(target_parsed):
                    findings.append(finding("link.anchor-exists", "error", display, f"Linked anchor is missing: {target}", reference["line"], 1))
            if reference["kind"] == "image" or candidate.suffix.lower() in IMAGE_EXTENSIONS:
                image_display = f"{locale}/{relative_actual}"
                if image_display not in images:
                    images.add(image_display)
                    image_checks.append((candidate, image_display))
            else:
                resources.add(f"{locale}/{relative_actual}")
    if whole_book:
        from .core import summary_items

        summary = physical_files.get(summary_path, locale_root / Path(*PurePosixPath(summary_path).parts))
        items = summary_items(summary) if summary.is_file() else []
        linked = {item["path"] for item in items}
        seen = set()
        for item in items:
            path = item["path"]
            if path in seen:
                findings.append(finding("navigation.duplicate-entry", "error", f"{locale}/{summary_path}", f"Summary.md links the same page more than once: {path}", item["line"], 1))
            seen.add(path)
            target = physical_files.get(path, locale_root / Path(*PurePosixPath(path).parts))
            target_parsed = parsed.get(str(target.resolve())) if target.is_file() else None
            if target_parsed:
                tokens = target_parsed["tokens"]
                h1_title = next((_inline_text(tokens[index + 1]) for index, token in enumerate(tokens[:-1]) if token["type"] == "heading_open" and token["tag"] == "h1" and tokens[index + 1]["type"] == "inline"), None)
                if h1_title is not None and item["title"].strip() != h1_title.strip():
                    findings.append(finding("navigation.title-matches-h1", "warning", f"{locale}/{summary_path}", f"Summary title '{item['title']}' differs from page H1 '{h1_title}'.", item["line"], 1))
        for logical in logical_paths:
            if logical != summary_path and logical.startswith(content_root.rstrip("/") + "/") and logical not in linked:
                findings.append(finding("navigation.page-linked", "error", f"{locale}/{logical}", "Markdown page is not linked from Summary.md.", 1, 1))
        asset_directory = locale_root / Path(*PurePosixPath(assets_root).parts)
        if physical_files or asset_directory.is_dir():
            referenced = images | resources
            assets = sorted((logical, physical) for logical, physical in physical_files.items() if logical.startswith(assets_root.rstrip("/") + "/")) if physical_files else [(path.relative_to(locale_root).as_posix(), path) for path in sorted(asset_directory.rglob("*")) if path.is_file()]
            for relative, asset in assets:
                display = f"{locale}/{relative}"
                if relative in forced_images and display not in images:
                    images.add(display)
                    image_checks.append((asset, display))
                if display not in referenced:
                    findings.append(finding("resource.referenced", "warning", display, "Managed resource is not referenced by checked Markdown.", 1, 1))
    if image_checks:
        with ThreadPoolExecutor(max_workers=min(image_workers, len(image_checks))) as executor:
            for future in [executor.submit(_image_findings, path, display, image_rules) for path, display in image_checks]:
                findings.extend(future.result())
    return findings, {"images": sorted(images), "resources": sorted(resources)}
