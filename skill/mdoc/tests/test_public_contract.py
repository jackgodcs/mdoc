from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skill" / "mdoc"
PUBLIC_TEXT_ROOTS = [
    ROOT / "README.md",
    SKILL / "SKILL.md",
    SKILL / "references",
    SKILL / "assets",
    SKILL / "schemas",
]
TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".txt"}
REMOVED_PROTOCOL_FILES = {
    "product-profile.yaml",
    "decisions.yaml",
    "sources.yaml",
    "screenshots.yaml",
    "state.yaml",
    "structure.yaml",
    "publish-plan.yaml",
}
FORBIDDEN_PUBLIC_TEXT = {
    "old schema_version 2": re.compile(r"schema_version\s*:\s*2|\"schema_version\"\s*:\s*2"),
    "old repository flag": re.compile(r"--repository\b"),
    "removed product profile file": re.compile(r"product-profile\.yaml|product profile"),
    "removed screenshot state file": re.compile(r"screenshots\.yaml"),
    "removed decisions state file": re.compile(r"decisions\.yaml"),
    "removed task state yaml": re.compile(r"state\.yaml"),
    "removed source split file": re.compile(r"sources\.yaml"),
    "removed structure split file": re.compile(r"structure\.yaml"),
    "removed publish plan yaml": re.compile(r"publish-plan\.yaml"),
    "removed task root": re.compile(r"manual-tasks"),
    "removed setup command": re.compile(r"\bmdoc setup\b|\bnew-task\b|\bresume\b|\bswitch-book\b"),
    "advisory task gate": re.compile(r"Quality Gate .*?(?:可选|optional)|(?:可选|optional).*?Quality Gate|不是发布必选项"),
}


def public_text_files() -> list[Path]:
    files: list[Path] = []
    for root in PUBLIC_TEXT_ROOTS:
        if root.is_file():
            files.append(root)
            continue
        files.extend(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES)
    return sorted(files)


class PublicContractTests(unittest.TestCase):
    def test_public_docs_templates_and_schemas_do_not_advertise_removed_protocol(self) -> None:
        problems: list[str] = []
        for path in public_text_files():
            text = path.read_text(encoding="utf-8")
            relative = path.relative_to(ROOT).as_posix()
            if path.name in REMOVED_PROTOCOL_FILES:
                problems.append(f"removed protocol file remains: {relative}")
            for label, pattern in FORBIDDEN_PUBLIC_TEXT.items():
                if pattern.search(text):
                    problems.append(f"{label}: {relative}")
        self.assertEqual([], problems)


if __name__ == "__main__":
    unittest.main()
