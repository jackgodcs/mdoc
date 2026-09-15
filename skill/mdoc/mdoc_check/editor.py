from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path


_editor_selection = threading.Lock()


def preferences_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "mdoc" / "preferences" / "markdown-check.json"


def load_preferences() -> dict:
    try:
        value = json.loads(preferences_path().read_text(encoding="utf-8"))
        return value if value.get("schema_version") == 1 else {"schema_version": 1, "ui": {"files_per_page": 20}}
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "ui": {"files_per_page": 20}}


def save_preferences(value: dict) -> dict:
    path = preferences_path()
    value = {"schema_version": 1, **value}
    value.setdefault("ui", {}).setdefault("files_per_page", 20)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return value


def editor_kind(executable: Path) -> str:
    name = executable.name.casefold()
    if name in {"code.exe", "code-insiders.exe"}:
        return "vscode"
    if name == "cursor.exe":
        return "cursor"
    if name == "notepad++.exe":
        return "notepad++"
    if name in {"sublime_text.exe", "subl.exe"}:
        return "sublime"
    if name == "typora.exe":
        return "typora"
    if name in {"markdownpad2.exe", "markdownpad.exe"}:
        return "markdownpad"
    return "generic"


def choose_editor() -> Path | None:
    script = "import tkinter as tk\nfrom tkinter import filedialog\nr=tk.Tk();r.withdraw()\ntry: print(filedialog.askopenfilename(title='选择 Markdown 编辑器',filetypes=[('Windows 应用程序','*.exe')]),end='')\nfinally: r.destroy()"
    if not _editor_selection.acquire(blocking=False):
        raise ValueError("编辑器选择窗口已打开，请先完成当前选择。")
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"}, creationflags=flags)
        if result.returncode:
            raise OSError("无法打开编辑器选择窗口。" + (f" {result.stderr.strip()}" if result.stderr.strip() else ""))
        selected = result.stdout.strip()
        return Path(selected).resolve() if selected else None
    finally:
        _editor_selection.release()


def command(executable: Path, source: Path, line: int, column: int) -> tuple[list[str], bool]:
    kind = editor_kind(executable)
    if kind in {"vscode", "cursor"}:
        return [str(executable), "--goto", f"{source.resolve()}:{line}:{column}"], True
    if kind == "notepad++":
        return [str(executable), f"-n{line}", f"-c{column}", str(source.resolve())], True
    if kind == "sublime":
        return [str(executable), f"{source.resolve()}:{line}:{column}"], True
    return [str(executable), str(source.resolve())], False


def open_source(source: Path, line: int, column: int, mode: str = "remembered") -> dict:
    preferences = load_preferences()
    if mode == "remembered":
        mode = (preferences.get("editor") or {}).get("mode", "explicit-exe")
    if mode == "windows-default":
        if os.name != "nt":
            raise OSError("Windows 默认程序仅支持 Windows。")
        preferences["editor"] = {"mode": "windows-default"}
        save_preferences(preferences)
        os.startfile(str(source.resolve()))
        return {"status": "opened", "positioned": False, "editor": "Windows 默认程序"}
    configured = preferences.get("editor") or {}
    executable = Path(configured.get("executable", ""))
    if not executable.is_file():
        raise ValueError("尚未选择有效的 Markdown 编辑器。")
    arguments, positioned = command(executable.resolve(), source, line, column)
    subprocess.Popen(arguments)
    return {"status": "opened", "positioned": positioned, "editor": executable.stem, "line": line, "column": column}


def open_default(source: Path) -> dict:
    if os.name != "nt":
        raise OSError("Windows 默认程序仅支持 Windows。")
    os.startfile(str(source.resolve()))
    return {"status": "opened", "application": "Windows 默认程序"}


def select_editor() -> dict:
    selected = choose_editor()
    if not selected:
        return {"status": "cancelled"}
    preferences = load_preferences()
    preferences["editor"] = {"mode": "explicit-exe", "executable": str(selected), "kind": editor_kind(selected)}
    save_preferences(preferences)
    return {"status": "selected", "editor": preferences["editor"]}
