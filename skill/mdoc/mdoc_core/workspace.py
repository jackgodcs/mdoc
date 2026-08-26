from __future__ import annotations

import copy
import time
from pathlib import Path

from .config import validate_schema
from .errors import MdocError
from .io import canonical_digest, file_digest, read_json, read_yaml, write_json_atomic, write_yaml_atomic


CANDIDATE_NAME = "workspace-candidate.json"
LOCAL_CANDIDATE_NAME = "workspace-local-candidate.json"


def _control(workspace: Path) -> Path:
    return workspace.resolve() / ".mdoc"


def _authority_digest(path: Path) -> str | None:
    return file_digest(path) if path.is_file() else None


def _validate_paths(workspace: Path, value: dict) -> None:
    root = workspace.resolve()
    for book_id, book in value["books"].items():
        book_root = (root / book["root"]).resolve()
        try:
            book_root.relative_to(root)
        except ValueError as exc:
            raise MdocError("MDOC-PATH-UNSAFE", f"书册路径越出工作区：{book_id}") from exc
        if not book_root.is_dir():
            raise MdocError("MDOC-BOOK-MISSING", f"书册目录不存在：{book_id}", {"path": book["root"]})
        if book["source_locale"] not in book["locales"]:
            raise MdocError("MDOC-BOOK-SOURCE-LOCALE-INVALID", f"书册源语言未注册：{book_id}")
        seen: set[Path] = set()
        for locale_id, locale in book["locales"].items():
            locale_root = (book_root / locale["root"]).resolve()
            try:
                locale_root.relative_to(book_root)
            except ValueError as exc:
                raise MdocError("MDOC-PATH-UNSAFE", f"语言路径越出书册：{book_id}/{locale_id}") from exc
            if locale_root in seen:
                raise MdocError("MDOC-BOOK-LOCALE-ROOT-DUPLICATE", f"语言目录重复：{book_id}/{locale_id}")
            if not locale_root.is_dir():
                raise MdocError("MDOC-BOOK-LOCALE-MISSING", f"语言目录不存在：{book_id}/{locale_id}")
            seen.add(locale_root)


def validate_portable(workspace: Path, value: dict) -> dict:
    validate_schema(value, "workspace.schema.json", "workspace-draft.yaml")
    normalized = copy.deepcopy(value)
    _validate_paths(workspace, normalized)
    return normalized


def _draft_template() -> dict:
    return {
        "schema_version": 1,
        "workspace": {"id": "replace-me", "formal_vcs": "none"},
        "product": {"id": "replace-me", "display_name": "Replace me"},
        "books": {
            "replace-me": {
                "root": "replace-me",
                "source_locale": "zh",
                "locales": {"zh": {"root": "zh", "language": "zh"}},
                "content_root": "Main",
                "assets_root": "images",
                "navigation": {"summary": "Summary.md"},
            }
        },
        "locales": {},
        "writing": {},
        "screenshots": {"auto_open_assistant": True},
        "quality_gate": {"default_profile": "standard", "required_reviews": [], "safe_fixes": True, "rules": []},
        "publishing": {"allow_deletions": False},
        "generators": {},
        "build_adapters": {},
        "retention": {},
    }


def init(workspace: Path) -> dict:
    control = _control(workspace)
    draft = control / "workspace-draft.yaml"
    authority = control / "workspace.yaml"
    candidate = control / "cache" / CANDIDATE_NAME
    if authority.exists():
        raise MdocError("MDOC-WORKSPACE-EXISTS", "该目录已经包含 mdoc 工作区。")
    if draft.exists() or candidate.exists():
        raise MdocError("MDOC-WORKSPACE-DRAFT-EXISTS", "工作区草稿或候选配置已经存在。")
    write_yaml_atomic(draft, _draft_template())
    return {"status": "workspace_draft_created", "draft": str(draft)}


def _diff(before: dict | None, after: dict) -> dict:
    if before is None:
        return {"kind": "create", "changed_sections": sorted(after)}
    keys = sorted(set(before) | set(after))
    return {"kind": "revise", "changed_sections": [key for key in keys if before.get(key) != after.get(key)]}


def _active_task_references(control: Path, authority: dict) -> list[dict]:
    tasks = control / "tasks"
    if not tasks.is_dir():
        return []
    references: list[dict] = []
    for directory in sorted(path for path in tasks.iterdir() if path.is_dir()):
        definition_path = directory / "task.yaml"
        if not definition_path.is_file():
            continue
        state_path = directory / "task-state.json"
        if state_path.is_file():
            state = read_json(state_path)
            if state.get("status") in {"accepted", "cancelled"}:
                continue
        definition = read_yaml(definition_path)
        task = definition.get("task", {})
        book_id = task.get("book")
        record = {
            "task_id": task.get("id", directory.name),
            "book": book_id,
            "locales": sorted(_task_locales(definition)),
            "generator": (definition.get("generator") or {}).get("id"),
            "build_adapter": (definition.get("quality_gate") or {}).get("build_adapter"),
            "reviews": sorted((definition.get("quality_gate") or {}).get("required_reviews", [])),
        }
        if book_id in authority.get("books", {}):
            book_adapter = authority["books"][book_id].get("release_build_adapter")
            if record["build_adapter"] is None and book_adapter:
                record["build_adapter"] = book_adapter
        references.append(record)
    return references


