from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import __version__ as PILLOW_VERSION

from .model import finding


ROOT = Path(__file__).resolve().parents[1]
TOOLCHAIN = Path(os.environ.get("MDOC_TOOLCHAIN_ROOT", Path(os.environ.get("LOCALAPPDATA", "")) / "mdoc" / "toolchain"))
NODE = Path(os.environ.get("MDOC_CHECK_NODE", TOOLCHAIN / "node" / "node.exe"))
BRIDGE = Path(os.environ.get("MDOC_CHECK_BRIDGE", TOOLCHAIN / "markdown-check" / "bridges" / "markdown.mjs"))
CSPELL = Path(os.environ.get("MDOC_CHECK_CSPELL", TOOLCHAIN / "markdown-check" / "node_modules" / "cspell" / "bin.mjs"))
VALE = Path(os.environ.get("MDOC_CHECK_VALE", TOOLCHAIN / "vale" / "vale.exe"))
class CheckerError(RuntimeError):
    pass


def _batches(files: list[Path], command_chars: int = 12000) -> list[list[Path]]:
    batches = []
    current = []
    size = 0
    for path in files:
        length = len(str(path.resolve())) + 3
        if current and size + length > command_chars:
            batches.append(current)
            current, size = [], 0
        current.append(path)
        size += length
    if current:
        batches.append(current)
    return batches


def _remaining(deadline: float) -> int:
    seconds = math.ceil(deadline - time.monotonic())
    if seconds <= 0:
        raise CheckerError("Checker timed out.")
    return seconds


