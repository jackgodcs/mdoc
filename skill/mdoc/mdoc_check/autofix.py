from __future__ import annotations

import difflib
import json
import re
import tempfile
import time
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from ruamel.yaml import YAML

from .checkers import BRIDGE, NODE, CheckerError, _run, cspell, english_markdown, vale
from .core import PROTOTYPE_CONFIG, _merge, _rule_id, book_context, check_config, load_workspace
from .domain import ENGLISH_PUNCTUATION, HEADING, HTML_CLOSE_WITHOUT_BLANK, HTML_TABLE_TAG, INLINE_DISABLE, LOCAL_ABSOLUTE

MARKDOWN_RULES = {"MD004", "MD005", "MD009", "MD010", "MD012", "MD018", "MD022", "MD031", "MD032", "MD034", "MD037", "MD038", "MD039", "MD047", "MD053", "MD054"}
MDOC_RULES = {"html.block-blank-line", "markdown.md023", "path.forward-slash", "path.case-exact", "terminology.brand-name"}
REFERENCE = re.compile(r"(?P<prefix>!?\[[^]\n]*\]\(|<(?:a|img)\b[^>]*?\b(?:href|src)\s*=\s*[\"'])(?P<target>[^\"')>\n]+)", re.IGNORECASE)
INDENTED_HEADING = re.compile(r"(?m)^(?P<indent> {1,3})(?=#{1,6}\s)")


def configuration(root: Path) -> dict:
    loaded = {}
    for path in (PROTOTYPE_CONFIG, root / ".mdoc" / "check.yaml"):
        if path.is_file():
            loaded = _merge(loaded, YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {})
    value = loaded.get("auto_fix", {})
    if not isinstance(value.get("enabled", True), bool):
        raise ValueError("auto_fix.enabled must be true or false.")
    timeout = value.get("timeout_seconds", 30)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        raise ValueError("auto_fix.timeout_seconds must be a positive integer.")
    markdown = value.get("markdownlint", {}).get("enabled_rules", [])
    custom = value.get("mdoc", {}).get("enabled_rules", [])
    if not isinstance(markdown, list) or any(str(rule).upper() not in MARKDOWN_RULES for rule in markdown):
        raise ValueError("auto_fix.markdownlint.enabled_rules contains an unsupported rule.")
    if not isinstance(custom, list) or any(str(rule).lower() not in MDOC_RULES for rule in custom):
        raise ValueError("auto_fix.mdoc.enabled_rules contains an unsupported rule.")
    return {"enabled": value.get("enabled", True), "timeout_seconds": timeout, "markdownlint": [str(rule).upper() for rule in markdown], "mdoc": [str(rule).lower() for rule in custom]}


def _markdown(content: str, rules: list[str], timeout: int, ignored: list[dict] | None = None) -> dict:
    config = json.loads((PROTOTYPE_CONFIG.parent / "markdownlint.json").read_text(encoding="utf-8"))
    request = {"action": "fix-content", "content": content, "config": config, "rules": rules, "ignored": ignored or []}
    result = json.loads(_run([str(NODE), str(BRIDGE)], input_text=json.dumps(request), timeout=timeout).stdout)
    result["content"] = result["content"].replace("\r\n", "\n").replace("\r", "\n")
    return result


def _exact_reference(locale_root: Path, logical: str, target: str) -> str | None:
    parsed = urlsplit(target)
    if parsed.scheme or target.startswith(("#", "//")):
        return None
    current = locale_root / Path(*PurePosixPath(logical).parent.parts)
    actual = []
    for part in PurePosixPath(parsed.path.replace("\\", "/")).parts:
        if part in {"", "."}: continue
        if part == "..":
            actual.append(part); current = current.parent; continue
        if not current.is_dir(): return None
        matches = [child.name for child in current.iterdir() if child.name.casefold() == part.casefold()]
        if len(matches) != 1: return None
        actual.append(matches[0]); current /= matches[0]
    try: current.resolve().relative_to(locale_root.resolve())
    except ValueError: return None
    return urlunsplit(("", "", "/".join(actual) or ".", parsed.query, parsed.fragment))


