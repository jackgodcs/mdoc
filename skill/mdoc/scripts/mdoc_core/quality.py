from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from .adapters import run_build
from .io import canonical_digest, file_digest, write_json_atomic
from .virtual_book import CandidateFile, VirtualBook


LINK = re.compile(r"!?\[[^]]*]\(([^)]+)\)")
HEADING = re.compile(r"^(#{1,6})\s+\S", re.MULTILINE)
HAN = re.compile(r"[\u3400-\u9fff]")
LOCAL_PATH = re.compile(r"(?:file://|(?i:[A-Z]:[\\/](?:Users|Documents and Settings)[\\/]))")


def finding(rule: str, severity: str, confidence: str, path: str, message: str, *, required: bool = True, line: int | None = None) -> dict:
    value = {"rule": rule, "severity": severity, "confidence": confidence, "path": path, "message": message, "required": required}
    if line is not None:
        value["line"] = line
    value["fingerprint"] = canonical_digest({key: value[key] for key in ("rule", "path", "message")})
    return value


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _markdown_findings(view: VirtualBook, item: CandidateFile, language: str, rules: dict) -> list[dict]:
    display = f"{item.locale}/{item.path}"
    try:
        text = item.physical.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return [finding("text.utf8", "error", "exact", display, "Markdown must be readable UTF-8.")]
    results: list[dict] = []
    placeholder = re.search(r"\b(?:TODO|TBD)\b", text)
    if placeholder:
        results.append(finding("content.no-placeholder", "error", "probable", display, "Unresolved placeholder found.", line=_line(text, placeholder.start())))
    local = LOCAL_PATH.search(text)
    if local:
        results.append(finding("content.no-local-path", "error", "exact", display, "Local absolute path found.", line=_line(text, local.start())))
    han = HAN.search(text) if language == "en" else None
    if han:
        results.append(finding("locale.en-no-han", "error", "exact", display, "English locale contains Han characters.", line=_line(text, han.start())))
    for term in rules.get("forbidden_terms", ()):
        offset = text.find(term)
        if offset >= 0:
            results.append(finding("content.forbidden-term", "error", "exact", display, f"Forbidden term found: {term}", line=_line(text, offset)))
    levels = [(len(match.group(1)), match.start()) for match in HEADING.finditer(text)]
    if not levels or levels[0][0] != 1 or sum(level == 1 for level, _ in levels) != 1:
        results.append(finding("markdown.single-h1", "error", "exact", display, "Markdown must contain exactly one leading H1."))
    for (previous, _), (current, offset) in zip(levels, levels[1:]):
        if current > previous + 1:
            results.append(finding("markdown.heading-order", "warning", "exact", display, "Heading hierarchy skips a level.", line=_line(text, offset), required=False))
    for custom in rules.get("rules", ()):
        if custom["kind"] == "review":
            continue
        pattern = custom.get("pattern")
        matched = bool(pattern and re.search(pattern, text, re.MULTILINE))
        bad = matched if custom["kind"] == "forbid_regex" else not matched
        if bad:
            results.append(finding(f"custom.{custom['id']}", custom["severity"], custom["nature"], display, f"Declarative rule failed: {custom['id']}", required=custom.get("required", custom["nature"] == "exact")))
    for match in LINK.finditer(text):
        target = match.group(1).strip().split(maxsplit=1)[0].strip("<>\"'")
        parsed = urlsplit(target)
        if not target or target.startswith("#") or parsed.scheme or target.startswith("//"):
            continue
        if view.resolve_link(item, unquote(parsed.path).replace(chr(92), "/")) is None:
            results.append(finding("link.target-exists", "error", "exact", display, f"Linked target is missing: {target}", line=_line(text, match.start())))
    return results


