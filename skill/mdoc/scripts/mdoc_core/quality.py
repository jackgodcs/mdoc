from __future__ import annotations

import os
import posixpath
import re
import subprocess
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

from .io import canonical_digest, write_json_atomic
from .paths import book_definition, changes, locale_root

LINK = re.compile(r"!?\[[^]]*]\(([^)]+)\)")
HEADING = re.compile(r"^(#{1,6})\s+\S", re.MULTILINE)
HAN = re.compile(r"[\u3400-\u9fff]")
LOCAL_PATH = re.compile(r"(?:file://|(?i:[A-Z]:[\\/](?:Users|Documents and Settings)[\\/]))")


def finding(rule: str, severity: str, confidence: str, path: str, message: str) -> dict:
    return {"rule": rule, "severity": severity, "confidence": confidence, "path": path, "message": message}


def _markdown_findings(path: Path, display: str, language: str, rules: dict, alternate: Path | None = None) -> list[dict]:
    results: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return [finding("text.utf8", "error", "exact", display, "Markdown must be readable UTF-8.")]
    if re.search(r"\b(?:TODO|TBD)\b", text):
        results.append(finding("content.no-placeholder", "error", "probable", display, "Unresolved placeholder found."))
    if LOCAL_PATH.search(text):
        results.append(finding("content.no-local-path", "error", "exact", display, "Local absolute path found."))
    if language == "en" and HAN.search(text):
        results.append(finding("locale.en-no-han", "error", "exact", display, "English locale contains Han characters."))
    for term in rules.get("forbidden_terms", ()):
        if term in text:
            results.append(finding("content.forbidden-term", "error", "exact", display, f"Forbidden term found: {term}"))
    levels = [len(match.group(1)) for match in HEADING.finditer(text)]
    if not levels or levels[0] != 1 or levels.count(1) != 1:
        results.append(finding("markdown.single-h1", "error", "exact", display, "Markdown must contain exactly one leading H1."))
    if any(current > previous + 1 for previous, current in zip(levels, levels[1:])):
        results.append(finding("markdown.heading-order", "warning", "exact", display, "Heading hierarchy skips a level."))
    for custom in rules.get("rules", ()):
        matched = re.search(custom["pattern"], text, re.MULTILINE) is not None
        bad = matched if custom["kind"] == "forbid_regex" else not matched
        if bad:
            results.append(finding(f"custom.{custom['id']}", custom["severity"], "exact", display, f"Declarative rule failed: {custom['id']}"))
    for raw in LINK.findall(text):
        target = raw.strip().split(maxsplit=1)[0].strip("<>\"'")
        parsed = urlsplit(target)
        if not target or target.startswith("#") or parsed.scheme or target.startswith("//"):
            continue
        decoded = unquote(parsed.path).replace("\\", "/")
        parts = [part for part in decoded.split("/") if part not in {"", "."}]
        resolved = (path.parent / Path(*parts)).resolve()
        alternate_resolved = (alternate.parent / Path(*parts)).resolve() if alternate else None
        if not resolved.exists() and (alternate_resolved is None or not alternate_resolved.exists()):
            results.append(finding("link.target-exists", "error", "exact", display, f"Linked target is missing: {target}"))
    return results


def _safe_fix(path: Path) -> None:
    if path.suffix.lower() != ".md":
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return
    fixed = "\n".join(line.rstrip() for line in text.splitlines()).rstrip() + "\n"
    if fixed != text:
        path.write_text(fixed, encoding="utf-8", newline="\n")


