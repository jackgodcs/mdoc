#!/usr/bin/env python3
"""PDF Check build-adapter execution."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path


def run_adapter(adapter: dict, source_root: Path, output: Path, role: str, extra_environment: dict | None = None) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = output.with_suffix(".build.json")
    manifest.write_text(json.dumps({"schema_version": 1, "adapter_id": adapter["id"], "locale": adapter.get("locale"), "role": role, "source_root": str(source_root.resolve()), "output_path": str(output.resolve())}, indent=2), encoding="utf-8")
    environment = os.environ.copy()
    environment.update(extra_environment or {})
    environment.update({"GV_MANUAL_SOURCE_ROOT": str(source_root.resolve()), "GV_MANUAL_OUTPUT_PATH": str(output.resolve()), "GV_MANUAL_LOCALE": adapter.get("locale", "unknown"), "GV_MANUAL_BUILD_ROLE": role, "GV_MANUAL_INSTRUMENTED": "true" if role == "mapping" else "false", "GV_MANUAL_BUILD_MANIFEST": str(manifest.resolve())})
    started = time.time()
    command = list(adapter.get("command") or [])
    if not command:
        return {"id": adapter.get("id"), "status": "not-configured", "component": "pdf", "required": bool(adapter.get("required"))}
    completed = subprocess.run(command, cwd=adapter.get("working_directory") or source_root, env=environment, capture_output=True, text=True, timeout=int(adapter.get("timeout_seconds", 900)), check=False)
    output.with_suffix(".log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    valid = completed.returncode == 0 and output.is_file() and output.read_bytes()[:5] == b"%PDF-"
    return {"id": adapter["id"], "component": "pdf", "status": "passed" if valid else "failed", "returncode": completed.returncode, "duration_seconds": round(time.time() - started, 3), "required": bool(adapter.get("required")), "artifact": str(output)}
