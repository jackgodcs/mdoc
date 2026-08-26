from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from .errors import MdocError
from .io import inside, relative_path


def book_definition(task) -> Mapping:
    return task.workspace.config["books"][task.definition["task"]["book"]]


def book_root(task) -> Path:
    return inside(task.workspace.repository, relative_path(book_definition(task)["root"], "book.root"))


def locale_root(task, locale: str) -> Path:
    book = book_definition(task)
    if locale not in book["locales"]:
        raise MdocError("MDOC-TASK-LOCALE-INVALID", f"Unknown locale: {locale}")
    return inside(book_root(task), relative_path(book["locales"][locale]["root"], f"locales.{locale}.root"))


def formal_target(task, locale: str, raw: str) -> Path:
    return inside(locale_root(task, locale), relative_path(raw, "change.path"))


def staged_target(task, locale: str, raw: str) -> Path:
    return inside(task.directory / "staging" / locale, relative_path(raw, "change.path"))


def changes(task) -> Iterable[tuple[dict, Path, Path]]:
    for change in task.definition["manifest"]:
        yield change, formal_target(task, change["locale"], change["path"]), staged_target(task, change["locale"], change["path"])
