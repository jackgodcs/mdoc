from __future__ import annotations

from pathlib import Path, PurePosixPath

from .core import book_context, load_workspace


def source_path(workspace: Path, report: dict, display: str, finding_book: str | None = None) -> Path:
    locale, logical = display.split("/", 1)
    logical_path = PurePosixPath(logical)
    if logical_path.is_absolute() or ".." in logical_path.parts:
        raise ValueError("源文件路径越界。")
    config = load_workspace(workspace)
    scope = report.get("scope") or {}
    book_id = finding_book or scope.get("book")
    if report.get("context") == "task":
        from .tasks import candidate_file, load_task

        definition, _state, directory = load_task(workspace, scope["task"])
        return candidate_file(workspace, config, definition, directory, locale, logical)
    if not book_id:
        for candidate_id, book in config["books"].items():
            if locale not in book["locales"]:
                continue
            _book, root, _language = book_context(workspace, config, candidate_id, locale)
            if (root / Path(*logical_path.parts)).is_file():
                book_id = candidate_id
                break
    if not book_id:
        raise ValueError("无法确定文件所属书册。")
    _book, locale_root, _language = book_context(workspace, config, book_id, locale)
    path = (locale_root / Path(*logical_path.parts)).resolve()
    path.relative_to(locale_root)
    return path