def _safe_fix(item: CandidateFile) -> dict | None:
    if item.origin != "staging" or item.physical.suffix.lower() != ".md":
        return None
    try:
        before = item.physical.read_bytes()
        before.decode("utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    if not before or before.endswith(b"\n"):
        return None
    after = before + b"\n"
    item.physical.write_bytes(after)
    return {"kind": "final-newline", "path": f"{item.locale}/{item.path}", "before_sha256": canonical_digest(before.hex()), "after_sha256": canonical_digest(after.hex())}


def _selected(item: CandidateFile, locale: str | None, path: str | None, changed_paths: set[Path] | None) -> bool:
    if locale and item.locale != locale:
        return False
    if path:
        scope = PurePosixPath(path.replace(chr(92), "/"))
        logical = PurePosixPath(item.path)
        if logical != scope and scope not in logical.parents:
            return False
    return changed_paths is None or item.physical.resolve() in changed_paths


def _summary_findings(view: VirtualBook, locale: str) -> list[dict]:
    summary_path = view.book["navigation"]["summary"].replace(chr(92), "/")
    summary = view.resolve(locale, summary_path)
    display = f"{locale}/{summary_path}"
    if summary is None:
        return [finding("navigation.summary-exists", "error", "exact", display, "Summary file is missing.")]
    try:
        text = summary.physical.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return [finding("text.utf8", "error", "exact", display, "Summary must be readable UTF-8.")]
    linked: set[str] = set()
    for raw in LINK.findall(text):
        target = raw.strip().split(maxsplit=1)[0].strip("<>\"'")
        parsed = urlsplit(target)
        if parsed.scheme or target.startswith(("#", "//")):
            continue
        resolved = view.resolve_link(summary, unquote(parsed.path))
        if resolved and resolved.path.lower().endswith(".md"):
            linked.add(resolved.path)
    content_root = PurePosixPath(view.book["content_root"].replace(chr(92), "/"))
    results: list[dict] = []
    for item in view.files(locale):
        logical = PurePosixPath(item.path)
        if item.path.lower().endswith(".md") and item.path != summary_path and content_root in logical.parents and item.path not in linked:
            results.append(finding("navigation.page-linked", "error", "exact", f"{locale}/{item.path}", "Markdown page is not linked from Summary."))
    return results


def _scan(view: VirtualBook, *, locale: str | None = None, path: str | None = None, changed_paths: set[Path] | None = None, apply_fixes: bool = False) -> tuple[list[dict], list[dict], int]:
    findings: list[dict] = []
    fixes: list[dict] = []
    scanned = 0
    locales = [locale] if locale else sorted(view.book["locales"])
    for locale_id in locales:
        selected = [item for item in view.files(locale_id) if _selected(item, locale, path, changed_paths)]
        if path and not selected:
            root = view.locale_root(locale_id)
            scope = (root / Path(*path.replace(chr(92), "/").split("/"))).resolve()
            try:
                scope.relative_to(root)
            except ValueError as exc:
                raise ValueError("Quality Gate path escapes the selected locale.") from exc
            if not scope.exists():
                raise ValueError(f"Quality Gate scope does not exist: {locale_id}/{path}")
        if apply_fixes:
            for item in selected:
                fixed = _safe_fix(item)
                if fixed:
                    fixes.append(fixed)
        for item in selected:
            scanned += 1
            if item.physical.suffix.lower() == ".md":
                findings.extend(_markdown_findings(view, item, view.book["locales"][locale_id]["language"], view.workspace.config["quality_gate"]))
        findings.extend(_summary_findings(view, locale_id))
    return findings, fixes, scanned


def _blocking(findings: list[dict]) -> list[dict]:
    return [item for item in findings if item["severity"] == "error" and item.get("required", True)]


def _task_file_digest(task) -> str:
    values = {}
    for item in task.definition["manifest"]:
        key = f"{item['locale']}/{item['path']}"
        if item["action"] == "delete":
            values[key] = "delete"
        else:
            path = task.directory / "staging" / item["locale"] / Path(*item["path"].replace(chr(92), "/").split("/"))
            values[key] = file_digest(path) if path.is_file() else None
    return canonical_digest(values)


def _review_states(task, state: dict, input_digest: str) -> dict:
    required = sorted(set(task.workspace.config["quality_gate"].get("required_reviews", ())) | set(task.definition["quality_gate"].get("required_reviews", ())))
    result = {}
    for name in required:
        review = state.get("reviews", {}).get(name)
        if not review:
            status = "waiting_for_review"
        elif review.get("status") != "human_accepted":
            status = review.get("status", "waiting_for_review")
        elif review.get("input_digest") != input_digest:
            status = "stale"
        else:
            status = "human_accepted"
        result[name] = {"status": status, "input_digest": review.get("input_digest") if review else None}
    return result


def _build(workspace, view: VirtualBook, profile: str, adapter_name: str | None, *, execute: bool, record_root: Path) -> dict:
    if profile == "standard":
        return {"status": "not_requested"}
    adapter_name = adapter_name or view.book.get("release_build_adapter")
    adapter = workspace.config.get("build_adapters", {}).get(adapter_name or "")
    if profile == "release" and not adapter:
        return {"status": "not_configured", "adapter": adapter_name}
    if not adapter:
        return {"status": "not_requested", "adapter": adapter_name}
    if not execute:
        return {"status": "deferred_until_publish", "adapter": adapter_name, "config_digest": canonical_digest(adapter)}
    return run_build(workspace, view, adapter_name, record_root)


def _classify(findings: list[dict], changed: set[str]) -> list[dict]:
    for item in findings:
        item["classification"] = "introduced" if item["path"] in changed else "pre_existing"
        item["suppression"] = "active"
    return findings


def task_check(task, state: dict, *, published: bool = False) -> dict:
    view = VirtualBook.task(task, published=published)
    findings, fixes, scanned = _scan(view, apply_fixes=not published and task.workspace.config["quality_gate"].get("safe_fixes", False))
    input_digest = _task_file_digest(task)
    reviews = _review_states(task, state, input_digest)
    pending = [name for name, value in reviews.items() if value["status"] != "human_accepted"]
    profile = task.definition["quality_gate"]["profile"]
    build = _build(
        task.workspace, view, profile, task.definition["quality_gate"].get("build_adapter"),
        execute=profile == "release" or published, record_root=task.directory / "builds",
    )
    status = "passed" if not _blocking(findings) and not pending and build["status"] in {"not_requested", "passed"} else "blocked"
    changed = {f"{item['locale']}/{item['path']}" for item in task.definition["manifest"]}
    report = {"schema_version": 1, "context": "published-task" if published else "task", "task_id": task.task_id, "book": task.definition["task"]["book"], "profile": profile, "status": status, "input_digest": input_digest, "candidate_digest": view.digest(), "files_scanned": scanned, "findings": _classify(findings, changed), "blocking_count": len(_blocking(findings)), "fixes": fixes, "reviews": reviews, "pending_reviews": pending, "build": build, "created_at": int(time.time())}
    return _store(task.workspace, report, "tasks", task.task_id)


def _changed_paths(workspace) -> set[Path]:
    vcs = workspace.config["workspace"]["formal_vcs"]
    if vcs not in {"git", "svn"}:
        raise ValueError("--changed requires workspace.formal_vcs to be git or svn.")
    try:
        if vcs == "git":
            result = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=workspace.repository, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            names = [line[3:].strip().strip('"').split(" -> ")[-1].strip('"') for line in result.stdout.splitlines() if len(line) > 3]
        else:
            result = subprocess.run(["svn", "status"], cwd=workspace.repository, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            names = [line[8:].strip() for line in result.stdout.splitlines() if len(line) > 8]
    except OSError as exc:
        raise ValueError(f"Configured VCS command is unavailable: {vcs}") from exc
    if result.returncode != 0:
        raise ValueError(f"Configured VCS status failed: {result.stderr.strip() or result.returncode}")
    return {(workspace.repository / Path(name.replace("/", os.sep))).resolve() for name in names}


def book_check(workspace, book_id: str, *, profile: str | None = None, locale: str | None = None, path: str | None = None, changed: bool = False) -> dict:
    view = VirtualBook.formal(workspace, book_id)
    selected_profile = profile or workspace.config["quality_gate"]["default_profile"]
    findings, fixes, scanned = _scan(view, locale=locale, path=path, changed_paths=_changed_paths(workspace) if changed else None, apply_fixes=False)
    build = _build(
        workspace, view, selected_profile, view.book.get("release_build_adapter"),
        execute=not path and not changed and locale is None,
        record_root=workspace.control / "cache" / "builds" / book_id,
    )
    status = "passed" if not _blocking(findings) and build["status"] in {"not_requested", "passed"} else "blocked"
    report = {"schema_version": 1, "context": "book", "book": book_id, "profile": selected_profile, "status": status, "scope": {"locale": locale, "path": path, "changed": changed}, "input_digest": view.digest(), "files_scanned": scanned, "findings": _classify(findings, set()), "blocking_count": len(_blocking(findings)), "fixes": fixes, "reviews": {}, "pending_reviews": [], "build": build, "created_at": int(time.time())}
    return _store(workspace, report, "books", book_id)


def _store(workspace, report: dict, group: str, name: str | None = None) -> dict:
    report["digest"] = canonical_digest({key: value for key, value in report.items() if key not in {"created_at", "path"}})
    root = workspace.control / "quality-reports" / group
    if name is not None:
        root /= name
    historical = root / f"{time.time_ns()}-{report['context']}.json"
    report["path"] = str(historical)
    write_json_atomic(historical, report)
    write_json_atomic(root / "latest.json", report)
    return report
