from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path

from .errors import MdocError
from .io import file_digest, read_json, write_json_atomic


def _remove(path: Path) -> None:
    if path.exists():
        try:
            path.chmod(path.stat().st_mode | stat.S_IWRITE)
        except OSError:
            pass
        path.unlink(missing_ok=True)


def _rollback(record: dict) -> None:
    for item in reversed(record.get("files", [])):
        target = Path(item["target"])
        if item["existed"]:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item["backup"], target)
        else:
            _remove(target)


def recover(task, state: dict) -> None:
    root = task.directory / "transactions"
    if not root.is_dir():
        return
    known = {item["id"] for item in state["publish"]["transactions"]}
    for path in sorted(root.glob("*/transaction.json")):
        record = read_json(path)
        if record.get("status") != "started":
            continue
        _rollback(record)
        record["status"] = "rolled_back"
        record["recovery"] = "interrupted_transaction"
        write_json_atomic(path, record)
        if record["id"] not in known:
            state["publish"]["transactions"].append(record)


def execute(task, state: dict, publish_plan: dict, post_check) -> dict:
    transaction_id = f"{time.time_ns()}-{publish_plan['revision']}"
    root = task.directory / "transactions" / transaction_id
    backups = root / "backups"
    record = {"schema_version": 1, "id": transaction_id, "revision": publish_plan["revision"], "status": "started", "files": [], "started_at": int(time.time())}
    write_json_atomic(root / "transaction.json", record)
    try:
        for operation in publish_plan["operations"]:
            target = Path(operation["formal"])
            current = file_digest(target) if target.is_file() else None
            if current != operation.get("expected_before_sha256"):
                raise MdocError("MDOC-PUBLISH-CONFLICT", "发布锁内目标已发生变化。", {"target": operation["target"]})
            if operation["action"] != "delete":
                staged = Path(operation["staged"])
                if not staged.is_file() or file_digest(staged) != operation.get("staged_sha256"):
                    raise MdocError("MDOC-PUBLISH-SOURCE-CHANGED", "发布锁内 staging 源已发生变化。", {"target": operation["target"]})
            backup = backups / operation["target"]
            existed = target.is_file()
            if existed:
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            record["files"].append({"target": str(target), "backup": str(backup) if existed else None, "existed": existed, "before_sha256": file_digest(target) if existed else None})
            write_json_atomic(root / "transaction.json", record)
            if operation["action"] == "delete":
                _remove(target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.{transaction_id}.tmp")
                shutil.copy2(staged, temporary)
                os.replace(temporary, target)
            record["files"][-1]["after_sha256"] = file_digest(target) if target.is_file() else None
            write_json_atomic(root / "transaction.json", record)
        report = post_check()
        if report["status"] != "passed":
            raise MdocError("MDOC-PUBLISHED-QUALITY-FAILED", "发布后的 Quality Gate 未通过。", {"report": report.get("digest")})
        record["status"] = "committed"
        record["report_digest"] = report.get("digest")
        record["committed_at"] = int(time.time())
    except Exception:
        _rollback(record)
        record["status"] = "rolled_back"
        record["rolled_back_at"] = int(time.time())
        write_json_atomic(root / "transaction.json", record)
        state["publish"]["transactions"].append(record)
        raise
    write_json_atomic(root / "transaction.json", record)
    state["publish"]["transactions"].append(record)
    return record
