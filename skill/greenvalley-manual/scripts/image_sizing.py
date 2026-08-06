#!/usr/bin/env python3
"""Apply and validate deterministic screenshot display widths without inspecting image content."""

from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

DEFAULT_STEPS = (50, 100, 200, 300, 400, 500, 600, 700, 800)
IMG_RE = re.compile(r'<img\s+(?P<attrs>[^>]*?)(?P<close>/?)>', re.IGNORECASE)
ATTR_RE = re.compile(r'(?P<name>[A-Za-z_:][-A-Za-z0-9_:.]*)="(?P<value>[^"]*)"')


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"not a readable PNG header: {path}")
    return struct.unpack(">II", header[16:24])


def display_width(original_width: int, scale: float, steps: tuple[int, ...]) -> int:
    if original_width <= 0 or scale <= 0 or not steps:
        raise ValueError("original width, scale, and steps must be positive")
    ordered = tuple(sorted(set(steps)))
    reference = min(max(original_width * scale, ordered[0]), ordered[-1])
    return min(ordered, key=lambda step: (abs(step - reference), step))


def attributes(source: str) -> dict[str, str]:
    return {match.group("name").lower(): match.group("value") for match in ATTR_RE.finditer(source)}


def image_path(markdown: Path, src: str) -> Path:
    return (markdown.parent / Path(src)).resolve()


def replace_tag(markdown: Path, match: re.Match[str], chosen_widths: dict[str, int]) -> str:
    attrs = attributes(match.group("attrs"))
    src = attrs.get("src")
    if not src or re.match(r"^[a-z]+://", src, re.I):
        return match.group(0)
    target = image_path(markdown, src)
    png_size(target)
    chosen = chosen_widths[Path(src).name]
    raw = match.group("attrs")
    if re.search(r'\bwidth="[^"]*"', raw, re.I):
        raw = re.sub(r'\bwidth="[^"]*"', f'width="{chosen}"', raw, count=1, flags=re.I)
    else:
        raw = raw.rstrip() + f' width="{chosen}"'
    responsive = "max-width: 100%; height: auto;"
    if re.search(r'\bstyle="[^"]*"', raw, re.I):
        raw = re.sub(r'\bstyle="[^"]*"', f'style="{responsive}"', raw, count=1, flags=re.I)
    else:
        raw = raw.rstrip() + f' style="{responsive}"'
    return f"<img {raw.strip()}{match.group('close')}>"


def apply(root: Path, scale: float, steps: tuple[int, ...]) -> tuple[int, int]:
    chosen_widths = grouped_widths(root, scale, steps)
    files = tags = 0
    for markdown in sorted(root.rglob("*.md")):
        source = markdown.read_text(encoding="utf-8")
        changed = 0
        def rewrite(match: re.Match[str]) -> str:
            nonlocal changed
            result = replace_tag(markdown, match, chosen_widths)
            changed += result != match.group(0)
            return result
        result = IMG_RE.sub(rewrite, source)
        if result != source:
            markdown.write_text(result, encoding="utf-8")
            files += 1
            tags += changed
    return files, tags


def check(root: Path, scale: float, steps: tuple[int, ...]) -> list[str]:
    errors: list[str] = []
    expected_widths = grouped_widths(root, scale, steps)
    widths: dict[str, set[int]] = {}
    for markdown in sorted(root.rglob("*.md")):
        source = markdown.read_text(encoding="utf-8")
        for match in IMG_RE.finditer(source):
            attrs = attributes(match.group("attrs"))
            src = attrs.get("src")
            if not src or re.match(r"^[a-z]+://", src, re.I):
                continue
            target = image_path(markdown, src)
            try:
                original, _ = png_size(target)
            except (OSError, ValueError) as exc:
                errors.append(f"{markdown}: {exc}")
                continue
            expected = expected_widths[Path(src).name]
            try:
                actual = int(attrs.get("width", ""))
            except ValueError:
                actual = -1
            if actual != expected:
                errors.append(f"{markdown}: {Path(src).name} width {actual} must be {expected}")
            style = re.sub(r"\s+", " ", attrs.get("style", "").strip().lower())
            if style != "max-width: 100%; height: auto;":
                errors.append(f"{markdown}: {Path(src).name} must use responsive image style")
            widths.setdefault(Path(src).name, set()).add(actual)
    for filename, values in sorted(widths.items()):
        if len(values) > 1:
            errors.append(f"{filename}: inconsistent display widths across locales/pages: {sorted(values)}")
    return errors


def grouped_widths(root: Path, scale: float, steps: tuple[int, ...]) -> dict[str, int]:
    """Use the smallest calculated step for same-named locale variants."""
    candidates: dict[str, set[int]] = {}
    for markdown in sorted(root.rglob("*.md")):
        source = markdown.read_text(encoding="utf-8")
        for match in IMG_RE.finditer(source):
            attrs = attributes(match.group("attrs"))
            src = attrs.get("src")
            if not src or re.match(r"^[a-z]+://", src, re.I):
                continue
            original, _ = png_size(image_path(markdown, src))
            candidates.setdefault(Path(src).name, set()).add(display_width(original, scale, steps))
    return {filename: min(values) for filename, values in candidates.items()}


def parse_steps(value: str) -> tuple[int, ...]:
    steps = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not steps or any(step <= 0 for step in steps):
        raise argparse.ArgumentTypeError("steps must be comma-separated positive integers")
    return steps


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("apply", "check"))
    parser.add_argument("markdown_root", type=Path)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--steps", type=parse_steps, default=DEFAULT_STEPS)
    args = parser.parse_args()
    root = args.markdown_root.resolve()
    if args.command == "apply":
        files, tags = apply(root, args.scale, args.steps)
        print(f"UPDATED: {tags} image tag(s) in {files} Markdown file(s)")
        return 0
    errors = check(root, args.scale, args.steps)
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"FAILED: {len(errors)} image sizing error(s)")
        return 1
    print("PASSED: image sizing follows configured scale, strict steps, and boundaries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