def _custom(content: str, rules: list[str], locale_root: Path, logical: str, ignored: list[dict]) -> tuple[str, list[dict], list[str]]:
    edits = []
    def ignored_at(rule: str, offset: int, target: str = "") -> bool:
        line = content.count("\n", 0, offset) + 1
        return any(item.get("rule") == rule and item.get("line") == line and (not target or target in item.get("message", "")) for item in ignored)
    if "html.block-blank-line" in rules:
        for match in HTML_CLOSE_WITHOUT_BLANK.finditer(content):
            if not ignored_at("html.block-blank-line", match.start()): edits.append((match.end(), match.end(), "\n", ["html.block-blank-line"]))
    if "markdown.md023" in rules:
        for match in INDENTED_HEADING.finditer(content):
            if not ignored_at("markdown.md023", match.start()): edits.append((match.start(), match.end(), "", ["markdown.md023"]))
    for match in REFERENCE.finditer(content):
        target = match.group("target"); replacement = target; fixed_rules = []
        parsed_target = urlsplit(target)
        if "path.forward-slash" in rules and not parsed_target.scheme and not target.startswith("//") and "\\" in replacement: replacement = replacement.replace("\\", "/"); fixed_rules.append("path.forward-slash")
        if "path.case-exact" in rules:
            exact = _exact_reference(locale_root, logical, replacement)
            if exact is not None and exact != replacement: replacement = exact; fixed_rules.append("path.case-exact")
        if replacement != target:
            fixed_rules = [rule for rule in fixed_rules if not ignored_at(rule, match.start("target"), target)]
            if fixed_rules: edits.append((match.start("target"), match.end("target"), replacement, fixed_rules))
    edits.sort(reverse=True); occupied = None; applied = []; skipped = []
    for start, end, replacement, rules in edits:
        if occupied is not None and end > occupied:
            skipped.extend(rules); continue
        content = content[:start] + replacement + content[end:]; occupied = start
        line = content.count("\n", 0, start) + 1; column = start - content.rfind("\n", 0, start)
        applied.extend({"rule": rule, "line": line, "column": column} for rule in rules)
    return content, applied, skipped


def _reported_brands(content: str, findings: list[dict]) -> tuple[str, list[dict], list[str]]:
    edits = []; skipped = []; starts = []; offset = 0
    for line in content.splitlines(keepends=True):
        starts.append(offset); offset += len(line)
    for item in findings:
        if item.get("rule") != "terminology.brand-name" or item.get("ignore_id") or item.get("status") == "stale": continue
        details = item.get("details") or {}; actual = details.get("actual"); expected = details.get("expected"); line = item.get("line"); column = item.get("column")
        if not isinstance(actual, str) or not isinstance(expected, str) or not isinstance(line, int) or not isinstance(column, int) or not 1 <= line <= len(starts):
            skipped.append("terminology.brand-name"); continue
        start = starts[line - 1] + column - 1; end = start + len(actual)
        if content[start:end] != actual:
            skipped.append("terminology.brand-name"); continue
        edits.append((start, end, expected))
    actions = []
    for start, end, replacement in sorted(edits, reverse=True):
        content = content[:start] + replacement + content[end:]
        actions.append({"rule": "terminology.brand-name", "line": content.count("\n", 0, start) + 1, "column": start - content.rfind("\n", 0, start)})
    return content, actions, skipped


def _ranges(before: str, after: str, actions: list[dict], deadline: float) -> tuple[list[dict], bool]:
    if before == after: return [], False
    if time.monotonic() >= deadline: return [], True
    before_lines = before.splitlines(keepends=True); after_lines = after.splitlines(keepends=True)
    if len(before_lines) * len(after_lines) > 2_000_000: return [], True
    before_starts = [0]; after_starts = [0]
    for line in before_lines: before_starts.append(before_starts[-1] + len(line))
    for line in after_lines: after_starts.append(after_starts[-1] + len(line))
    positions = []
    for item in actions:
        line = min(max(1, int(item.get("line", 1))), max(1, len(after_starts) - 1))
        positions.append((item, min(len(after), after_starts[line - 1] + max(0, int(item.get("column", 1)) - 1))))
    ranges = []; degraded = False
    for tag, before_line_start, before_line_end, line_start, line_end in difflib.SequenceMatcher(None, before_lines, after_lines).get_opcodes():
        if tag == "equal": continue
        if time.monotonic() >= deadline: return [], True
        before_start, before_end = before_starts[before_line_start], before_starts[before_line_end]
        start, end = after_starts[line_start], after_starts[line_end]
        blocks = [(before_start, before_end, start, end)]
        if before_end - before_start + end - start <= 8192:
            blocks = []
            for nested_tag, nested_before_start, nested_before_end, nested_start, nested_end in difflib.SequenceMatcher(None, before[before_start:before_end], after[start:end]).get_opcodes():
                if nested_tag != "equal": blocks.append((before_start + nested_before_start, before_start + nested_before_end, start + nested_start, start + nested_end))
        else:
            degraded = True
        for block_before_start, block_before_end, block_start, block_end in blocks:
            if block_start == block_end and after: block_start, block_end = max(0, block_start - 1), min(len(after), block_start + 1)
            if block_start == block_end: continue
            distance = min((abs(position - block_start) for _item, position in positions), default=0)
            rules = sorted({item["rule"] for item, position in positions if abs(position - block_start) == distance}) or ["auto-fix"]
            ranges.append({"from": block_start, "to": block_end, "before_from": block_before_start, "before_to": block_before_end, "rules": rules})
    return ranges, degraded or not ranges


