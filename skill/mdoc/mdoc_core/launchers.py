from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from .config import validate_schema
from .io import read_yaml, write_json_atomic
from .workspace import _validate_paths


HEADER = "REM 由 mdoc 自动生成，请勿手动修改"
MANIFEST = "launcher-manifest.json"


def _safe_id(value: str) -> str:
    parts = []
    for character in value:
        if character.isascii() and (character.isalnum() or character in "-_"):
            parts.append(character.casefold())
        elif character.isspace() or character in '<>:"/\\|?*':
            parts.append("-")
        else:
            parts.append(f"u{ord(character):x}")
    return re.sub(r"-+", "-", "".join(parts)).strip("-") or "item"


def _mapped_ids(values) -> dict[str, str]:
    result: dict[str, str] = {}
    used: dict[str, str] = {}
    for value in values:
        safe = _safe_id(value)
        if safe in used and used[safe] != value:
            safe = f"{safe}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:8]}"
        used[safe] = value
        result[value] = safe
    return result


def _cmd(title: str, details: list[str], command: str, *, delayed_refresh: bool = False, refresh_after_success: bool = False) -> str:
    lines = [
        "@echo off", HEADER, "chcp 65001 >nul", "setlocal",
        r'set "MDOC_WORKSPACE=%~dp0..\.."',
        r'set "MDOC_CMD=%LOCALAPPDATA%\mdoc\bin\mdoc.cmd"',
        'set "MDOC_LAUNCHER_ACTIVE=1"', f"title {title}", "echo ============================================================",
        f"echo {title}", "echo 开始时间: %date% %time%",
    ]
    lines.extend(f"echo {item}" for item in details)
    lines.extend([
        "echo ============================================================",
        'if not exist "%MDOC_CMD%" (', "  echo [MDOC-INSTALL-MISSING] 未找到已安装的 mdoc。",
        "  echo 请先安装或更新 mdoc 后重试。", "  pause", "  exit /b 2", ")",
        "echo [启动] 正在执行任务，长时间运行时请勿关闭窗口。",
        "echo.",
    ])
    if delayed_refresh:
        lines.extend([
            'start "mdoc launcher refresh" cmd.exe /d /s /c "ping 127.0.0.1 -n 2 ^>nul ^& call ^"%MDOC_CMD%^" workspace launchers refresh --workspace ^"%MDOC_WORKSPACE%^" ^& echo. ^& pause"',
            "exit /b 0",
        ])
    else:
        lines.extend([command, 'set "MDOC_EXIT=%ERRORLEVEL%"'])
        if refresh_after_success:
            lines.append('if "%MDOC_EXIT%"=="0" start "mdoc launcher refresh" cmd.exe /d /s /c "ping 127.0.0.1 -n 2 ^>nul ^& call ^"%MDOC_CMD%^" workspace launchers refresh --workspace ^"%MDOC_WORKSPACE%^" ^& echo. ^& pause"')
        lines.extend(["echo.", "echo ============================================================",
            'if "%MDOC_EXIT%"=="0" echo [完成] 命令执行成功。',
            'if not "%MDOC_EXIT%"=="0" echo [失败] 命令执行失败，退出码: %MDOC_EXIT%。',
            'if not "%MDOC_EXIT%"=="0" echo 请根据上方错误编号和修复建议处理后重试。',
            "echo 结束时间: %date% %time%", "echo ============================================================",
            "pause", "exit /b %MDOC_EXIT%",
        ])
    return "\r\n".join(lines) + "\r\n"


