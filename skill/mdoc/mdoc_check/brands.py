from __future__ import annotations

import re
from pathlib import Path

from ruamel.yaml import YAML

from .model import finding


DEFAULT_CONFIG = """# 工作区通用产品名和品牌名规则。
# 当前语言的补充规则使用 check-brands.<language>.yaml。
schema_version: 1
brands:
  - canonical: LiDAR360MLS
    variants:
      - LiDAR360 MLS
      - Lidar360MLS
      - lidar360mls

  - canonical: NVIDIA
    variants:
      - Nvidia
      - nVidia
"""
LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*\Z")
LATIN_ADJACENT = re.compile(r"[A-Za-z0-9_]")
FENCED = re.compile(r"(?ms)^[ \t]{0,3}(`{3,}|~{3,})[^\n]*\n.*?^[ \t]{0,3}\1[ \t]*$")
INDENTED = re.compile(r"(?m)^(?: {4}|\t).*$")
INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)*\1")
AUTOLINK = re.compile(r"<(?:(?:https?|mailto):[^>]+|[^<> ]+@[^<> ]+)>", re.IGNORECASE)
LINK_TARGET = re.compile(r"(?P<prefix>!?\[[^]\n]*\]\s*\()(?P<target>[^)\n]*)(?P<close>\))")
REFERENCE_TARGET = re.compile(r"(?m)^[ \t]{0,3}\[[^]\n]+\]:[ \t]*(?P<target>\S+).*$")
HIDDEN_HTML = re.compile(r"(?is)<(code|pre|script|style)\b[^>]*>.*?</\1\s*>")
HTML_TAG = re.compile(r"(?s)<!--.*?-->|<[^>]+>")
IMAGE_TAG = re.compile(r"(?is)<img\b[^>]*>")
ALT_ATTRIBUTE = re.compile(r"(?i)\balt\s*=\s*([\"'])(.*?)\1")


def _validate_layer(value: object, path: Path) -> list[dict]:
    if not isinstance(value, dict) or set(value) - {"schema_version", "brands"}:
        raise ValueError(f"品牌配置根节点无效或包含未知字段：{path}")
    if value.get("schema_version") != 1 or isinstance(value.get("schema_version"), bool):
        raise ValueError(f"品牌配置 schema_version 必须为整数 1：{path}")
    brands = value.get("brands")
    if not isinstance(brands, list) or len(brands) > 500:
        raise ValueError(f"品牌配置 brands 必须是最多包含 500 项的列表：{path}")
    seen = set(); results = []
    for index, item in enumerate(brands, 1):
        if not isinstance(item, dict) or set(item) != {"canonical", "variants"}:
            raise ValueError(f"品牌配置第 {index} 项必须且只能包含 canonical 和 variants：{path}")
        canonical = _text(item["canonical"], f"第 {index} 项 canonical", path)
        if canonical in seen:
            raise ValueError(f"同一配置文件重复声明标准名称 {canonical}：{path}")
        seen.add(canonical)
        variants = item["variants"]
        if not isinstance(variants, list) or len(variants) > 100:
            raise ValueError(f"{canonical} 的 variants 必须是最多包含 100 项的列表：{path}")
        results.append({"canonical": canonical, "variants": list(dict.fromkeys(_text(item, f"{canonical} 的 variant", path) for item in variants)), "path": path})
    return results


def _text(value: object, label: str, path: Path) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"品牌配置 {label} 必须是 1-200 字符、无首尾空白和控制字符的纯文本：{path}")
    return value


