from __future__ import annotations

import copy
from pathlib import Path

from .config import load_workspace, task_directory, validate_schema
from .errors import MdocError
from .io import canonical_digest, read_yaml, relative_path, write_yaml_atomic
from .state import initial_state, save_state, transition


INTENTS = {"create_module", "add_feature", "update_content", "add_locale"}


def _template(task_id: str, book: str, intent: str) -> dict:
    return {
        "schema_version": 1,
        "task": {"id": task_id, "book": book, "intent": intent, "title": "Replace me"},
        "scope": {
            "locales": [],
            "pages": {"create": [], "update": [], "delete": []},
            "assets": {"create": [], "update": [], "delete": []},
            "navigation": {"update": []},
        },
        "locale_plan": {"source": "", "targets": {}},
        "screenshots": [],
        "evidence": [],
        "quality_gate": {"profile": "standard", "required_reviews": []},
    }


def create(workspace_path: Path, task_id: str, book: str, intent: str) -> dict:
    workspace = load_workspace(workspace_path)
    directory = task_directory(workspace, task_id)
    if book not in workspace.config["books"]:
        raise MdocError("MDOC-TASK-BOOK-MISSING", f"任务引用了未注册书册：{book}")
    if intent not in INTENTS:
        raise MdocError("MDOC-TASK-INTENT-INVALID", f"任务 intent 无效：{intent}")
    if directory.exists():
        raise MdocError("MDOC-TASK-EXISTS", f"任务已经存在：{task_id}")
    directory.mkdir(parents=True)
    draft = directory / "task-draft.yaml"
    write_yaml_atomic(draft, _template(task_id, book, intent))
    return {"status": "task_draft_created", "task_id": task_id, "draft": str(draft)}


def _manifest(scope: dict) -> list[dict]:
    result: list[dict] = []
    for kind in ("page", "asset"):
        section = scope[f"{kind}s"]
        for action in ("create", "update", "delete"):
            for raw in section[action]:
                item = copy.deepcopy(raw)
                item.setdefault("evidence", [])
                item.update({"action": action, "kind": kind, "path": relative_path(item["path"], f"scope.{kind}s.{action}.path").as_posix()})
                result.append(item)
    for raw in scope["navigation"]["update"]:
        item = copy.deepcopy(raw)
        item.setdefault("evidence", [])
        item.update({"action": "update", "kind": "navigation", "path": relative_path(item["path"], "scope.navigation.update.path").as_posix()})
        result.append(item)
    result.sort(key=lambda item: ({"page": 0, "asset": 1, "navigation": 2}[item["kind"]], item["locale"], item["path"], item["action"]))
    return result


def _validate_locale_plan(draft: dict, book: dict) -> None:
    selected = set(draft["scope"]["locales"])
    plan = draft["locale_plan"]
    if plan["source"] != book["source_locale"] or plan["source"] not in selected:
        raise MdocError("MDOC-TASK-SOURCE-LOCALE-INVALID", "任务源语言必须等于书册 source_locale，并包含在 scope.locales 中。")
    if selected != {plan["source"], *plan["targets"]}:
        raise MdocError("MDOC-TASK-LOCALE-PLAN-INCOMPLETE", "scope.locales 必须与 locale_plan 的源语言和目标语言完全一致。")
    if not selected.issubset(book["locales"]):
        raise MdocError("MDOC-TASK-LOCALE-INVALID", "任务包含书册未注册的语言。")
    for field in ("content", "screenshots"):
        visiting: set[str] = set()
        visited: set[str] = {plan["source"]}

        def visit(locale: str) -> None:
            if locale in visiting:
                raise MdocError("MDOC-TASK-LOCALE-CYCLE", f"语言 {field} 依赖存在循环。")
            if locale in visited:
                return
            visiting.add(locale)
            strategy = plan["targets"][locale][field]
            if isinstance(strategy, dict):
                source = strategy["copy_from"]
                if source not in selected:
                    raise MdocError("MDOC-TASK-LOCALE-SOURCE-MISSING", f"语言复制来源未声明：{source}")
                if source != plan["source"]:
                    visit(source)
            visiting.remove(locale)
            visited.add(locale)

        for locale in plan["targets"]:
            visit(locale)