def _task_locales(definition: dict) -> set[str]:
    locales = set((definition.get("scope") or {}).get("locales", []))
    plan = definition.get("locale_plan") or {}
    if plan.get("source"):
        locales.add(plan["source"])
    locales.update((plan.get("targets") or {}).keys())
    for item in definition.get("manifest", []):
        if item.get("locale"):
            locales.add(item["locale"])
    for screenshot in definition.get("screenshots", []):
        locales.update(screenshot.get("locales", []))
        locales.update((screenshot.get("destinations") or {}).keys())
    return locales


def _reject_removed_active_references(control: Path, before: dict | None, after: dict) -> None:
    if before is None:
        return
    active = _active_task_references(control, before)
    if not active:
        return
    after_books = after.get("books", {})
    after_generators = after.get("generators", {})
    after_build_adapters = after.get("build_adapters", {})
    after_reviews = set(after.get("quality_gate", {}).get("required_reviews", []))
    after_rules = {rule["id"] for rule in after.get("quality_gate", {}).get("rules", [])}
    before_rules = {rule["id"] for rule in before.get("quality_gate", {}).get("rules", [])}
    before_reviews = set(before.get("quality_gate", {}).get("required_reviews", []))
    removed_global_rules = sorted(before_rules - after_rules)
    removed_global_reviews = sorted(before_reviews - after_reviews)
    conflicts: list[dict] = []
    for reference in active:
        book_id = reference["book"]
        task_id = reference["task_id"]
        if book_id not in after_books:
            conflicts.append({"task_id": task_id, "kind": "book", "id": book_id})
            continue
        after_locales = set(after_books[book_id].get("locales", {}))
        for locale in reference["locales"]:
            if locale not in after_locales:
                conflicts.append({"task_id": task_id, "kind": "locale", "book": book_id, "id": locale})
        generator_id = reference.get("generator")
        if generator_id and generator_id not in after_generators:
            conflicts.append({"task_id": task_id, "kind": "generator", "id": generator_id})
        adapter_id = reference.get("build_adapter")
        if adapter_id and adapter_id not in after_build_adapters:
            conflicts.append({"task_id": task_id, "kind": "build_adapter", "id": adapter_id})
        for review in reference.get("reviews", []):
            if review not in after_reviews:
                conflicts.append({"task_id": task_id, "kind": "review", "id": review})
        for rule_id in removed_global_rules:
            conflicts.append({"task_id": task_id, "kind": "quality_rule", "id": rule_id})
        for review in removed_global_reviews:
            conflicts.append({"task_id": task_id, "kind": "required_review", "id": review})
    if conflicts:
        raise MdocError("MDOC-WORKSPACE-ACTIVE-TASK-REFERENCE", "候选配置删除了未结束任务仍引用的对象。", {"conflicts": conflicts})


def apply(workspace: Path) -> dict:
    control = _control(workspace)
    draft_path = control / "workspace-draft.yaml"
    authority_path = control / "workspace.yaml"
    draft = read_yaml(draft_path)
    normalized = validate_portable(workspace.resolve(), draft)
    authority = read_yaml(authority_path) if authority_path.is_file() else None
    _reject_removed_active_references(control, authority, normalized)
    candidate = {
        "schema_version": 1,
        "kind": "workspace_candidate",
        "draft_digest": canonical_digest(draft),
        "authority_digest": _authority_digest(authority_path),
        "normalized_digest": canonical_digest(normalized),
        "normalized": normalized,
        "diff": _diff(authority, normalized),
        "created_at": int(time.time()),
    }
    candidate_path = control / "cache" / CANDIDATE_NAME
    write_json_atomic(candidate_path, candidate)
    return {"status": "waiting_for_workspace_confirmation", "candidate": str(candidate_path), "digest": candidate["normalized_digest"], "diff": candidate["diff"]}


