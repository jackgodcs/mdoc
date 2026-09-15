from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ruamel.yaml import YAML

from .core import PROTOTYPE_CONFIG, _merge, book_context, load_workspace, summary_items
from .editor import load_preferences, save_preferences
from .model import digest
from .sources import source_path


def configuration(workspace: Path) -> dict:
    from .maintenance import configuration as retention_configuration
    value = {"enabled": True}
    for path in (PROTOTYPE_CONFIG, workspace / ".mdoc" / "check.yaml"):
        if path.is_file():
            value = _merge(value, (YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}).get("pdf_preview", {}))
    if not isinstance(value.get("enabled"), bool):
        raise ValueError("pdf_preview.enabled must be true or false.")
    retention = retention_configuration(workspace); value["retention_days"] = retention["pdf_preview_days"]; value["max_bytes"] = retention["pdf_preview_max_bytes"]; value["orphan_grace_minutes"] = retention["orphan_grace_minutes"]
    return value


def _display_context(workspace: Path, report: dict, display: str | None = None) -> tuple[str, str, str | None]:
    scope = report.get("scope") or {}; book = scope.get("book"); locale = scope.get("locale")
    if display and "/" in display: locale = display.split("/", 1)[0]
    if not book or not locale: raise ValueError("当前报告无法唯一确定书册和语言。")
    return book, locale, display.split("/", 1)[1] if display else None


def target_context(workspace: Path, report: dict, display: str) -> dict:
    config = load_workspace(workspace); book_id, locale, logical = _display_context(workspace, report, display)
    book, locale_root, _language = book_context(workspace, config, book_id, locale)
    if not logical or Path(logical).suffix.lower() not in {".md", ".markdown"}:
        return {"enabled": False, "reason": "当前文件不是 Markdown 文件。"}
    entries = summary_items(locale_root / book["navigation"]["summary"]); occurrences = []; summary_lines = (locale_root / book["navigation"]["summary"]).read_text(encoding="utf-8-sig").splitlines(); entry_lines = []
    for line_number, line in enumerate(summary_lines, 1):
        match = __import__("re").search(r"]\(([^)]+)\)", line)
        if match:
            path = unquote(urlsplit(match.group(1).strip().split(maxsplit=1)[0]).path).replace("\\", "/").removeprefix("./")
            entry_lines.append((path, line_number))
    for index, item in enumerate(entries):
        if item["path"].casefold() != logical.casefold(): continue
        trail = []
        for candidate in reversed(entries[:index]):
            if candidate["depth"] < item["depth"] and (not trail or candidate["depth"] < trail[-1][0]): trail.append((candidate["depth"], candidate["title"]))
        breadcrumb = " / ".join([value for _depth, value in reversed(trail)] + [item["title"]])
        section_index = index
        if index + 1 >= len(entries) or entries[index + 1]["depth"] <= item["depth"]:
            section_index = next((candidate for candidate in range(index - 1, -1, -1) if entries[candidate]["depth"] < item["depth"] and candidate + 1 < len(entries) and entries[candidate + 1]["depth"] > entries[candidate]["depth"]), index)
        occurrences.append({"id": str(index), "target": item["path"], "summary_line": entry_lines[index][1] if index < len(entry_lines) and entry_lines[index][0].casefold() == item["path"].casefold() else None, "section_target": entries[section_index]["path"], "section_summary_line": entry_lines[section_index][1] if section_index < len(entry_lines) and entry_lines[section_index][0].casefold() == entries[section_index]["path"].casefold() else None, "section_fallback": section_index == index and (index + 1 >= len(entries) or entries[index + 1]["depth"] <= item["depth"]), "title": item["title"], "breadcrumb": breadcrumb or item["title"], "depth": item["depth"]})
    standalone = report.get("context") == "task" or not occurrences
    return {"enabled": True, "book": book_id, "locale": locale, "path": display, "logical": logical, "occurrences": occurrences, "independent_supported": True, "standalone": standalone, "page_available": True, "section_available": bool(occurrences) and report.get("context") != "task", "reason": "将使用独立文件 PDF 生成，不包含整册章节编号。" if standalone else ""}


def _result_root(workspace: Path) -> Path:
    return workspace.resolve() / ".mdoc" / "cache" / "pdf-preview"


def _prune_empty(path: Path) -> None:
    boundary = path.resolve()
    for candidate in (path.resolve(), *path.resolve().parents):
        if candidate.name == "pdf-preview" and candidate.parent.name == "cache":
            boundary = candidate
            break
    current = path.resolve()
    if current != boundary and boundary not in current.parents:
        return
    while True:
        try: current.rmdir()
        except OSError: break
        if current == boundary: break
        current = current.parent


