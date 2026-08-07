#!/usr/bin/env python3
"""Optional, zero-install Quality Gate for mdoc Markdown manuals."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

QUICK = {
    "MDOC-PATH-MISSING", "MDOC-PATH-CASE", "MDOC-PATH-ABSOLUTE", "MDOC-FILENAME-SPACE",
    "MDOC-LINK-LEVEL", "MDOC-HTML-IMG-SYNTAX", "MDOC-HTML-BLOCK-SYNTAX",
    "MDOC-HEADING-SYNTAX", "MDOC-FENCE-UNCLOSED", "MDOC-EMPHASIS-SYNTAX",
    "MDOC-PLACEHOLDER", "MDOC-IMAGE-SYNTAX", "MDOC-IMAGE-READABLE",
    "MDOC-TABLE-SYNTAX", "MDOC-TABLE-HTML",
}
FULL = QUICK | {
    "MDOC-LINK-AMBIGUOUS", "MDOC-ANCHOR-MISSING", "MDOC-ANCHOR-DUPLICATE",
    "MDOC-HTML-BLANK-LINE", "MDOC-HEADING-HIERARCHY", "MDOC-LIST-COMPAT",
    "MDOC-PARAGRAPH-LONG", "MDOC-IMAGE-WIDTH", "MDOC-IMAGE-WIDTH-STEP",
    "MDOC-IMAGE-DIMENSION", "MDOC-IMAGE-LOCALE-WIDTH", "MDOC-IMAGE-INLINE-WIDTH",
    "MDOC-BARE-URL", "MDOC-AUTOLINK-POLICY", "MDOC-PRODUCT-NAME", "MDOC-TERM-FORBIDDEN",
    "MDOC-TERM-CASE", "MDOC-TERM-INCONSISTENT", "MDOC-SPELLING", "MDOC-SPELLING-UNAVAILABLE",
    "MDOC-LOCALE-PUNCT", "MDOC-PUNCT-SPACING", "MDOC-FULLWIDTH-MIXED",
    "MDOC-QUOTE-STYLE", "MDOC-TABLE-STYLE", "MDOC-TABLE-VISUAL",
}
PROFILES = {"quick": QUICK, "full": FULL, "release": FULL}
UNSUPPRESSIBLE = {"MDOC-PATH-MISSING", "MDOC-PATH-CASE", "MDOC-PATH-ABSOLUTE", "MDOC-FENCE-UNCLOSED", "MDOC-HTML-IMG-SYNTAX"}
LINK_RE = re.compile(r"(?<!!)\[[^]\n]*\]\((?P<target><[^>]+>|[^)\n]+)\)")
MD_IMAGE_RE = re.compile(r"!\[[^]\n]*\]\((?P<target><[^>]+>|[^)\n]+)\)")
HTML_IMAGE_RE = re.compile(r"<img\b(?P<attrs>[^>]*)>", re.I)
ATTR_RE = re.compile(r"(?P<name>[A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?P<quote>[\"'])(?P<value>.*?)(?P=quote)")
ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/|file://)", re.I)
URL_RE = re.compile(r"https?://[^\s<>()]+", re.I)
HEADING_RE = re.compile(r"^(?P<marks>#{1,6})(?P<space>\s*)(?P<title>.*?)(?:\s+#+)?$")
SUPPRESS_NEXT_RE = re.compile(r"<!--\s*mdoc-lint-disable-next-line\s+(MDOC-[A-Z0-9-]+)\s+reason=[\"']([^\"']+)[\"']\s*-->")
SUPPRESS_START_RE = re.compile(r"<!--\s*mdoc-lint-disable\s+(MDOC-[A-Z0-9-]+)\s+reason=[\"']([^\"']+)[\"']\s*-->")
SUPPRESS_END_RE = re.compile(r"<!--\s*mdoc-lint-enable\s+(MDOC-[A-Z0-9-]+)\s*-->")
DEFAULTS = {"validation": {
    "mode": "advisory", "auto_run": False, "default_profile": "full",
    "publish_policy": {"required_before_publish": False, "block_on": ["error"]},
    "markdown": {"require_blank_line_after_html_block": False, "ordered_list_style": "standard", "paragraph_max_characters": 1200, "table_style": "either"},
    "images": {"require_width_for_block_images": False, "width_steps": [], "max_width_px": 8192, "max_height_px": 4096, "max_pixel_count": 16777216, "require_locale_width_consistency": True},
    "terminology": {"product_name": "", "forbidden_variants": []},
    "spelling": {"enabled": False, "required_locales": ["en"], "engine": "wordlist", "dictionary_file": ""},
    "punctuation": {"en": "ascii", "zh": "chinese", "ja": "japanese"}, "custom_patterns": [],
}}


@dataclass
class Finding:
    rule_id: str
    severity: str
    confidence: str
    file: str
    line: int
    message: str
    evidence: dict = field(default_factory=dict)
    suggested_fix: str | None = None
    fix_capability: str = "none"
    status: str = "new"
    suppressed: bool = False
    suppression_reason: str | None = None
    fingerprint: str = ""

    def finish(self) -> "Finding":
        # Keep baselines stable when prose is inserted above an unchanged problem.
        value = json.dumps([self.rule_id, self.file, self.evidence], ensure_ascii=False, sort_keys=True)
        self.fingerprint = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
        return self


def deep_merge(left: dict, right: dict) -> dict:
    result = dict(left)
    for key, value in right.items():
        result[key] = deep_merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def simple_yaml(path: Path) -> dict:
    source = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        return json.loads(source)
    try:
        import yaml  # type: ignore
        return yaml.safe_load(source) or {}
    except ImportError:
        def value_of(value: str):
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                return [value_of(item) for item in value[1:-1].split(",") if item.strip()]
            if value.lower() in {"true", "false"}:
                return value.lower() == "true"
            if value.lower() in {"null", "~"}:
                return None
            if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
                return float(value) if "." in value else int(value)
            return value.strip("'\"")

        rows = [(len(raw) - len(raw.lstrip()), raw.strip()) for raw in source.splitlines() if raw.strip() and not raw.lstrip().startswith("#")]

        def parse(index: int, indent: int):
            is_list = rows[index][1].startswith("- " )
            result = [] if is_list else {}
            while index < len(rows) and rows[index][0] == indent and rows[index][1].startswith("- " ) == is_list:
                content = rows[index][1][2:].strip() if is_list else rows[index][1]
                if is_list:
                    if ":" in content:
                        key, raw_value = content.split(":", 1)
                        item = {key.strip(): value_of(raw_value) if raw_value.strip() else {}}
                        index += 1
                        if index < len(rows) and rows[index][0] > indent:
                            child, index = parse(index, rows[index][0])
                            if raw_value.strip():
                                if isinstance(child, dict):
                                    item.update(child)
                            else:
                                item[key.strip()] = child
                        result.append(item)
                    else:
                        result.append(value_of(content))
                        index += 1
                else:
                    key, raw_value = content.split(":", 1)
                    index += 1
                    if raw_value.strip():
                        result[key.strip()] = value_of(raw_value)
                    elif index < len(rows) and rows[index][0] > indent:
                        result[key.strip()], index = parse(index, rows[index][0])
                    else:
                        result[key.strip()] = {}
            return result, index

        return parse(0, rows[0][0])[0] if rows else {}


def read_config(paths: Path | list[Path] | None) -> dict:
    config = json.loads(json.dumps(DEFAULTS))
    items = [paths] if isinstance(paths, Path) else (paths or [])
    for path in items:
        if path and path.exists():
            config = deep_merge(config, simple_yaml(path))
    return config


def load_terminology(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return [dict(row) for row in csv.DictReader(stream)]


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def locale_for(root: Path, path: Path) -> str:
    parts = Path(relative(root, path)).parts
    return parts[0] if parts and parts[0] in {"en", "zh", "ja"} else "unknown"


def decode_target(raw: str) -> str:
    return unquote(re.sub(r"\s+[\"'][^\"']*[\"']$", "", raw.strip().strip("<>")))


def actual_case(path: Path) -> Path | None:
    path = path.resolve()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        if not current.is_dir():
            return None
        exact = current / part
        if exact.exists():
            current = exact
            continue
        matches = [entry for entry in current.iterdir() if entry.name.casefold() == part.casefold()]
        if len(matches) != 1:
            return None
        current = matches[0]
    return current if current.exists() else None


def case_matches(path: Path) -> list[Path]:
    """Return filesystem entries matching the requested path case-insensitively."""
    path = path.resolve()
    if path.exists():
        try:
            return [entry for entry in path.parent.iterdir() if entry.name.casefold() == path.name.casefold()]
        except OSError:
            return [path]
    candidates = [Path(path.anchor)]
    for part in path.parts[1:]:
        next_candidates = []
        for parent in candidates:
            if parent.is_dir():
                try:
                    next_candidates.extend(entry for entry in parent.iterdir() if entry.name.casefold() == part.casefold())
                except OSError:
                    continue
        candidates = next_candidates
        if not candidates:
            break
    return candidates


def heading_anchors(path: Path) -> set[str]:
    anchors, counts = set(), {}
    try:
        source = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return anchors
    for line in source.splitlines():
        match = HEADING_RE.match(line)
        if not match:
            continue
        title = re.sub(r"<[^>]+>|[*_`~]", "", match.group("title")).strip().casefold()
        slug = re.sub(r"[^\w\- \u4e00-\u9fff\u3040-\u30ff]", "", title, flags=re.UNICODE)
        slug = re.sub(r"\s+", "-", slug).strip("-")
        index = counts.get(slug, 0)
        counts[slug] = index + 1
        anchors.add(slug if index == 0 else f"{slug}-{index}")
    return anchors


def image_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:32]
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    if data.startswith((b"GIF87a", b"GIF89a")):
        return struct.unpack("<HH", data[6:10])
    if data.startswith(b"BM"):
        return struct.unpack("<II", data[18:26])
    if data.startswith(b"\xff\xd8"):
        blob = path.read_bytes()
        index = 2
        while index + 9 < len(blob):
            if blob[index] != 0xFF:
                index += 1
                continue
            marker = blob[index + 1]
            size = int.from_bytes(blob[index + 2:index + 4], "big")
            if marker in range(0xC0, 0xC4):
                return int.from_bytes(blob[index + 7:index + 9], "big"), int.from_bytes(blob[index + 5:index + 7], "big")
            index += max(size + 2, 2)
    raise ValueError("unsupported or unreadable image header")


def visible_line(line: str) -> str:
    line = re.sub(r"\x60[^\x60]*\x60", "", line)
    line = re.sub(r"<!--.*?-->", "", line)
    return re.sub(r"<[^>]+>", "", line)


class Linter:
    def __init__(self, root: Path, config: dict, profile: str, baseline: Path | None = None, terminology: Path | None = None, include_files: set[str] | None = None):
        self.root, self.config, self.profile = root.resolve(), config, profile
        self.enabled, self.findings = PROFILES[profile], []
        data = json.loads(baseline.read_text(encoding="utf-8")) if baseline and baseline.exists() else {"fingerprints": []}
        self.baseline_set = set(data.get("fingerprints", []))
        self.include_files = include_files
        self.terminology = load_terminology(terminology)
        self.image_widths: dict[str, list[tuple[str, str, int, int]]] = {}
        self.spelling_reported: set[tuple[str, str]] = set()
        spelling = config["validation"].get("spelling", {})
        dictionary = spelling.get("dictionary_file")
        dictionary_path = (terminology.parent / dictionary).resolve() if terminology and dictionary else None
        self.dictionary = {word.casefold() for word in dictionary_path.read_text(encoding="utf-8-sig").splitlines() if word.strip() and not word.lstrip().startswith("#")} if dictionary_path and dictionary_path.exists() else None
        terms = config["validation"].get("terminology", {})
        self.term_variants = {str(item): terms.get("product_name", "") for item in terms.get("forbidden_variants", [])}

    def add(self, finding: Finding, suppressions: dict[int, dict[str, str]] | None = None):
        if finding.rule_id not in self.enabled and not finding.rule_id.startswith("PRODUCT-"):
            return
        if suppressions and finding.rule_id not in UNSUPPRESSIBLE:
            entry = suppressions.get(finding.line, {})
            if finding.rule_id in entry:
                finding.suppressed, finding.suppression_reason = True, entry[finding.rule_id]
        finding.finish()
        finding.status = "existing" if finding.fingerprint in self.baseline_set else "new"
        self.findings.append(finding)

    def scan(self):
        for path in sorted(self.root.rglob("*.md")):
            if self.include_files is not None and relative(self.root, path) not in self.include_files:
                continue
            if " " in path.name:
                self.add(Finding("MDOC-FILENAME-SPACE", "error", "exact", relative(self.root, path), 1, "Markdown filename contains spaces", {"name": path.name}))
            self.scan_file(path)
        self.finish_locale_image_widths()
        current = {finding.fingerprint for finding in self.findings}
        for fingerprint in sorted(self.baseline_set - current):
            self.findings.append(Finding("BASELINE-RESOLVED", "passed", "exact", "", 0, "Baseline finding resolved", {"fingerprint": fingerprint}, fingerprint=fingerprint, status="resolved"))

    def scan_file(self, path: Path):
        source = path.read_text(encoding="utf-8-sig")
        rel, lines = relative(self.root, path), source.splitlines()
        in_fence, fence_line, active = False, 0, {}
        next_suppression, suppressions = {}, {}
        headings, paragraph, table_rows = [], [], []
        validation = self.config["validation"]
        for number, line in enumerate(lines, 1):
            match = SUPPRESS_NEXT_RE.search(line)
            if match:
                next_suppression[number + 1] = {match.group(1): match.group(2)}
            match = SUPPRESS_START_RE.search(line)
            if match:
                active[match.group(1)] = match.group(2)
            match = SUPPRESS_END_RE.search(line)
            if match:
                active.pop(match.group(1), None)
            suppressions[number] = dict(active) | next_suppression.get(number, {})
            if re.match(r"^\s*(?:\x60{3}|~~~)", line):
                if not in_fence:
                    in_fence, fence_line = True, number
                else:
                    in_fence = False
                continue
            if in_fence:
                continue
            text = visible_line(line)
            self.check_heading(rel, number, line, headings, suppressions)
            self.check_paths(path, rel, number, line, suppressions)
            self.check_html(path, rel, number, lines, line, suppressions)
            self.check_language(rel, number, text, locale_for(self.root, path), suppressions)
            self.check_terms(rel, number, text, suppressions)
            self.check_emphasis(rel, number, text, suppressions)
            if re.search(r"(?:<TODO>|\{\{[^}]+\}\}|<[^>]*(?:placeholder|insert|replace)[^>]*>)", line, re.I):
                self.add(Finding("MDOC-PLACEHOLDER", "error", "exact", rel, number, "Unresolved placeholder", {"text": line.strip()}), suppressions)
            if re.match(r"^\s*\d+\.\s+", line) and validation.get("markdown", {}).get("ordered_list_style") == "compact":
                self.add(Finding("MDOC-LIST-COMPAT", "warning", "probable", rel, number, "Ordered-list spacing may be incompatible with the configured builder", {"text": line.strip()}), suppressions)
            bare = URL_RE.search(text)
            if bare and not re.search(r"[\x60<(]https?://", line, re.I):
                self.add(Finding("MDOC-BARE-URL", "suggestion", "review", rel, number, "Bare URL may need code formatting or an explicit link", {"url": bare.group(0)}), suppressions)
            for linked in re.finditer(r"\[[^]\n]+\]\((https?://[^)\s]+)\)", line, re.I):
                if validation.get("markdown", {}).get("external_url_style", "link") == "code":
                    self.add(Finding("MDOC-AUTOLINK-POLICY", "warning", "exact", rel, number, "External URL is linked but the configured policy requires code formatting", {"url": linked.group(1)}), suppressions)
            if line.strip().startswith("|") and line.strip().endswith("|"):
                table_rows.append((number, line.count("|") - 1))
            elif table_rows:
                self.finish_table(rel, table_rows, suppressions)
                table_rows = []
            if text.strip() and not HEADING_RE.match(line) and not line.lstrip().startswith(("- ", "* ", "+ ", "<", "|")):
                paragraph.append((number, text.strip()))
            else:
                self.finish_paragraph(rel, paragraph, suppressions)
                paragraph = []
        if in_fence:
            self.add(Finding("MDOC-FENCE-UNCLOSED", "error", "exact", rel, fence_line, "Unclosed fenced code block", {"start": fence_line}), suppressions)
        for rule, reason in active.items():
            self.add(Finding("MDOC-HTML-BLOCK-SYNTAX", "error", "exact", rel, len(lines), "Unclosed lint suppression block", {"rule": rule, "reason": reason}), suppressions)
        self.finish_table(rel, table_rows, suppressions)
        self.finish_paragraph(rel, paragraph, suppressions)
        self.finish_headings(rel, headings, suppressions)
        if source.count("<table") != source.count("</table>"):
            self.add(Finding("MDOC-TABLE-HTML", "error", "exact", rel, 1, "HTML table tags are unbalanced", {"open": source.count("<table"), "close": source.count("</table>")}), suppressions)
        table_style = validation.get("markdown", {}).get("table_style", "either")
        if table_style == "markdown" and re.search(r"<table\b", source, re.I):
            self.add(Finding("MDOC-TABLE-STYLE", "warning", "exact", rel, 1, "HTML table conflicts with configured Markdown table style", {"configured": table_style}), suppressions)
        if table_style == "html" and re.search(r"(?m)^\s*\|.*\|\s*$", source):
            self.add(Finding("MDOC-TABLE-STYLE", "warning", "exact", rel, 1, "Markdown table conflicts with configured HTML table style", {"configured": table_style}), suppressions)
        self.check_file_patterns(rel, source, suppressions)

    def check_file_patterns(self, rel, source, suppressions):
        for item in self.config["validation"].get("custom_patterns", []):
            rule_id = item.get("id", "PRODUCT-CUSTOM")
            pattern = item.get("pattern")
            kind = item.get("type")
            if not pattern or kind not in {"required_text", "filename_pattern", "path_pattern"}:
                continue
            target = source if kind == "required_text" else (Path(rel).name if kind == "filename_pattern" else rel)
            failed = not re.search(pattern, target, re.M) if kind == "required_text" else bool(re.search(pattern, target))
            if failed:
                self.add(Finding(rule_id, item.get("severity", "warning"), "exact", rel, 1, item.get("message", "Product validation pattern failed"), {"pattern": pattern, "type": kind}, item.get("replacement")), suppressions)

    def check_heading(self, rel, number, line, headings, suppressions):
        match = HEADING_RE.match(line)
        if not match:
            return
        if not match.group("space"):
            fixed = f"{match.group('marks')} {match.group('title').rstrip('#').strip()}"
            self.add(Finding("MDOC-HEADING-SYNTAX", "error", "exact", rel, number, "Heading marker must be followed by a space", {"text": line}, fixed, "safe"), suppressions)
        headings.append((number, len(match.group("marks")), re.sub(r"\s+", " ", match.group("title").strip()).casefold()))

    def finish_headings(self, rel, headings, suppressions):
        seen, previous = {}, 0
        for line, level, title in headings:
            if previous and level > previous + 1:
                self.add(Finding("MDOC-HEADING-HIERARCHY", "warning", "probable", rel, line, "Heading level skips a level", {"previous": previous, "current": level}), suppressions)
            previous = level
            if title in seen:
                self.add(Finding("MDOC-ANCHOR-DUPLICATE", "warning", "probable", rel, line, "Duplicate heading may create an ambiguous anchor", {"title": title, "first_line": seen[title]}), suppressions)
            seen.setdefault(title, line)

    def check_paths(self, path, rel, number, line, suppressions):
        nodes = [(match, False) for match in LINK_RE.finditer(line)] + [(match, True) for match in MD_IMAGE_RE.finditer(line)]
        for match, image in nodes:
            raw_target = match.group("target").strip()
            target = decode_target(raw_target)
            parsed = urlsplit(target)
            if parsed.scheme in {"http", "https", "mailto"}:
                policy = self.config["validation"].get("markdown", {}).get("autolink_policy", "allow")
                if policy == "code" and not image:
                    self.add(Finding("MDOC-AUTOLINK-POLICY", "suggestion", "exact", rel, number, "Configured policy requires literal URLs to use code formatting", {"target": target}), suppressions)
                continue
            if " " in target and not (raw_target.startswith("<") and raw_target.endswith(">")):
                self.add(Finding("MDOC-LINK-AMBIGUOUS", "warning", "probable", rel, number, "Link target contains an unescaped space", {"target": target}), suppressions)
            clean, _, fragment = target.partition("#")
            if ABSOLUTE_RE.match(clean):
                self.add(Finding("MDOC-PATH-ABSOLUTE", "error", "exact", rel, number, "Local absolute path is not portable", {"target": clean}), suppressions)
                continue
            requested = path if not clean else path.parent / clean
            matches = case_matches(requested)
            if len(matches) > 1:
                self.add(Finding("MDOC-LINK-AMBIGUOUS", "error", "exact", rel, number, "Local target is ambiguous under case-insensitive resolution", {"target": clean, "matches": [item.name for item in matches]}), suppressions)
                continue
            actual = matches[0] if matches else None
            rule = "MDOC-PATH-MISSING" if image else "MDOC-LINK-LEVEL"
            if actual is None:
                self.add(Finding(rule, "error", "exact", rel, number, "Local target does not exist", {"target": clean}), suppressions)
                continue
            actual_rel = Path(os.path.relpath(actual, path.parent)).as_posix()
            written = Path(clean).as_posix()
            if written.casefold() == actual_rel.casefold() and written != actual_rel:
                self.add(Finding("MDOC-PATH-CASE", "error", "exact", rel, number, "Path case differs from the filesystem", {"written": clean, "actual": actual_rel}, actual_rel, "safe"), suppressions)
            if fragment and actual.suffix.lower() == ".md" and unquote(fragment).casefold() not in heading_anchors(actual):
                self.add(Finding("MDOC-ANCHOR-MISSING", "error", "exact", rel, number, "Markdown heading anchor does not exist", {"target": clean, "anchor": unquote(fragment)}), suppressions)
            if image:
                self.check_image(rel, number, clean, actual, {}, False, suppressions)

    def check_html(self, path, rel, number, lines, line, suppressions):
        if re.search(r"<img\b[^>]*[\"']\s+[\"']\s*(?:width|height|style)=", line, re.I):
            fixed = re.sub(r"([\"'])\s+[\"']\s+(?=(?:width|height|style)=)", r"\1 ", line, count=1, flags=re.I)
            self.add(Finding("MDOC-HTML-IMG-SYNTAX", "error", "exact", rel, number, "Image tag contains an isolated quote attribute", {"text": line.strip()}, fixed, "safe"), suppressions)
        for match in HTML_IMAGE_RE.finditer(line):
            attrs = {item.group("name").lower(): item.group("value") for item in ATTR_RE.finditer(match.group("attrs"))}
            src = attrs.get("src")
            if not src:
                self.add(Finding("MDOC-IMAGE-SYNTAX", "error", "exact", rel, number, "Image tag has no quoted src attribute", {"tag": match.group(0)}), suppressions)
                continue
            if ABSOLUTE_RE.match(src):
                self.add(Finding("MDOC-PATH-ABSOLUTE", "error", "exact", rel, number, "Image uses an absolute path", {"target": src}), suppressions)
                continue
            if re.match(r"^[a-z]+://", src, re.I):
                continue
            actual = actual_case(path.parent / unquote(src))
            if actual is None:
                self.add(Finding("MDOC-PATH-MISSING", "error", "exact", rel, number, "Image target does not exist", {"target": src}), suppressions)
                continue
            actual_rel = Path(os.path.relpath(actual, path.parent)).as_posix()
            written = Path(src).as_posix()
            if written.casefold() == actual_rel.casefold() and written != actual_rel:
                self.add(Finding("MDOC-PATH-CASE", "error", "exact", rel, number, "Image path case differs from the filesystem", {"written": src, "actual": actual_rel}, actual_rel, "safe"), suppressions)
            self.check_image(rel, number, src, actual, attrs, "vertical-align" in attrs.get("style", ""), suppressions)
        if re.match(r"^\s*<div\b[^>]*>\s*$", line, re.I) and self.config["validation"].get("markdown", {}).get("require_blank_line_after_html_block"):
            if number < len(lines) and lines[number].strip():
                self.add(Finding("MDOC-HTML-BLANK-LINE", "warning", "probable", rel, number, "Configured builder requires a blank line after div", {"next": lines[number].strip()}, "insert blank line", "safe"), suppressions)
        if re.search(r"</?(?:div|table|tr|td|th)\b[^>]*$", line, re.I):
            self.add(Finding("MDOC-HTML-BLOCK-SYNTAX", "error", "exact", rel, number, "HTML block tag is not closed on this line", {"text": line.strip()}), suppressions)

    def check_image(self, rel, number, src, actual, attrs, inline, suppressions):
        try:
            width, height = image_size(actual)
        except (OSError, ValueError) as exc:
            self.add(Finding("MDOC-IMAGE-READABLE", "error", "exact", rel, number, "Image header is unreadable", {"target": src, "error": str(exc)}), suppressions)
            return
        limits = self.config["validation"].get("images", {})
        if width > limits.get("max_width_px", 8192) or height > limits.get("max_height_px", 4096) or width * height > limits.get("max_pixel_count", 16777216):
            self.add(Finding("MDOC-IMAGE-DIMENSION", "warning", "exact", rel, number, "Image dimensions exceed configured limits", {"target": src, "width": width, "height": height}), suppressions)
        configured = attrs.get("width")
        if configured:
            try:
                display_width = int(re.sub(r"px$", "", configured))
                parts = Path(rel).parts
                locale = parts[0] if parts and parts[0] in {"en", "zh", "ja"} else "unknown"
                actual_parts = Path(relative(self.root, actual)).parts
                image_key = Path(*actual_parts[1:]).as_posix().casefold() if actual_parts and actual_parts[0] in {"en", "zh", "ja"} else Path(src).name.casefold()
                self.image_widths.setdefault(image_key, []).append((rel, locale, display_width, number))
            except ValueError:
                pass
        if not inline and limits.get("require_width_for_block_images") and not configured:
            self.add(Finding("MDOC-IMAGE-WIDTH", "warning", "probable", rel, number, "Block image has no display width", {"target": src, "width": width}), suppressions)
        steps = [int(item) for item in limits.get("width_steps", [])]
        if configured and steps:
            try:
                display = int(re.sub(r"px$", "", configured))
                if display not in steps:
                    self.add(Finding("MDOC-IMAGE-WIDTH-STEP", "warning", "exact", rel, number, "Image width is not a configured step", {"target": src, "width": display, "steps": steps}), suppressions)
            except ValueError:
                self.add(Finding("MDOC-IMAGE-WIDTH-STEP", "warning", "exact", rel, number, "Image width is not an integer pixel value", {"target": src, "width": configured}), suppressions)
        if inline and configured:
            try:
                if int(re.sub(r"px$", "", configured)) > 64:
                    self.add(Finding("MDOC-IMAGE-INLINE-WIDTH", "warning", "probable", rel, number, "Inline icon is unusually wide", {"target": src, "width": configured}), suppressions)
            except ValueError:
                pass

    def check_terms(self, rel, number, text, suppressions):
        for variant, canonical in self.term_variants.items():
            if variant and variant in text:
                self.add(Finding("MDOC-PRODUCT-NAME", "warning", "exact", rel, number, "Forbidden product-name variant", {"written": variant, "canonical": canonical}, canonical, "safe"), suppressions)
        for item in self.config["validation"].get("custom_patterns", []):
            if item.get("type") in {"forbidden_text", "terminology_variant", "punctuation"} and item.get("pattern") and re.search(item["pattern"], text):
                self.add(Finding(item.get("id", "PRODUCT-CUSTOM"), item.get("severity", "warning"), "probable", rel, number, item.get("message", "Product validation pattern matched"), {"pattern": item["pattern"]}, item.get("replacement")), suppressions)
        locale = Path(rel).parts[0] if Path(rel).parts and Path(rel).parts[0] in {"en", "zh", "ja"} else "source"
        for term in self.terminology:
            expected = (term.get(locale) or term.get("source") or "").strip()
            if expected:
                match = re.search(re.escape(expected), text, re.I)
                if match and match.group(0) != expected:
                    self.add(Finding("MDOC-TERM-CASE", "warning", "exact", rel, number, "Configured term uses different letter case", {"written": match.group(0), "expected": expected}, expected, "safe"), suppressions)
            for forbidden in (term.get("forbidden_variants") or "").split("|"):
                forbidden = forbidden.strip()
                if forbidden and forbidden in text:
                    self.add(Finding("MDOC-TERM-FORBIDDEN", "warning", "exact", rel, number, "Forbidden terminology variant", {"written": forbidden, "expected": expected}, expected or None), suppressions)
            other_values = {str(term.get(key) or "").strip() for key in ("source", "en", "ja") if key != locale}
            for other in other_values - {"", expected}:
                if other in text:
                    self.add(Finding("MDOC-TERM-INCONSISTENT", "suggestion", "review", rel, number, "Term from another locale appears in prose", {"written": other, "expected": expected}), suppressions)

    def check_emphasis(self, rel, number, text, suppressions):
        marker_counts = {"**": text.count("**"), "__": len(re.findall(r"(?<!\w)__(?=\S)|(?<=\S)__(?!\w)", text))}
        for marker, count in marker_counts.items():
            if count % 2:
                self.add(Finding("MDOC-EMPHASIS-SYNTAX", "error", "exact", rel, number, "Unbalanced strong-emphasis marker", {"marker": marker, "text": text.strip()}), suppressions)

    def check_language(self, rel, number, text, locale, suppressions):
        if locale == "en":
            match = re.search(r"[。，“”‘’：；、（）【】]", text)
            if match:
                self.add(Finding("MDOC-LOCALE-PUNCT", "suggestion", "review", rel, number, "English prose contains CJK punctuation", {"character": match.group(0)}), suppressions)
            if re.search(r"[，。！？][A-Za-z]", text):
                self.add(Finding("MDOC-FULLWIDTH-MIXED", "warning", "probable", rel, number, "Full-width punctuation is mixed with Latin prose", {"text": text.strip()}), suppressions)
            if re.search(r"[,;:][A-Za-z]", text):
                self.add(Finding("MDOC-PUNCT-SPACING", "suggestion", "review", rel, number, "English punctuation may need a following space", {"text": text.strip()}), suppressions)
        if locale == "ja" and "、" in text and self.config["validation"].get("punctuation", {}).get("ja") == "ascii":
            self.add(Finding("MDOC-LOCALE-PUNCT", "suggestion", "review", rel, number, "Japanese prose contains a forbidden punctuation form", {"character": "、"}), suppressions)
        quote_style = self.config["validation"].get("punctuation", {}).get("quote_style")
        if quote_style == "ascii" and re.search(r"[“”‘’]", text):
            self.add(Finding("MDOC-QUOTE-STYLE", "suggestion", "review", rel, number, "Curly quotes conflict with configured ASCII quote style", {"text": text.strip()}), suppressions)
        spelling = self.config["validation"].get("spelling", {})
        if spelling.get("enabled") and locale in spelling.get("required_locales", ["en"]):
            if self.dictionary is None:
                key = (rel, locale)
                if key not in self.spelling_reported:
                    self.spelling_reported.add(key)
                    self.add(Finding("MDOC-SPELLING-UNAVAILABLE", "warning", "exact", rel, number, "Spelling is enabled but the configured offline dictionary is unavailable", {"engine": spelling.get("engine", "wordlist"), "dictionary_file": spelling.get("dictionary_file", "")}), suppressions)
            else:
                unknown = sorted({word for word in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text) if word.casefold() not in self.dictionary})
                if unknown:
                    self.add(Finding("MDOC-SPELLING", "suggestion", "review", rel, number, "Words are absent from the configured offline dictionary", {"words": unknown[:20]}), suppressions)

    def finish_locale_image_widths(self):
        if not self.config["validation"].get("images", {}).get("require_locale_width_consistency", True):
            return
        for image, entries in self.image_widths.items():
            widths = {width for _, _, width, _ in entries}
            locales = {locale for _, locale, _, _ in entries}
            if len(widths) > 1 and len(locales) > 1:
                for rel, locale, width, line in entries:
                    self.add(Finding("MDOC-IMAGE-LOCALE-WIDTH", "warning", "exact", rel, line, "Same-name locale images use inconsistent display widths", {"image": image, "locale": locale, "width": width, "all_widths": sorted(widths)}))

    def finish_paragraph(self, rel, paragraph, suppressions):
        if paragraph:
            joined = " ".join(text for _, text in paragraph)
            maximum = self.config["validation"].get("markdown", {}).get("paragraph_max_characters", 1200)
            if len(joined) > maximum:
                self.add(Finding("MDOC-PARAGRAPH-LONG", "suggestion", "review", rel, paragraph[0][0], "Paragraph may need manual line or step breaks", {"characters": len(joined), "lines": len(paragraph)}), suppressions)

    def finish_table(self, rel, rows, suppressions):
        if len(rows) < 2:
            return
        counts = {count for _, count in rows}
        if len(counts) > 1:
            self.add(Finding("MDOC-TABLE-SYNTAX", "error", "exact", rel, rows[0][0], "Markdown table rows have different column counts", {"counts": sorted(counts)}), suppressions)
        if max(counts) > 8:
            self.add(Finding("MDOC-TABLE-VISUAL", "warning", "review", rel, rows[0][0], "Wide table may not fit PDF pages", {"columns": max(counts)}), suppressions)


def write_report(linter: Linter, output: Path, profile: str, builds: list[dict] | None = None, policy: dict | None = None) -> dict:
    active = [finding for finding in linter.findings if not finding.suppressed and finding.status != "resolved"]
    counts = {key: sum(finding.severity == key for finding in active) for key in ("error", "warning", "suggestion", "passed")}
    policy = policy or {}
    blocking = bool(policy.get("mode") == "required" and policy.get("required_before_publish"))
    block_on = policy.get("block_on", ["error"])
    required_components = set(policy.get("required_components", []))
    blocked_by_findings = blocking and (not required_components or "static" in required_components) and any(counts.get(level, 0) for level in block_on)
    blocked_by_builds = False
    if blocking:
        for component, build_type in {"html_build": "html", "pdf_build": "pdf"}.items():
            if component in required_components and not any(item.get("component") == build_type and item.get("status") == "passed" for item in (builds or [])):
                blocked_by_builds = True
        blocked_by_builds = blocked_by_builds or any(item.get("required") and item.get("status") != "passed" for item in (builds or []))
    pdf_report_path = output.parent / "pdf-check.json"
    pdf_check = json.loads(pdf_report_path.read_text(encoding="utf-8")) if pdf_report_path.exists() else {"status": "not_requested", "counts": {"effective_errors": 0}}
    blocked_by_pdf = pdf_check_blocks(policy, pdf_check)
    data = {"schema_version": 2, "book_root": ".", "profile": profile, "generated_at": int(time.time()), "counts": counts, "policy": policy, "publish_blocked": blocked_by_findings or blocked_by_builds or blocked_by_pdf, "builds": builds or [], "pdf_check": pdf_check, "findings": [asdict(finding) for finding in linter.findings]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = output.with_name("validation-summary.md")
    pdf_counts = pdf_check.get("counts", {})
    lines = ["# Manual Quality Gate", "", f"Profile: {profile}", f"Mode: {policy.get('mode', 'advisory')}", f"Publish blocked: {'yes' if data['publish_blocked'] else 'no'}", "", f"Errors: {counts['error']}; warnings: {counts['warning']}; suggestions: {counts['suggestion']}.", f"PDF Check: {pdf_check.get('status', 'not_requested')}; effective errors: {pdf_counts.get('effective_errors', 0)}; ignored errors: {pdf_counts.get('ignored_errors', 0)}; warnings: {pdf_counts.get('warnings', 0)}; suggestions: {pdf_counts.get('suggestions', 0)}.", ""]
    lines.extend(f"- {finding.severity}/{finding.confidence} {finding.rule_id} {finding.file}:{finding.line} — {finding.message}" for finding in active[:200])
    if len(active) > 200:
        lines.append(f"- … {len(active) - 200} more finding(s); see JSON report.")
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return data


def pdf_check_blocks(policy: dict, report: dict) -> bool:
    required = policy.get("mode") == "required" and policy.get("required_before_publish") and "pdf_check" in set(policy.get("required_components", []))
    return bool(required and (report.get("status") != "completed" or report.get("counts", {}).get("effective_errors", 0) > 0))


def run_builds(adapters: list[dict], root: Path, output: Path) -> list[dict]:
    results, logs = [], output.parent / "build"
    logs.mkdir(parents=True, exist_ok=True)
    for adapter in adapters:
        identifier, started = adapter["id"], time.time()
        try:
            completed = subprocess.run(adapter["command"], cwd=adapter.get("working_directory") or root, capture_output=True, text=True, timeout=int(adapter.get("timeout_seconds", 600)), check=False)
            log, status, code = completed.stdout + completed.stderr, ("passed" if completed.returncode == 0 else "failed"), completed.returncode
        except subprocess.TimeoutExpired as exc:
            log, status, code = (exc.stdout or "") + (exc.stderr or ""), "timeout", None
        (logs / f"{identifier}.log").write_text(log, encoding="utf-8")
        results.append({"id": identifier, "component": adapter.get("component", "other"), "status": status, "returncode": code, "duration_seconds": round(time.time() - started, 3), "required": bool(adapter.get("required"))})
    return results


def safe_fix(root: Path, report_path: Path, apply: bool) -> int:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    edits: dict[Path, list[dict]] = {}
    for finding in data.get("findings", []):
        if finding.get("fix_capability") == "safe" and not finding.get("suppressed") and finding.get("file"):
            edits.setdefault(root / finding["file"], []).append(finding)
    changed = 0
    for path, findings in edits.items():
        raw = path.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        newline = "\r\n" if b"\r\n" in raw else "\n"
        lines = text.splitlines()
        for finding in sorted(findings, key=lambda item: item["line"], reverse=True):
            index = finding["line"] - 1
            if not 0 <= index < len(lines):
                continue
            rule = finding["rule_id"]
            if rule == "MDOC-PATH-CASE":
                lines[index] = lines[index].replace(finding["evidence"]["written"], finding["evidence"]["actual"])
            elif rule in {"MDOC-HTML-IMG-SYNTAX", "MDOC-HEADING-SYNTAX"}:
                lines[index] = finding["suggested_fix"]
            elif rule == "MDOC-PRODUCT-NAME":
                lines[index] = lines[index].replace(finding["evidence"]["written"], finding["suggested_fix"])
            elif rule == "MDOC-TERM-CASE":
                lines[index] = lines[index].replace(finding["evidence"]["written"], finding["suggested_fix"])
            elif rule == "MDOC-HTML-BLANK-LINE":
                lines.insert(index + 1, "")
        result = newline.join(lines) + (newline if text.endswith(("\n", "\r")) else "")
        encoded = (b"\xef\xbb\xbf" if bom else b"") + result.encode("utf-8")
        if encoded != raw:
            changed += 1
            print(f"{'APPLY' if apply else 'PLAN'}: {path.relative_to(root)}")
            if apply:
                path.write_bytes(encoded)
    print(f"{'UPDATED' if apply else 'PLANNED'}: {changed} file(s)")
    return 0


def task_scope(root: Path, task_dir: Path, product: dict) -> set[str] | None:
    structure_path = task_dir / "structure.yaml"
    task_path = task_dir / "task.yaml"
    if not structure_path.exists() or not task_path.exists():
        return None
    structure, task = simple_yaml(structure_path), simple_yaml(task_path)
    pages = [item.get("file") for item in structure.get("pages", []) if isinstance(item, dict) and item.get("file")]
    document_path = task.get("target", {}).get("document_path", "")
    content = product.get("manual_layout", {}).get("content_directory", "Main")
    locales = [path.name for path in root.iterdir() if path.is_dir()] if root.exists() else []
    return {(Path(locale) / content / document_path / page).as_posix() for locale in locales for page in pages}


def effective_policy(config: dict, task_config: dict) -> dict:
    validation = config.get("validation", {})
    task_validation = task_config.get("validation", {})
    ranks = {"disabled": 0, "advisory": 1, "required": 2}
    base_mode = validation.get("mode", "advisory")
    requested = task_validation.get("mode", "inherit")
    mode = base_mode if requested == "inherit" else max((base_mode, requested), key=lambda item: ranks[item])
    publish = validation.get("publish_policy", {})
    required_components = list(dict.fromkeys(list(publish.get("required_components", [])) + list(task_validation.get("required_components", []))))
    profile_ranks = {"quick": 0, "full": 1, "release": 2}
    required_profile = publish.get("required_profile", "release")
    requested_profile = task_validation.get("profile", required_profile)
    required_profile = max((required_profile, requested_profile), key=lambda item: profile_ranks[item])
    return {"mode": mode, "required_before_publish": bool(publish.get("required_before_publish", False)), "required_profile": required_profile, "block_on": publish.get("block_on", ["error"]), "required_components": required_components}


def resolve_context(args) -> dict:
    if args.book_root:
        config = read_config(args.product_profile)
        return {"root": args.book_root.resolve(), "config": config, "task": {}, "terminology": None, "include_files": None, "output_root": (args.output or args.book_root / ".quality-gate").resolve(), "adapters": []}
    workspace = args.workspace.resolve()
    workspace_config = simple_yaml(workspace / "workspace.yaml")
    local_config = simple_yaml(workspace / "workspace.local.yaml") if (workspace / "workspace.local.yaml").exists() else {}
    repository_value = local_config.get("local", {}).get("manual_repository") or workspace_config["repository"]["root"]
    repository_root = Path(repository_value)
    if not repository_root.is_absolute():
        repository_root = workspace / repository_root
    task_dir = workspace / workspace_config.get("tasks", {}).get("root", "manual-tasks") / args.task
    task_config = simple_yaml(task_dir / "task.yaml")
    target_book = task_config.get("task", {}).get("target_book") or workspace_config["manual"].get("active_book") or workspace_config["manual"]["active_version"]
    formal_root = repository_root / target_book
    profile = workspace / workspace_config["product"]["profile"]
    config = read_config([profile, workspace / "workspace.yaml", workspace / "workspace.local.yaml", task_dir / "task.yaml", task_dir / "task.local.yaml"])
    root = task_dir / "staging" if args.phase == "staging" else formal_root
    include_files = None if args.phase == "staging" else task_scope(formal_root, task_dir, simple_yaml(profile))
    report_root = args.output or task_dir / "reports" / "quality-gate"
    adapters = config.get("validation", {}).get("build_adapters", [])
    return {"root": root.resolve(), "config": config, "task": task_config, "terminology": task_dir / "terminology.csv", "include_files": include_files, "output_root": report_root.resolve(), "adapters": adapters}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "baseline", "fix"):
        item = sub.add_parser(name)
        group = item.add_mutually_exclusive_group(required=True)
        group.add_argument("--book-root", type=Path)
        group.add_argument("--workspace", type=Path)
        item.add_argument("--task")
        item.add_argument("--phase", choices=("staging", "formal"), default="formal")
        item.add_argument("--product-profile", type=Path)
        item.add_argument("--profile", choices=tuple(PROFILES))
        item.add_argument("--baseline", type=Path)
        item.add_argument("--output", type=Path)
        item.add_argument("--apply", action="store_true")
        item.add_argument("--safe", action="store_true")
    args = parser.parse_args()
    if args.workspace and not args.task:
        parser.error("--task is required with --workspace")
    context = resolve_context(args)
    root, output_root, adapters = context["root"], context["output_root"], context["adapters"]
    profile = args.profile or context["task"].get("validation", {}).get("profile") or context["config"]["validation"].get("default_profile", "full")
    output = output_root / f"{args.phase}-report.json"
    if args.command == "fix":
        if not args.safe:
            parser.error("fix requires --safe")
        if not output.exists():
            parser.error(f"run check first; report missing: {output}")
        return safe_fix(root, output, args.apply)
    if not root.is_dir():
        parser.error(f"manual root is missing: {root}")
    config = context["config"]
    policy = effective_policy(config, context["task"])
    linter = Linter(root, config, profile, args.baseline, context["terminology"], context["include_files"])
    linter.scan()
    builds = run_builds(adapters, root, output) if profile == "release" and adapters else ([{"status": "not_configured", "required": False}] if profile == "release" else [])
    data = write_report(linter, output, profile, builds, policy)
    if args.command == "baseline":
        baseline = args.baseline or output_root / "validation-baseline.json"
        fingerprints = sorted(finding["fingerprint"] for finding in data["findings"] if finding["status"] != "resolved")
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(json.dumps({"schema_version": 2, "fingerprints": fingerprints}, indent=2) + "\n", encoding="utf-8")
        print(f"BASELINE: {baseline}")
    counts = data["counts"]
    print(f"COMPLETE: {counts['error']} error(s), {counts['warning']} warning(s), {counts['suggestion']} suggestion(s)")
    print(f"REPORT: {output}")
    return 1 if data["publish_blocked"] else 0


if __name__ == "__main__":
    sys.exit(main())
