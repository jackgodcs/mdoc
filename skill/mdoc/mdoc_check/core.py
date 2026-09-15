from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath

from ruamel.yaml import YAML

from .checkers import CheckerError, cspell, english_markdown, markdownlint, tool_versions, vale
from .model import finding


from urllib.parse import unquote, urlsplit

HAN = re.compile(r"[\u3400-\u9fff]")
PROTOTYPE_CONFIG = Path(__file__).resolve().parents[1] / "config" / "mdoc.yaml"
MANDATORY_RULES = {"text.utf8", "locale.en-no-han", "image.non-empty", "image.decodable", "path.no-local-absolute", "path.inside-locale"}


def _finding_identity(item: dict) -> tuple:
    return tuple(item.get(key) for key in ("checker", "native_rule", "rule", "severity", "mandatory", "path", "message"))


def load_workspace(root: Path) -> dict:
    path = root / ".mdoc" / "workspace.yaml"
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


def _merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in (override or {}).items():
        result[key] = _merge(result.get(key, {}), value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def check_config(root: Path, language: str) -> tuple[dict, list[Path]]:
    paths = [PROTOTYPE_CONFIG, root / ".mdoc" / "check.yaml", root / ".mdoc" / f"check.{language}.yaml"]
    loaded = {}
    existing = []
    for path in paths:
        if path.is_file():
            loaded = _merge(loaded, YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {})
            existing.append(path)
    external = loaded["execution"].get("external_checkers", 3)
    if not isinstance(external, int) or isinstance(external, bool) or not 1 <= external <= 3:
        raise ValueError("execution.external_checkers must be an integer from 1 to 3.")
    maximum = loaded["execution"].get("image_workers_max", 8)
    workers = loaded["execution"].get("image_workers", "auto")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
        raise ValueError("execution.image_workers_max must be a positive integer.")
    if workers != "auto" and (not isinstance(workers, int) or isinstance(workers, bool) or workers < 1):
        raise ValueError("execution.image_workers must be 'auto' or a positive integer.")
    scan_lines = loaded.get("language_detection", {}).get("scan_lines", 20)
    if not isinstance(scan_lines, int) or isinstance(scan_lines, bool) or not 1 <= scan_lines <= 100:
        raise ValueError("language_detection.scan_lines must be an integer from 1 to 100.")
    overrides = loaded.get("severity_overrides", {})
    if not isinstance(overrides, dict) or any(value not in {"error", "warning"} for value in overrides.values()):
        raise ValueError("severity_overrides must map rule IDs to 'error' or 'warning'.")
    disabled = loaded.get("disabled_rules", [])
    if not isinstance(disabled, list) or any(not isinstance(rule, str) for rule in disabled):
        raise ValueError("disabled_rules must be a list of rule IDs.")
    legacy = [rule for rule in [*overrides, *disabled] if str(rule).casefold() == "vale.mdoc.productname"]
    if legacy:
        raise ValueError("vale.MDOC.ProductName 已废除，请改用 terminology.brand-name。")
    loaded["severity_overrides"] = {_rule_id(rule): value for rule, value in overrides.items()}
    loaded["disabled_rules"] = {_rule_id(rule) for rule in disabled}
    protected = MANDATORY_RULES & (set(loaded["severity_overrides"]) | loaded["disabled_rules"])
    if protected:
        raise ValueError("Mandatory rules cannot be overridden or disabled: " + ", ".join(sorted(protected)))
    loaded["execution"]["image_workers_resolved"] = min(maximum, max(2, (os.cpu_count() or 2) // 2)) if workers == "auto" else min(workers, maximum)
    return loaded, existing


def _rule_id(rule) -> str:
    value = str(rule).lower()
    return value.removeprefix("markdown.") if re.fullmatch(r"markdown.md\d{3}", value) else value


def apply_rule_config(findings: list[dict], config: dict) -> list[dict]:
    overrides = config.get("severity_overrides", {})
    disabled = config.get("disabled_rules", set())
    results = []
    for item in findings:
        rule = _rule_id(item["rule"])
        if rule in disabled and not item.get("mandatory"):
            continue
        if rule in overrides and not item.get("mandatory"):
            item["severity"] = overrides[rule]
        results.append(item)
    return results


def book_context(root: Path, config: dict, book_id: str, locale: str) -> tuple[dict, Path, str]:
    book = config["books"][book_id]
    locale_config = book["locales"][locale]
    locale_root = (root / book["root"] / locale_config["root"]).resolve()
    return book, locale_root, locale_config["language"]


def summary_entries(summary: Path) -> list[tuple[int, str]]:
    return [(item["depth"], item["path"]) for item in summary_items(summary)]


def summary_items(summary: Path) -> list[dict]:
    from .checkers import BRIDGE, NODE, _run

    raw = json.loads(_run([str(NODE), str(BRIDGE)], input_text=json.dumps({"action": "summary", "file": str(summary.resolve())})).stdout)
    entries = []
    for item in raw:
        path = unquote(urlsplit(item["href"]).path).replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        entries.append({**item, "path": path})
    return entries


def select_markdown(locale_root: Path, book: dict, scope: str, target: str | None) -> list[str]:
    if target:
        normalized_target = target.replace("\\", "/")
        candidate = PurePosixPath(normalized_target)
        if candidate.is_absolute() or ".." in candidate.parts or candidate.suffix.lower() not in {".md", ".markdown"}:
            raise ValueError("Target must be a relative Markdown path inside the locale root.")
    if scope == "page":
        return [normalized_target] if target else []
    if scope == "section":
        entries = summary_entries(locale_root / book["navigation"]["summary"])
        normalized = target.replace("\\", "/") if target else ""
        for index, (indent, path) in enumerate(entries):
            if path == normalized:
                selected = [path]
                for child_indent, child_path in entries[index + 1 :]:
                    if child_indent <= indent:
                        break
                    selected.append(child_path)
                return selected
        raise ValueError(f"Section target is not present in Summary.md: {target}")
    content_root = locale_root / Path(*book["content_root"].replace("\\", "/").split("/"))
    pages = [path.relative_to(locale_root).as_posix() for path in content_root.rglob("*") if path.is_file() and path.suffix.lower() in {".md", ".markdown"}]
    summary = book["navigation"]["summary"].replace("\\", "/")
    return sorted({summary, *pages})


def internal_findings(locale_root: Path, locale: str, language: str, logical_paths: list[str], physical_files: dict[str, Path] | None = None) -> list[dict]:
    results = []
    for logical in logical_paths:
        physical = physical_files.get(logical, locale_root / Path(*PurePosixPath(logical).parts)) if physical_files is not None else locale_root / Path(*PurePosixPath(logical).parts)
        display = f"{locale}/{logical}"
        try:
            text = physical.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            results.append(finding("text.utf8", "error", display, "Markdown must be readable UTF-8.", 1, 1, mandatory=True))
            continue
        if language == "en":
            match = HAN.search(text)
            if match:
                before = text[: match.start()]
                line = before.count("\n") + 1
                column = len(before.rsplit("\n", 1)[-1]) + 1
                results.append(finding("locale.en-no-han", "error", display, f"English Markdown contains Han character: {match.group(0)}", line, column, mandatory=True))
    return results


def _snapshot(paths: list[Path], workers: int = 1) -> dict[str, str | None]:
    def state(path: Path) -> tuple[str, str | None]:
        try:
            import hashlib
            return str(path.resolve()), hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return str(path.resolve()), None
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(paths)))) as executor:
        return dict(executor.map(state, paths))


