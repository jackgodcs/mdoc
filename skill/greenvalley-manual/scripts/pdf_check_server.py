#!/usr/bin/env python3
"""Local-only PDF Check viewer server helpers."""

from __future__ import annotations

import json
import secrets
import subprocess
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pdf_check_core import confirm_ignore
from pdf_check_render import prepare_viewer_resources
from pdf_check_source_opener import (
    choose_editor, choose_with_windows, default_preferences_path, editor_command,
    load_preferences, open_mode, open_with_windows, save_preferences,
)


def load_report(report_path: Path) -> dict:
    return json.loads(report_path.read_text(encoding="utf-8"))


def resolve_source(report: dict, finding_id: str, book_root: Path) -> tuple[Path, int]:
    item = next((entry for entry in report.get("findings", []) if entry.get("id") == finding_id), None)
    if not item or not item.get("source_locations"):
        raise FileNotFoundError("finding has no source mapping")
    location = item["source_locations"][0]
    path = (book_root / location["file"]).resolve()
    root = book_root.resolve()
    if path.suffix.lower() != ".md" or not path.is_relative_to(root) or not path.is_file():
        raise PermissionError("source path is outside the allowed manual root")
    return path, int(location.get("start_line", 1))


def serve(report_path: Path, work_root: Path, book_root: Path, viewer_root: Path, overrides_path: Path | None = None, open_browser: bool = True, recheck_command: list[str] | None = None, preview_pdfs: dict[str, Path] | None = None) -> tuple[ThreadingHTTPServer, str]:
    token = secrets.token_urlsafe(24)
    class Handler(BaseHTTPRequestHandler):
        def allowed(self):
            return self.headers.get("X-GV-Token") == token or f"token={token}" in self.path
        def send_file(self, path: Path, content_type: str):
            if not path.is_file():
                self.send_error(404); return
            data = path.read_bytes(); self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def send_viewer_asset(self, asset: str):
            path = (viewer_root / asset).resolve()
            if not path.is_relative_to(viewer_root.resolve()):
                self.send_error(404); return
            self.send_file(path, {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript"}.get(path.suffix, "application/octet-stream"))
        def do_GET(self):
            if not self.allowed(): self.send_error(403); return
            clean = self.path.split("?", 1)[0]
            if clean == "/api/report":
                report = load_report(report_path)
                data = json.dumps(report, ensure_ascii=False).encode("utf-8"); self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.end_headers(); self.wfile.write(data); return
            if clean.startswith("/pages/"):
                report = load_report(report_path)
                parts = Path(clean).parts
                if len(parts) != 4: self.send_error(404); return
                artifact, name = parts[2], parts[3]
                if artifact not in {item.get("id") for item in report.get("artifacts", [])}: self.send_error(404); return
                self.send_file(work_root / "current" / "problem-pages" / artifact / name, "image/png"); return
            asset = "index.html" if clean == "/" else clean.lstrip("/")
            self.send_viewer_asset(asset)
        def do_POST(self):
            if not self.allowed(): self.send_error(403); return
            length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length) or b"{}")
            report = load_report(report_path)
            if self.path == "/api/open-source":
                source, line = resolve_source(report, payload.get("finding_id"), book_root)
                preferences = load_preferences()
                command = editor_command(preferences, source, line)
                if command: subprocess.Popen(command)
                elif open_mode(preferences) == "windows-default": open_with_windows(source)
                else: self.send_response(409); self.end_headers(); self.wfile.write(b"source editor is not configured"); return
            elif self.path == "/api/editor/select":
                selected = choose_editor()
                if selected:
                    save_preferences(default_preferences_path(), {"mode": "explicit-exe", "executable": str(selected), "argument_style": payload.get("argument_style", "file")})
            elif self.path == "/api/editor/windows-default":
                source, _ = resolve_source(report, payload.get("finding_id"), book_root)
                save_preferences(default_preferences_path(), {"mode": "windows-default"})
                choose_with_windows(source)
            elif self.path == "/api/recheck" and recheck_command:
                completed = subprocess.run(recheck_command, capture_output=True, text=True, timeout=3600, check=False)
                if completed.returncode not in {0, 1}:
                    self.send_response(500); self.end_headers(); self.wfile.write((completed.stdout + completed.stderr).encode("utf-8")); return
                if preview_pdfs:
                    prepare_viewer_resources(preview_pdfs, load_report(report_path), work_root)
            elif self.path == "/api/ignore" and overrides_path:
                confirm_ignore(report, payload.get("finding_id"), overrides_path, payload.get("reason") or "用户确认忽略")
            else: self.send_error(404); return
            self.send_response(204); self.end_headers()
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/?token={token}"
    if open_browser: webbrowser.open(url)
    return server, url
