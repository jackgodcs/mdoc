from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import shutil

from .errors import MdocError
from .io import canonical_digest, file_digest, inside, relative_path
from .paths import book_definition, book_root, changes


@dataclass(frozen=True)
class CandidateFile:
    locale: str
    path: str
    physical: Path
    origin: str
    changed: bool


def _logical(raw: str, label: str = "candidate.path") -> str:
    return relative_path(raw, label).as_posix()


class VirtualBook:
    """A read-only logical view over a formal book and an optional task overlay."""

    def __init__(self, workspace, book_id: str, overlays: dict[tuple[str, str], CandidateFile | None] | None = None):
        self.workspace = workspace
        self.book_id = book_id
        self.book = workspace.config["books"][book_id]
        self.root = inside(workspace.repository, relative_path(self.book["root"], f"books.{book_id}.root"))
        self._overlays = overlays or {}

    @classmethod
    def formal(cls, workspace, book_id: str) -> "VirtualBook":
        return cls(workspace, book_id)

    @classmethod
    def task(cls, task, *, published: bool = False) -> "VirtualBook":
        overlays: dict[tuple[str, str], CandidateFile | None] = {}
        for item, formal, staged in changes(task):
            key = (item["locale"], _logical(item["path"], "manifest.path"))
            if item["action"] == "delete":
                overlays[key] = None
            else:
                physical = formal if published else staged
                overlays[key] = CandidateFile(key[0], key[1], physical, "formal" if published else "staging", True)
        return cls(task.workspace, task.definition["task"]["book"], overlays)

    def locale_root(self, locale: str) -> Path:
        if locale not in self.book["locales"]:
            raise MdocError("MDOC-QUALITY-LOCALE-INVALID", f"书册未注册语言：{locale}")
        return inside(self.root, relative_path(self.book["locales"][locale]["root"], f"books.{self.book_id}.locales.{locale}.root"))

    def resolve(self, locale: str, raw: str) -> CandidateFile | None:
        logical = _logical(raw)
        key = (locale, logical)
        if key in self._overlays:
            candidate = self._overlays[key]
            return candidate if candidate is not None and candidate.physical.is_file() else None
        physical = inside(self.locale_root(locale), Path(*PurePosixPath(logical).parts))
        if not physical.is_file():
            return None
        return CandidateFile(locale, logical, physical, "formal", False)

    def files(self, locale: str) -> list[CandidateFile]:
        root = self.locale_root(locale)
        result: dict[str, CandidateFile] = {
            path.relative_to(root).as_posix(): CandidateFile(locale, path.relative_to(root).as_posix(), path, "formal", False)
            for path in root.rglob("*") if path.is_file()
        }
        for (overlay_locale, logical), candidate in self._overlays.items():
            if overlay_locale != locale:
                continue
            if candidate is None or not candidate.physical.is_file():
                result.pop(logical, None)
            else:
                result[logical] = candidate
        return [result[key] for key in sorted(result)]

    def resolve_link(self, source: CandidateFile, raw: str) -> CandidateFile | None:
        normalized = raw.replace(chr(92), "/")
        joined = PurePosixPath(source.path).parent.joinpath(PurePosixPath(normalized))
        parts: list[str] = []
        for part in joined.parts:
            if part in {"", "."}:
                continue
            if part == "..":
                if not parts:
                    return None
                parts.pop()
            else:
                parts.append(part)
        if not parts:
            return None
        return self.resolve(source.locale, "/".join(parts))

    def digest(self) -> str:
        values = {
            f"{item.locale}/{item.path}": file_digest(item.physical)
            for locale in sorted(self.book["locales"])
            for item in self.files(locale)
        }
        return canonical_digest(values)

    def materialize(self, destination: Path) -> None:
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        for locale in sorted(self.book["locales"]):
            locale_root = destination / self.book["locales"][locale]["root"]
            for item in self.files(locale):
                target = locale_root / Path(*PurePosixPath(item.path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item.physical, target)