def _single(root: Path, config: dict, book_id: str, locale: str, scope: str, target: str | None, level: str, internal_only: bool, progress=None, physical_files: dict[str, Path] | None = None, forced_images: set[str] | None = None, classification_paths: set[str] | None = None, baseline_findings: Counter | None = None, classify_all_new: bool = False, task_id: str | None = None, selected_paths: list[str] | None = None) -> dict:
    from .domain import inspect
    from .ignores import annotate, apply, load as load_ignores

    started = time.monotonic()
    book, locale_root, language = book_context(root, config, book_id, locale)
    effective_config, check_config_paths = check_config(root, language)
    from .brands import load as load_brands
    brands, brand_paths = load_brands(root, language, progress)
    check_config_paths.extend(brand_paths)
    image_workers = effective_config["execution"]["image_workers_resolved"]
    dictionary_paths = [root / ".mdoc" / "check-words.txt", root / ".mdoc" / f"check-words.{'en' if language == 'ja' else language}.txt"]
    check_config_paths.extend(path for path in dictionary_paths if path.is_file())
    ignore_rules, ignore_paths = load_ignores(root, language)
    check_config_paths.extend(ignore_paths)
    timeout = effective_config["execution"]["timeouts_seconds"].get(scope, effective_config["execution"]["timeouts_seconds"]["page"])
    if selected_paths is not None:
        paths = sorted(selected_paths)
    elif physical_files is not None:
        content_prefix = book["content_root"].replace("\\", "/").rstrip("/") + "/"
        summary_path = book["navigation"]["summary"].replace("\\", "/")
        paths = sorted(path for path in physical_files if path == summary_path or path.startswith(content_prefix) and path.lower().endswith((".md", ".markdown")))
    else:
        paths = select_markdown(locale_root, book, scope, target)
    if progress:
        progress(f"[{book_id}/{locale}] 已选择 {len(paths)} 个 Markdown，开始领域检查")
    physical = [physical_files.get(path, locale_root / Path(*PurePosixPath(path).parts)) if physical_files is not None else locale_root / Path(*PurePosixPath(path).parts) for path in paths]
    displays = {path: f"{locale}/{logical}" for path, logical in zip(physical, paths)}
    workspace_config = root / ".mdoc" / "workspace.yaml"
    summary_logical = book["navigation"]["summary"].replace("\\", "/")
    summary = physical_files.get(summary_logical, locale_root / Path(*PurePosixPath(summary_logical).parts)) if physical_files is not None else locale_root / Path(*PurePosixPath(summary_logical).parts)
    base_inputs = sorted(set([*physical, summary, workspace_config, *check_config_paths]))
    base_before = _snapshot(base_inputs, image_workers)
    executor = None
    futures = {}
    checkers = {"mdoc": {"status": "passed"}, "pillow": {"status": "passed"}}
    if not internal_only:
        def timed(job):
            checker_started = time.monotonic()
            return job(), round((time.monotonic() - checker_started) * 1000)

        jobs = {"markdownlint": lambda: timed(lambda: markdownlint(physical, displays, timeout, effective_config["severity_overrides"])), "vale": lambda: timed(lambda: vale(physical, displays, timeout))}
        cspell_files = physical if language == "en" else english_markdown(physical, effective_config["language_detection"]["scan_lines"]) if language == "ja" else []
        brand_dictionary = None
        if cspell_files and brands["cspell_words"]:
            import tempfile
            handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False)
            handle.write("\n".join(brands["cspell_words"]) + "\n"); handle.close(); brand_dictionary = Path(handle.name); dictionary_paths.append(brand_dictionary)
        if progress:
            progress(f"[{book_id}/{locale}] 正在运行 markdownlint、Vale" + (f"、CSpell（{len(cspell_files)} 个英文文件）" if cspell_files else ""))
        if cspell_files:
            jobs["cspell"] = lambda: timed(lambda: cspell(cspell_files, {path: displays[path] for path in cspell_files}, timeout, dictionary_paths))
        else:
            checkers["cspell"] = {"status": "not_applicable"}
        executor = ThreadPoolExecutor(max_workers=min(effective_config["execution"]["external_checkers"], len(jobs)))
        futures = {executor.submit(job): name for name, job in jobs.items()}
    else:
        for name in ("markdownlint", "vale", "cspell"):
            checkers[name] = {"status": "not_requested"}
    try:
        findings = internal_findings(locale_root, locale, language, paths, physical_files)
        domain_findings, resources = inspect(locale_root, locale, language, paths, book["content_root"], book["assets_root"], book["navigation"]["summary"], scope in {"book", "task"}, effective_config["images"], physical_files, forced_images, image_workers, brands, {path: path.relative_to(root).as_posix() for path in brand_paths})
        findings.extend(domain_findings)
    except Exception:
        if executor:
            executor.shutdown(cancel_futures=True)
        if 'brand_dictionary' in locals() and brand_dictionary is not None:
            brand_dictionary.unlink(missing_ok=True)
        raise
    if progress:
        progress(f"[{book_id}/{locale}] 领域检查完成，发现 {len(resources['images'])} 个直接图片依赖")
    direct_resources = []
    for display in [*resources["images"], *resources["resources"]]:
        _, logical = display.split("/", 1)
        direct_resources.append(physical_files.get(logical, locale_root / Path(*PurePosixPath(logical).parts)) if physical_files is not None else locale_root / Path(*PurePosixPath(logical).parts))
    all_inputs = sorted(set([*base_inputs, *direct_resources]))
    before = _snapshot(all_inputs, image_workers)
    changed_during_discovery = base_before != _snapshot(base_inputs, image_workers)
    try:
        for future in as_completed(futures):
            name = futures[future]
            try:
                checker_findings, duration_ms = future.result()
                findings.extend(checker_findings)
                checkers[name] = {"status": "passed", "duration_ms": duration_ms, "findings": len(checker_findings)}
            except (CheckerError, ValueError, json.JSONDecodeError) as exc:
                checkers[name] = {"status": "failed", "error": str(exc)}
    except BaseException:
        if executor:
            executor.shutdown(cancel_futures=True)
        if 'brand_dictionary' in locals() and brand_dictionary is not None:
            brand_dictionary.unlink(missing_ok=True)
        raise
    if executor:
        executor.shutdown()
    if 'brand_dictionary' in locals() and brand_dictionary is not None:
        brand_dictionary.unlink(missing_ok=True)
    changed = changed_during_discovery or before != _snapshot(all_inputs, image_workers)
    findings = apply_rule_config(findings, effective_config)
    findings.sort(key=lambda item: (item["path"], item["line"], item["column"], 0 if item["severity"] == "error" else 1, item["rule"]))
    for item in findings:
        item["book"] = book_id
    annotate(findings, {logical: path for logical, path in zip(paths, physical)})
    if classification_paths is not None:
        for item in findings:
            identity = _finding_identity(item)
            pre_existing = bool(baseline_findings and baseline_findings[identity])
            if pre_existing:
                baseline_findings[identity] -= 1
            item["classification"] = "introduced" if (classify_all_new or item["path"] in classification_paths) and not pre_existing else "pre_existing"
            item["suppression"] = "active" if item["classification"] == "pre_existing" and item["severity"] == "error" else "inactive"
    ignore_states = apply(findings, ignore_rules, locale)
    detected_errors = sum(item["severity"] == "error" for item in findings)
    errors = sum(item["severity"] == "error" and item.get("suppression") != "active" for item in findings)
    warnings = sum(item["severity"] == "warning" for item in findings)
    incomplete = changed or any(value["status"] == "failed" for value in checkers.values())
    status = "incomplete" if incomplete else "blocked" if errors else "passed"
    if progress:
        progress(f"[{book_id}/{locale}] 完成：{status}，{errors} 个错误，{warnings} 个警告")
    checkers["mdoc"]["status"] = "blocked" if any(item["checker"] == "mdoc" and item["severity"] == "error" for item in findings) else "passed"
    checkers["pillow"]["status"] = "blocked" if any(item["rule"].startswith("image.") and item["severity"] == "error" for item in findings) else "passed"
    return {
        "schema_version": 1,
        "kind": "mdoc_check_report",
        "context": "task" if task_id else "book",
        "level": level,
        "options": {"internal_only": internal_only},
        "scope": {"kind": scope, "book": book_id, "locale": locale, "target": target, **({"task": task_id} if task_id else {})},
        "status": status,
        "checkers": checkers,
        "inputs": {
            "markdown": sorted(f"{locale}/{path}" for path in paths),
            "navigation": [f"{locale}/{book['navigation']['summary']}"],
            **resources,
            "configuration": [".mdoc/workspace.yaml", *["config/mdoc.yaml" if path == PROTOTYPE_CONFIG else path.relative_to(root).as_posix() for path in check_config_paths]],
        },
        "findings": findings,
        "ignores": ignore_states,
        "counts": {"detected_errors": detected_errors, "detected_warnings": warnings, "ignored_errors": detected_errors - errors, "ignored_warnings": sum(item["severity"] == "warning" and bool(item.get("ignore_id")) for item in findings), "effective_errors": errors, "effective_warnings": sum(item["severity"] == "warning" and not item.get("ignore_id") for item in findings), "stale_ignores": sum(item["status"] == "stale" for item in ignore_states)},
        "input_changed": changed,
        "created_at": int(time.time()),
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def run_selected(root: Path, selection_path: Path, level: str = "basic", internal_only: bool = False, progress=None) -> dict:
    from .reports import file_records, records_from_report, replace_file_records, workspace_root

    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if selection.get("schema_version") != 1 or selection.get("kind") != "mdoc_check_file_selection":
        raise ValueError("File selection must use schema_version 1 and kind mdoc_check_file_selection.")
    source_report = selection.get("source_report")
    files = selection.get("files")
    if not isinstance(source_report, str) or not isinstance(files, list) or not files or any(not isinstance(item, str) for item in files):
        raise ValueError("File selection requires source_report and a non-empty files list.")
    report_root = workspace_root(root).resolve()
    report_path = (report_root / Path(*PurePosixPath(source_report).parts)).resolve(); report_path.relative_to(report_root)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if selection.get("base_revision") != report.get("revision"):
        raise ValueError("Source report revision has changed; refresh the selection.")
    allowed = {item["path"] for item in file_records(report_path)}
    selected = sorted(set(files))
    outside = sorted(set(selected) - allowed)
    if outside:
        raise ValueError("Selected files are outside the source report: " + ", ".join(outside))
    config = load_workspace(root); scope = report["scope"]; book_id = scope.get("book")
    if not book_id:
        raise ValueError("File selection currently requires a book or task report.")
    physical_by_locale = {}
    if report["context"] == "task":
        from .tasks import candidate_files, load_task
        definition, _state, directory = load_task(root, scope["task"]); physical_by_locale = candidate_files(root, config, definition, directory)
    else:
        book = config["books"][book_id]
        for locale in book["locales"]:
            _book, locale_root, _language = book_context(root, config, book_id, locale)
            physical_by_locale[locale] = {path.relative_to(locale_root).as_posix(): path for path in locale_root.rglob("*") if path.is_file()}
    results = []; replacements = []; failures = []
    for locale in sorted({item.split("/", 1)[0] for item in selected}):
        locale_selected = [item for item in selected if item.startswith(locale + "/")]
        deleted = [item for item in locale_selected if next(value for value in file_records(report_path) if value["path"] == item)["existence"] == "deleted"]
        for display in deleted:
            logical = display.split("/", 1)[1]; findings = []
            _book, _locale_root, language = book_context(root, config, book_id, locale)
            effective_config, _ = check_config(root, language)
            if report["context"] == "task":
                from .model import finding
                from .tasks import load_task
                definition, _state_value, directory = load_task(root, scope["task"])
                bounded = [item for item in definition["manifest"] if item["action"] != "delete" and item["path"].lower().endswith((".md", ".markdown"))]
                for item in bounded:
                    source = directory / "staging" / item["locale"] / Path(*PurePosixPath(item["path"]).parts)
                    if not source.is_file():
                        _book, locale_root, _language = book_context(root, config, book_id, item["locale"]); source = locale_root / Path(*PurePosixPath(item["path"]).parts)
                    if source.is_file() and logical in source.read_text(encoding="utf-8", errors="replace"):
                        issue = finding("delete.reference-remains", "error", display, f"Deleted path is still referenced by {item['locale']}/{item['path']}", 1, 1); issue["book"] = book_id; findings.append(issue)
            findings = apply_rule_config(findings, effective_config)
            record = next(value for value in file_records(report_path) if value["path"] == display); record = {**record, "checked_at": int(time.time()), "counts": {"effective_errors": len(findings), "effective_warnings": 0, "ignored": 0, "findings": len(findings)}, "status": "blocked" if findings else "passed", "findings": findings}; replacements.append(record)
        markdown = [item.split("/", 1)[1] for item in locale_selected if item not in deleted and item.lower().endswith((".md", ".markdown"))]
        for start in range(0, len(markdown), 20):
            logical = markdown[start:start + 20]
            if not logical: continue
            try:
                if report["context"] == "task":
                    _book, formal_root, _language = book_context(root, config, book_id, locale)
                    baseline = _single(root, config, book_id, locale, "files", None, level, internal_only, physical_files={path.relative_to(formal_root).as_posix(): path for path in formal_root.rglob("*") if path.is_file()}, selected_paths=logical)
                    baseline_findings = Counter(_finding_identity(item) for item in baseline["findings"])
                    result = _single(root, config, book_id, locale, "files", None, level, internal_only, progress, physical_by_locale[locale], classification_paths=set(selected), baseline_findings=baseline_findings, classify_all_new=scope.get("mode") != "contributor", task_id=scope["task"], selected_paths=logical)
                else: result = _single(root, config, book_id, locale, "files", None, level, internal_only, progress, physical_by_locale[locale], selected_paths=logical)
                if result["status"] == "incomplete": raise ValueError("检查器未完整完成。")
                results.append(result); checked = {f"{locale}/{item}" for item in logical} | set(result.get("inputs", {}).get("images", [])) | set(result.get("inputs", {}).get("resources", [])); replacements.extend(item for item in records_from_report(root, result) if item["path"] in checked)
            except (OSError, ValueError, CheckerError) as exc: failures.extend(f"{locale}/{item}" for item in logical)
    updated = replace_file_records(report_path, replacements)
    return {"schema_version": 1, "kind": "mdoc_check_result", "status": "incomplete" if failures else updated["status"], "context": updated["context"], "revision": updated["revision"], "updated_files": len(replacements), "failed_files": len(failures), "failures": failures, "counts": updated["counts"], "report": {"context_id": source_report, "path": str(report_path)}}


def run(root: Path, book_id: str | None, locale: str | None, scope: str, target: str | None, level: str, internal_only: bool = False, progress=None, task_id: str | None = None, contributor_manifest: Path | None = None, skip_check: bool = False) -> dict:
    config = load_workspace(root)
    profile = (config.get("quality_gate") or {}).get("default_profile")
    if profile in {"standard", "release"}:
        raise ValueError("工作区仍使用旧 Quality Gate profile，请先将 default_profile 改为 basic 或 full。")
    if scope != "task" and any((task_id, contributor_manifest, skip_check)):
        raise ValueError("--task, --contributor-manifest and --skip-check are valid only for task scope.")
    if scope == "task":
        from .tasks import candidate_files, contributor_selection, load_task

        if not task_id or any((book_id, locale, target)):
            raise ValueError("task scope requires --task and does not accept --book, --locale or --target.")
        definition, _state, directory = load_task(root, task_id)
        task_book = definition["task"]["book"]
        if task_book not in config["books"]:
            raise ValueError("Task references an unknown book.")
        selected = contributor_selection(contributor_manifest.resolve(), task_id, definition) if contributor_manifest else None
        if skip_check:
            inputs = {"task": [f".mdoc/tasks/{task_id}/task.yaml", f".mdoc/tasks/{task_id}/task-state.json"]}
            if contributor_manifest:
                inputs["contributor_manifest"] = [str(contributor_manifest.resolve())]
            return {"schema_version": 1, "kind": "mdoc_check_report", "context": "task", "level": level, "options": {"internal_only": internal_only}, "scope": {"kind": "task", "task": task_id, "book": task_book, "mode": "contributor" if selected else "coordinator", **({"files": selected} if selected else {})}, "status": "skipped", "skip_reason": "user_requested", "findings": [], "inputs": inputs, "counts": {"detected_errors": 0, "detected_warnings": 0, "ignored_errors": 0, "ignored_warnings": 0, "effective_errors": 0, "effective_warnings": 0, "stale_ignores": 0}, "created_at": int(time.time()), "duration_ms": 0}
        candidates = candidate_files(root, config, definition, directory)
        if selected:
            selected_locales = {value.split("/", 1)[0] for value in selected}
            candidates = {locale: files for locale, files in candidates.items() if locale in selected_locales}
        task_images = {locale: {item["path"].replace("\\", "/") for item in definition["manifest"] if item["locale"] == locale and item["kind"] == "asset" and item["action"] != "delete"} for locale in candidates}
        changed = set(selected) if selected else {f"{item['locale']}/{item['path'].replace(chr(92), '/')}" for item in definition["manifest"]}
        authority_inputs = [directory / "task.yaml", directory / "task-state.json", *([contributor_manifest.resolve()] if contributor_manifest else [])]
        authority_before = _snapshot(authority_inputs)
        baseline_units = [_single(root, config, task_book, current_locale, "book", None, level, internal_only, forced_images=task_images[current_locale]) for current_locale in sorted(candidates)]
        baseline_findings = Counter(_finding_identity(item) for unit in baseline_units for item in unit["findings"])
        baseline_count = sum(baseline_findings.values())
        units = [_single(root, config, task_book, current_locale, "task", None, level, internal_only, progress, candidates[current_locale], task_images[current_locale], changed, baseline_findings, selected is None, task_id) for current_locale in sorted(candidates)]
        findings = sorted((item for unit in units for item in unit["findings"]), key=lambda item: (item["path"], item["line"], item["column"], item["rule"]))
        authority_changed = authority_before != _snapshot(authority_inputs)
        incomplete = authority_changed or any(unit["status"] == "incomplete" for unit in [*baseline_units, *units])
        errors = sum(unit["counts"]["effective_errors"] for unit in units)
        warnings = sum(unit["counts"]["effective_warnings"] for unit in units)
        ignore_states = [item for unit in units for item in unit.get("ignores", [])]
        inputs = {key: sorted({value for unit in units for value in unit["inputs"].get(key, [])}) for key in ("markdown", "navigation", "images", "resources", "configuration")}
        inputs["task"] = [f".mdoc/tasks/{task_id}/task.yaml", f".mdoc/tasks/{task_id}/task-state.json"]
        if contributor_manifest:
            inputs["contributor_manifest"] = [str(contributor_manifest.resolve())]
        return {"schema_version": 1, "kind": "mdoc_check_report", "context": "task", "level": level, "options": {"internal_only": internal_only}, "scope": {"kind": "task", "task": task_id, "book": task_book, "mode": "contributor" if selected else "coordinator", **({"files": selected} if selected else {})}, "status": "incomplete" if incomplete else "blocked" if errors else "passed", "units": units, "baseline": {"status": "incomplete" if any(unit["status"] == "incomplete" for unit in baseline_units) else "completed", "findings": baseline_count}, "findings": findings, "inputs": inputs, "ignores": ignore_states, "counts": {"detected_errors": sum(unit["counts"]["detected_errors"] for unit in units), "detected_warnings": sum(unit["counts"]["detected_warnings"] for unit in units), "ignored_errors": sum(unit["counts"]["ignored_errors"] for unit in units), "ignored_warnings": sum(unit["counts"]["ignored_warnings"] for unit in units), "effective_errors": errors, "effective_warnings": warnings, "stale_ignores": sum(item["status"] == "stale" for item in ignore_states)}, "input_changed": authority_changed or any(unit["input_changed"] for unit in units), "created_at": int(time.time()), "duration_ms": sum(unit["duration_ms"] for unit in [*baseline_units, *units])}
    if scope != "workspace":
        if not book_id or not locale:
            raise ValueError("page, section and book scopes require --book and --locale.")
        if scope in {"page", "section"} and not target:
            raise ValueError("page and section scopes require --target.")
        if scope == "book" and target:
            raise ValueError("book scope does not accept --target.")
        if book_id not in config["books"] or locale not in config["books"][book_id]["locales"]:
            raise ValueError("Unknown book or locale.")
        return _single(root, config, book_id, locale, scope, target, level, internal_only, progress)
    if book_id or locale or target:
        raise ValueError("workspace scope does not accept --book, --locale or --target.")
    started = time.monotonic()
    units = []
    scheduled = [(current_book, current_locale) for current_book, book in sorted(config["books"].items()) for current_locale in sorted(book["locales"])]
    for index, (current_book, current_locale) in enumerate(scheduled, 1):
        if progress:
            progress(f"工作区单元 {index}/{len(scheduled)}：{current_book}/{current_locale}")
        units.append(_single(root, config, current_book, current_locale, "book", None, level, internal_only, progress))
    status = "incomplete" if any(unit["status"] == "incomplete" for unit in units) else "blocked" if any(unit["status"] == "blocked" for unit in units) else "passed"
    findings = [item for unit in units for item in unit["findings"]]
    ignore_states = [item for unit in units for item in unit.get("ignores", [])]
    for current_book, book in sorted(config["books"].items()):
        navigation = {}
        for current_locale in sorted(book["locales"]):
            _, locale_root, _ = book_context(root, config, current_book, current_locale)
            navigation[current_locale] = summary_entries(locale_root / book["navigation"]["summary"])
        reference_locale = book["source_locale"]
        reference = navigation[reference_locale]
        for current_locale, entries in navigation.items():
            if current_locale != reference_locale and entries != reference:
                issue = finding("navigation.locale-parity", "warning", f"{current_locale}/{book['navigation']['summary']}", f"Navigation differs from source locale {reference_locale}.", 1, 1)
                issue["book"] = current_book
                _, locale_root, language = book_context(root, config, current_book, current_locale)
                rule_config, _ = check_config(root, language)
                if not apply_rule_config([issue], rule_config):
                    continue
                summary_logical = book["navigation"]["summary"].replace("\\", "/")
                from .ignores import annotate, apply, load as load_ignores

                annotate([issue], {summary_logical: locale_root / Path(*PurePosixPath(summary_logical).parts)})
                parity_ignores, _ignore_paths = load_ignores(root, language)
                ignore_states.extend(apply([issue], parity_ignores, current_locale))
                findings.append(issue)
    findings.sort(key=lambda item: (item["path"], item["line"], item["column"], 0 if item["severity"] == "error" else 1, item["rule"]))
    detected_errors = sum(item["severity"] == "error" for item in findings)
    detected_warnings = sum(item["severity"] == "warning" for item in findings)
    errors = sum(item["severity"] == "error" and item.get("suppression") != "active" for item in findings)
    warnings = sum(item["severity"] == "warning" and item.get("suppression") != "active" for item in findings)
    status = "incomplete" if any(unit["status"] == "incomplete" for unit in units) else "blocked" if errors else "passed"
    return {"schema_version": 1, "kind": "mdoc_check_report", "context": "workspace", "level": level, "options": {"internal_only": internal_only}, "scope": {"kind": "workspace"}, "status": status, "units": units, "findings": findings, "ignores": ignore_states, "counts": {"detected_errors": detected_errors, "detected_warnings": detected_warnings, "ignored_errors": detected_errors - errors, "ignored_warnings": detected_warnings - warnings, "effective_errors": errors, "effective_warnings": warnings, "stale_ignores": sum(item["status"] == "stale" for item in ignore_states)}, "created_at": int(time.time()), "duration_ms": round((time.monotonic() - started) * 1000)}


def doctor() -> dict:
    expected = {"markdownlint": "0.41.1", "markdown_it": "15.0.1", "cspell": "10.3.0", "vale": "3.20.0", "pillow": "12.2.0"}
    try:
        actual = tool_versions()
        mismatches = {name: {"expected": version, "actual": actual.get(name)} for name, version in expected.items() if actual.get(name) != version}
    except CheckerError as exc:
        return {"schema_version": 1, "kind": "mdoc_check_doctor", "status": "incomplete", "error": str(exc)}
    smoke = {}
    if not mismatches:
        import tempfile
        from PIL import Image

        from .domain import inspect

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Main").mkdir()
            (root / "images").mkdir()
            page = root / "Main" / "Smoke.md"
            page.write_text("# Smoke\n\nUse sentnce.", encoding="utf-8")
            Image.new("RGB", (2, 2), "white").save(root / "images" / "Smoke.png")
            displays = {page: "en/Main/Smoke.md"}
            try:
                smoke["markdownlint"] = bool(markdownlint([page], displays))
                smoke["vale"] = isinstance(vale([page], displays), list)
                smoke["cspell"] = bool(cspell([page], displays))
                from .brands import inspect as inspect_brands
                domain_findings, _ = inspect(root, "en", "en", ["Main/Smoke.md"], "Main", "images", "Summary.md", False)
                smoke["mdoc_pillow"] = isinstance(domain_findings, list)
                smoke["mdoc_brand"] = bool(inspect_brands("Use Lidar360MLS.", "en/Main/Smoke.md", {"brands": [{"canonical": "LiDAR360MLS", "variants": {"Lidar360MLS": [Path("check-brands.yaml")]}}]}, {Path("check-brands.yaml"): ".mdoc/check-brands.yaml"}))
            except CheckerError as exc:
                return {"schema_version": 1, "kind": "mdoc_check_doctor", "status": "incomplete", "versions": actual, "mismatches": mismatches, "error": str(exc)}
    return {"schema_version": 1, "kind": "mdoc_check_doctor", "status": "passed" if not mismatches and all(smoke.values()) else "incomplete", "versions": actual, "mismatches": mismatches, "smoke": smoke}


def store(report: dict, workspace: Path | None = None) -> dict:
    if workspace is not None:
        from .reports import store_full
        from .web import create_launcher

        result = store_full(report, workspace)
        create_launcher(workspace)
        return result
    root = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "mdoc" / "reports"
    workspace_id = "doctor"
    context = report.get("context", report["kind"])
    directory = root / workspace_id / context
    scope = report.get("scope") or {}
    if context == "book":
        directory /= Path(scope["book"]) / scope["locale"] / scope["kind"]
        if scope.get("target"):
            from .model import digest
            directory /= digest(scope["target"])[:12]
    elif context == "task":
        directory /= Path(scope["task"]) / scope.get("mode", "coordinator")
        if scope.get("files"):
            from .model import digest
            directory /= digest("\n".join(scope["files"]))[:12]
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.time_ns()
    historical = directory / f"{stamp}.json"
    report["path"] = str(historical)
    findings = report.get("findings", [])
    findings_name = f"{stamp}.findings.jsonl"
    findings_payload = "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in findings)
    (directory / findings_name).write_text(findings_payload, encoding="utf-8")
    compact = {key: value for key, value in report.items() if key != "findings"}
    if compact.get("units"):
        compact["units"] = [{key: value for key, value in unit.items() if key not in {"findings", "inputs", "ignores"}} for unit in compact["units"]]
    compact["storage"] = {"format": "split-jsonl-v1", "findings": findings_name}
    historical.write_text(json.dumps(compact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    latest = {**compact, "storage": {"format": "split-jsonl-v1", "findings": "latest.findings.jsonl"}}
    (directory / "latest.findings.jsonl").write_text(findings_payload, encoding="utf-8")
    (directory / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metadata = {key: compact.get(key) for key in ("schema_version", "kind", "context", "status", "created_at", "scope", "counts")}
    metadata["finding_count"] = len(findings)
    (directory / "latest.meta.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    histories = sorted((path for path in directory.glob("[0-9]*.json")), key=lambda path: path.name)
    for expired in histories[:-10]:
        expired_findings = expired.with_name(expired.stem + ".findings.jsonl")
        expired.unlink()
        expired_findings.unlink(missing_ok=True)
    return report
