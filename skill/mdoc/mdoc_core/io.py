from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .errors import MdocError

try:
    from ruamel.yaml import YAML
except ImportError as exc:  # pragma: no cover - exercised by doctor in incomplete runtimes
    raise MdocError("MDOC-RUNTIME-DEPENDENCY-MISSING", "ruamel.yaml is required by the mdoc runtime.") from exc


YAML_READER = YAML(typ="safe")
YAML_WRITER = YAML()
YAML_WRITER.default_flow_style = False
YAML_WRITER.indent(mapping=2, sequence=4, offset=2)


def read_yaml(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = YAML_READER.load(stream)
    except FileNotFoundError as exc:
        raise MdocError("MDOC-FILE-MISSING", f"Required file is missing: {path}") from exc
    except Exception as exc:
        raise MdocError("MDOC-YAML-INVALID", f"Invalid YAML: {path}", {"cause": str(exc)}) from exc
    if not isinstance(value, dict):
        raise MdocError("MDOC-YAML-INVALID", f"YAML root must be an object: {path}")
    return value


def write_yaml_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            YAML_WRITER.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MdocError("MDOC-FILE-MISSING", f"Required file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MdocError("MDOC-JSON-INVALID", f"Invalid JSON: {path}", {"cause": str(exc)}) from exc
    if not isinstance(value, dict):
        raise MdocError("MDOC-JSON-INVALID", f"JSON root must be an object: {path}")
    return value


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(json_value(value), stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def canonical_digest(value: object) -> str:
    encoded = json.dumps(json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_path(raw: str, label: str) -> Path:
    normalized = raw.replace("\\", "/")
    raw_parts = normalized.split("/")
    if Path(raw).is_absolute() or raw.startswith(("/", "\\")) or (len(raw) >= 2 and raw[1] == ":") or any(part == ".." for part in raw_parts):
        raise MdocError("MDOC-PATH-UNSAFE", f"{label} must be a repository-relative path: {raw}")
    # A book may deliberately use the workspace directory itself as its root.
    # Preserve that explicit relative form instead of normalizing it to an
    # empty path; absolute and parent-directory paths remain rejected above.
    if normalized and all(part in {"", "."} for part in raw_parts):
        return Path(".")
    path = Path(*[part for part in raw_parts if part not in {"", "."}])
    if not path.parts:
        raise MdocError("MDOC-PATH-UNSAFE", f"{label} must not be empty: {raw}")
    return path


def inside(root: Path, relative: Path) -> Path:
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise MdocError("MDOC-PATH-UNSAFE", f"Path escapes its allowed root: {relative}") from exc
    return target