def load(root: Path, language: str, progress=None) -> tuple[dict, list[Path]]:
    common = root / ".mdoc" / "check-brands.yaml"
    language_path = root / ".mdoc" / f"check-brands.{language}.yaml"
    if not common.exists():
        common.parent.mkdir(parents=True, exist_ok=True)
        common.write_text(DEFAULT_CONFIG, encoding="utf-8")
        if progress:
            progress("已初始化品牌配置：.mdoc/check-brands.yaml")
    paths = [path for path in (common, language_path) if path.is_file()]
    layers = [_validate_layer(YAML(typ="safe").load(path.read_text(encoding="utf-8")), path) for path in paths]
    merged = {}
    for layer in layers:
        for brand in layer:
            entry = merged.setdefault(brand["canonical"], {"canonical": brand["canonical"], "variants": {}, "sources": []})
            entry["sources"].append(brand["path"])
            for variant in brand["variants"]:
                entry["variants"].setdefault(variant, []).append(brand["path"])
    if len(merged) > 1000:
        raise ValueError("合并后的品牌配置不能超过 1000 个标准名称。")
    ownership = {}
    for canonical, brand in merged.items():
        for spelling in [canonical, *brand["variants"]]:
            folded = spelling.casefold()
            owner = ownership.get(folded)
            if owner and owner != canonical:
                raise ValueError(f"品牌写法 {spelling} 同时映射到 {owner} 和 {canonical}。")
            ownership[folded] = canonical
    variants = sum(len(brand["variants"]) for brand in merged.values())
    if variants > 20000:
        raise ValueError("合并并去重后的品牌变体不能超过 20000 项。")
    return {"brands": list(merged.values()), "cspell_words": sorted(canonical for canonical in merged if LATIN_WORD.fullmatch(canonical))}, paths


def _masked(source: str) -> str:
    chars = list(source)
    alt_ranges = []
    for tag in IMAGE_TAG.finditer(source):
        alt = ALT_ATTRIBUTE.search(tag.group(0))
        if alt:
            alt_ranges.append((tag.start() + alt.start(2), tag.start() + alt.end(2)))
    def hide(start: int, end: int, preserve_alt: bool = False) -> None:
        for index in range(start, end):
            if preserve_alt and any(left <= index < right for left, right in alt_ranges): continue
            if chars[index] not in "\r\n": chars[index] = " "
    for pattern in (FENCED, INDENTED, HIDDEN_HTML, INLINE_CODE, AUTOLINK):
        for match in pattern.finditer(source): hide(match.start(), match.end())
    for match in HTML_TAG.finditer(source): hide(match.start(), match.end(), True)
    for pattern in (LINK_TARGET, REFERENCE_TARGET):
        for match in pattern.finditer(source): hide(match.start("target"), match.end("target"))
    return "".join(chars)


def inspect(source: str, display: str, config: dict, relative_sources: dict[Path, str]) -> list[dict]:
    visible = _masked(source); candidates = []
    canonicals = [brand["canonical"] for brand in config["brands"]]
    protected = []
    for canonical in sorted(canonicals, key=len, reverse=True):
        protected.extend((match.start(), match.end()) for match in re.finditer(re.escape(canonical), visible))
    for brand in config["brands"]:
        for variant, paths in brand["variants"].items():
            for match in re.finditer(re.escape(variant), visible):
                start, end = match.span()
                if any(start < right and end > left for left, right in protected): continue
                if variant[0].isascii() and LATIN_ADJACENT.match(variant[0]) and start and LATIN_ADJACENT.match(visible[start - 1]): continue
                if variant[-1].isascii() and LATIN_ADJACENT.match(variant[-1]) and end < len(visible) and LATIN_ADJACENT.match(visible[end]): continue
                candidates.append((start, end, variant, brand["canonical"], paths))
    selected = []
    for candidate in sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(candidate[0] < item[1] and candidate[1] > item[0] for item in selected): continue
        selected.append(candidate)
    results = []
    for start, end, actual, expected, paths in selected:
        before = source[:start]; line = before.count("\n") + 1; column = start - before.rfind("\n")
        configurations = [relative_sources[path] for path in reversed(paths)]
        results.append(finding("terminology.brand-name", "error", display, f"产品或品牌名称应写为 {expected}，当前写法为 {actual}。", line, column, end_line=line, end_column=column + len(actual), details={"actual": actual, "expected": expected, "configuration": configurations[0], "configurations": configurations}))
    return results
