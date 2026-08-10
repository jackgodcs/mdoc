#!/usr/bin/env python3
"""Shared, package-bounded install/update transaction for mdoc."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path


class TransactionError(Exception):
    pass


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_dir():
        for item in sorted(path.rglob("*"), key=lambda value: value.as_posix().lower()):
            if item.is_file():
                digest.update(item.relative_to(path).as_posix().encode("utf-8"))
                digest.update(b"\0")
                digest.update(item.read_bytes())
        return digest.hexdigest()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_extract(package: Path, destination: Path) -> dict:
    shutil.rmtree(destination, ignore_errors=True)
    if package.is_dir():
        shutil.copytree(package, destination)
    else:
        with zipfile.ZipFile(package) as archive:
            for member in archive.infolist():
                name = member.filename.replace("\\", "/")
                if name.startswith("/") or (len(name) > 1 and name[1] == ":") or ".." in Path(name).parts:
                    raise TransactionError(f"MDOC-PACKAGE-UNSAFE: {member.filename}")
            archive.extractall(destination)
    manifest_path = destination / "PACKAGE-MANIFEST.json"
    if not manifest_path.is_file():
        raise TransactionError("MDOC-PACKAGE-MANIFEST-MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("product") != "mdoc" or manifest.get("platform") != "windows-x86_64":
        raise TransactionError("MDOC-PACKAGE-INCOMPATIBLE")
    root = destination.resolve()
    for item in manifest.get("files", []):
        target = (destination / item["path"]).resolve()
        if root not in target.parents or not target.is_file():
            raise TransactionError(f"MDOC-PACKAGE-FILE-MISSING: {item['path']}")
        if sha256(target) != item["sha256"]:
            raise TransactionError(f"MDOC-PACKAGE-SHA256-MISMATCH: {item['path']}")
    return manifest


def source_kind(executable: Path, runtime_root: Path) -> str:
    value = str(executable.resolve()).lower().replace("/", "\\")
    if value.startswith(str((runtime_root / "python").resolve()).lower().replace("/", "\\")):
        return "mdoc-managed"
    if "codex-runtimes" in value and "dependencies\\python" in value:
        return "codex-runtime"
    if "\\temp\\" in value or "e2e" in value:
        return "ineligible-temporary"
    return "system-or-user"


def acquire_lock(runtime_root: Path, operation: str) -> Path:
    root = runtime_root / ".repair"
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "install.lock"
    if lock.is_file():
        try:
            pid = int(json.loads(lock.read_text(encoding="utf-8")).get("pid") or 0)
        except Exception:
            pid = 0
        if pid_is_running(pid):
            raise TransactionError(f"MDOC-TRANSACTION-LOCKED: pid={pid}")
        lock.unlink(missing_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "operation": operation, "started_at": int(time.time())}), encoding="utf-8")
    return lock


def plan_path(root: Path) -> Path:
    return root / "state" / "plans" / "update-current.json"


def runtime_rebuild_decision(manifest: dict, root: Path) -> tuple[bool, list[str]]:
    contract = manifest.get("runtime_contract") or {}
    state_path = root / "state" / "installed-runtime.json"
    if not state_path.is_file():
        return True, ["installed_runtime_state_missing"]
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return True, ["installed_runtime_state_untrusted"]
    checks = (
        ("toolchain_version", "toolchain_version", "toolchain_version_changed"),
        ("python", "python_contract", "python_contract_changed"),
        ("profile", "profile", "profile_changed"),
        ("requirements_sha256", "requirements_sha256", "requirements_changed"),
    )
    reasons = [reason for package_key, state_key, reason in checks if contract.get(package_key) != state.get(state_key)]
    if state.get("capability_probe") != "ready":
        reasons.append("capability_probe_failed")
    if state.get("python_source") in {None, "ineligible-temporary", "unknown-private"}:
        reasons.append("python_source_ineligible")
    if contract.get("runtime_rebuild_required") is True:
        reasons.append("package_requires_runtime_rebuild")
    return bool(reasons), reasons


def create_plan(package: Path, installation: Path, root: Path, operation: str) -> dict:
    staging = root / ".repair" / "plan-package"
    manifest = safe_extract(package, staging)
    runtime_rebuild, reasons = runtime_rebuild_decision(manifest, root)
    data = {"schema_version": 1, "status": "planned", "operation": operation, "package": str(package.resolve()), "package_sha256": sha256(package), "installation": str(installation.resolve()), "version": manifest["version"], "runtime_rebuild": runtime_rebuild, "runtime_rebuild_reasons": reasons}
    target = plan_path(root); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(staging, ignore_errors=True)
    return data


def apply_plan(root: Path, confirm: bool) -> dict:
    if not confirm:
        raise TransactionError("MDOC-CONFIRMATION-REQUIRED")
    target = plan_path(root)
    if not target.is_file():
        raise TransactionError("MDOC-PLAN-MISSING")
    plan = json.loads(target.read_text(encoding="utf-8"))
    if plan.get("runtime_rebuild"):
        raise TransactionError("MDOC-RUNTIME-REPAIR-REQUIRED: runtime must be repaired before skill switch")
    package = Path(plan["package"]); installation = Path(plan["installation"])
    if not package.exists() or sha256(package) != plan["package_sha256"]:
        raise TransactionError("MDOC-PLAN-STALE")
    lock = acquire_lock(root, plan["operation"])
    run = root / ".repair" / "runs" / f"{int(time.time())}-{os.getpid()}"
    active = root / ".repair" / "active-run.json"
    active.write_text(json.dumps({"run": str(run), "pid": os.getpid()}), encoding="utf-8")
    backup = run / "installation.old"
    try:
        manifest = safe_extract(package, run / "package")
        source = run / "package" / "skill" / "mdoc"
        if not (source / "SKILL.md").is_file():
            raise TransactionError("MDOC-PACKAGE-SKILL-MISSING")
        new = run / "installation.new"
        shutil.copytree(source, new)
        support = new / "runtime-support"; support.mkdir(exist_ok=True)
        for name in ("runtime-bootstrap", "bootstrap", "runtime"):
            source_support = run / "package" / name
            if source_support.exists():
                shutil.copytree(source_support, support / name, dirs_exist_ok=True)
        repair = run / "package" / "repair-mdoc-runtime.ps1"
        if repair.is_file(): shutil.copy2(repair, support / repair.name)
        if installation.exists(): shutil.move(str(installation), backup)
        installation.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(new), installation)
        except Exception:
            if backup.exists() and not installation.exists(): shutil.move(str(backup), installation)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        result = {"schema_version": 1, "status": "updated" if plan["operation"] == "update" else "installed", "version": manifest["version"], "installation": str(installation)}
        record = root / "state" / "records" / "latest-update.json"; record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        target.unlink(missing_ok=True)
        return result
    finally:
        active.unlink(missing_ok=True); lock.unlink(missing_ok=True); shutil.rmtree(run, ignore_errors=True)


def cancel(root: Path, confirm: bool) -> dict:
    if not confirm: raise TransactionError("MDOC-CONFIRMATION-REQUIRED")
    active = root / ".repair" / "active-run.json"
    if not active.is_file(): return {"status": "nothing_to_cancel"}
    data = json.loads(active.read_text(encoding="utf-8")); pid = int(data.get("pid") or 0)
    if pid_is_running(pid):
        raise TransactionError("MDOC-TRANSACTION-ACTIVE")
    shutil.rmtree(Path(data.get("run", "")), ignore_errors=True); active.unlink(missing_ok=True)
    (root / ".repair" / "install.lock").unlink(missing_ok=True)
    return {"status": "cancelled"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation", choices=("install", "update", "cancel"), required=True)
    parser.add_argument("--package", type=Path); parser.add_argument("--installation", type=Path); parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--plan", action="store_true"); parser.add_argument("--apply", action="store_true"); parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.operation == "cancel": result = cancel(args.runtime_root, args.confirm)
        elif args.plan: result = create_plan(args.package, args.installation, args.runtime_root, args.operation)
        elif args.apply: result = apply_plan(args.runtime_root, args.confirm)
        else: raise TransactionError("MDOC-TRANSACTION-MODE-REQUIRED")
        print(json.dumps(result, ensure_ascii=False)); return 0
    except TransactionError as exc:
        print(str(exc), file=sys.stderr); return 2


if __name__ == "__main__": raise SystemExit(main())
