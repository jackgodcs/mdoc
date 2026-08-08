#!/usr/bin/env python3
"""Windows source-editor preferences and safe argument construction."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def default_preferences_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "mdoc" / "manual-tools" / "pdf-check-preferences.json"


def load_preferences(path: Path | None = None) -> dict:
    path = path or default_preferences_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 2}


def save_preferences(path: Path, source_editor: dict):
    executable = source_editor.get("executable")
    if source_editor.get("mode") == "explicit-exe" and (not executable or Path(executable).suffix.lower() != ".exe" or not Path(executable).is_file()):
        raise ValueError("source editor must be an existing .exe file")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({"schema_version": 2, "source_editor": source_editor}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def editor_command(preferences: dict, source: Path, line: int) -> list[str] | None:
    editor = preferences.get("source_editor", {})
    if editor.get("mode") != "explicit-exe":
        return None
    executable = Path(editor["executable"]).resolve()
    style = editor.get("argument_style", "file")
    if style == "goto":
        return [str(executable), "--goto", f"{source.resolve()}:{line}"]
    if style == "file-line":
        return [str(executable), str(source.resolve()), "--line", str(line)]
    return [str(executable), str(source.resolve())]


def open_mode(preferences: dict) -> str:
    mode = preferences.get("source_editor", {}).get("mode")
    return mode if mode in {"explicit-exe", "windows-default"} else "not-configured"


def choose_editor() -> Path | None:
    # Tk is optional for mdoc. Import it only when the user explicitly opens
    # the executable picker so headless/minimal Python runtimes can still run
    # deterministic PDF checks and use the Windows-default editor mode.
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk(); root.withdraw()
    try:
        selected = filedialog.askopenfilename(title="选择 Markdown 编辑器", filetypes=[("Windows 应用程序", "*.exe")])
        return Path(selected).resolve() if selected else None
    finally:
        root.destroy()


def choose_with_windows(source: Path):
    subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(source.resolve())])


def open_with_windows(source: Path):
    if os.name != "nt":
        raise OSError("Windows default application opening is only supported on Windows")
    os.startfile(str(source.resolve()))
