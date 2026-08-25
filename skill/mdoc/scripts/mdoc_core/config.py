from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .errors import MdocError
from .io import canonical_digest, inside, read_yaml, relative_path
from .models import TaskContext, WorkspaceContext, freeze

try:
    from jsonschema import Draft202012Validator
except ImportError as exc:  # pragma: no cover
    raise MdocError("MDOC-RUNTIME-DEPENDENCY-MISSING", "jsonschema is required by the mdoc runtime.") from exc


SKILL_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = SKILL_ROOT / "schemas"


def validate_schema(value: dict, schema_name: str, label: str) -> None:
    schema = json.loads((SCHEMA_ROOT / schema_name).read_text(encoding="utf-8"))
    findings = []
    for error in sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path)):
        location = ".".join(str(item) for item in error.path) or "$"
        findings.append({"path": location, "message": error.message})
    if findings:
        raise MdocError("MDOC-CONFIG-SCHEMA-INVALID", f"{label} does not match its schema.", {"findings": findings})


def _merge_local(base: dict, local: dict) -> dict:
    result = copy.deepcopy(base)
    if not local:
        return result
    allowed = {"applications", "resources", "runtimes", "ui"}
    unknown = set(local) - ({"schema_version"} | allowed)
    if unknown:
        raise MdocError("MDOC-LOCAL-OVERRIDE-INVALID", "workspace.local.yaml contains non-local fields.", {"fields": sorted(unknown)})
    result["local"] = {key: copy.deepcopy(local[key]) for key in allowed if key in local}
    return result


def locate_repository(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".mdoc" / "workspace.yaml").is_file():
            return candidate
    raise MdocError("MDOC-WORKSPACE-NOT-FOUND", f"No .mdoc/workspace.yaml found from: {start}")


def load_workspace(start: Path) -> WorkspaceContext:
    repository = locate_repository(start)
    control = repository / ".mdoc"
    portable = read_yaml(control / "workspace.yaml")
    local_path = control / "workspace.local.yaml"
    local = read_yaml(local_path) if local_path.is_file() else {"schema_version": 1}
    validate_schema(portable, "workspace.schema.json", "workspace.yaml")
    validate_schema(local, "workspace-local.schema.json", "workspace.local.yaml")
    normalized = _merge_local(portable, local)
    seen_rule_ids = set()
    for rule in normalized["quality_gate"].get("rules", ()):
        if rule["id"] in seen_rule_ids:
            raise MdocError("MDOC-QUALITY-RULE-DUPLICATE", f"Quality Gate rule id is duplicated: {rule['id']}")
        seen_rule_ids.add(rule["id"])
    for rule in normalized["quality_gate"].get("rules", ()):
        try:
            re.compile(rule["pattern"])
        except re.error as exc:
            raise MdocError("MDOC-QUALITY-RULE-INVALID", f"Invalid regex for Quality Gate rule: {rule['id']}", {"cause": str(exc)}) from exc
    for book_id, book in normalized["books"].items():
        if book["source_locale"] not in book["locales"]:
            raise MdocError("MDOC-BOOK-SOURCE-LOCALE-INVALID", f"Book source_locale is not declared: {book_id}")
        adapter = book.get("release_build_adapter")
        adapters = normalized["quality_gate"].get("build_adapters", {})
        if adapter and adapter not in adapters:
            raise MdocError("MDOC-BUILD-ADAPTER-MISSING", f"Book references an unknown release build adapter: {adapter}")
        if adapter and not adapters[adapter].get("artifact", "").lower().endswith(".pdf"):
            raise MdocError("MDOC-PDF-ARTIFACT-REQUIRED", "A book release adapter must produce a PDF artifact.", {"book": book_id})
        root = relative_path(book["root"], f"books.{book_id}.root")
        book_root = inside(repository, root)
        if not book_root.is_dir():
            raise MdocError("MDOC-BOOK-MISSING", f"Configured book root is missing: {book_id}", {"path": root.as_posix()})
        seen_locale_roots = set()
        for locale_id, locale in book["locales"].items():
            locale_path = relative_path(locale["root"], f"books.{book_id}.locales.{locale_id}.root")
            locale_root = inside(book_root, locale_path)
            if locale_root in seen_locale_roots:
                raise MdocError("MDOC-BOOK-LOCALE-ROOT-DUPLICATE", "Book locales must use distinct roots.", {"book": book_id, "locale": locale_id})
            seen_locale_roots.add(locale_root)
            if not locale_root.is_dir():
                raise MdocError("MDOC-BOOK-LOCALE-MISSING", f"Configured locale root is missing: {book_id}/{locale_id}", {"path": locale_path.as_posix()})
        relative_path(book["content_root"], f"books.{book_id}.content_root")
        relative_path(book["assets_root"], f"books.{book_id}.assets_root")
        relative_path(book["navigation"]["summary"], f"books.{book_id}.navigation.summary")
    return WorkspaceContext(repository, control, freeze(normalized), freeze(local), canonical_digest(normalized))


