from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


def freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class WorkspaceContext:
    repository: Path
    control: Path
    config: Mapping[str, Any]
    local: Mapping[str, Any]
    digest: str


@dataclass(frozen=True)
class TaskContext:
    workspace: WorkspaceContext
    task_id: str
    directory: Path
    definition: Mapping[str, Any]
    digest: str

