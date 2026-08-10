#!/usr/bin/env python3
"""Unified Windows-first command line entry point for mdoc."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import re
import importlib.util
import shutil
import platform
import subprocess
import importlib.util
from pathlib import Path


def product_version() -> str:
    source_version = Path(__file__).resolve().parents[3] / "VERSION"
    if source_version.is_file():
        return source_version.read_text(encoding="utf-8").strip()
    return "1.2.0-rc.1"


VERSION = product_version()
SCHEMA_VERSION = 2
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


class MdocError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def quote_yaml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def scalar(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    prefix = f"  {key}:"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            value = line.split(":", 1)[1].strip()
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value.strip("'\"")
    return None


def replace_scalar(path: Path, key: str, value: str) -> None:
    source = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"(?m)^(  {re.escape(key)}:)\s*.*$")
    updated, count = pattern.subn(rf"\1 {quote_yaml(value)}", source, count=1)
    if count != 1:
        raise MdocError("MDOC-WORKSPACE-CONFIG-INVALID", f"缺少配置字段：{key}")
    path.write_text(updated, encoding="utf-8", newline="\n")


def task_root(workspace: Path) -> Path:
    return workspace / "manual-tasks"


def task_info(workspace: Path, task_id: str) -> dict:
    task = task_root(workspace) / task_id / "task.yaml"
    if not task.is_file():
        raise MdocError("MDOC-TASK-NOT-FOUND", f"任务不存在：{task_id}")
    return {
        "id": scalar(task, "id") or task_id,
        "operation": scalar(task, "operation") or "",
        "target_book": scalar(task, "target_book") or "",
        "title": scalar(task, "source") or "",
        "path": str(task.parent.resolve()),
    }


def context(workspace: Path, operation_book: str | None = None) -> dict:
    config = workspace / "workspace.yaml"
    local = workspace / "workspace.local.yaml"
    if not config.is_file() or not local.is_file():
        raise MdocError("MDOC-WORKSPACE-NOT-INITIALIZED", f"工作区未初始化：{workspace}")
    active = scalar(config, "active_book")
    repository = scalar(local, "manual_repository")
    if not active or not repository:
        raise MdocError("MDOC-WORKSPACE-CONFIG-INVALID", "工作区缺少活动书册或正式仓库绑定。")
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": str(workspace.resolve()),
        "repository": str(Path(repository).resolve()),
        "active_book": active,
        "operation_book": operation_book or active,
    }


def write_registry(workspace: Path, repository: Path) -> None:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "mdoc"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "workspace-registry.json"
    data = {"schema_version": SCHEMA_VERSION, "workspaces": []}
    if target.is_file():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MdocError("MDOC-REGISTRY-CORRUPT", f"工作区注册表损坏，请运行 workspace registry repair：{target}") from exc
    entry = {"workspace": str(workspace.resolve()), "repository": str(repository.resolve())}
    entries = [item for item in data.get("workspaces", []) if item.get("workspace") != entry["workspace"]]
    data = {"schema_version": SCHEMA_VERSION, "workspaces": [entry, *entries][:50]}
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def registry_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "mdoc" / "workspace-registry.json"


def read_registry() -> dict:
    target = registry_path()
    if not target.is_file():
        return {"schema_version": SCHEMA_VERSION, "workspaces": []}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MdocError("MDOC-REGISTRY-CORRUPT", f"工作区注册表损坏，请运行 workspace registry repair：{target}") from exc
    if not isinstance(data.get("workspaces"), list):
        raise MdocError("MDOC-REGISTRY-CORRUPT", f"工作区注册表结构无效：{target}")
    return data


def atomic_write_json(target: Path, data: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def command_setup(args) -> dict:
    repository = args.repository.resolve()
    workspace = args.workspace.resolve()
    book_root = repository / args.book
    if not repository.is_dir():
        raise MdocError("MDOC-WORKSPACE-REPOSITORY-MISSING", f"正式仓库不存在：{repository}")
    if not book_root.is_dir():
        raise MdocError("MDOC-WORKSPACE-BOOK-MISSING", f"书册不存在：{book_root}")
    if (workspace / "workspace.yaml").exists() or (workspace / "workspace.local.yaml").exists():
        raise MdocError("MDOC-WORKSPACE-ALREADY-INITIALIZED", f"工作区已初始化，请使用 status/configure：{workspace}")
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "manual-tasks").mkdir(exist_ok=True)
    (workspace / ".work" / "mdoc").mkdir(parents=True, exist_ok=True)
    (workspace / "workspace.yaml").write_text(
        "schema_version: 2\n"
        f"workspace:\n  id: {quote_yaml(workspace.name)}\n"
        "product:\n  profile: product-profile.yaml\n"
        "repository:\n  root: .\n"
        f"manual:\n  active_book: {quote_yaml(args.book)}\n  active_version: {quote_yaml(args.book)}\n"
        "tasks:\n  root: manual-tasks\n"
        "defaults:\n  interaction_mode: guided\n  screenshot_capture_mode: assisted\n"
        "validation:\n  report_root: .work/mdoc\n",
        encoding="utf-8", newline="\n"
    )
    (workspace / "workspace.local.yaml").write_text(
        "schema_version: 2\n" f"local:\n  manual_repository: {quote_yaml(str(repository))}\n",
        encoding="utf-8", newline="\n"
    )
    profile = Path(__file__).parent.parent / "assets" / "templates" / "product-profile.yaml"
    profile_text = profile.read_text(encoding="utf-8")
    profile_text = profile_text.replace("<product-id>", workspace.name).replace("<product-display-name>", workspace.name)
    profile_text = profile_text.replace("<source-locale>", "zh-CN").replace("targets: []", "targets: [en]")
    (workspace / "product-profile.yaml").write_text(profile_text, encoding="utf-8", newline="\n")
    (workspace / ".gitignore").write_text("workspace.local.yaml\nmanual-tasks/*/task.local.yaml\n.work/\n.pdf-check/\ncaptures/\n", encoding="utf-8", newline="\n")
    launcher = '@echo off\r\n' + r'"%LOCALAPPDATA%\mdoc\bin\mdoc.cmd" status --workspace "%~dp0"' + '\r\n'
    (workspace / "open-mdoc.cmd").write_text(launcher, encoding="utf-8", newline="")
    write_registry(workspace, repository)
    return context(workspace) | {"status": "initialized"}


def workspace_schema_version(workspace: Path) -> int | None:
    config = workspace / "workspace.yaml"
    if not config.is_file():
        return None
    match = re.search(r"(?m)^schema_version:\s*(\d+)\s*$", config.read_text(encoding="utf-8"))
    return int(match.group(1)) if match else None


def command_workspace_inspect(args) -> dict:
    workspace = args.workspace.resolve()
    detected = workspace_schema_version(workspace)
    if detected is None:
        raise MdocError("MDOC-WORKSPACE-NOT-INITIALIZED", f"工作区未初始化：{workspace}")
    current = context(workspace)
    migration_status = "current" if detected == SCHEMA_VERSION else ("migration_required" if detected < SCHEMA_VERSION else "unsupported")
    return current | {"detected_schema_version": detected, "migration_status": migration_status}


def digest_path(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        digest.update(b"missing")
        return digest.hexdigest()
    if path.is_file():
        digest.update(b"file\0")
        digest.update(path.read_bytes())
        return digest.hexdigest()
    digest.update(b"directory\0")
    for item in sorted(path.rglob("*"), key=lambda value: value.as_posix().lower()):
        relative = item.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if item.is_file():
            digest.update(item.read_bytes())
    return digest.hexdigest()


def plan_paths(workspace: Path, kind: str) -> tuple[Path, Path]:
    root = workspace / ".work" / "mdoc"
    return root / "plans" / f"{kind}-current.json", root / "records" / f"latest-{kind}.json"


def save_plan(workspace: Path, kind: str, inputs: list[Path], actions: list[dict]) -> dict:
    target, _ = plan_paths(workspace, kind)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1, "kind": kind, "status": "planned",
        "workspace": str(workspace),
        "inputs": [{"path": str(path), "sha256": digest_path(path)} for path in inputs],
        "actions": actions,
    }
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def load_valid_plan(workspace: Path, kind: str, confirmed: bool) -> tuple[dict, Path, Path]:
    if not confirmed:
        raise MdocError("MDOC-CONFIRMATION-REQUIRED", f"{kind} 执行必须显式提供 --confirm。")
    target, record = plan_paths(workspace, kind)
    if not target.is_file():
        raise MdocError("MDOC-PLAN-MISSING", f"请先运行 workspace {kind} --plan。")
    data = json.loads(target.read_text(encoding="utf-8"))
    for item in data.get("inputs", []):
        if digest_path(Path(item["path"])) != item["sha256"]:
            raise MdocError("MDOC-PLAN-STALE", f"计划生成后输入已变化：{item['path']}")
    return data, target, record


def finish_plan(data: dict, target: Path, record: Path, status: str) -> dict:
    summary = {
        "schema_version": 1, "kind": data["kind"], "status": status,
        "workspace": data["workspace"], "action_count": len(data.get("actions", [])),
    }
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target.unlink(missing_ok=True)
    return summary


def stable_launcher_text() -> str:
    return '@echo off\r\n' + r'"%LOCALAPPDATA%\mdoc\bin\mdoc.cmd" status --workspace "%~dp0"' + '\r\n'


def command_workspace_adopt(args) -> dict:
    workspace = args.workspace.resolve()
    current = context(workspace)
    launcher = workspace / "open-mdoc.cmd"
    gitignore = workspace / ".gitignore"
    inputs = [workspace / "workspace.yaml", workspace / "workspace.local.yaml", launcher, gitignore]
    if args.plan:
        actions = []
        if not launcher.is_file() or launcher.read_text(encoding="utf-8", errors="replace") != stable_launcher_text():
            actions.append({"action": "write_stable_launcher", "path": str(launcher)})
        required = {"workspace.local.yaml", "manual-tasks/*/task.local.yaml", ".work/", ".pdf-check/", "captures/"}
        existing = set(gitignore.read_text(encoding="utf-8").splitlines()) if gitignore.is_file() else set()
        if not required.issubset(existing):
            actions.append({"action": "merge_gitignore", "path": str(gitignore)})
        actions.append({"action": "register_workspace"})
        return current | save_plan(workspace, "adopt", inputs, actions)
    data, target, record = load_valid_plan(workspace, "adopt", args.confirm)
    for action in data["actions"]:
        if action["action"] == "write_stable_launcher":
            launcher.write_text(stable_launcher_text(), encoding="utf-8", newline="")
        elif action["action"] == "merge_gitignore":
            lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.is_file() else []
            for line in ("workspace.local.yaml", "manual-tasks/*/task.local.yaml", ".work/", ".pdf-check/", "captures/"):
                if line not in lines:
                    lines.append(line)
            gitignore.write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif action["action"] == "register_workspace":
            write_registry(workspace, Path(current["repository"]))
    return current | finish_plan(data, target, record, "adopted")


def command_workspace_migrate(args) -> dict:
    workspace = args.workspace.resolve()
    detected = workspace_schema_version(workspace)
    if detected != 1:
        raise MdocError("MDOC-MIGRATION-NOT-REQUIRED", f"仅支持 schema v1 到 v2，当前为：{detected}")
    config = workspace / "workspace.yaml"
    local = workspace / "workspace.local.yaml"
    inputs = [config, local, workspace / "manual-tasks"]
    if args.plan:
        return save_plan(workspace, "migrate", inputs, [{"action": "schema_v1_to_v2", "files": [str(config), str(local)]}])
    data, target, record = load_valid_plan(workspace, "migrate", args.confirm)
    for path in (config, local):
        if path.is_file():
            source = path.read_text(encoding="utf-8")
            updated, count = re.subn(r"(?m)^schema_version:\s*1\s*$", "schema_version: 2", source, count=1)
            if count != 1:
                raise MdocError("MDOC-MIGRATION-CONFLICT", f"无法确认 schema v1 标记：{path}")
            path.write_text(updated, encoding="utf-8", newline="\n")
    summary = finish_plan(data, target, record, "migrated")
    return {**summary, "schema_version": 2, "workspace": str(workspace)}


def cleanup_candidates(workspace: Path, repository: Path | None) -> list[tuple[Path, str]]:
    result = [(workspace / ".pdf-check", "regenerable-cache"), (workspace / "captures", "regenerable-cache")]
    work = workspace / ".work"
    if work.is_dir():
        result.extend((item, "regenerable-cache") for item in work.iterdir() if item.name != "mdoc")
    if repository:
        result.extend([(repository / ".work", "regenerable-cache"), (repository / ".mdoc-development", "development-checkout")])
    return result


def command_workspace_cleanup(args) -> dict:
    workspace = args.workspace.resolve()
    try:
        current = context(workspace)
        repository = Path(current["repository"])
    except MdocError:
        current, repository = {"workspace": str(workspace), "active_book": None}, None
    candidates = [(path, category) for path, category in cleanup_candidates(workspace, repository) if path.exists()]
    if args.plan:
        actions = [{"action": "delete", "path": str(path), "category": category, "sha256": digest_path(path)} for path, category in candidates]
        return current | save_plan(workspace, "cleanup", [path for path, _ in candidates], actions)
    data, target, record = load_valid_plan(workspace, "cleanup", args.confirm)
    for action in data["actions"]:
        path = Path(action["path"])
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    return current | finish_plan(data, target, record, "cleaned")


def command_workspace_list(args) -> dict:
    data = read_registry()
    entries = []
    for item in data["workspaces"]:
        workspace = Path(item.get("workspace", ""))
        state = "available" if workspace.exists() else "missing"
        entries.append(item | {"state": state})
    return {"schema_version": SCHEMA_VERSION, "workspaces": entries}


def command_workspace_register(args) -> dict:
    write_registry(args.workspace.resolve(), args.repository.resolve())
    return {"schema_version": SCHEMA_VERSION, "status": "registered", "workspace": str(args.workspace.resolve())}


def command_workspace_unregister(args) -> dict:
    if not args.confirm:
        raise MdocError("MDOC-CONFIRMATION-REQUIRED", "注销工作区登记必须显式提供 --confirm。")
    target = registry_path()
    data = read_registry()
    requested = str(args.workspace.resolve())
    before = len(data["workspaces"])
    data["workspaces"] = [item for item in data["workspaces"] if item.get("workspace") != requested]
    atomic_write_json(target, data)
    return {"schema_version": SCHEMA_VERSION, "status": "unregistered", "removed": before - len(data["workspaces"])}


def command_workspace_prune(args) -> dict:
    target = registry_path()
    data = read_registry()
    retained, removed = [], 0
    for item in data["workspaces"]:
        value = str(item.get("workspace", ""))
        if value.startswith("\\"):
            retained.append(item | {"state": "unreachable"})
        elif Path(value).exists():
            retained.append(item)
        else:
            removed += 1
    data["workspaces"] = retained
    atomic_write_json(target, data)
    return {"schema_version": SCHEMA_VERSION, "status": "pruned", "removed": removed, "retained": len(retained)}


def registry_repair_plan_path() -> Path:
    return registry_path().parent / "state" / "plans" / "registry-repair-current.json"


def command_workspace_registry_repair(args) -> dict:
    scan_root = args.scan_root.resolve()
    plan = registry_repair_plan_path()
    if args.plan:
        found = []
        if scan_root.is_dir():
            for config in sorted(scan_root.rglob("workspace.yaml")):
                workspace = config.parent
                local = workspace / "workspace.local.yaml"
                repository = scalar(local, "manual_repository") if local.is_file() else None
                if repository:
                    found.append({"workspace": str(workspace.resolve()), "repository": str(Path(repository).resolve())})
        data = {"schema_version": 1, "status": "planned", "scan_root": str(scan_root), "scan_sha256": digest_path(scan_root), "workspaces": found}
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return data
    if not args.confirm:
        raise MdocError("MDOC-CONFIRMATION-REQUIRED", "注册表修复必须显式提供 --confirm。")
    if not plan.is_file():
        raise MdocError("MDOC-PLAN-MISSING", "请先运行 workspace registry repair --plan。")
    data = json.loads(plan.read_text(encoding="utf-8"))
    if data["scan_root"] != str(scan_root) or data["scan_sha256"] != digest_path(scan_root):
        raise MdocError("MDOC-PLAN-STALE", "扫描目录在计划生成后已变化。")
    target = registry_path()
    if target.is_file():
        corrupt = target.with_name("workspace-registry.corrupt-latest.json")
        for old in target.parent.glob("workspace-registry.corrupt-*.json"):
            old.unlink(missing_ok=True)
        os.replace(target, corrupt)
    atomic_write_json(target, {"schema_version": SCHEMA_VERSION, "workspaces": data["workspaces"]})
    plan.unlink(missing_ok=True)
    return {"schema_version": SCHEMA_VERSION, "status": "repaired", "registered": len(data["workspaces"])}


def command_new_task(args) -> dict:
    workspace = args.workspace.resolve()
    current = context(workspace)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", args.id):
        raise MdocError("MDOC-TASK-ID-INVALID", "任务 ID 仅允许小写字母、数字和连字符。")
    directory = task_root(workspace) / args.id
    if directory.exists():
        raise MdocError("MDOC-TASK-EXISTS", f"任务已存在：{args.id}")
    directory.mkdir(parents=True)
    (directory / "task.yaml").write_text(
        "schema_version: 2\n"
        f"task:\n  id: {quote_yaml(args.id)}\n  operation: {args.operation}\n  target_book: {quote_yaml(current['active_book'])}\n"
        "interaction:\n  mode: guided\n"
        "capture:\n  mode: assisted\n"
        "target:\n  summary_parent: TODO\n  document_path: TODO\n  image_path: TODO\n"
        f"module:\n  title:\n    source: {quote_yaml(args.title)}\n    translations: {{}}\n"
        "scope: {}\nvalidation:\n  profile: full\n  mode: inherit\n  required_components: []\n",
        encoding="utf-8", newline="\n"
    )
    for name in ("structure.yaml", "screenshots.yaml", "sources.yaml", "state.yaml", "decisions.yaml"):
        template = Path(__file__).parent.parent / "assets" / "templates" / name
        (directory / name).write_text(template.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    return current | task_info(workspace, args.id) | {"status": "created", "operation_book": current["active_book"]}


def command_tasks(args) -> dict:
    workspace = args.workspace.resolve()
    current = context(workspace)
    tasks = [task_info(workspace, path.name) for path in sorted(task_root(workspace).iterdir()) if path.is_dir() and (path / "task.yaml").is_file()]
    return current | {"tasks": tasks}


def command_resume(args) -> dict:
    workspace = args.workspace.resolve()
    task = task_info(workspace, args.task)
    return context(workspace, task["target_book"]) | task


def command_switch_book(args) -> dict:
    workspace = args.workspace.resolve()
    current = context(workspace)
    target = Path(current["repository"]) / args.book
    if not target.is_dir():
        raise MdocError("MDOC-WORKSPACE-BOOK-MISSING", f"书册不存在：{target}")
    replace_scalar(workspace / "workspace.yaml", "active_book", args.book)
    replace_scalar(workspace / "workspace.yaml", "active_version", args.book)
    return context(workspace) | {"status": "switched"}


def module_status(name: str) -> str:
    try:
        return "available" if importlib.util.find_spec(name) else "unavailable"
    except (ImportError, ModuleNotFoundError):
        return "unavailable"


def doctor_runtime() -> tuple[dict, dict, dict]:
    names = ("ruamel.yaml", "jsonschema", "pdfplumber", "pypdf", "pypdfium2", "PIL")
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "mdoc"
    state_path = root / "state" / "installed-runtime.json"
    executable = Path(sys.executable)
    source = "current-process"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            candidate = Path(state.get("runtime_python") or "")
            if candidate.is_file():
                executable, source = candidate, "installed-runtime"
        except (OSError, json.JSONDecodeError):
            pass
    probe = (
        "import importlib.util,json,platform,sys\n"
        "names=['ruamel.yaml','jsonschema','pdfplumber','pypdf','pypdfium2','PIL','venv','tkinter']\n"
        "modules={}\n"
        "for name in names:\n"
        "  try: modules[name]='available' if importlib.util.find_spec(name) else 'unavailable'\n"
        "  except (ImportError,ModuleNotFoundError): modules[name]='unavailable'\n"
        "print(json.dumps({'version':sys.version.split()[0],'implementation':platform.python_implementation(),'architecture':platform.machine(),'modules':modules}))\n"
    )
    completed = subprocess.run([str(executable), "-c", probe], text=True, capture_output=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode == 0:
        payload = json.loads(completed.stdout)
        statuses = payload["modules"]
        modules = {name: statuses[name] for name in names}
        features = {name: statuses[name] for name in ("venv", "tkinter")}
        runtime = {"status": "available", "version": payload["version"], "executable": str(executable), "implementation": payload["implementation"], "architecture": payload["architecture"], "source": source, "features": features}
        return runtime, modules, features
    modules = {name: module_status(name) for name in names}
    features = {"venv": module_status("venv"), "tkinter": module_status("tkinter")}
    return {"status": "unavailable", "executable": str(executable), "source": source, "features": features}, modules, features


def command_doctor(args) -> dict:
    detected_schema = workspace_schema_version(args.workspace.resolve()) if args.workspace else None
    current = (
        context(args.workspace.resolve())
        if args.workspace
        else {
            "schema_version": SCHEMA_VERSION,
            "workspace": None,
            "repository": None,
            "active_book": None,
            "operation_book": None,
            "workspace_status": "unbound",
        }
    )
    runtime_python, modules, features = doctor_runtime()
    tools = {}
    required = modules["ruamel.yaml"] == "available" and modules["jsonschema"] == "available"
    pdf_ready = all(modules[name] == "available" for name in ("pdfplumber", "pypdf", "pypdfium2", "PIL"))
    screenshot_ready = modules["PIL"] == "available" and features["tkinter"] == "available"
    if "workspace_status" not in current:
        current["workspace_status"] = "bound"
    status = "ready" if required and pdf_ready and screenshot_ready else ("ready_with_warnings" if required else "repair_required")
    if detected_schema is not None and detected_schema < SCHEMA_VERSION:
        status = "migration_required"
    result = current | {
        "status": status,
        "runtime": {"python": runtime_python},
        "modules": modules, "tools": tools,
        "capabilities": {"core": "ready" if required else "needs-repair", "pdf_check": "ready" if pdf_ready else "optional-missing", "screenshot_assistant": "ready" if screenshot_ready else "optional-missing"},
    }
    if args.repair:
        repair = Path(__file__).parent.parent / "runtime-support" / "repair-mdoc-runtime.ps1"
        if not repair.is_file():
            raise MdocError("MDOC-DOCTOR-REPAIR-UNAVAILABLE", "当前安装缺少运行时修复组件，请重新安装 mdoc。")
        if not args.toolkit and not args.allow_network_download:
            raise MdocError("MDOC-DOCTOR-NETWORK-CONSENT-REQUIRED", "请提供离线 --toolkit，或在明确同意联网后添加 --allow-network-download。")
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(repair), "-Profile", args.profile, "-Installation", str(Path(__file__).parent.parent)]
        if args.toolkit:
            command.extend(["-Toolkit", str(args.toolkit.resolve())])
        if args.python:
            command.extend(["-Python", str(args.python.resolve())])
        if args.allow_network_download:
            command.append("-AllowNetworkDownload")
        if args.proxy:
            command.extend(["-Proxy", args.proxy])
        completed = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace", check=False)
        if completed.returncode != 0:
            raise MdocError("MDOC-DOCTOR-REPAIR-FAILED", (completed.stderr or completed.stdout).strip())
        result["repair"] = {"status": "completed", "profile": args.profile}
    return result


def command_configure(args) -> dict:
    workspace = args.workspace.resolve()
    current = context(workspace)
    return current | {
        "workspace_config": str((workspace / "workspace.yaml").resolve()),
        "local_config": str((workspace / "workspace.local.yaml").resolve()),
        "product_profile": str((workspace / "product-profile.yaml").resolve()),
    }


def command_bind_local(args) -> dict:
    workspace = args.workspace.resolve()
    current = context(workspace)
    repository = args.repository.resolve()
    if not (repository / current["active_book"]).is_dir():
        raise MdocError("MDOC-WORKSPACE-BOOK-MISSING", f"新仓库不包含活动书册：{current['active_book']}")
    replace_scalar(workspace / "workspace.local.yaml", "manual_repository", str(repository))
    write_registry(workspace, repository)
    return context(workspace) | {"status": "bound"}


def command_diagnose(args) -> dict:
    workspace = args.workspace.resolve()
    current = context(workspace)
    report_dir = workspace / ".work" / "mdoc" / "diagnostics"
    shutil.rmtree(report_dir, ignore_errors=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / "latest.json"
    data = {
        "schema_version": SCHEMA_VERSION,
        "product_version": VERSION,
        "platform": sys.platform,
        "python_version": sys.version.split()[0],
        "active_book": current["active_book"],
        "workspace_config_present": (workspace / "workspace.yaml").is_file(),
        "local_binding_present": (workspace / "workspace.local.yaml").is_file(),
        "task_count": sum(1 for item in task_root(workspace).glob("*/task.yaml")),
        "privacy": "sanitized-local-only",
    }
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return current | {"diagnostic_report": str(report), "privacy": "sanitized-local-only"}


def command_uninstall(args) -> dict:
    installation = args.installation.resolve()
    if not args.confirm:
        raise MdocError("MDOC-UNINSTALL-CONFIRMATION-REQUIRED", "卸载必须显式提供 --confirm。")
    if installation.name.lower() != "mdoc":
        raise MdocError("MDOC-UNINSTALL-TARGET-INVALID", f"拒绝删除非 mdoc 目录：{installation}")
    if installation.exists():
        shutil.rmtree(installation)
    runtime_root = args.runtime_root.resolve()
    if not args.keep_tools and runtime_root.name.lower() == "mdoc" and runtime_root.exists():
        state_path = runtime_root / "state" / "installed-runtime.json"
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                state = {}
            installer = Path(state.get("python_installer") or "")
            if state.get("python_ownership") == "managed-by-mdoc" and installer.is_file():
                completed = subprocess.run([str(installer), "/uninstall", "/quiet"], check=False)
                if completed.returncode != 0:
                    raise MdocError("MDOC-UNINSTALL-PYTHON-FAILED", f"受管 Python 卸载失败：{completed.returncode}")
            path_entry = state.get("path_entry")
            if path_entry:
                try:
                    import winreg
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
                        user_path, value_type = winreg.QueryValueEx(key, "Path")
                        parts = [item for item in user_path.split(";") if item and os.path.normcase(item) != os.path.normcase(path_entry)]
                        winreg.SetValueEx(key, "Path", 0, value_type, ";".join(parts))
                except (OSError, FileNotFoundError):
                    pass
            start_menu = Path(state.get("start_menu") or "")
            if start_menu.name.lower() == "mdoc" and start_menu.exists():
                shutil.rmtree(start_menu)
        shutil.rmtree(runtime_root)
    return {"schema_version": SCHEMA_VERSION, "status": "uninstalled", "installation": str(installation), "runtime_root": str(runtime_root), "tools_retained": args.keep_tools}


def command_check(args) -> int:
    current = context(args.workspace.resolve(), task_info(args.workspace.resolve(), args.task)["target_book"])
    emit(current, False)
    import manual_lint
    argv = ["manual_lint.py", "check", "--workspace", str(args.workspace.resolve()), "--task", args.task, "--profile", args.profile]
    previous = sys.argv
    try:
        sys.argv = argv
        return manual_lint.main()
    finally:
        sys.argv = previous


def command_pdf_check(args) -> int:
    current = context(args.workspace.resolve(), task_info(args.workspace.resolve(), args.task)["target_book"])
    emit(current, False)
    import manual_pdf_check
    mode = "open" if args.open else "check"
    return manual_pdf_check.main([mode, "--workspace", str(args.workspace.resolve()), "--task", args.task])


def command_screenshots(args) -> dict:
    workspace = args.workspace.resolve()
    task = task_info(workspace, args.task)
    import screenshot_state
    manifest = screenshot_state.synchronize(workspace, args.task, Path(__file__).parent.parent)
    return context(workspace, task["target_book"]) | {"task": args.task, "screenshot_summary": screenshot_state.summary(manifest)}


def command_update(args) -> dict:
    transaction = load_transaction_module()
    root = args.runtime_root.resolve()
    try:
        if args.plan:
            if not args.package or not args.installation:
                raise MdocError("MDOC-UPDATE-PACKAGE-REQUIRED", "更新计划需要 --package 和 --installation。")
            if args.installation.name.lower() != "mdoc":
                raise MdocError("MDOC-UPDATE-TARGET-INVALID", f"安装目录必须以 mdoc 命名：{args.installation}")
            if args.manifest:
                release = json.loads(args.manifest.read_text(encoding="utf-8"))
                if transaction.sha256(args.package) != release.get("sha256"):
                    raise MdocError("MDOC-UPDATE-SHA256-MISMATCH", "包外 Manifest SHA-256 不匹配。")
            return transaction.create_plan(args.package.resolve(), args.installation.resolve(), root, "update")
        if args.apply:
            return transaction.apply_plan(root, args.confirm)
        if args.package and args.manifest and args.installation:
            release = json.loads(args.manifest.read_text(encoding="utf-8"))
            if transaction.sha256(args.package) != release.get("sha256"):
                raise MdocError("MDOC-UPDATE-SHA256-MISMATCH", "包外 Manifest SHA-256 不匹配。")
            return transaction.create_plan(args.package.resolve(), args.installation.resolve(), root, "update")
        raise MdocError("MDOC-UPDATE-MODE-REQUIRED", "请使用 --plan，或 --apply --confirm。")
    except transaction.TransactionError as exc:
        code = str(exc).split(":", 1)[0]
        if code == "MDOC-PACKAGE-UNSAFE": code = "MDOC-UPDATE-PACKAGE-UNSAFE"
        raise MdocError(code, str(exc)) from exc


def load_transaction_module():
    candidates = [
        Path(__file__).resolve().parents[3] / "runtime-bootstrap" / "mdoc_install_transaction.py",
        Path(__file__).resolve().parent.parent / "runtime-support" / "runtime-bootstrap" / "mdoc_install_transaction.py",
    ]
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        raise MdocError("MDOC-TRANSACTION-UNAVAILABLE", "当前安装缺少共享安装事务组件。")
    spec = importlib.util.spec_from_file_location("mdoc_install_transaction", source)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def command_runtime_cancel(args) -> dict:
    transaction = load_transaction_module()
    try:
        return transaction.cancel(args.runtime_root.resolve(), args.confirm)
    except transaction.TransactionError as exc:
        raise MdocError(str(exc).split(":", 1)[0], str(exc)) from exc


def emit(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False))
        return
    if data.get("active_book"):
        print(f"活动书册：{data['active_book']}")
        print(f"本次操作书册：{data['operation_book']}")
        print(f"正式仓库：{data['repository']}")
        print(f"流程工作区：{data['workspace']}")
    elif data.get("status"):
        print(f"状态：{data['status']}")
    if data.get("tasks") is not None:
        for task in data["tasks"]:
            print(f"- {task['id']} [{task['operation']}] -> {task['target_book']}")
    elif data.get("id"):
        print(f"任务：{data['id']} ({data.get('operation', '')})")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="mdoc", description="Windows 本地与局域网产品手册工作流")
    root.add_argument("--version", action="version", version=f"mdoc {VERSION}")
    sub = root.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="初始化并绑定流程工作区")
    setup.add_argument("--repository", type=Path, required=True)
    setup.add_argument("--workspace", type=Path, required=True)
    setup.add_argument("--book", required=True)
    setup.add_argument("--json", action="store_true")
    status = sub.add_parser("status", help="显示活动书册和工作区状态")
    status.add_argument("--workspace", type=Path, required=True)
    status.add_argument("--json", action="store_true")
    new_task = sub.add_parser("new-task", help="为当前活动书册创建任务")
    new_task.add_argument("--workspace", type=Path, required=True)
    new_task.add_argument("--id", required=True)
    new_task.add_argument("--operation", choices=("create_module", "add_feature", "update_feature", "add_locale"), required=True)
    new_task.add_argument("--title", required=True)
    new_task.add_argument("--json", action="store_true")
    tasks = sub.add_parser("tasks", help="列出工作区任务")
    tasks.add_argument("--workspace", type=Path, required=True)
    tasks.add_argument("--json", action="store_true")
    resume = sub.add_parser("resume", help="恢复任务并显示其固定目标书册")
    resume.add_argument("--workspace", type=Path, required=True)
    resume.add_argument("--task", required=True)
    resume.add_argument("--json", action="store_true")
    switch = sub.add_parser("switch-book", help="切换活动书册")
    switch.add_argument("--workspace", type=Path, required=True)
    switch.add_argument("--book", required=True)
    switch.add_argument("--json", action="store_true")
    doctor = sub.add_parser("doctor", help="检查核心与可选运行环境")
    doctor.add_argument("--workspace", type=Path)
    doctor.add_argument("--repair", action="store_true", help="保留给经用户同意的受控修复流程")
    doctor.add_argument("--toolkit", type=Path)
    doctor.add_argument("--python", type=Path)
    doctor.add_argument("--profile", choices=("Full", "Core", "Existing", "Offline"), default="Full")
    doctor.add_argument("--allow-network-download", action="store_true")
    doctor.add_argument("--proxy", help="显式无凭据 HTTP/HTTPS/SOCKS5 代理")
    doctor.add_argument("--json", action="store_true")
    check = sub.add_parser("check", help="运行可选 Quality Gate")
    check.add_argument("--workspace", type=Path, required=True)
    check.add_argument("--task", required=True)
    check.add_argument("--profile", choices=("quick", "full", "release"), default="quick")
    pdf = sub.add_parser("pdf-check", help="检查 PDF 并可打开本地问题预览")
    pdf.add_argument("--workspace", type=Path, required=True)
    pdf.add_argument("--task", required=True)
    pdf.add_argument("--open", action="store_true")
    diagnose = sub.add_parser("diagnose", help="生成仅保留 latest 的本地脱敏诊断摘要")
    diagnose.add_argument("--workspace", type=Path, required=True)
    diagnose.add_argument("--json", action="store_true")
    uninstall = sub.add_parser("uninstall", help="卸载 mdoc 技能运行时，不删除流程工作区或正式手册")
    uninstall.add_argument("--installation", type=Path, default=Path.home() / ".codex" / "skills" / "mdoc")
    uninstall.add_argument("--runtime-root", type=Path, default=Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "mdoc")
    uninstall.add_argument("--keep-tools", action="store_true")
    uninstall.add_argument("--confirm", action="store_true")
    uninstall.add_argument("--json", action="store_true")
    configure = sub.add_parser("configure", help="显示当前工作区配置位置")
    configure.add_argument("--workspace", type=Path, required=True)
    configure.add_argument("--json", action="store_true")
    bind = sub.add_parser("bind-local", help="更新本机正式仓库绑定")
    bind.add_argument("--workspace", type=Path, required=True)
    bind.add_argument("--repository", type=Path, required=True)
    bind.add_argument("--json", action="store_true")
    screenshots = sub.add_parser("screenshots", help="同步截图状态并生成一键截图助手")
    screenshots.add_argument("--workspace", type=Path, required=True)
    screenshots.add_argument("--task", required=True)
    screenshots.add_argument("--json", action="store_true")
    update = sub.add_parser("update", help="从已校验的离线发布包更新 mdoc")
    update.add_argument("--package", type=Path)
    update.add_argument("--manifest", type=Path)
    update.add_argument("--installation", type=Path)
    update.add_argument("--runtime-root", type=Path, default=Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "mdoc")
    update_mode = update.add_mutually_exclusive_group()
    update_mode.add_argument("--plan", action="store_true")
    update_mode.add_argument("--apply", action="store_true")
    update.add_argument("--confirm", action="store_true")
    update.add_argument("--json", action="store_true")
    runtime = sub.add_parser("runtime", help="运行时事务维护")
    runtime_sub = runtime.add_subparsers(dest="runtime_command", required=True)
    cancel_runtime = runtime_sub.add_parser("cancel", help="清理确认已停止的陈旧事务")
    cancel_runtime.add_argument("--runtime-root", type=Path, default=Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "mdoc")
    cancel_runtime.add_argument("--confirm", action="store_true")
    cancel_runtime.add_argument("--json", action="store_true")
    workspace = sub.add_parser("workspace", help="检查、接管、迁移和清理流程工作区")
    workspace_sub = workspace.add_subparsers(dest="workspace_command", required=True)
    inspect = workspace_sub.add_parser("inspect", help="只读检查工作区和 schema 状态")
    inspect.add_argument("--workspace", type=Path, required=True)
    inspect.add_argument("--json", action="store_true")
    listing = workspace_sub.add_parser("list", help="列出本机登记的工作区")
    listing.add_argument("--json", action="store_true")
    register = workspace_sub.add_parser("register", help="登记一个工作区")
    register.add_argument("--workspace", type=Path, required=True)
    register.add_argument("--repository", type=Path, required=True)
    register.add_argument("--json", action="store_true")
    unregister = workspace_sub.add_parser("unregister", help="只注销登记，不删除目录")
    unregister.add_argument("--workspace", type=Path, required=True)
    unregister.add_argument("--confirm", action="store_true")
    unregister.add_argument("--json", action="store_true")
    prune = workspace_sub.add_parser("prune", help="移除确定不存在的本地登记")
    prune.add_argument("--json", action="store_true")
    registry = workspace_sub.add_parser("registry", help="注册表维护")
    registry_sub = registry.add_subparsers(dest="registry_command", required=True)
    repair_registry = registry_sub.add_parser("repair", help="从显式扫描根修复损坏注册表")
    repair_registry.add_argument("--scan-root", type=Path, required=True)
    registry_mode = repair_registry.add_mutually_exclusive_group(required=True)
    registry_mode.add_argument("--plan", action="store_true")
    registry_mode.add_argument("--apply", action="store_true")
    repair_registry.add_argument("--confirm", action="store_true")
    repair_registry.add_argument("--json", action="store_true")
    for name, help_text in (
        ("adopt", "接管已有 schema v2 工作区"),
        ("migrate", "迁移旧版工作区 schema"),
        ("cleanup", "清理已识别的可再生缓存和开发残留"),
    ):
        operation = workspace_sub.add_parser(name, help=help_text)
        operation.add_argument("--workspace", type=Path, required=True)
        mode = operation.add_mutually_exclusive_group(required=True)
        mode.add_argument("--plan", action="store_true")
        mode.add_argument("--apply", action="store_true")
        operation.add_argument("--confirm", action="store_true")
        operation.add_argument("--json", action="store_true")
    return root


def main() -> int:
    configure_utf8_console()
    args = parser().parse_args()
    try:
        if args.command == "check":
            return command_check(args)
        if args.command == "pdf-check":
            return command_pdf_check(args)
        handlers = {
            "setup": command_setup, "status": lambda item: context(item.workspace.resolve()),
            "new-task": command_new_task, "tasks": command_tasks,
            "resume": command_resume, "switch-book": command_switch_book, "doctor": command_doctor,
            "diagnose": command_diagnose, "uninstall": command_uninstall,
            "configure": command_configure, "bind-local": command_bind_local,
            "screenshots": command_screenshots, "update": command_update,
        }
        if args.command == "runtime":
            data = {"cancel": command_runtime_cancel}[args.runtime_command](args)
        elif args.command == "workspace":
            workspace_handlers = {
                "inspect": command_workspace_inspect,
                "list": command_workspace_list,
                "register": command_workspace_register,
                "unregister": command_workspace_unregister,
                "prune": command_workspace_prune,
                "adopt": command_workspace_adopt,
                "migrate": command_workspace_migrate,
                "cleanup": command_workspace_cleanup,
            }
            if args.workspace_command == "registry":
                data = {"repair": command_workspace_registry_repair}[args.registry_command](args)
            else:
                data = workspace_handlers[args.workspace_command](args)
        else:
            data = handlers[args.command](args)
        emit(data, args.json)
        if args.command == "doctor":
            return {"migration_required": 3, "repair_required": 4}.get(data.get("status"), 0)
        return 0
    except MdocError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
