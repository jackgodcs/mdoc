#!/usr/bin/env python3
"""Unified Windows-first command line entry point for mdoc."""

from __future__ import annotations

import argparse
import json
import os
import sys
import re
import importlib.util
import shutil
import platform
import subprocess
from pathlib import Path


def product_version() -> str:
    source_version = Path(__file__).resolve().parents[3] / "VERSION"
    if source_version.is_file():
        return source_version.read_text(encoding="utf-8").strip()
    return "1.1.0"


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
        except (OSError, json.JSONDecodeError):
            pass
    entry = {"workspace": str(workspace.resolve()), "repository": str(repository.resolve())}
    entries = [item for item in data.get("workspaces", []) if item.get("workspace") != entry["workspace"]]
    data = {"schema_version": SCHEMA_VERSION, "workspaces": [entry, *entries][:50]}
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    launcher = f'@echo off\r\n"{sys.executable}" "{Path(__file__).resolve()}" status --workspace "%~dp0"\r\n'
    (workspace / "open-mdoc.cmd").write_text(launcher, encoding="utf-8", newline="")
    write_registry(workspace, repository)
    return context(workspace) | {"status": "initialized"}


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


def command_doctor(args) -> dict:
    current = context(args.workspace.resolve())
    modules = {name: module_status(name) for name in ("ruamel.yaml", "jsonschema", "pdfplumber", "pypdf", "pypdfium2", "PIL")}
    tools = {}
    features = {"venv": module_status("venv"), "tkinter": module_status("tkinter")}
    required = modules["ruamel.yaml"] == "available" and modules["jsonschema"] == "available"
    pdf_ready = all(modules[name] == "available" for name in ("pdfplumber", "pypdf", "pypdfium2", "PIL"))
    screenshot_ready = modules["PIL"] == "available" and features["tkinter"] == "available"
    result = current | {
        "runtime": {"python": {"status": "available", "version": sys.version.split()[0], "executable": sys.executable, "implementation": platform.python_implementation(), "architecture": platform.machine(), "features": features}},
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
    if not args.package or not args.manifest or not args.installation:
        raise MdocError("MDOC-UPDATE-PACKAGE-REQUIRED", "更新需要 --package、--manifest 和 --installation；联网获取前必须先获得用户同意。")
    import hashlib
    import zipfile
    package, manifest, installation = args.package.resolve(), args.manifest.resolve(), args.installation.resolve()
    release = json.loads(manifest.read_text(encoding="utf-8"))
    actual = hashlib.sha256(package.read_bytes()).hexdigest()
    if actual != release.get("sha256"):
        raise MdocError("MDOC-UPDATE-SHA256-MISMATCH", "更新包 SHA-256 与 Release Manifest 不一致。")
    if installation.name.lower() != "mdoc":
        raise MdocError("MDOC-UPDATE-TARGET-INVALID", f"安装目录必须以 mdoc 命名：{installation}")
    temporary = installation.with_name(installation.name + ".updating")
    shutil.rmtree(temporary, ignore_errors=True)
    with zipfile.ZipFile(package) as archive:
        for member in archive.infolist():
            name = member.filename.replace("\\", "/")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in Path(name).parts:
                raise MdocError("MDOC-UPDATE-PACKAGE-UNSAFE", f"更新包包含不安全路径：{member.filename}")
        archive.extractall(temporary)
    source = temporary / "skill" / "mdoc"
    if not (source / "SKILL.md").is_file():
        shutil.rmtree(temporary, ignore_errors=True)
        raise MdocError("MDOC-UPDATE-PACKAGE-INVALID", "更新包不包含 skill/mdoc/SKILL.md。")
    staged = installation.with_name(installation.name + ".new")
    shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(source, staged)
    shutil.rmtree(installation, ignore_errors=True)
    staged.replace(installation)
    shutil.rmtree(temporary, ignore_errors=True)
    return {"schema_version": SCHEMA_VERSION, "status": "updated", "version": release.get("version"), "installation": str(installation)}


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
    doctor.add_argument("--workspace", type=Path, required=True)
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
    update.add_argument("--json", action="store_true")
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
        data = handlers[args.command](args)
        emit(data, args.json)
        return 0
    except MdocError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