def _validate_manifest(workspace, draft: dict, manifest: list[dict]) -> None:
    book_id = draft["task"]["book"]
    book = workspace.config["books"][book_id]
    selected = set(draft["scope"]["locales"])
    seen: set[tuple[str, str]] = set()
    evidence = {item["id"]: item for item in draft["evidence"]}
    if len(evidence) != len(draft["evidence"]):
        raise MdocError("MDOC-TASK-EVIDENCE-DUPLICATE", "证据 ID 必须唯一。")
    for item in manifest:
        key = (item["locale"], item["path"])
        if key in seen:
            raise MdocError("MDOC-TASK-SCOPE-DUPLICATE", "任务 scope 包含重复目标。", {"target": "/".join(key)})
        if item["locale"] not in selected:
            raise MdocError("MDOC-TASK-LOCALE-INVALID", "manifest 文件使用了 scope 外语言。", {"locale": item["locale"]})
        if item["kind"] == "navigation" and item["path"] != book["navigation"]["summary"].replace(chr(92), "/"):
            raise MdocError("MDOC-NAVIGATION-TARGET-INVALID", "导航修改只能指向书册 Summary。")
        missing = set(item.get("evidence", [])) - set(evidence)
        if missing:
            raise MdocError("MDOC-TASK-EVIDENCE-MISSING", "manifest 引用了不存在的证据。", {"ids": sorted(missing)})
        if item["kind"] == "page" and not item.get("evidence"):
            raise MdocError("MDOC-TASK-EVIDENCE-REQUIRED", "每个页面必须关联至少一个证据。", {"target": "/".join(key)})
        locale_root = workspace.repository / book["root"] / book["locales"][item["locale"]]["root"]
        target = (locale_root / item["path"]).resolve()
        try:
            target.relative_to(locale_root.resolve())
        except ValueError as exc:
            raise MdocError("MDOC-PATH-UNSAFE", f"任务目标越出语言目录：{item['path']}") from exc
        if item["action"] == "create" and target.exists():
            raise MdocError("MDOC-TASK-TARGET-EXISTS", "create 目标已经存在。", {"target": "/".join(key)})
        if item["action"] in {"update", "delete"} and not target.is_file():
            raise MdocError("MDOC-TASK-TARGET-MISSING", "update/delete 目标不存在。", {"target": "/".join(key)})
        seen.add(key)
    for item in draft["evidence"]:
        if item["critical"] and item["kind"] == "inference":
            raise MdocError("MDOC-TASK-EVIDENCE-INFERENCE-ONLY", "关键事实不能只由 AI 推断支撑。")
        for target in item["supports"]:
            relative_path(target.split("/", 1)[1] if "/" in target else target, "evidence.supports")
    targets = {(item["locale"], item["path"]): item for item in manifest}
    for shot in draft["screenshots"]:
        for locale in shot["locales"]:
            destination = relative_path(shot["destinations"].get(locale, ""), "screenshots.destinations").as_posix()
            target = targets.get((locale, destination))
            if not target or target["kind"] != "asset" or target["action"] == "delete" or Path(destination).name != shot["filename"]:
                raise MdocError("MDOC-SCREENSHOT-SCOPE-INVALID", "截图目标必须是同语言、同文件名的非删除 asset manifest 项。")
    if any(item["action"] == "delete" for item in manifest) and not workspace.config["publishing"]["allow_deletions"]:
        raise MdocError("MDOC-DELETION-DISABLED", "工作区未允许删除任务。")


def define(workspace_path: Path, task_id: str) -> dict:
    workspace = load_workspace(workspace_path)
    directory = task_directory(workspace, task_id)
    draft = read_yaml(directory / "task-draft.yaml")
    validate_schema(draft, "task-draft.schema.json", "task-draft.yaml")
    if draft["task"]["id"] != task_id:
        raise MdocError("MDOC-TASK-ID-MISMATCH", "任务目录与草稿 ID 不一致。")
    book_id = draft["task"]["book"]
    if book_id not in workspace.config["books"]:
        raise MdocError("MDOC-TASK-BOOK-MISSING", f"任务引用了未注册书册：{book_id}")
    _validate_locale_plan(draft, workspace.config["books"][book_id])
    manifest = _manifest(draft["scope"])
    if not manifest:
        raise MdocError("MDOC-TASK-MANIFEST-EMPTY", "任务 manifest 不能为空。")
    _validate_manifest(workspace, draft, manifest)
    normalized = copy.deepcopy(draft)
    normalized["manifest"] = manifest
    normalized["definition_digest"] = canonical_digest(normalized)
    validate_schema(normalized, "task.schema.json", "task.yaml")
    write_yaml_atomic(directory / "task.yaml", normalized)
    state = initial_state(task_id)
    transition(state, "waiting_for_definition_confirmation", "task_defined", {"kind": "definition_confirmation", "digest": normalized["definition_digest"]})
    save_state(directory / "task-state.json", state)
    return {"status": state["status"], "task_id": task_id, "definition_digest": normalized["definition_digest"], "manifest": manifest}
