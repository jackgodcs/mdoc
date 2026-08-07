#!/usr/bin/env python3
"""Build deterministic mdoc release assets without network access."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
DIST = ROOT / "dist"
ASSET = DIST / f"mdoc-{VERSION}.zip"
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
    skill = ROOT / "skill" / "mdoc"
    for path in sorted(skill.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            result.append((path, (Path("skill") / "mdoc" / path.relative_to(skill)).as_posix()))
    result.extend((ROOT / name, name) for name in ("install-mdoc.cmd", "install-mdoc.ps1"))
    return result


def main() -> int:
    shutil.rmtree(DIST, ignore_errors=True)
    DIST.mkdir()
    with zipfile.ZipFile(ASSET, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, name in collect():
            archive.writestr(zip_info(name, path.suffix in {".py", ".cmd", ".ps1"}), path.read_bytes())
    digest = sha256(ASSET)
    (DIST / f"{ASSET.name}.sha256").write_text(f"{digest}  {ASSET.name}\n", encoding="ascii", newline="\n")
    manifest = {
        "schema_version": 1, "product": "mdoc", "version": VERSION,
        "platform": "windows-x86_64", "asset": ASSET.name, "sha256": digest,
        "license": "Apache-2.0", "copyright": "Copyright 2026 cshuan",
    }
    (DIST / "RELEASE-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    components = [{"type": "application", "name": "mdoc", "version": VERSION, "licenses": [{"license": {"id": "Apache-2.0"}}]}]
    sbom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1, "components": components}
    (DIST / "mdoc-sbom.cdx.json").write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8", newline="\n")
    for name in ("install-mdoc.cmd", "install-mdoc.ps1"):
        shutil.copy2(ROOT / name, DIST / name)
    print(ASSET)
    print(digest)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
