#!/usr/bin/env python3
"""Build deterministic mdoc release assets without network access."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
DIST = ROOT / "dist"
ASSET = DIST / f"mdoc-{VERSION}-windows-x64.zip"
STAGED_ASSET = DIST / f".{ASSET.name}.building"
FILES = [ROOT / name for name in ("LICENSE", "NOTICE", "VERSION", "README.md", "SECURITY.md", "THIRD-PARTY-NOTICES.md")]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def zip_info(name: str, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = ((stat.S_IFREG | (0o755 if executable else 0o644)) << 16)
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def collect() -> list[tuple[Path, str]]:
    result = [(path, path.name) for path in FILES]
    result.extend((ROOT / name, name) for name in ("安装 mdoc.cmd", "install-mdoc.ps1", "repair-mdoc-runtime.ps1", "开始使用.txt"))
    result.extend((path, path.relative_to(ROOT).as_posix()) for folder in ("bootstrap", "runtime", "runtime-bootstrap") for path in sorted((ROOT / folder).rglob("*")) if path.is_file() and not path.name.startswith("test_") and "__pycache__" not in path.parts)
    skill = ROOT / "skill" / "mdoc"
    for path in sorted(skill.rglob("*")):
        relative = path.relative_to(skill)
        if path.is_file() and "__pycache__" not in path.parts and "tests" not in relative.parts and not path.name.startswith("test_"):
            result.append((path, (Path("skill") / "mdoc" / path.relative_to(skill)).as_posix()))
    return result


def main() -> int:
    DIST.mkdir(exist_ok=True)
    for path in DIST.iterdir():
        if path not in {ASSET, STAGED_ASSET}:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    STAGED_ASSET.unlink(missing_ok=True)
    collected = collect()
    requirements = ROOT / "runtime" / "requirements-v1.json"
    bootstrap = json.loads((ROOT / "bootstrap" / "toolchain-bootstrap.json").read_text(encoding="utf-8"))
    package_manifest = {
        "schema_version": 1,
        "product": "mdoc",
        "version": VERSION,
        "platform": "windows-x86_64",
        "license": "Apache-2.0",
        "copyright": "Copyright 2026 cshuan",
        "runtime_contract": {
            "toolchain_version": bootstrap["catalog_version"],
            "python": ">=3.12.0,<3.13.0",
            "profile": "Full",
            "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
            "runtime_rebuild_required": False,
        },
        "files": [
            {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path, name in sorted(collected, key=lambda item: item[1])
        ],
    }
    sbom = {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
        "components": [{"type": "application", "name": "mdoc", "version": VERSION, "licenses": [{"license": {"id": "Apache-2.0"}}]}],
    }
    with zipfile.ZipFile(STAGED_ASSET, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, name in collected:
            archive.writestr(zip_info(name, path.suffix in {".py", ".cmd", ".ps1"}), path.read_bytes())
        archive.writestr(zip_info("PACKAGE-MANIFEST.json"), (json.dumps(package_manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        archive.writestr(zip_info("metadata/mdoc-sbom.cdx.json"), (json.dumps(sbom, indent=2) + "\n").encode("utf-8"))
    digest = sha256(STAGED_ASSET)
    if ASSET.is_file() and sha256(ASSET) == digest:
        STAGED_ASSET.unlink()
    else:
        os.replace(STAGED_ASSET, ASSET)
    print(ASSET)
    print(digest)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
