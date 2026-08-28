from __future__ import annotations

import shutil
import struct
import time
import zlib
from collections.abc import Mapping
from pathlib import Path

from .errors import MdocError
from .io import canonical_digest, file_digest
from .paths import staged_target

EXPLICIT = {"blocked", "needs_retake", "waived", "not_applicable"}
VALID = {"pending", "captured", "blocked", "needs_retake", "waived", "not_applicable", "accepted"}
USER_SETTABLE = {"pending", "blocked", "needs_retake", "waived", "not_applicable"}


def png_info(path: Path) -> dict | None:
    """Return validated PNG or JPEG metadata for declared screenshot assets.

    The legacy function name remains part of the public Python surface. New
    screenshot tasks may use .png, .jpg, or .jpeg capture filenames.
    """

    try:
        data = path.read_bytes()
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            offset = 8
            width = height = None
            seen_iend = False
            seen_idat = False
            while offset + 12 <= len(data):
                length = struct.unpack(">I", data[offset:offset + 4])[0]
                chunk_type = data[offset + 4:offset + 8]
                end = offset + 12 + length
                if end > len(data):
                    return None
                payload = data[offset + 8:offset + 8 + length]
                expected = struct.unpack(">I", data[offset + 8 + length:end])[0]
                if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected:
                    return None
                if offset == 8:
                    if chunk_type != b"IHDR" or length != 13:
                        return None
                    width, height = struct.unpack(">II", payload[:8])
                if chunk_type == b"IDAT":
                    seen_idat = True
                if chunk_type == b"IEND":
                    seen_iend = length == 0 and end == len(data)
                    break
                offset = end
            if not seen_iend or not seen_idat or not width or not height:
                return None
            return {"sha256": file_digest(path), "width": width, "height": height, "bytes": len(data), "format": "PNG"}
        if data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
            return None
        offset = 2
        start_of_frame = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        standalone = {0x01, *range(0xD0, 0xD8)}
        while offset + 1 < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                return None
            marker = data[offset]
            offset += 1
            if marker == 0xD9:
                return None
            if marker in standalone:
                continue
            if offset + 2 > len(data):
                return None
            length = struct.unpack(">H", data[offset:offset + 2])[0]
            if length < 2 or offset + length > len(data):
                return None
            if marker in start_of_frame:
                if length < 8:
                    return None
                height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
                if not width or not height:
                    return None
                return {"sha256": file_digest(path), "width": width, "height": height, "bytes": len(data), "format": "JPEG"}
            offset += length
        return None
    except (OSError, struct.error):
        return None


def declared(task) -> list[tuple[dict, str, str, Path]]:
    items = []
    for requirement in task.definition["screenshots"]:
        for locale in requirement["locales"]:
            target = task.directory / "captures" / locale / requirement["filename"]
            items.append((requirement, f"{requirement['id']}:{locale}", locale, target))
    return items


