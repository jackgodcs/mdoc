from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mdoc.py"
YAML_WRITER = YAML()


def cli(*arguments: str, expected: int = 0) -> dict:
    result = subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != expected:
        raise AssertionError(result.stdout or result.stderr)
    return json.loads(result.stdout)


def write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        YAML_WRITER.dump(value, stream)