def _metadata(path: Path) -> dict | None:
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return None


class PdfPreviewManager:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve(); self.lock = threading.Lock(); self.job = None; self.doctor_cache = None
        self.cleanup()

    def cleanup(self) -> None:
        root = _result_root(self.workspace); config = configuration(self.workspace); threshold = time.time() - config["retention_days"] * 86400
        if not root.is_dir(): return
        for pending in root.rglob("pending-*"):
            if pending.is_dir() and time.time() - pending.stat().st_mtime >= config["orphan_grace_minutes"] * 60: shutil.rmtree(pending, ignore_errors=True)
        for meta in root.rglob("result.json"):
            if config["retention_days"] == 0 or meta.stat().st_mtime < threshold: shutil.rmtree(meta.parent, ignore_errors=True)
        retained = [(meta.stat().st_mtime, meta.parent, sum(path.stat().st_size for path in meta.parent.rglob("*") if path.is_file())) for meta in root.rglob("result.json")]
        total = sum(item[2] for item in retained)
        for _modified, directory, size in sorted(retained)[:-1]:
            if total <= config["max_bytes"]: break
            shutil.rmtree(directory, ignore_errors=True); total -= size
        for directory in sorted((path for path in root.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True): _prune_empty(directory)
        _prune_empty(root)

    def doctor(self, refresh: bool = False) -> dict:
        config = configuration(self.workspace)
        if not config["enabled"]: return {"enabled": False, "status": "disabled", "reason": "工作区已关闭 PDF 预览。"}
        if self.doctor_cache and not refresh: return self.doctor_cache
        command = shutil.which("mdoc") or shutil.which("mdoc.cmd")
        if not command: self.doctor_cache = {"enabled": True, "status": "failed", "reason": "未找到 mdoc 命令。"}; return self.doctor_cache
        completed = subprocess.run(self._command(command, "--json", "pdf", "doctor", "--workspace", str(self.workspace)), capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try: data = json.loads(completed.stdout)
        except json.JSONDecodeError: data = {"status": "failed", "reason": completed.stderr.strip() or "PDF 环境检查没有返回有效结果。"}
        data.update({"enabled": True, "command": command}); data.setdefault("reason", "" if data.get("status") == "passed" else "PDF 生成环境不可用。"); self.doctor_cache = data; return data

    @staticmethod
    def _command(command: str, *arguments: str) -> list[str]:
        if Path(command).suffix.casefold() in {".cmd", ".bat"}:
            return [os.environ.get("ComSpec", "cmd.exe"), "/d", "/s", "/c", command, *arguments]
        return [command, *arguments]

    def _public_result(self, result: dict | None) -> dict | None:
        if not result: return None
        pdf = Path(result.get("pdf", "")); return {**{key: value for key, value in result.items() if key != "pdf"}, "available": pdf.is_file(), "size": pdf.stat().st_size if pdf.is_file() else 0}

    def status(self) -> dict:
        with self.lock: return dict(self.job) if self.job else {"status": "idle"}

    def context(self, report_id: str, report: dict, display: str) -> dict:
        key = digest(display)[:16]; target = target_context(self.workspace, report, display); result = _metadata(_result_root(self.workspace) / "files" / key / "result.json")
        return {**target, "key": key, "environment": self.doctor(), "result": self._public_result(result), "job": self.status()}

    def feedback_context(self, book: str, locale: str, logical: str) -> dict:
        report = {"context": "book", "scope": {"kind": "page", "book": book, "locale": locale, "target": logical}}
        return self.context("feedback", report, f"{locale}/{logical}")

    def start_feedback(self, book: str, locale: str, logical: str, origin: dict) -> dict:
        report = {"context": "book", "scope": {"kind": "page", "book": book, "locale": locale, "target": logical}}
        context = target_context(self.workspace, report, f"{locale}/{logical}"); occurrence = context["occurrences"][0]["id"] if context["occurrences"] else None
        return self.start("feedback", report, f"{locale}/{logical}", "page", occurrence, origin)

    def book_preview_context(self, report_id: str, report: dict) -> dict:
        if report.get("context") == "task": return {"enabled": False, "reason": "任务报告不支持构建整册 PDF。", "environment": self.doctor(), "result": None, "job": self.status()}
        try: book, locale, _logical = _display_context(self.workspace, report)
        except ValueError as exc: return {"enabled": False, "reason": str(exc), "environment": self.doctor(), "result": None, "job": self.status()}
        result = _metadata(_result_root(self.workspace) / "books" / book / locale / "result.json")
        return {"enabled": True, "book": book, "locale": locale, "key": f"{book}/{locale}", "environment": self.doctor(), "result": self._public_result(result), "job": self.status()}

    def start(self, report_id: str, report: dict, display: str | None, scope: str, occurrence: str | None, origin: dict) -> dict:
        from .maintenance import cleanup
        cleanup(self.workspace, automatic=True)
        if scope not in {"page", "section", "book"}: raise ValueError("未知 PDF 构建范围。")
        available = self.book_preview_context(report_id, report) if scope == "book" else target_context(self.workspace, report, display or "")
        if not available.get("enabled"): raise ValueError(available.get("reason") or "PDF 预览不可用。")
        environment = self.doctor()
        if environment.get("status") != "passed": raise ValueError(environment.get("reason") or "PDF 生成环境不可用。")
        with self.lock:
            if self.job and self.job.get("status") not in {"completed", "failed"}: raise ValueError("当前报告服务已有 PDF 构建任务，请等待完成后重试。")
            job_id = uuid.uuid4().hex; self.job = {"id": job_id, "status": "waiting", "scope": scope, "path": display, "report": report_id, "origin": origin, "started_at": int(time.time())}
        threading.Thread(target=self._run, args=(job_id, report, display, scope, occurrence), daemon=True).start(); return self.status()

    def _run(self, job_id: str, report: dict, display: str | None, scope: str, occurrence: str | None) -> None:
        lock_path = self.workspace / ".mdoc" / "locks" / "pdf-build.lock"; lock_path.parent.mkdir(parents=True, exist_ok=True); acquired = False
        if lock_path.is_file():
            try:
                pid = int(lock_path.read_text(encoding="ascii").strip()); os.kill(pid, 0)
            except (OSError, ValueError): lock_path.unlink(missing_ok=True)
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.write(descriptor, str(os.getpid()).encode()); os.close(descriptor); acquired = True
        except FileExistsError:
            self._finish(job_id, "failed", error="该工作区已有 PDF 构建任务，请等待完成后重试。", stage="waiting"); return
        pending = None; root = None
        try:
            book, locale, logical = _display_context(self.workspace, report, display); title = None
            if scope != "book":
                context = target_context(self.workspace, report, display or "")
                selected = next((item for item in context["occurrences"] if item["id"] == str(occurrence)), None)
                if not selected and not context.get("standalone"): raise ValueError(context.get("reason") or "请选择有效的 Summary.md 目录位置。")
                if selected: logical = selected["section_target"] if scope == "section" else selected["target"]; title = selected["breadcrumb"]
            root = _result_root(self.workspace) / (Path("books") / book / locale if scope == "book" else Path("files") / digest(display or "")[:16])
            pending = root / ("pending-" + job_id); pending.mkdir(parents=True, exist_ok=True); output = pending / "preview.pdf"
            self._update(job_id, "preparing", "正在准备 PDF 构建环境。")
            if scope != "book" and context.get("standalone"):
                physical = source_path(self.workspace, report, display or ""); pdf_config = pending / "pdf-config.yaml"
                with pdf_config.open("w", encoding="utf-8", newline="\n") as stream: YAML().dump(load_workspace(self.workspace).get("pdf", {}).get("defaults", {}), stream)
                command = ["--json", "pdf", "build", "--file", str(physical), "--pdf-config", str(pdf_config), "--output", str(output), "--discard-work"]
                scope = "file"; title = physical.stem
            else:
                command = ["--json", "pdf", "build", "--workspace", str(self.workspace), "--book", book, "--locale", locale, "--scope", scope, "--output", str(output), "--yes", "--discard-work"]
                if scope != "book":
                    command.extend(["--target", logical]); summary_line = selected.get("section_summary_line") if scope == "section" else selected.get("summary_line")
                    if summary_line: command.extend(["--summary-line", str(summary_line)])
            self._update(job_id, "honkit", "正在调用 mdoc PDF 构建流程。")
            completed = subprocess.run(self._command(self.doctor()["command"], *command), capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try: data = json.loads(completed.stdout)
            except json.JSONDecodeError: data = {"status": "failed", "error": {"message": completed.stderr.strip() or "PDF 构建未返回有效结果。"}}
            item = (data.get("results") or [data])[0]; status = item.get("status", data.get("status"))
            error = item.get("error") or data.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            if completed.returncode or status == "failed" or not output.is_file():
                self._update(job_id, self.status().get("status", "honkit"), (message or "PDF 构建失败。")); raise ValueError(message or "PDF 构建失败。")
            final = root / "preview.pdf"; final.parent.mkdir(parents=True, exist_ok=True); os.replace(output, final); shutil.rmtree(pending, ignore_errors=True)
            result = {"status": "completed", "scope": scope, "book": book, "locale": locale, "path": display, "target": logical, "title": title or book, "generated_at": int(time.time()), "pdf": str(final), "key": f"{book}/{locale}" if scope == "book" else digest(display or "")[:16], "stale": False, "findings": item.get("findings", []), "log": (completed.stderr or completed.stdout)[-20000:]}
            (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self._finish(job_id, "completed", result=self._public_result(result))
        except Exception as exc:
            self._finish(job_id, "failed", error=str(exc), stage=self.status().get("status"), log=(locals().get("completed").stderr or locals().get("completed").stdout)[-20000:] if locals().get("completed") else "")
        finally:
            if pending: shutil.rmtree(pending, ignore_errors=True)
            if root: _prune_empty(root)
            if acquired: lock_path.unlink(missing_ok=True)

    def _update(self, job_id: str, status: str, message: str) -> None:
        with self.lock:
            if self.job and self.job["id"] == job_id: self.job.update({"status": status, "message": message})

    def _finish(self, job_id: str, status: str, **values) -> None:
        with self.lock:
            if self.job and self.job["id"] == job_id: self.job.update({"status": status, "finished_at": int(time.time()), **values})

    def pdf_path(self, kind: str, key: str) -> Path:
        if kind == "file":
            if not __import__("re").fullmatch(r"[0-9a-f]{16}", key): raise ValueError("PDF 预览标识无效。")
            path = _result_root(self.workspace) / "files" / key / "preview.pdf"
        elif kind == "book":
            book, locale = key.split("/", 1)
            if book not in load_workspace(self.workspace)["books"] or locale not in load_workspace(self.workspace)["books"][book]["locales"]: raise ValueError("PDF 预览标识无效。")
            path = _result_root(self.workspace) / "books" / book / locale / "preview.pdf"
        else: raise ValueError("未知 PDF 预览类型。")
        if not path.is_file(): raise ValueError("PDF 预览不存在或已过期。")
        meta = path.with_name("result.json"); value = _metadata(meta)
        if value and time.time() - float(value.get("last_accessed_at", 0)) >= 86400:
            value["last_accessed_at"] = int(time.time()); temporary = meta.with_name(meta.name + ".tmp"); temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); os.replace(temporary, meta)
        return path

    def open_default(self, kind: str, key: str) -> dict:
        path = self.pdf_path(kind, key)
        if os.name != "nt": raise ValueError("Windows 默认程序仅支持 Windows。")
        os.startfile(str(path)); return {"status": "opened"}

    def open_folder(self, kind: str, key: str) -> dict:
        path = self.pdf_path(kind, key)
        if os.name != "nt": raise ValueError("打开所在文件夹仅支持 Windows。")
        subprocess.Popen(["explorer.exe", "/select,", str(path)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)); return {"status": "opened"}

    def save_as(self, kind: str, key: str) -> dict:
        source = self.pdf_path(kind, key); preferences = load_preferences(); initial = (preferences.get("pdf_preview") or {}).get("save_directory") or str(Path.home())
        script = "import tkinter as tk\nfrom tkinter import filedialog\nr=tk.Tk();r.withdraw()\ntry: print(filedialog.asksaveasfilename(title='另存 PDF',initialdir=" + repr(initial) + ",initialfile=" + repr(source.name) + ",defaultextension='.pdf',filetypes=[('PDF 文件','*.pdf')],confirmoverwrite=False),end='')\nfinally:r.destroy()"
        completed = subprocess.run([__import__("sys").executable, "-c", script], capture_output=True, text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)); target = Path(completed.stdout.strip()) if completed.stdout.strip() else None
        if not target: return {"status": "cancelled"}
        if target.exists():
            confirmation = "import tkinter as tk\nfrom tkinter import messagebox\nr=tk.Tk();r.withdraw()\ntry: print('yes' if messagebox.askyesno('\u8986\u76d6 PDF'," + repr(f"文件已存在：\n{target}\n\n是否覆盖？") + ",parent=r) else 'no',end='')\nfinally:r.destroy()"
            answer = subprocess.run([__import__("sys").executable, "-c", confirmation], capture_output=True, text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if answer.stdout.strip() != "yes": return {"status": "cancelled"}
        shutil.copy2(source, target); preferences["pdf_preview"] = {"save_directory": str(target.parent)}; save_preferences(preferences); return {"status": "saved", "path": str(target)}

    def mark_stale(self, display: str) -> None:
        paths = [_result_root(self.workspace) / "files" / digest(display)[:16] / "result.json", *_result_root(self.workspace).glob("books/*/*/result.json")]
        for meta in paths:
            value = _metadata(meta)
            if value and not value.get("stale"): value["stale"] = True; meta.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