def _rule_help(rules: dict[str, int]) -> dict[str, dict]:
    catalog = json.loads(Path(__file__).with_name("rule_help.zh-CN.json").read_text(encoding="utf-8"))
    result = {}
    for rule in rules:
        key = f"markdown.{rule.lower()}" if re.fullmatch(r"MD\d{3}", rule, re.IGNORECASE) else rule
        value = catalog.get(key, {"title": "自动格式修复", "description": "当前内容不符合该格式规则。", "suggestion": "已按规则要求调整格式。"})
        result[rule] = {name: value[name] for name in ("title", "description", "suggestion", "url") if name in value}
    return result


def _local_unfixable(content: str, language: str, logical: str, disabled_rules: set[str], ignored_rules: set[str]) -> int:
    count = 0
    if _rule_id("path.no-local-absolute") not in disabled_rules and "path.no-local-absolute" not in ignored_rules and any(LOCAL_ABSOLUTE.search(match.group("target")) for match in REFERENCE.finditer(content)): count += 1
    if logical.casefold() != "summary.md" and _rule_id("markdown.single-h1") not in disabled_rules and "markdown.single-h1" not in ignored_rules and len([match for match in HEADING.finditer(content) if len(match.group(1)) == 1]) != 1: count += 1
    if language == "en" and _rule_id("locale.en-punctuation") not in disabled_rules and "locale.en-punctuation" not in ignored_rules and ENGLISH_PUNCTUATION.search(content): count += 1
    if _rule_id("markdown.inline-disable") not in disabled_rules and "markdown.inline-disable" not in ignored_rules and INLINE_DISABLE.search(content): count += 1
    stack = []; malformed = False
    for tag in HTML_TABLE_TAG.finditer(content):
        closing, name = bool(tag.group(1)), tag.group(2).lower()
        if not closing: stack.append(name)
        elif not stack or stack.pop() != name: malformed = True; break
    if _rule_id("html.table-structure") not in disabled_rules and "html.table-structure" not in ignored_rules and (malformed or stack): count += 1
    return count


