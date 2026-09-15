from __future__ import annotations

import re
import threading
from pathlib import Path


WORD = re.compile(r"^[A-Za-z][A-Za-z0-9._+-]*$")
LOCK = threading.Lock()


def path(root: Path, language: str | None = None) -> Path:
    return root / ".mdoc" / (f"check-words.{language}.txt" if language else "check-words.txt")


def load(root: Path, language: str | None = None) -> list[str]:
    source = path(root, language)
    if not source.is_file():
        return []
    return sorted({line.strip() for line in source.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")}, key=str.lower)


def add(root: Path, word: str, language: str | None = None) -> list[str]:
    word = word.strip()
    if not WORD.fullmatch(word):
        raise ValueError("Dictionary word must be one Latin word without spaces.")
    with LOCK:
        words = load(root, language)
        if word.lower() not in {item.lower() for item in words}:
            words.append(word)
        words.sort(key=str.lower)
        target = path(root, language)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(words) + "\n", encoding="utf-8")
        return words


def remove(root: Path, word: str, language: str | None = None) -> list[str]:
    with LOCK:
        words = [item for item in load(root, language) if item.lower() != word.strip().lower()]
        target = path(root, language)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(("\n".join(words) + "\n") if words else "", encoding="utf-8")
        return words