def confirm(workspace: Path) -> dict:
    control = _control(workspace)
    draft_path = control / "workspace-draft.yaml"
    authority_path = control / "workspace.yaml"
    candidate_path = control / "cache" / CANDIDATE_NAME
    candidate = read_json(candidate_path)
    if candidate.get("kind") != "workspace_candidate":
        raise MdocError("MDOC-WORKSPACE-CANDIDATE-INVALID", "工作区候选配置无效。")
    draft = read_yaml(draft_path)
    stale = (
        candidate.get("draft_digest") != canonical_digest(draft)
        or candidate.get("authority_digest") != _authority_digest(authority_path)
        or candidate.get("normalized_digest") != canonical_digest(candidate.get("normalized"))
    )
    if stale:
        raise MdocError("MDOC-WORKSPACE-CANDIDATE-STALE", "草稿或权威配置已变化，请重新执行 workspace apply。")
    normalized = validate_portable(workspace.resolve(), candidate["normalized"])
    if canonical_digest(normalized) != candidate["normalized_digest"]:
        raise MdocError("MDOC-WORKSPACE-CANDIDATE-STALE", "规范化结果已变化，请重新执行 workspace apply。")
    write_yaml_atomic(authority_path, normalized)
    candidate_path.unlink(missing_ok=True)
    draft_path.unlink(missing_ok=True)
    return {"status": "workspace_ready", "workspace": str(workspace.resolve()), "digest": canonical_digest(normalized)}


def revise(workspace: Path) -> dict:
    control = _control(workspace)
    authority = control / "workspace.yaml"
    draft = control / "workspace-draft.yaml"
    candidate = control / "cache" / CANDIDATE_NAME
    if draft.exists() or candidate.exists():
        raise MdocError("MDOC-WORKSPACE-DRAFT-EXISTS", "工作区草稿或候选配置已经存在。")
    value = read_yaml(authority)
    validate_portable(workspace.resolve(), value)
    write_yaml_atomic(draft, value)
    return {"status": "workspace_draft_created", "draft": str(draft), "authority_digest": file_digest(authority)}


def local_init(workspace: Path) -> dict:
    control = _control(workspace)
    draft = control / "workspace.local-draft.yaml"
    authority = control / "workspace.local.yaml"
    candidate = control / "cache" / LOCAL_CANDIDATE_NAME
    if draft.exists() or candidate.exists() or authority.exists():
        raise MdocError("MDOC-WORKSPACE-LOCAL-DRAFT-EXISTS", "本机配置草稿、候选或权威配置已经存在。")
    write_yaml_atomic(draft, {"schema_version": 1, "applications": {}, "resources": {}, "runtimes": {}})
    return {"status": "workspace_local_draft_created", "draft": str(draft)}


def _validate_local(value: dict) -> dict:
    validate_schema(value, "workspace-local.schema.json", "workspace.local-draft.yaml")
    return copy.deepcopy(value)


def local_apply(workspace: Path) -> dict:
    control = _control(workspace)
    draft_path = control / "workspace.local-draft.yaml"
    authority_path = control / "workspace.local.yaml"
    draft = read_yaml(draft_path)
    normalized = _validate_local(draft)
    candidate = {
        "schema_version": 1,
        "kind": "workspace_local_candidate",
        "draft_digest": canonical_digest(draft),
        "authority_digest": _authority_digest(authority_path),
        "normalized_digest": canonical_digest(normalized),
        "normalized": normalized,
        "created_at": int(time.time()),
    }
    candidate_path = control / "cache" / LOCAL_CANDIDATE_NAME
    write_json_atomic(candidate_path, candidate)
    return {"status": "waiting_for_workspace_local_confirmation", "candidate": str(candidate_path), "digest": candidate["normalized_digest"]}


def local_confirm(workspace: Path) -> dict:
    control = _control(workspace)
    draft_path = control / "workspace.local-draft.yaml"
    authority_path = control / "workspace.local.yaml"
    candidate_path = control / "cache" / LOCAL_CANDIDATE_NAME
    candidate = read_json(candidate_path)
    draft = read_yaml(draft_path)
    stale = (
        candidate.get("kind") != "workspace_local_candidate"
        or candidate.get("draft_digest") != canonical_digest(draft)
        or candidate.get("authority_digest") != _authority_digest(authority_path)
        or candidate.get("normalized_digest") != canonical_digest(candidate.get("normalized"))
    )
    if stale:
        raise MdocError("MDOC-WORKSPACE-LOCAL-CANDIDATE-STALE", "本机配置草稿或权威配置已变化，请重新执行 workspace local apply。")
    normalized = _validate_local(candidate["normalized"])
    write_yaml_atomic(authority_path, normalized)
    candidate_path.unlink(missing_ok=True)
    draft_path.unlink(missing_ok=True)
    return {"status": "workspace_local_ready", "workspace": str(workspace.resolve()), "digest": canonical_digest(normalized)}


def local_revise(workspace: Path) -> dict:
    control = _control(workspace)
    authority = control / "workspace.local.yaml"
    draft = control / "workspace.local-draft.yaml"
    candidate = control / "cache" / LOCAL_CANDIDATE_NAME
    if draft.exists() or candidate.exists():
        raise MdocError("MDOC-WORKSPACE-LOCAL-DRAFT-EXISTS", "本机配置草稿或候选已经存在。")
    value = _validate_local(read_yaml(authority))
    write_yaml_atomic(draft, value)
    return {"status": "workspace_local_draft_created", "draft": str(draft), "authority_digest": file_digest(authority)}