def _run(command: list[str], *, cwd: Path = ROOT, input_text: str | None = None, timeout: int = 60, accepted: set[int] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, cwd=cwd, input=input_text, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env={**os.environ, "NO_COLOR": "1"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckerError(str(exc)) from exc
    if result.returncode not in (accepted or {0}):
        raise CheckerError(result.stderr.strip() or result.stdout.strip() or f"Checker exited with {result.returncode}")
    return result


def tool_versions() -> dict[str, str]:
    markdown = json.loads(_run([str(NODE), str(BRIDGE)], input_text=json.dumps({"action": "versions"})).stdout)
    cspell_version = _run([str(NODE), str(CSPELL), "--version"]).stdout.strip()
    vale_version = _run([str(VALE), "--version"]).stdout.strip().rsplit(" ", 1)[-1]
    return {"markdownlint": markdown["markdownlint"], "markdown_it": markdown["markdownIt"], "cspell": cspell_version, "vale": vale_version, "pillow": PILLOW_VERSION}


def markdownlint(files: list[Path], displays: dict[Path, str], timeout: int = 60, severity_overrides: dict[str, str] | None = None) -> list[dict]:
    config = json.loads((ROOT / "config" / "markdownlint.json").read_text(encoding="utf-8"))
    severity_overrides = {str(rule).lower().removeprefix("markdown."): value for rule, value in (severity_overrides or {}).items()}
    request = {"action": "lint", "files": [str(path.resolve()) for path in files], "config": config}
    raw = json.loads(_run([str(NODE), str(BRIDGE)], input_text=json.dumps(request), timeout=timeout).stdout)
    results = []
    display_by_resolved = {str(path.resolve()).lower(): display for path, display in displays.items()}
    for name, errors in raw.items():
        display = display_by_resolved.get(str(Path(name).resolve()).lower(), name)
        for error in errors:
            native = error["ruleNames"][0]
            start = error.get("errorRange") or [1, 1]
            message = error.get("errorDetail") or error.get("errorContext") or error["ruleDescription"]
            severity = severity_overrides.get(native.lower(), error.get("severity", "error"))
            results.append(finding(f"markdown.{native.lower()}", severity, display, message, error["lineNumber"], start[0], checker="markdownlint", native_rule=native, end_line=error["lineNumber"], end_column=start[0] + max(start[1] - 1, 0)))
    return results


def vale(files: list[Path], displays: dict[Path, str], timeout: int = 60) -> list[dict]:
    deadline = time.monotonic() + timeout
    display_by_resolved = {str(path.resolve()).lower(): display for path, display in displays.items()}
    results = []
    for batch in _batches(files):
        command = [str(VALE), "--no-global", "--no-exit", "--no-wrap", "--output=JSON", f"--config={ROOT / 'config' / 'vale' / '.vale.ini'}", *[str(path.resolve()) for path in batch]]
        raw_text = _run(command, timeout=_remaining(deadline)).stdout.strip()
        for name, alerts in json.loads(raw_text or "{}").items():
            display = display_by_resolved.get(str(Path(name).resolve()).lower(), name)
            for alert in alerts:
                native = alert["Check"]
                severity = "error" if alert["Severity"].lower() == "error" else "warning"
                span = alert.get("Span") or [1, 1]
                results.append(finding(f"vale.{native}", severity, display, alert["Message"], alert["Line"], span[0], checker="vale", native_rule=native, end_line=alert["Line"], end_column=span[1], suggestions=alert.get("Match") and [alert["Match"]] or []))
    return results


def english_markdown(files: list[Path], scan_lines: int) -> list[Path]:
    raw = json.loads(_run([str(NODE), str(BRIDGE)], input_text=json.dumps({"action": "detect-english", "files": [str(path.resolve()) for path in files], "scanLines": scan_lines})).stdout)
    selected = {str(Path(path).resolve()).lower() for path in raw}
    return [path for path in files if str(path.resolve()).lower() in selected]


def cspell(files: list[Path], displays: dict[Path, str], timeout: int = 60, dictionary_files: list[Path] | None = None) -> list[dict]:
    reporter = "@cspell/cspell-json-reporter"
    deadline = time.monotonic() + timeout
    dictionary_definitions = []
    if dictionary_files:
        base = json.loads((ROOT / "config" / "cspell.json").read_text(encoding="utf-8"))
        for index, path in enumerate(dictionary_files):
            if path.is_file():
                dictionary_definitions.append({"name": f"mdoc{index}", "path": str(path.resolve()), "addWords": True})
        if dictionary_definitions:
            import tempfile

            config = {**base, "dictionaryDefinitions": [*base.get("dictionaryDefinitions", []), *dictionary_definitions], "dictionaries": [*base.get("dictionaries", []), *[item["name"] for item in dictionary_definitions]]}
            temporary = tempfile.NamedTemporaryFile("w", prefix="mdoc-check-cspell-", suffix=".json", encoding="utf-8", delete=False)
            json.dump(config, temporary, ensure_ascii=False, indent=2); temporary.close()
            config_path = Path(temporary.name)
        else:
            config_path = ROOT / "config" / "cspell.json"
    else:
        config_path = ROOT / "config" / "cspell.json"
    display_by_resolved = {str(path.resolve()).lower(): display for path, display in displays.items()}
    results = []
    seen = set()
    try:
        for batch in _batches(files):
            command = [str(NODE), str(CSPELL), "lint", "--config", str(config_path), "--no-config-search", "--no-progress", "--no-summary", "--no-color", "--reporter", reporter, "--file", *[str(path.resolve()) for path in batch]]
            result = _run(command, timeout=_remaining(deadline), accepted={0, 1})
            raw = json.loads(result.stdout or "{}")
            if raw.get("error"):
                raise CheckerError(str(raw["error"]))
            for issue in raw.get("issues", []):
                parsed = urlsplit(issue["uri"])
                issue_path = Path(unquote(parsed.path).lstrip("/") if parsed.scheme == "file" else issue["uri"])
                display = display_by_resolved.get(str(issue_path.resolve()).lower(), issue["uri"])
                key = (display, issue["text"].lower())
                if key in seen:
                    continue
                seen.add(key)
                suggestions = issue.get("suggestions") or []
                results.append(finding("spelling.unknown-word", "warning", display, f"Unknown English word: {issue['text']}", issue["row"], issue["col"], checker="cspell", native_rule="unknown-word", end_line=issue["row"], end_column=issue["col"] + issue.get("len", len(issue["text"])) - 1, suggestions=suggestions))
    finally:
        if dictionary_definitions:
            config_path.unlink(missing_ok=True)
    return results
