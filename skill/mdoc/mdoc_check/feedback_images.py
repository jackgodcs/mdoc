from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import re
from pathlib import Path

from PIL import Image

from .feedback import IMAGE_SUFFIXES, configuration, image_references


class ImageCandidates:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve(); self.lock = threading.RLock(); self.candidates = {}; self.undo = {}
        self.root = Path(tempfile.gettempdir()) / "mdoc" / "feedback" / hashlib.sha256(str(self.workspace).encode()).hexdigest()[:16]
        shutil.rmtree(self.root, ignore_errors=True); self.root.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _target(self, book: str, locale: str, logical: str, reference: str) -> Path:
        item = next((value for value in image_references(self.workspace, book, locale, logical) if value["reference"].casefold() == reference.casefold()), None)
        if not item or not item.get("editable"):
            raise ValueError(item.get("reason") if item else "当前图片引用不存在。")
        return Path(item["path"])

    def _candidate(self, target: Path) -> tuple[str, dict] | tuple[None, None]:
        with self.lock:
            return next(((key, item) for key, item in self.candidates.items() if item["target"] == target), (None, None))

    @staticmethod
    def _remove_files(path: Path, remove_parent: bool = False) -> None:
        legacy = path.with_suffix(".png") if path.suffix.casefold() != ".png" else None; path.unlink(missing_ok=True)
        if legacy: legacy.unlink(missing_ok=True)
        for image in (path, *([legacy] if legacy else [])):
            image.with_name(image.stem + ".mdoc-image-edit.json").unlink(missing_ok=True); shutil.rmtree(image.with_name(image.stem + ".mdoc-image-edit-assets"), ignore_errors=True)
        if remove_parent:
            try: path.parent.rmdir()
            except OSError: pass

    def list(self, book: str, locale: str, logical: str) -> list[dict]:
        results, grouped = [], {}
        for item in image_references(self.workspace, book, locale, logical):
            key = str(item.get("path") or item["reference"]).casefold()
            if key in grouped: grouped[key]["lines"] = sorted(set(grouped[key]["lines"] + item["lines"])); continue
            grouped[key] = item; results.append(item)
        for item in results:
            target = Path(item["path"]) if item.get("path") else None; key, candidate = self._candidate(target) if target else (None, None)
            item["candidate_key"] = key or ""; item["candidate"] = bool(candidate and candidate["path"].is_file()); item["revertible"] = bool(target and str(target) in self.undo)
            if target and target.is_file(): item["info"] = self._validate(target)
            if candidate and candidate["path"].is_file(): item["candidate_info"] = self._validate(candidate["path"])
        return results

    def has_pending(self) -> bool:
        with self.lock: return any(item["path"].is_file() for item in self.candidates.values())

    def _validate(self, path: Path) -> dict:
        limits = configuration(self.workspace)["images"]
        if path.stat().st_size > int(limits["max_bytes"]): raise ValueError("图片超过 20 MB 限制。")
        with Image.open(path) as image:
            image.load(); width, height = image.size; actual = (image.format or "").casefold()
        if width > int(limits["max_width"]) or height > int(limits["max_height"]): raise ValueError("图片像素超过 4096×4096 限制。")
        expected = {".png":"png", ".jpg":"jpeg", ".jpeg":"jpeg"}.get(path.suffix.casefold())
        warning = f"图片实际格式为 {actual.upper()}，但扩展名为 {path.suffix[1:].upper()}。" if expected and actual != expected else ""
        return {"width":width, "height":height, "bytes":path.stat().st_size, "format":actual, "warning":warning}

    def create(self, session: str, book: str, locale: str, logical: str, reference: str, source: Path | None = None) -> dict:
        if not re.fullmatch(r"[0-9a-f-]{16,64}", session, re.IGNORECASE): raise ValueError("图片编辑会话标识无效。")
        target = self._target(book, locale, logical, reference); old_key, old = self._candidate(target); key = hashlib.sha256(f"{session}\0{target}".encode()).hexdigest()[:20]; directory = self.root / session
        candidate = directory / (key + target.suffix.lower())
        if old: self._remove_files(old["path"], True)
        directory.mkdir(parents=True, exist_ok=True)
        if source and source.suffix.casefold() != target.suffix.casefold():
            with Image.open(source) as image:
                converted=image.convert("RGBA") if target.suffix.casefold()==".png" else image.convert("RGB")
                converted.save(candidate,format="PNG" if target.suffix.casefold()==".png" else "JPEG",quality=95,subsampling=0)
        else: shutil.copy2(source or target, candidate)
        info = self._validate(candidate)
        with self.lock:
            if old_key and old_key != key: self.candidates.pop(old_key, None)
            self.candidates[key] = {"path":candidate, "target":target, "book":book, "locale":locale, "logical":logical, "reference":reference}
        return {"key":key, "target":str(target), "candidate":str(candidate), "info":info}

    def copy_locale(self, session: str, book: str, locale: str, logical: str, reference: str, source_locale: str) -> dict:
        source = self._target(book, source_locale, logical, reference)
        return self.create(session, book, locale, logical, reference, source)

    def path(self, key: str) -> Path:
        item = self.candidates.get(key)
        if not item or not item["path"].is_file(): raise ValueError("图片候选已失效。")
        return item["path"]

    def upload(self, session: str, book: str, locale: str, logical: str, reference: str, data: bytes, suffix: str) -> dict:
        if suffix.casefold() not in IMAGE_SUFFIXES: raise ValueError("只支持 PNG/JPG/JPEG 图片。")
        temporary = self.root / session / ("upload" + suffix.lower()); temporary.parent.mkdir(parents=True, exist_ok=True); temporary.write_bytes(data)
        self._validate(temporary); value = self.create(session, book, locale, logical, reference, temporary); temporary.unlink(missing_ok=True); return value

    def open_editor(self, key: str) -> dict:
        item = self.candidates.get(key)
        if not item: raise ValueError("图片候选已失效。")
        launcher = Path.home() / ".codex" / "skills" / "mdoc" / "Open-mdoc-Image-Editor.cmd"
        if not launcher.is_file(): raise ValueError("未找到 mdoc 图片编辑器。")
        subprocess.Popen([os.environ.get("ComSpec", "cmd.exe"), "/d", "/s", "/c", str(launcher), str(item["path"]), "--managed-candidate"], creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)); return {"status":"opened"}

    def info(self, key: str) -> dict:
        item = self.candidates.get(key)
        if not item or not item["path"].is_file(): raise ValueError("图片候选已失效。")
        legacy = item["path"].with_suffix(".png") if item["path"].suffix.casefold() != ".png" else None
        if legacy: legacy.unlink(missing_ok=True)
        return {"key":key, "target":str(item["target"]), "candidate":str(item["path"]), "info":self._validate(item["path"])}

    def discard(self, key: str) -> dict:
        with self.lock: item = self.candidates.pop(key, None)
        if not item: raise ValueError("图片候选已失效。")
        self._remove_files(item["path"], True); return {"discarded": True}

    def save(self, key: str) -> dict:
        item = self.candidates.get(key)
        if not item: raise ValueError("图片候选已失效。")
        target, candidate = item["target"], item["path"]; source_info = self._validate(candidate)
        if not target.is_file(): raise ValueError("正式图片已不存在。")
        before = target.read_bytes(); old = self._validate(target); output = candidate.read_bytes()
        if candidate.suffix.casefold() != target.suffix.casefold():
            with Image.open(candidate) as image:
                converted = image.convert("RGBA") if target.suffix.casefold()==".png" else image.convert("RGB")
                handle,path=tempfile.mkstemp(prefix=target.name+".",suffix=".tmp",dir=target.parent);os.close(handle)
                try: converted.save(path,format="PNG" if target.suffix.casefold()==".png" else "JPEG",quality=95,subsampling=0);output=Path(path).read_bytes()
                finally: Path(path).unlink(missing_ok=True)
        backup = self.root / "undo" / (hashlib.sha256(str(target).encode()).hexdigest()+target.suffix); backup.parent.mkdir(parents=True,exist_ok=True); backup.write_bytes(before)
        temporary=target.with_name("."+target.name+".tmp");temporary.write_bytes(output);os.replace(temporary,target);self._validate(target)
        self.undo[str(target)]={"backup":backup,"state":hashlib.sha256(output).hexdigest()};self.candidates.pop(key,None);self._remove_files(candidate, True)
        return {"saved":True,"target":str(target),"old":old,"new":source_info,"warning":"图片尺寸或宽高比已变化。" if (old["width"],old["height"])!=(source_info["width"],source_info["height"]) else ""}

    def revert(self, book: str, locale: str, logical: str, reference: str, force: bool = False) -> dict:
        target=self._target(book,locale,logical,reference);item=self.undo.get(str(target))
        if not item: raise ValueError("当前服务会话没有可撤销的图片替换。")
        if hashlib.sha256(target.read_bytes()).hexdigest()!=item["state"] and not force: return {"reverted":False,"conflict":True}
        temporary=target.with_name("."+target.name+".tmp");shutil.copy2(item["backup"],temporary);os.replace(temporary,target);self.undo.pop(str(target),None);item["backup"].unlink(missing_ok=True);return {"reverted":True,"conflict":False}
