from __future__ import annotations

import shutil
import time
from collections.abc import Mapping

from .errors import MdocError
from .io import canonical_digest, file_digest, write_json_atomic
from .models import thaw
from .paths import changes


def _copy_source(task, locale: str, kind: str) -> str | None:
    if kind != "page" or locale == task.definition["locale_plan"]["source"]:
        return None
    strategy = task.definition["locale_plan"]["targets"][locale]["content"]
    return strategy.get("copy_from") if isinstance(strategy, Mapping) else None


def _agent_owned(task, change: dict) -> bool:
    return _copy_source(task, change["locale"], change["kind"]) is None


def prepare(task) -> dict:
    files = []
    for change, formal, staged in changes(task):
        if change["action"] == "update" and not staged.exists():
            if not formal.is_file():
                raise MdocError("MDOC-UPDATE-TARGET-MISSING", f"Update target is missing: {change['locale']}/{change['path']}")
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(formal, staged)
        if _agent_owned(task, change):
            files.append({
                "action": change["action"], "locale": change["locale"], "path": change["path"],
                "kind": change["kind"], "staging_path": str(staged),
            })
    local = task.workspace.local
    request_evidence = []
    for item in task.definition["evidence"]:
        binding = item["location"].split(":", 1)[1]
        value = thaw(local.get("resources", {}).get(binding) or local.get("applications", {}).get(binding))
        request_evidence.append({"id": item["id"], "kind": item["kind"], "location": item["location"], "value": value, "supports": list(item["supports"]), "critical": item["critical"]})
    request = {"schema_version": 1, "task_id": task.task_id, "definition_digest": task.digest, "files": files, "evidence": request_evidence}
    write_json_atomic(task.directory / "authoring-request.json", request)
    return request


def submit(task, state: dict) -> dict:
    manifest = {(item["locale"], item["path"]): item for item in task.definition["manifest"]}
    pending = {locale for locale in task.definition["locale_plan"]["targets"]}
    while pending:
        progressed = False
        for locale in list(pending):
            strategy = task.definition["locale_plan"]["targets"][locale]["content"]
            if not isinstance(strategy, Mapping):
                pending.remove(locale)
                progressed = True
                continue
            source_locale = strategy["copy_from"]
            if source_locale in pending:
                continue
            for change, _formal, staged in changes(task):
                if change["kind"] != "page" or change["locale"] != locale or change["action"] == "delete":
                    continue
                source_change = manifest.get((source_locale, change["path"]))
                if not source_change or source_change["kind"] != "page" or source_change["action"] == "delete":
                    raise MdocError("MDOC-COPY-SOURCE-MISSING", "copy_from 目标缺少同路径来源 manifest。", {"locale": locale, "source": source_locale, "path": change["path"]})
                source = next(source_staged for item, _source_formal, source_staged in changes(task) if item["locale"] == source_locale and item["path"] == change["path"])
                if not source.is_file():
                    raise MdocError("MDOC-AUTHORING-INCOMPLETE", "copy_from 来源文件尚未完成。", {"files": [f"{source_locale}/{change['path']}"]})
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, staged)
            pending.remove(locale)
            progressed = True
        if not progressed:
            raise MdocError("MDOC-TASK-LOCALE-CYCLE", "内容 copy_from 依赖存在循环。")
    expected, missing = {}, []
    for change, _formal, staged in changes(task):
        if change["action"] == "delete":
            continue
        if not staged.is_file():
            missing.append(f"{change['locale']}/{change['path']}")
        else:
            expected[f"{change['locale']}/{change['path']}"] = file_digest(staged)
    if missing:
        raise MdocError("MDOC-AUTHORING-INCOMPLETE", "Declared staging files are missing.", {"files": missing})
    staging = task.directory / "staging"
    allowed = {str(staged.resolve()) for change, _formal, staged in changes(task) if change["action"] != "delete"}
    extras = [str(path) for path in staging.rglob("*") if path.is_file() and str(path.resolve()) not in allowed]
    if extras:
        raise MdocError("MDOC-AUTHORING-OUT-OF-SCOPE", "Staging contains files outside the frozen manifest.", {"files": extras})
    submission = {"at": int(time.time()), "files": expected, "digest": canonical_digest(expected)}
    state["authoring_submission"] = submission
    return submission