def task_directory(workspace: WorkspaceContext, task_id: str) -> Path:
    if not task_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in task_id):
        raise MdocError("MDOC-TASK-ID-INVALID", "Task id must use lowercase letters, digits, and hyphens.")
    return workspace.control / "tasks" / task_id


def validate_task_definition(
    workspace: WorkspaceContext,
    task_id: str,
    definition: dict,
    label: str = "task.yaml",
    *,
    check_initial_targets: bool = False,
) -> TaskContext:
    directory = task_directory(workspace, task_id)
    validate_schema(definition, "task.schema.json", label)
    if definition["task"]["id"] != task_id:
        raise MdocError("MDOC-TASK-ID-MISMATCH", "Task directory and task.yaml id differ.")
    if definition["quality_gate"]["profile"] != "release" and definition["quality_gate"].get("build_adapter"):
        raise MdocError("MDOC-BUILD-ADAPTER-UNEXPECTED", "Build adapters are only valid for release tasks.")
    book_id = definition["task"]["book"]
    if book_id not in workspace.config["books"]:
        raise MdocError("MDOC-TASK-BOOK-MISSING", f"Task references an unknown book: {book_id}")
    locales = set(workspace.config["books"][book_id]["locales"])
    if not set(definition["locales"]).issubset(locales):
        raise MdocError("MDOC-TASK-LOCALE-INVALID", "Task declares a locale outside the selected book.")
    for locale_id, strategies in definition["locales"].items():
        has_changes = any(item["locale"] == locale_id for item in definition["changes"])
        has_screenshots = any(locale_id in item["locales"] for item in definition["screenshots"])
        if not has_changes and not has_screenshots and any(value != "not_applicable" for value in strategies.values()):
            raise MdocError("MDOC-TASK-LOCALE-SCOPE-MISSING", "A participating locale must declare a file change or screenshot.", {"locale": locale_id})
    seen = set()
    for change in definition["changes"]:
        key = (change["locale"], change["path"].replace("\\", "/"))
        if key in seen:
            raise MdocError("MDOC-TASK-SCOPE-DUPLICATE", "Task change manifest contains a duplicate target.", {"target": key})
        if change["locale"] not in locales:
            raise MdocError("MDOC-TASK-LOCALE-INVALID", "Task change uses a locale outside the selected book.", {"locale": change["locale"]})
        if change["kind"] == "navigation" and change["path"].replace("\\", "/") != workspace.config["books"][book_id]["navigation"]["summary"].replace("\\", "/"):
            raise MdocError("MDOC-NAVIGATION-TARGET-INVALID", "Navigation changes must target the configured Summary file.", {"path": change["path"]})
        if change["action"] == "delete" and change["kind"] == "navigation":
            raise MdocError("MDOC-NAVIGATION-DELETE-FORBIDDEN", "Task manifests cannot delete the book navigation file.")
        locale_path = relative_path(workspace.config["books"][book_id]["locales"][change["locale"]]["root"], "locale.root")
        locale_directory = inside(inside(workspace.repository, relative_path(workspace.config["books"][book_id]["root"], "book.root")), locale_path)
        target = inside(locale_directory, relative_path(change["path"], "change.path"))
        if check_initial_targets:
            if change["action"] in {"update", "delete"} and not target.is_file():
                raise MdocError("MDOC-TASK-TARGET-MISSING", "Update and delete changes require an existing file.", {"target": f"{change['locale']}/{change['path']}"})
            if change["action"] == "create" and target.exists():
                raise MdocError("MDOC-TASK-TARGET-EXISTS", "Create changes require an absent target.", {"target": f"{change['locale']}/{change['path']}"})
        seen.add(key)
    change_targets = {(item["locale"], item["path"].replace("\\", "/")): item for item in definition["changes"]}
    screenshot_keys = set()
    for screenshot in definition["screenshots"]:
        for locale in screenshot["locales"]:
            key = (screenshot["id"], locale)
            if key in screenshot_keys:
                raise MdocError("MDOC-SCREENSHOT-DUPLICATE", "Screenshot manifest contains a duplicate id/locale pair.", {"id": screenshot["id"], "locale": locale})
            if locale not in workspace.config["books"][book_id]["locales"]:
                raise MdocError("MDOC-TASK-LOCALE-INVALID", "Screenshot locale is outside the selected book.", {"locale": locale})
            screenshot_keys.add(key)
            destination = screenshot["destinations"].get(locale)
            if locale not in definition["locales"] or not destination:
                raise MdocError("MDOC-SCREENSHOT-DESTINATION-MISSING", "Every screenshot locale needs a declared destination.", {"id": screenshot["id"], "locale": locale})
            target = change_targets.get((locale, destination.replace("\\", "/")))
            if Path(destination).name != screenshot["filename"]:
                raise MdocError("MDOC-SCREENSHOT-FILENAME-MISMATCH", "Screenshot destination filename must match the capture filename.", {"id": screenshot["id"], "locale": locale})
            if not target or target["action"] == "delete" or target["kind"] != "asset":
                raise MdocError("MDOC-SCREENSHOT-SCOPE-INVALID", "Screenshot destinations must be declared non-delete asset changes.", {"id": screenshot["id"], "locale": locale, "destination": destination})
    evidence_ids = {item["id"] for item in definition["evidence"]}
    if len(evidence_ids) != len(definition["evidence"]):
        raise MdocError("MDOC-TASK-EVIDENCE-DUPLICATE", "Evidence ids must be unique.")
    for change in definition["changes"]:
        missing = set(change.get("evidence", [])) - evidence_ids
        if missing:
            raise MdocError("MDOC-TASK-EVIDENCE-MISSING", "A change references unknown evidence.", {"ids": sorted(missing)})
        if change["kind"] == "page" and not change.get("evidence"):
            raise MdocError("MDOC-TASK-EVIDENCE-REQUIRED", "Every page change must cite at least one evidence item.", {"target": f"{change['locale']}/{change['path']}"})
    for field in ("content", "screenshots"):
        visiting, visited = set(), set()
        def visit(locale: str) -> None:
            if locale in visiting:
                raise MdocError("MDOC-TASK-LOCALE-CYCLE", f"Locale {field} copy strategies contain a cycle.")
            if locale in visited:
                return
            visiting.add(locale)
            strategy = definition["locales"][locale][field]
            if isinstance(strategy, dict):
                source = strategy["copy_from"]
                if source not in definition["locales"]:
                    raise MdocError("MDOC-TASK-LOCALE-SOURCE-MISSING", "Locale copy source is not declared.", {"locale": source, "field": field})
                visit(source)
            visiting.remove(locale)
            visited.add(locale)
        for locale in definition["locales"]:
            visit(locale)
    source_locale = workspace.config["books"][book_id]["source_locale"]
    for locale_id, strategies in definition["locales"].items():
        for field in ("content", "screenshots"):
            strategy = strategies[field]
            if strategy == "rewrite" and locale_id != source_locale:
                raise MdocError("MDOC-TASK-LOCALE-STRATEGY-INVALID", "Only the book source locale can use rewrite.", {"locale": locale_id, "field": field})
            if isinstance(strategy, dict) and strategy["copy_from"] == locale_id:
                raise MdocError("MDOC-TASK-LOCALE-CYCLE", "A locale cannot copy from itself.", {"locale": locale_id})
    for item in definition["evidence"]:
        namespace, name = item["binding"].split(".", 1)
        if item["required"] and name not in workspace.local.get(namespace, {}):
            raise MdocError("MDOC-TASK-EVIDENCE-BINDING-MISSING", "Required evidence binding is not present in workspace.local.yaml.", {"binding": item["binding"]})
    if any(item["action"] == "delete" for item in definition["changes"]) and not workspace.config["publishing"]["allow_deletions"]:
        raise MdocError("MDOC-DELETION-DISABLED", "This workspace does not allow deletion tasks.")
    if definition["quality_gate"]["profile"] == "release" and not definition["quality_gate"].get("build_adapter"):
        raise MdocError("MDOC-BUILD-ADAPTER-MISSING", "Release task requires quality_gate.build_adapter.")
    adapter = definition["quality_gate"].get("build_adapter")
    adapters = workspace.config["quality_gate"].get("build_adapters", {})
    if adapter and adapter not in adapters:
        raise MdocError("MDOC-BUILD-ADAPTER-MISSING", f"Task references an unknown build adapter: {adapter}")
    if definition["quality_gate"]["profile"] == "release" and not adapters[adapter].get("artifact", "").lower().endswith(".pdf"):
        raise MdocError("MDOC-PDF-ARTIFACT-REQUIRED", "Release Quality Gate requires a PDF artifact from its build adapter.")
    for item in definition["evidence"]:
        if item["critical"] and item["kind"] == "inference":
            raise MdocError("MDOC-TASK-EVIDENCE-INFERENCE-ONLY", "Critical evidence cannot be AI inference alone.", {"evidence": item["id"]})
    evidence_by_id = {item["id"]: item for item in definition["evidence"]}
    for change in definition["changes"]:
        key = f"{change['locale']}/{change['path']}"
        for evidence_id in change.get("evidence", []):
            supports = evidence_by_id[evidence_id]["supports"]
            if key not in supports and "*" not in supports:
                raise MdocError("MDOC-TASK-EVIDENCE-SCOPE-INVALID", "Change evidence does not declare support for its target.", {"target": key, "evidence": evidence_id})
    return TaskContext(workspace, task_id, directory, freeze(definition), canonical_digest(definition))


def load_task(workspace: WorkspaceContext, task_id: str) -> TaskContext:
    directory = task_directory(workspace, task_id)
    return validate_task_definition(workspace, task_id, read_yaml(directory / "task.yaml"))

