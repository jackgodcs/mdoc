from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import winreg
from pathlib import Path


class UninstallError(RuntimeError):
    pass


def full(path) -> Path:
    if path in (None, ""):
        raise UninstallError("MDOC-UNINSTALL-PATH-MISSING: An ownership path is missing.")
    return Path(path).expanduser().resolve()


def child(path, root) -> bool:
    try:
        candidate, parent = full(path), full(root)
        candidate.relative_to(parent)
        return candidate != parent
    except (ValueError, UninstallError):
        return False


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UninstallError(f"MDOC-UNINSTALL-STATE-INVALID: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise UninstallError(f"MDOC-UNINSTALL-STATE-INVALID: {path}")
    return value


def load_state(runtime: Path, destination, recovery: bool) -> dict:
    manifest = runtime / "state" / "uninstall.json"
    if manifest.is_file():
        return load_json(manifest)
    if not recovery:
        raise UninstallError("MDOC-UNINSTALL-MANIFEST-MISSING: Use the release package with -Recovery only for a verified installation.")
    installed = runtime / "state" / "installed-runtime.json"
    updated = runtime / "state" / "records" / "latest-update.json"
    if not installed.is_file() or not updated.is_file():
        raise UninstallError("MDOC-UNINSTALL-RECOVERY-UNTRUSTED: Runtime state or update record is missing.")
    runtime_state, record = load_json(installed), load_json(updated)
    installation = full(destination or record.get("installation"))
    if full(record.get("installation")) != installation or not (installation / "SKILL.md").is_file():
        raise UninstallError("MDOC-UNINSTALL-RECOVERY-UNTRUSTED: The installation marker does not match the update record.")
    return {"schema_version": 1, "installation_id": "recovery", "installation": str(installation), "runtime_root": str(runtime), "path_entry": runtime_state.get("path_entry"), "path_added_by_mdoc": True, "start_menu": runtime_state.get("start_menu"), "uninstall_registry": r"Software\Microsoft\Windows\CurrentVersion\Uninstall\mdoc", "python_ownership": runtime_state.get("python_ownership"), "python_root": str(runtime / "python"), "python_installer": runtime_state.get("python_installer")}


def validate(state: dict, runtime: Path) -> None:
    if state.get("schema_version") != 1 or not state.get("installation_id"):
        raise UninstallError("MDOC-UNINSTALL-MANIFEST-INVALID: Invalid ownership manifest.")
    installation = full(state.get("installation"))
    if full(state.get("runtime_root")) != runtime or not child(state.get("path_entry"), runtime):
        raise UninstallError("MDOC-UNINSTALL-PATH-UNSAFE: Runtime ownership paths are inconsistent.")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if installation == full(Path.home()) or (local_app_data and runtime == full(local_app_data)):
        raise UninstallError("MDOC-UNINSTALL-PATH-UNSAFE: Refusing to remove a profile root.")
    if installation.exists() and not (installation / "SKILL.md").is_file():
        raise UninstallError("MDOC-UNINSTALL-INSTALLATION-UNTRUSTED: SKILL.md was not found.")
    start_menu, app_data = state.get("start_menu"), os.environ.get("APPDATA")
    if start_menu and (not app_data or not child(start_menu, Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")):
        raise UninstallError("MDOC-UNINSTALL-START-MENU-UNSAFE: Start Menu ownership is inconsistent.")
    if state.get("python_ownership") == "managed-by-mdoc" and (not child(state.get("python_root"), runtime) or not child(state.get("python_installer"), runtime)):
        raise UninstallError("MDOC-UNINSTALL-PYTHON-UNSAFE: Managed Python paths are outside RuntimeRoot.")


def normalized_path(value: str) -> str | None:
    try:
        return os.path.normcase(str(full(value)))
    except (OSError, UninstallError):
        return None


def remove_user_path(entry: Path) -> bool:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return False
        expected, kept, removed = normalized_path(str(entry)), [], False
        for item in str(value).split(";"):
            if item and normalized_path(item) == expected:
                removed = True
            elif item:
                kept.append(item)
        if removed:
            winreg.SetValueEx(key, "Path", 0, kind, ";".join(kept))
    if removed:
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, None)
    return removed


def process_rows() -> list[dict]:
    command = ["powershell.exe", "-NoProfile", "-Command", "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode or not result.stdout.strip():
        return []
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else [value]


def ancestor_pids(rows: list[dict], pid: int) -> set[int]:
    parents = {int(row.get("ProcessId") or 0): int(row.get("ParentProcessId") or 0) for row in rows}
    result = {pid}
    while pid in parents and parents[pid] and parents[pid] not in result:
        pid = parents[pid]
        result.add(pid)
    return result


def owned_processes(state: dict) -> list[dict]:
    roots = (str(full(state["runtime_root"])).casefold(), str(full(state["installation"])).casefold())
    rows = process_rows()
    excluded = ancestor_pids(rows, os.getpid())
    return [row for row in rows if int(row.get("ProcessId") or 0) not in excluded and any(root in ((row.get("ExecutablePath") or "") + " " + (row.get("CommandLine") or "")).casefold() for root in roots)]


def retry_remove(path: Path, pending: list[str]) -> bool:
    if not path.exists():
        return False
    for delay in (0, 1, 2, 3, 4):
        try:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            return True
        except OSError:
            if delay:
                time.sleep(delay)
    pending.append(str(path))
    return False


def write_cleanup_script(path: Path, paths: list[str], delay: bool = False) -> None:
    lines = ["@echo off"]
    if delay:
        lines.append("ping 127.0.0.1 -n 3 > nul")
    lines.extend(f'rmdir /s /q "{item}"' for item in paths)
    lines.append(f'del /q "{path}"')
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))


def register_run_once(paths: list[str]) -> None:
    script = Path(tempfile.gettempdir()) / f"mdoc-uninstall-runonce-{uuid.uuid4().hex}.cmd"
    write_cleanup_script(script, paths)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce") as key:
        winreg.SetValueEx(key, f"mdoc-uninstall-{uuid.uuid4().hex}", 0, winreg.REG_SZ, f'cmd.exe /d /c "{script}"')


def runtime_cleanup_targets(runtime: Path, preserve_python: bool) -> list[str]:
    if not preserve_python:
        return [str(runtime)]
    preserved = {"python", "installers"}
    return [str(path) for path in runtime.iterdir() if path.name.casefold() not in preserved]


def emit(status: str, code: int, removed: list[str], pending: list[str], warnings: list[str], as_json: bool) -> int:
    for path in Path(tempfile.gettempdir()).glob("mdoc-uninstall-result-*.json"):
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            days = 1 if previous.get("status") == "uninstalled" else 7
            if path.stat().st_mtime < time.time() - days * 86400:
                path.unlink()
        except (OSError, json.JSONDecodeError):
            pass
    result_path = Path(tempfile.gettempdir()) / f"mdoc-uninstall-result-{uuid.uuid4().hex}.json"
    value = {"schema_version": 1, "status": status, "exit_code": code, "removed": removed, "pending": pending, "warnings": warnings, "result": str(result_path)}
    result_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(f"Status: {status}")
        for item in removed: print(f"Removed: {item}")
        for item in pending: print(f"Pending cleanup: {item}")
        for item in warnings: print(f"Warning: {item}", file=sys.stderr)
        print(f"Result: {result_path}")
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True); parser.add_argument("--destination", type=Path)
    parser.add_argument("--confirm", action="store_true"); parser.add_argument("--json", action="store_true"); parser.add_argument("--recovery", action="store_true")
    args = parser.parse_args(argv); runtime = full(args.runtime_root)
    try:
        state = load_state(runtime, args.destination, args.recovery); validate(state, runtime)
        if not args.json:
            print("mdoc uninstall plan:"); print(f"  Skill: {state['installation']}"); print(f"  Runtime and Toolchain: {state['runtime_root']}"); print(f"  Start menu: {state.get('start_menu') or 'not registered'}"); print("  Manual workspaces and their .mdoc directories will not be removed.")
        if not args.confirm and input("Continue? [Y/N] ").strip().casefold() != "y":
            return emit("cancelled", 2, [], [], ["No files were changed."], args.json)
        processes = owned_processes(state)
        if processes:
            if not args.json:
                for row in processes: print(f"mdoc process: {row.get('Name')} PID={row.get('ProcessId')}")
            if not args.confirm and input("Stop these mdoc processes and continue? [Y/N] ").strip().casefold() != "y":
                return emit("cancelled", 2, [], [], ["No files were changed."], args.json)
            for row in processes: subprocess.run(["taskkill", "/PID", str(row["ProcessId"]), "/T", "/F"], capture_output=True)
        removed, pending, warnings, partial = [], [], [], False
        if state.get("path_added_by_mdoc") and remove_user_path(full(state["path_entry"])): removed.append(f"PATH: {state['path_entry']}")
        start_menu = Path(state["start_menu"]) if state.get("start_menu") else None
        if start_menu and retry_remove(start_menu, pending): removed.append(str(start_menu))
        registry = state.get("uninstall_registry")
        if registry:
            try: winreg.DeleteKey(winreg.HKEY_CURRENT_USER, registry); removed.append(f"Registry: HKCU\\{registry}")
            except FileNotFoundError: pass
            except OSError as exc: warnings.append(f"Installed Apps entry could not be removed: {exc}"); partial = True
        if state.get("python_ownership") == "managed-by-mdoc":
            installer = Path(state["python_installer"])
            if installer.is_file():
                result = subprocess.run([str(installer), "/quiet", "/uninstall"]); partial = result.returncode != 0
                if partial: warnings.append(f"Managed Python uninstall failed with exit code {result.returncode}.")
            else: warnings.append("Managed Python installer is missing; registered Python was not force-deleted."); partial = True
        installation = full(state["installation"]); renamed = installation.with_name(installation.name + f".uninstalling-{uuid.uuid4().hex}")
        if installation.exists(): installation.rename(renamed); removed.append(str(installation))
        installing = installation.with_name(installation.name + ".installing")
        if (installing / "SKILL.md").is_file(): retry_remove(installing, pending)
        retry_remove(renamed, pending)
        for name in ("runtime.new", "runtime.old", "toolchain.new", "toolchain.old", ".repair"): retry_remove(runtime / name, pending)
        cleanup = Path(tempfile.gettempdir()) / f"mdoc-uninstall-runtime-{uuid.uuid4().hex}.cmd"; write_cleanup_script(cleanup, runtime_cleanup_targets(runtime, partial), True)
        subprocess.Popen(["cmd.exe", "/d", "/c", str(cleanup)], creationflags=0x08000000)
        if pending: register_run_once(pending)
        if partial: return emit("partial_uninstall", 5, removed, pending, warnings, args.json)
        if pending: return emit("pending_cleanup", 3, removed, pending, warnings, args.json)
        return emit("uninstalled", 0, removed, [], warnings, args.json)
    except Exception as exc:
        return emit("failed", 4, [], [], [str(exc)], args.json)


if __name__ == "__main__":
    raise SystemExit(main())