def _targets(workspace: Path, config: dict) -> dict[str, dict]:
    targets: dict[str, dict] = {
        "open_image_editor.cmd": {"kind": "image_editor", "content": _cmd("mdoc 图片编辑器", ["工作区: %MDOC_WORKSPACE%"], 'if not "%~2"=="" ( echo 只支持零个或一个图片路径参数。 & exit /b 2 )\r\nif "%~1"=="" ( call "%MDOC_CMD%" image edit ) else ( call "%MDOC_CMD%" image edit --file "%~1" )')},
        "open_check_report.cmd": {"kind": "check_report", "content": _cmd("mdoc 检查报告", ["工作区: %MDOC_WORKSPACE%"], 'call "%MDOC_CMD%" check report --workspace "%MDOC_WORKSPACE%"')},
        "open_book_feedback_modify.cmd": {"kind": "feedback", "content": _cmd("mdoc 手册反馈修订", ["工作区: %MDOC_WORKSPACE%"], 'call "%MDOC_CMD%" feedback open --workspace "%MDOC_WORKSPACE%"')},
        "sync_workspace_config.cmd": {"kind": "workspace_sync", "content": _cmd("mdoc 工作区配置同步", ["工作区: %MDOC_WORKSPACE%"], 'call "%MDOC_CMD%" workspace sync --workspace "%MDOC_WORKSPACE%"', refresh_after_success=True)},
        "refresh_launchers.cmd": {"kind": "launcher_refresh", "content": _cmd("mdoc Launcher 刷新", ["工作区: %MDOC_WORKSPACE%"], "", delayed_refresh=True)},
        "check_all.cmd": {"kind": "check_workspace", "content": _cmd("mdoc 全工作区检查", ["工作区: %MDOC_WORKSPACE%", "范围: 所有书册和语言"], 'call "%MDOC_CMD%" check run --workspace "%MDOC_WORKSPACE%" --scope workspace --replace-latest --open-report')},
    }
    book_ids = _mapped_ids(config["books"].keys())
    for book_id, book in config["books"].items():
        locale_ids = _mapped_ids(book["locales"].keys())
        for locale_id in book["locales"]:
            stem = f"{book_ids[book_id]}_{locale_ids[locale_id]}"
            targets[f"check_{stem}.cmd"] = {"kind": "check_book_locale", "book": book_id, "locale": locale_id, "content": _cmd(f"mdoc 检查 {book_id}/{locale_id}", [f"书册: {book_id}", f"语言: {locale_id}"], f'call "%MDOC_CMD%" check run --workspace "%MDOC_WORKSPACE%" --book "{book_id}" --locale "{locale_id}" --scope book --replace-latest --open-report') }
            targets[f"build_pdf_{stem}.cmd"] = {"kind": "pdf_book_locale", "book": book_id, "locale": locale_id, "content": _cmd(f"mdoc PDF {book_id}/{locale_id}", [f"书册: {book_id}", f"语言: {locale_id}"], f'call "%MDOC_CMD%" pdf build --workspace "%MDOC_WORKSPACE%" --book "{book_id}" --locale "{locale_id}" --scope book --yes --open-output') }
        targets[f"build_pdf_{book_ids[book_id]}_all_locales.cmd"] = {"kind": "pdf_book_all_locales", "book": book_id, "content": _cmd(f"mdoc PDF {book_id} 全语言", [f"书册: {book_id}", "语言: 全部"], f'call "%MDOC_CMD%" pdf build --workspace "%MDOC_WORKSPACE%" --book "{book_id}" --all-locales --scope book --yes --open-output') }
    return targets


def refresh(workspace: Path) -> dict:
    workspace = workspace.resolve()
    control = workspace / ".mdoc"
    config = read_yaml(control / "workspace.yaml")
    validate_schema(config, "workspace.schema.json", "workspace.yaml")
    _validate_paths(workspace, config)
    local_path = control / "workspace.local.yaml"
    if local_path.is_file():
        validate_schema(read_yaml(local_path), "workspace-local.schema.json", "workspace.local.yaml")
    directory = control / "launchers"
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / MANIFEST
    previous = None
    warning = None
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if previous.get("schema_version") != 1 or not isinstance(previous.get("launchers"), list):
                raise ValueError("invalid manifest")
        except (OSError, ValueError, json.JSONDecodeError):
            warning = "旧 launcher manifest 已损坏；本次不删除旧文件，仅覆盖当前目标。"
            previous = None
    targets = _targets(workspace, config)
    for name, item in targets.items():
        (directory / name).write_text(item["content"], encoding="utf-8", newline="")
    removed = []
    if previous:
        for item in previous["launchers"]:
            name = item.get("name")
            if isinstance(name, str) and name not in targets and Path(name).name == name:
                (directory / name).unlink(missing_ok=True); removed.append(name)
    manifest = {"schema_version": 1, "launchers": [{"name": name, **{key: value for key, value in item.items() if key != "content"}} for name, item in sorted(targets.items())]}
    write_json_atomic(manifest_path, manifest)
    if warning:
        print(f"[mdoc] 警告: {warning}", file=sys.stderr)
    return {"status": "workspace_launchers_refreshed", "directory": str(directory), "launchers": len(targets), "removed": removed, "warning": warning}