def run(root: Path, report: dict, display: str, path: Path, content: str, state: dict, current_state: dict, active_ignores: list[dict], report_findings: list[dict] | None = None) -> dict:
    if state != current_state: raise ValueError("文件已被外部程序修改，请重新加载后再执行自动修复。")
    config = configuration(root)
    if not config["enabled"]: raise ValueError("工作区已关闭自动修复。")
    locale, logical = display.split("/", 1); workspace = load_workspace(root); book_id = report.get("scope", {}).get("book")
    if not book_id:
        for candidate_id in workspace["books"]:
            if locale not in workspace["books"][candidate_id]["locales"]: continue
            _candidate, candidate_root, _language = book_context(root, workspace, candidate_id, locale)
            try: path.resolve().relative_to(candidate_root)
            except ValueError: continue
            book_id = candidate_id; break
    if not book_id: raise ValueError("自动修复无法确定文件所属书册。")
    _book, locale_root, language = book_context(root, workspace, book_id, locale)
    if report.get("context") == "task": locale_root = path.parents[len(PurePosixPath(logical).parts) - 1]
    effective, _paths = check_config(root, language)
    started = time.monotonic(); original = content; seen = {content}; actions = []; skipped_rules = set()
    content_changed = content != path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    lines = content.splitlines(); exact_ignores = [item for item in active_ignores if not content_changed and 0 < int(item.get("line", 0)) <= len(lines) and lines[int(item["line"]) - 1] == item.get("anchor", "")]
    uncertain_ignores = [item for item in active_ignores if item not in exact_ignores]
    ignored_by_rule = {str(item.get("rule", "")).lower() for item in active_ignores}
    uncertain_rules = {str(item.get("rule", "")).lower() for item in uncertain_ignores}
    markdown_rules = [rule for rule in config["markdownlint"] if _rule_id(rule) not in effective["disabled_rules"] and f"markdown.{rule.lower()}" not in uncertain_rules]
    custom_rules = [rule for rule in config["mdoc"] if _rule_id(rule) not in effective["disabled_rules"] and rule not in uncertain_rules]
    skipped_rules.update(rule for rule in config["markdownlint"] if f"markdown.{rule.lower()}" in uncertain_rules)
    skipped_rules.update(rule for rule in config["mdoc"] if rule in uncertain_rules)
    ignored = [{"rule": item.get("native_rule"), "line": item.get("line"), "column": item.get("column")} for item in exact_ignores]
    protected_markdown = {str(item.get("native_rule")) for item in exact_ignores}
    protected_custom = {str(item.get("rule")) for item in exact_ignores}
    final_errors = []
    if "terminology.brand-name" in custom_rules:
        content, brand_actions, brand_skipped = _reported_brands(content, report_findings or [])
        actions.extend(brand_actions); skipped_rules.update(brand_skipped); custom_rules.remove("terminology.brand-name")
        if content != original: seen.add(content)
    for _round in range(10):
        remaining = config["timeout_seconds"] - (time.monotonic() - started)
        if remaining <= 0: raise ValueError("自动修复超时，当前内容未改变。")
        try: result = _markdown(content, markdown_rules, max(1, int(remaining)), ignored)
        except CheckerError as exc: raise ValueError("自动修复执行失败，当前内容未改变：" + str(exc)) from exc
        candidate = result["content"]; actions.extend(result["applied"]); final_errors = result["errors"]
        candidate, custom_actions, custom_skipped = _custom(candidate, custom_rules, locale_root, logical, exact_ignores); actions.extend(custom_actions); skipped_rules.update(custom_skipped)
        if candidate == content: break
        if candidate in seen: raise ValueError("自动修复结果发生循环，当前内容未改变。")
        seen.add(candidate); content = candidate; ignored = []
        skipped_rules.update(protected_markdown); skipped_rules.update(protected_custom)
        markdown_rules = [rule for rule in markdown_rules if rule not in protected_markdown]
        custom_rules = [rule for rule in custom_rules if rule not in protected_custom]
    else: raise ValueError("自动修复达到 10 轮仍未稳定，当前内容未改变。")
    remaining = config["timeout_seconds"] - (time.monotonic() - started)
    if remaining <= 0: raise ValueError("自动修复超时，当前内容未改变。")
    try: final_errors = _markdown(content, [], max(1, int(remaining)))["errors"]
    except CheckerError as exc: raise ValueError("自动修复执行失败，当前内容未改变：" + str(exc)) from exc
    warnings = []; extra = 0
    try:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / path.name; candidate.write_text(content, encoding="utf-8"); displays = {candidate: display}
            scan_lines = effective["language_detection"]["scan_lines"]
            remaining = config["timeout_seconds"] - (time.monotonic() - started)
            if remaining <= 0: raise TimeoutError
            if language == "en" or (language == "ja" and english_markdown([candidate], scan_lines)):
                dictionaries = [root / ".mdoc" / "check-words.txt", root / ".mdoc" / "check-words.en.txt"]
                extra += len(cspell([candidate], displays, max(1, int(remaining)), dictionaries))
            remaining = config["timeout_seconds"] - (time.monotonic() - started)
            if remaining <= 0: raise TimeoutError
            extra += len(vale([candidate], displays, max(1, int(remaining))))
    except Exception:
        warnings.append("自动修复已完成，但部分不可修复问题统计失败。")
    counts = Counter(item["rule"] for item in actions)
    ignored_keys = {(item.get("native_rule"), item.get("line"), item.get("column")) for item in exact_ignores}
    unfixable = sum((error["ruleNames"][0], error["lineNumber"], (error.get("errorRange") or [1])[0]) not in ignored_keys and error["ruleNames"][0] not in skipped_rules and (not error.get("fixInfo") or error["ruleNames"][0] not in markdown_rules) for error in final_errors) + extra
    unfixable += _local_unfixable(content, language, logical, effective["disabled_rules"], ignored_by_rule)
    rules = dict(sorted(counts.items()))
    try: ranges, degraded = _ranges(original, content, actions, started + config["timeout_seconds"])
    except Exception:
        ranges, degraded = [], True
    if degraded: warnings.append("自动修复已完成，但文件较大或处理超时，仅显示简化的修改范围。" if ranges else "自动修复已完成，但文件较大或处理超时，未显示修改范围。")
    return {"content": content, "changed": content != original, "applied": sum(counts.values()), "rules": rules, "rule_help": _rule_help(rules), "unfixable": unfixable, "skipped_rules": sorted(skipped_rules), "ranges": ranges, "warnings": warnings, "trailing_newline": content.endswith("\n")}
