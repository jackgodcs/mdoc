from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from ruamel.yaml import YAML


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _logical_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(f"Task path must stay inside its registered locale: {value}")
    return path.as_posix()


def load_task(root: Path, task_id: str) -> tuple[dict, dict, Path]:
    if not task_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in task_id):
        raise ValueError("Task id must use lowercase letters, digits and hyphens.")
    directory = root / ".mdoc" / "tasks" / task_id
    definition_path = directory / "task.yaml"
    state_path = directory / "task-state.json"
    definition = YAML(typ="safe").load(definition_path.read_text(encoding="utf-8"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if definition.get("schema_version") != 1 or state.get("schema_version") != 1:
        raise ValueError("Task must use schema_version 1.")
    if definition.get("task", {}).get("id") != task_id or state.get("task_id") != task_id:
        raise ValueError("Task id does not match its control directory.")
    expected = definition.get("definition_digest")
    authority = {key: value for key, value in definition.items() if key != "definition_digest"}
    if expected != _digest(authority):
        raise ValueError("Task definition digest is invalid.")
    confirmation = state.get("definition_confirmation") or {}
    if confirmation.get("digest") != expected:
        raise ValueError("Task definition is not confirmed or its confirmation is stale.")
    return definition, state, directory


def candidate_files(root: Path, workspace: dict, definition: dict, directory: Path) -> dict[str, dict[str, Path]]:
    book = workspace["books"][definition["task"]["book"]]
    result = {}
    task_locales = {item["locale"] for item in definition["manifest"]}
    for locale in sorted(task_locales):
        locale_config = book["locales"][locale]
        locale_root = (root / book["root"] / locale_config["root"]).resolve()
        result[locale] = {path.relative_to(locale_root).as_posix(): path for path in locale_root.rglob("*") if path.is_file()}
    missing = []
    for item in definition["manifest"]:
        locale, logical = item["locale"], _logical_path(item["path"])
        if locale not in result:
            raise ValueError(f"Task manifest path is outside its registered locale: {locale}/{logical}")
        if item["action"] == "delete":
            result[locale].pop(logical, None)
            continue
        staged = directory / "staging" / locale / Path(*PurePosixPath(logical).parts)
        if not staged.is_file():
            missing.append(f"{locale}/{logical}")
        else:
            result[locale][logical] = staged
    if missing:
        raise ValueError("Declared staging files are missing: " + ", ".join(sorted(missing)))
    return result


def candidate_file(root: Path, workspace: dict, definition: dict, directory: Path, locale: str, logical: str) -> Path:
    logical = _logical_path(logical)
    book = workspace["books"][definition["task"]["book"]]
    if locale not in book["locales"]:
        raise ValueError("Source locale is not registered by the task book.")
    manifest = next((item for item in definition["manifest"] if item["locale"] == locale and _logical_path(item["path"]) == logical), None)
    if manifest:
        if manifest["action"] == "delete":
            raise ValueError("Source is deleted by the task candidate.")
        path = directory / "staging" / locale / Path(*PurePosixPath(logical).parts)
        if not path.is_file():
            raise ValueError("Declared staging source is missing.")
        return path
    locale_root = (root / book["root"] / book["locales"][locale]["root"]).resolve()
    path = (locale_root / Path(*PurePosixPath(logical).parts)).resolve()
    path.relative_to(locale_root)
    if not path.is_file():
        raise ValueError("Source is not in the task candidate.")
    return path


def contributor_selection(path: Path, task_id: str, definition: dict) -> list[str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or value.get("kind") != "mdoc_check_contributor_manifest":
        raise ValueError("Contributor manifest must use schema_version 1 and kind mdoc_check_contributor_manifest.")
    if value.get("task_id") != task_id:
        raise ValueError("Contributor manifest task_id does not match --task.")
    files = value.get("files")
    if not isinstance(files, list) or not files or any(not isinstance(item, str) for item in files) or len(files) != len(set(files)):
        raise ValueError("Contributor manifest files must be a non-empty unique string list.")
    allowed = {f"{item['locale']}/{_logical_path(item['path'])}" for item in definition["manifest"] if item["action"] != "delete"}
    outside = sorted(set(files) - allowed)
    if outside:
        raise ValueError("Contributor manifest selects files outside the frozen task manifest: " + ", ".join(outside))
    return sorted(files)
