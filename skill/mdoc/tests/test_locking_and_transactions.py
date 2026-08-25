from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from skill.mdoc.scripts.mdoc_core.errors import MdocError
from skill.mdoc.scripts.mdoc_core.io import file_digest
from skill.mdoc.scripts.mdoc_core.locking import task_lock
from skill.mdoc.scripts.mdoc_core.transactions import execute, recover


class LockingAndTransactionTests(unittest.TestCase):
    def test_task_lock_rejects_live_owner_and_recovers_dead_owner(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            control = Path(root) / ".mdoc"
            task = SimpleNamespace(task_id="one", workspace=SimpleNamespace(control=control))
            lock = control / "locks" / "task-one.lock"
            lock.parent.mkdir(parents=True)
            lock.write_text(json.dumps({"schema_version": 1, "pid": os.getpid()}), encoding="utf-8")
            with self.assertRaises(MdocError) as caught:
                with task_lock(task):
                    pass
            self.assertEqual("MDOC-TASK-LOCKED", caught.exception.code)

            lock.write_text(json.dumps({"schema_version": 1, "pid": 2_147_483_647}), encoding="utf-8")
            with task_lock(task):
                self.assertTrue(lock.is_file())
            self.assertFalse(lock.exists())

    def test_failed_post_check_rolls_back_only_transaction_files(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "task"
            formal = Path(root) / "book" / "page.md"
            staged = directory / "staging" / "page.md"
            unrelated = Path(root) / "book" / "unrelated.md"
            formal.parent.mkdir(parents=True)
            directory.mkdir()
            staged.parent.mkdir(parents=True)
            formal.write_text("before", encoding="utf-8")
            staged.write_text("after", encoding="utf-8")
            unrelated.write_text("keep", encoding="utf-8")
            task = SimpleNamespace(task_id="one", directory=directory)
            state = {"publish": {"transactions": []}}
            plan = {"revision": 1, "operations": [{
                "action": "update", "target": "zh/page.md", "formal": str(formal), "staged": str(staged),
                "expected_before_sha256": file_digest(formal), "staged_sha256": file_digest(staged),
            }]}
            with self.assertRaises(MdocError):
                execute(task, state, plan, lambda: {"status": "blocked", "digest": "failed"})
            self.assertEqual("before", formal.read_text(encoding="utf-8"))
            self.assertEqual("keep", unrelated.read_text(encoding="utf-8"))
            self.assertEqual("rolled_back", state["publish"]["transactions"][0]["status"])

    def test_transaction_rejects_target_changed_after_plan(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "task"
            formal = Path(root) / "book" / "page.md"
            staged = directory / "staging" / "page.md"
            formal.parent.mkdir(parents=True)
            staged.parent.mkdir(parents=True)
            formal.write_text("baseline", encoding="utf-8")
            staged.write_text("candidate", encoding="utf-8")
            baseline = file_digest(formal)
            formal.write_text("external", encoding="utf-8")
            task = SimpleNamespace(task_id="one", directory=directory)
            state = {"publish": {"transactions": []}}
            plan = {"revision": 1, "operations": [{
                "action": "update", "target": "zh/page.md", "formal": str(formal), "staged": str(staged),
                "expected_before_sha256": baseline, "staged_sha256": file_digest(staged),
            }]}
            with self.assertRaises(MdocError) as caught:
                execute(task, state, plan, lambda: {"status": "passed"})
            self.assertEqual("MDOC-PUBLISH-CONFLICT", caught.exception.code)
            self.assertEqual("external", formal.read_text(encoding="utf-8"))

    def test_started_transaction_is_recovered_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "task"
            formal = Path(root) / "book" / "page.md"
            backup = directory / "transactions" / "tx" / "backups" / "zh" / "page.md"
            backup.parent.mkdir(parents=True)
            formal.parent.mkdir(parents=True)
            formal.write_text("partially published", encoding="utf-8")
            backup.write_text("before", encoding="utf-8")
            record = {"schema_version": 1, "id": "tx", "status": "started", "files": [{"target": str(formal), "backup": str(backup), "existed": True}]}
            path = directory / "transactions" / "tx" / "transaction.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            task = SimpleNamespace(directory=directory)
            state = {"publish": {"transactions": []}}
            recover(task, state)
            recover(task, state)
            self.assertEqual("before", formal.read_text(encoding="utf-8"))
            self.assertEqual(1, len(state["publish"]["transactions"]))
            self.assertEqual("rolled_back", json.loads(path.read_text(encoding="utf-8"))["status"])


if __name__ == "__main__":
    unittest.main()
