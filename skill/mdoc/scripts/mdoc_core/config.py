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
        if rule["kind"] == "review":
            continue
        try:
            re.compile(rule["pattern"])
        except re.error as exc:
            raise MdocError("MDOC-QUALITY-RULE-INVALID", f"Invalid regex for Quality Gate rule: {rule['id']}", {"cause": str(exc)}) from exc
    for book_id, book in normalized["books"].items():
        if book["source_locale"] not in book["locales"]:
            raise MdocError("MDOC-BOOK-SOURCE-LOCALE-INVALID", f"Book source_locale is not declared: {book_id}")
        adapter = book.get("release_build_adapter")
        adapters = normalized.get("build_adapters", {})
        if adapter and adapter not in adapters:
            raise MdocError("MDOC-BUILD-ADAPTER-MISSING", f"Book references an unknown release build adapter: {adapter}")
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


def load_task(workspace: WorkspaceContext, task_id: str) -> TaskContext:
    directory = task_directory(workspace, task_id)
    definition = read_yaml(directory / "task.yaml")
    validate_schema(definition, "task.schema.json", "task.yaml")
    if definition["task"]["id"] != task_id:
        raise MdocError("MDOC-TASK-ID-MISMATCH", "任务目录与 task.yaml ID 不一致。")
    expected = definition.pop("definition_digest")
    actual = canonical_digest(definition)
    definition["definition_digest"] = expected
    if expected != actual:
        raise MdocError("MDOC-TASK-DEFINITION-DIGEST-INVALID", "task.yaml 内容与 definition_digest 不一致。", {"expected": expected, "actual": actual})
    return TaskContext(workspace, task_id, directory, freeze(definition), expected)