def _build(workspace, book: dict, profile: str, adapter_name: str | None, *, execute: bool = True) -> dict:
    if profile != "release":
        return {"status": "not_required"}
    adapter = workspace.config["quality_gate"].get("build_adapters", {}).get(adapter_name or "")
    if not adapter:
        return {"status": "missing", "adapter": adapter_name}
    if not execute:
        return {"status": "deferred_until_publish", "adapter": adapter_name}
    root = (workspace.repository / book["root"]).resolve()
    try:
        completed = subprocess.run(list(adapter["command"]), cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=adapter.get("timeout_seconds", 600), check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "failed", "adapter": adapter_name, "error": str(exc)}
    artifact = root / adapter["artifact"] if adapter.get("artifact") else None
    result = {"status": "passed" if completed.returncode == 0 and artifact is not None and artifact.is_file() else "failed", "returncode": completed.returncode, "artifact": str(artifact) if artifact else None, "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]}
    if result["status"] == "passed" and artifact.suffix.lower() == ".pdf":
        try:
            from pdf_check_core import check_existing_pdf
            output = workspace.control / "cache" / "pdf-check" / canonical_digest({"artifact": str(artifact), "mtime": artifact.stat().st_mtime_ns})
            pdf = check_existing_pdf(root, artifact, output, {"artifact_id": adapter_name or "pdf", "locale": "all"})
            needs_review = any(item.get("confidence") == "review" and item.get("status") != "ignored-by-user" for item in pdf.get("findings", []))
            pdf_status = "passed" if pdf["counts"]["effective_errors"] == 0 and not needs_review else "blocked"
            result["pdf_check"] = {"status": pdf_status, "report": str(output / "pdf-check.json"), "counts": pdf["counts"], "pending_review": needs_review}
            if result["pdf_check"]["status"] != "passed":
                result["status"] = "failed"
        except Exception as exc:
            result["status"] = "failed"
            result["pdf_check"] = {"status": "failed", "error": str(exc)}
    return result


def _summary_findings(locale: Path, locale_id: str, book: dict, files: Iterable[Path]) -> list[dict]:
    summary = locale / book["navigation"]["summary"]
    display = f"{locale_id}/{book['navigation']['summary']}"
    if not summary.is_file():
        return [finding("navigation.summary-exists", "error", "exact", display, "Summary file is missing.")]
    try:
        text = summary.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return [finding("text.utf8", "error", "exact", display, "Summary must be readable UTF-8.")]
    linked = set()
    for raw in LINK.findall(text):
        target = unquote(urlsplit(raw.strip().split(maxsplit=1)[0].strip("<>\"'")).path)
        if target.lower().endswith(".md"):
            try:
                linked.add((summary.parent / target).resolve())
            except OSError:
                pass
    results = []
    content = (locale / book["content_root"]).resolve()
    for path in files:
        if path.suffix.lower() == ".md" and path.resolve() != summary.resolve() and content in path.resolve().parents and path.resolve() not in linked:
            results.append(finding("navigation.page-linked", "error", "exact", f"{locale_id}/{path.relative_to(locale).as_posix()}", "Markdown page is not linked from Summary."))
    return results


def _task_summary_findings(task, published: bool) -> list[dict]:
    book = book_definition(task)
    results = []
    for locale_id in task.definition["locales"]:
        root = locale_root(task, locale_id)
        content = (root / book["content_root"]).resolve()
        page_records = [(item, formal, staged) for item, formal, staged in changes(task) if item["locale"] == locale_id and item["kind"] == "page" and item["action"] != "delete" and content in formal.resolve().parents]
        if not page_records:
            continue
        summary_record = next(((item, formal, staged) for item, formal, staged in changes(task) if item["locale"] == locale_id and item["path"].replace("\\", "/") == book["navigation"]["summary"].replace("\\", "/")), None)
        summary = (summary_record[1] if published else summary_record[2]) if summary_record and summary_record[0]["action"] != "delete" else root / book["navigation"]["summary"]
        display = f"{locale_id}/{book['navigation']['summary']}"
        if not summary.is_file():
            results.append(finding("navigation.summary-exists", "error", "exact", display, "Summary file is missing from the task result."))
            continue
        try:
            text = summary.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            results.append(finding("text.utf8", "error", "exact", display, "Summary must be readable UTF-8."))
            continue
        linked = {posixpath.normpath(unquote(urlsplit(raw.strip().split(maxsplit=1)[0].strip("<>\"'")).path).replace("\\", "/")) for raw in LINK.findall(text) if urlsplit(raw.strip().split(maxsplit=1)[0].strip("<>\"'")).path.lower().endswith(".md")}
        for item, _formal, _staged in page_records:
            relative = item["path"].replace("\\", "/")
            if posixpath.normpath(relative) not in linked:
                results.append(finding("navigation.page-linked", "error", "exact", f"{locale_id}/{relative}", "Changed Markdown page is not linked from Summary."))
    return results


def task_check(task, state: dict, *, published: bool = False) -> dict:
    files: list[tuple[Path, Path, str, str]] = []
    findings = []
    book = book_definition(task)
    for item, formal, staged in changes(task):
        display = f"{item['locale']}/{item['path']}"
        if item["action"] == "delete":
            if (formal if published else staged).exists() and published:
                findings.append(finding("file.deleted", "error", "exact", display, "Declared deletion target still exists."))
            continue
        target = formal if published else staged
        if not target.is_file():
            findings.append(finding("file.exists", "error", "exact", display, "Declared file is missing."))
        else:
            if not published and task.workspace.config["quality_gate"].get("safe_fixes"):
                _safe_fix(target)
            language = book["locales"][item["locale"]]["language"]
            files.append((target, formal, display, language))
    for path, formal, display, language in files:
        if path.suffix.lower() == ".md":
            findings.extend(_markdown_findings(path, display, language, task.workspace.config["quality_gate"], None if published else formal))
    profile = task.definition["quality_gate"]["profile"]
    if profile in {"full", "release"} and task.workspace.config["quality_gate"].get("require_summary_links"):
        findings.extend(_task_summary_findings(task, published))
    required = sorted(set(task.workspace.config["quality_gate"].get("required_reviews", ())) | set(task.definition["quality_gate"].get("required_reviews", ())))
    pending = [name for name in required if state.get("reviews", {}).get(name, {}).get("status") != "passed"]
    build = _build(task.workspace, book, profile, task.definition["quality_gate"].get("build_adapter"), execute=published)
    acceptable_build = {"not_required", "passed", "deferred_until_publish"}
    status = "passed" if not any(item["severity"] == "error" for item in findings) and not pending and build["status"] in acceptable_build else "blocked"
    report = {"schema_version": 1, "context": "published-task" if published else "task-staging", "task_id": task.task_id, "book": task.definition["task"]["book"], "profile": profile, "status": status, "findings": findings, "pending_reviews": pending, "build": build, "created_at": int(time.time())}
    return _store(task.workspace, report, task.task_id)


def _changed_paths(workspace, book: dict) -> set[Path]:
    root = (workspace.repository / book["root"]).resolve()
    vcs = workspace.config["workspace"]["formal_vcs"]
    try:
        if vcs == "git":
            result = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=workspace.repository, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            names = [line[3:].strip().strip('"').split(" -> ")[-1].strip('"') for line in result.stdout.splitlines() if len(line) > 3]
        elif vcs == "svn":
            result = subprocess.run(["svn", "status"], cwd=workspace.repository, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            names = [line[8:].strip() for line in result.stdout.splitlines() if len(line) > 8]
        else:
            raise ValueError("--changed requires workspace.formal_vcs to be git or svn.")
    except OSError as exc:
        raise ValueError(f"Configured VCS command is unavailable: {vcs}") from exc
    if result.returncode != 0:
        raise ValueError(f"Configured VCS status failed: {result.stderr.strip() or result.returncode}")
    return {(workspace.repository / Path(name.replace("/", os.sep))).resolve() for name in names if (workspace.repository / Path(name.replace("/", os.sep))).resolve() == root or root in (workspace.repository / Path(name.replace("/", os.sep))).resolve().parents}


def book_check(workspace, book_id: str, *, profile: str | None = None, locale: str | None = None, path: str | None = None, changed: bool = False) -> dict:
    book = workspace.config["books"][book_id]
    selected_profile = profile or workspace.config["quality_gate"]["default_profile"]
    locales = [locale] if locale else sorted(book["locales"])
    selected_changed = _changed_paths(workspace, book) if changed else None
    findings: list[dict] = []
    scanned = []
    for locale_id in locales:
        root = (workspace.repository / book["root"] / book["locales"][locale_id]["root"]).resolve()
        scope = (root / Path(*path.replace("\\", "/").split("/"))).resolve() if path else root
        try:
            scope.relative_to(root)
        except ValueError as exc:
            raise ValueError("Quality Gate path escapes the selected locale.") from exc
        if not scope.exists():
            raise ValueError(f"Quality Gate scope does not exist: {locale_id}/{path or ''}")
        candidates = [item for item in ([scope] if scope.is_file() else scope.rglob("*")) if item.is_file()]
        if selected_changed is not None:
            candidates = [item for item in candidates if item.resolve() in selected_changed]
        scanned.extend(str(item) for item in candidates)
        language = book["locales"][locale_id]["language"]
        markdown_count = 0
        for item in candidates:
            if item.suffix.lower() == ".md":
                markdown_count += 1
                findings.extend(_markdown_findings(item, f"{locale_id}/{item.relative_to(root).as_posix()}", language, workspace.config["quality_gate"]))
        if not changed and markdown_count == 0:
            findings.append(finding("scope.markdown-present", "error", "exact", f"{locale_id}/{path or ''}", "Quality Gate scope contains no Markdown files."))
        if selected_profile in {"full", "release"} and workspace.config["quality_gate"].get("require_summary_links") and not path and not changed:
            findings.extend(_summary_findings(root, locale_id, book, candidates))
    build = _build(workspace, book, selected_profile, book.get("release_build_adapter")) if not path and not changed and locale is None else ({"status": "not_in_scope"} if selected_profile == "release" else {"status": "not_required"})
    status = "passed" if not any(item["severity"] == "error" for item in findings) and build["status"] in {"not_required", "not_in_scope", "passed"} else "blocked"
    report = {"schema_version": 1, "context": "book", "book": book_id, "profile": selected_profile, "status": status, "scope": {"locale": locale, "path": path, "changed": changed}, "files_scanned": len(scanned), "findings": findings, "pending_reviews": [], "build": build, "created_at": int(time.time())}
    return _store(workspace, report, "books", book_id)


def _store(workspace, report: dict, group: str, name: str | None = None) -> dict:
    report["digest"] = canonical_digest({key: value for key, value in report.items() if key != "created_at"})
    root = workspace.control / "quality-reports" / group
    if name is not None:
        root /= name
    write_json_atomic(root / "latest.json", report)
    write_json_atomic(root / f"{time.time_ns()}-{report['context']}.json", report)
    return report