def synchronize(task, state: dict) -> dict:
    plan = task.definition["locale_plan"]
    requirements = {item["id"]: item for item in task.definition["screenshots"]}
    for requirement_id, requirement in requirements.items():
        for locale in requirement["locales"]:
            strategy = plan["targets"].get(locale, {}).get("screenshots")
            if not isinstance(strategy, Mapping):
                continue
            source_locale = strategy["copy_from"]
            if source_locale not in requirement["locales"]:
                raise MdocError("MDOC-SCREENSHOT-COPY-SOURCE-MISSING", "截图 copy_from 来源语言未声明同一截图。", {"id": requirement_id, "locale": locale, "source": source_locale})
            source = task.directory / "captures" / source_locale / requirement["filename"]
            target = task.directory / "captures" / locale / requirement["filename"]
            source_info = png_info(source)
            if source_info and (not target.is_file() or file_digest(target) != source_info["sha256"]):
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.tmp")
                shutil.copyfile(source, temporary)
                temporary.replace(target)
    previous = state.get("screenshots", {})
    current = {}
    for requirement, key, locale, target in declared(task):
        old = previous.get(key, {})
        explicit = old.get("status") if old.get("status") in EXPLICIT else None
        info = png_info(target)
        if explicit:
            status = explicit
        elif info:
            status = "captured"
        else:
            status = "pending"
        current[key] = {
            "id": requirement["id"], "locale": locale, "filename": requirement["filename"],
            "required": requirement["required"], "status": status, "file": info,
        }
        if explicit and old.get("reason"):
            current[key]["reason"] = str(old["reason"])
    state["screenshots"] = current
    fingerprint = {
        key: {
            "id": item["id"], "locale": item["locale"], "filename": item["filename"], "required": item["required"],
            "status": item["status"] if item["status"] in EXPLICIT else ("present" if item["file"] else "missing"),
            "reason": item.get("reason", ""), "file": item["file"],
        }
        for key, item in current.items()
    }
    digest = canonical_digest(fingerprint)
    acceptance = state.get("screenshot_acceptance")
    if acceptance and acceptance.get("manifest_digest") != digest:
        acceptance["status"] = "stale"
    submission = state.get("screenshot_submission")
    if submission and submission.get("manifest_digest") != digest:
        submission["status"] = "stale"
    return {"digest": digest, "items": current}


def set_status(task, state: dict, key: str, status: str, reason: str = "") -> None:
    if status not in USER_SETTABLE:
        raise MdocError("MDOC-SCREENSHOT-STATUS-INVALID", f"Screenshot status cannot be set manually: {status}")
    synchronize(task, state)
    if key not in state["screenshots"]:
        raise MdocError("MDOC-SCREENSHOT-NOT-DECLARED", f"Screenshot is not declared: {key}")
    state["screenshots"][key]["status"] = status
    if status in EXPLICIT and reason.strip():
        state["screenshots"][key]["reason"] = reason.strip()
    else:
        state["screenshots"][key].pop("reason", None)
    state["screenshot_acceptance"] = None
    if state.get("screenshot_submission"):
        state["screenshot_submission"]["status"] = "stale"


def readiness(task, state: dict) -> tuple[bool, dict]:
    manifest = synchronize(task, state)
    blockers = []
    for key, item in manifest["items"].items():
        if item["required"] and item["status"] not in {"captured", "accepted", "waived", "not_applicable"}:
            blockers.append(key)
    return not blockers, {**manifest, "blockers": blockers}


def accept(task, state: dict) -> dict:
    ready, manifest = readiness(task, state)
    if not ready:
        raise MdocError("MDOC-SCREENSHOTS-INCOMPLETE", "Required screenshots are not complete.", {"items": manifest["blockers"]})
    copies = []
    for requirement, key, locale, capture in declared(task):
        item = state["screenshots"][key]
        if item["status"] != "captured":
            continue
        destination = requirement["destinations"].get(locale)
        if not destination:
            raise MdocError("MDOC-SCREENSHOT-DESTINATION-MISSING", f"No destination for {key}")
        if not capture.is_file():
            raise MdocError("MDOC-SCREENSHOT-FILE-MISSING", f"Captured screenshot is missing: {key}")
        copies.append((key, capture, staged_target(task, locale, destination)))
    for key, capture, target in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        shutil.copy2(capture, temporary)
        temporary.replace(target)
    manifest = synchronize(task, state)
    state["screenshot_acceptance"] = {"status": "accepted", "manifest_digest": manifest["digest"], "at": int(time.time())}
    return manifest


def submit(task, state: dict) -> dict:
    ready, manifest = readiness(task, state)
    if not ready:
        raise MdocError("MDOC-SCREENSHOTS-INCOMPLETE", "Required screenshots are not complete.", {"items": manifest["blockers"]})
    state["screenshot_submission"] = {"status": "submitted", "manifest_digest": manifest["digest"], "at": int(time.time())}
    return manifest
