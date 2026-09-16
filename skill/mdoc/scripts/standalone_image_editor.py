#!/usr/bin/env python3
"""Standalone image editor built on the mdoc screenshot editing canvas."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from image_text_editor import DEFAULT_STYLE, IMAGE_LAYER_KINDS, PASTED_IMAGE_KIND, ImageTextEditor, TemplateStore, _atomic_json, default_font, system_fonts


SUPPORTED_INPUTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
OUTPUT_TYPES = {
    ".png": ("PNG", {}), ".jpg": ("JPEG", {"quality": 95, "subsampling": 0}), ".jpeg": ("JPEG", {"quality": 95, "subsampling": 0}),
    ".bmp": ("BMP", {}), ".gif": ("GIF", {}), ".tif": ("TIFF", {"compression": "tiff_lzw"}), ".tiff": ("TIFF", {"compression": "tiff_lzw"}), ".webp": ("WEBP", {"lossless": True}),
}
PROJECT_SUFFIX = ".mdoc-image-edit.json"
ASSET_SUFFIX = ".mdoc-image-edit-assets"


def project_paths(image: Path) -> tuple[Path, Path]:
    return image.with_name(f"{image.stem}{PROJECT_SUFFIX}"), image.with_name(f"{image.stem}{ASSET_SUFFIX}")


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def project_asset(directory: Path, name: object) -> Path | None:
    relative = Path(str(name))
    return directory / relative if not relative.is_absolute() and len(relative.parts) == 1 and relative.name not in {"", ".", ".."} else None


def load_project(image: Path, fonts: dict[str, Path] | None = None) -> tuple[dict | None, Path | None, list[dict], int]:
    record_path, assets = project_paths(image)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, AttributeError):
        return None, None, [], 0
    if not isinstance(record, dict) or record.get("schema_version") != 1 or record.get("mode") != "standalone":
        return None, None, [], 0
    size = record.get("image_size")
    try:
        with Image.open(image) as source:
            current_size = list(source.size)
    except OSError:
        return None, None, [], 0
    if size != current_size:
        return {"warning": "图片尺寸已变化，本次未加载工程图层。"}, None, [], 0
    base = project_asset(assets, record.get("base_snapshot", "base.png"))
    if base is None or not base.is_file():
        return {"warning": "工程底图不可用，本次仅打开当前图片。"}, None, [], 0
    layers, skipped = [], 0
    for layer in record.get("layers", []) if isinstance(record.get("layers", []), list) else []:
        if not isinstance(layer, dict) or not layer.get("id"):
            skipped += 1
            continue
        image_layer = layer.get("system_type") in IMAGE_LAYER_KINDS
        required = ("image_x", "image_y", "image_w", "image_h") if image_layer else ("text_x", "text_y", "bg_x", "bg_y", "bg_w", "bg_h")
        if any(not isinstance(layer.get(key), (int, float)) for key in required):
            skipped += 1
            continue
        asset = project_asset(assets, layer.get("image_asset", "")) if layer.get("system_type") == PASTED_IMAGE_KIND else None
        if layer.get("system_type") == PASTED_IMAGE_KIND and (asset is None or not asset.is_file()):
            skipped += 1
            continue
        loaded = copy.deepcopy(layer)
        if not image_layer:
            for key, value in DEFAULT_STYLE.items():
                loaded.setdefault(key, default_font(fonts) if key == "font" and fonts else value)
            if fonts and loaded.get("font") not in fonts:
                loaded["font"] = default_font(fonts)
            loaded.setdefault("text", "")
        layers.append(loaded)
    return record, base, layers, skipped


class StandaloneImageEditor(ImageTextEditor):
    def __init__(self, owner, image_path: Path, on_open, managed_candidate: bool = False):
        tk.Toplevel.__init__(self, owner)
        self.owner, self.on_open = owner, on_open
        self.task = self.entry = self.on_saved = None
        self.contributor = False
        self.fonts = system_fonts()
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "mdoc" / "standalone"
        self.store = TemplateStore(self.fonts, local)
        self.session_templates: list[dict] = []
        self.templates, self.layers = {}, []
        self.selected_template_id = self.selected_layer_id = None
        self.selected_part, self.undo_stack, self.redo_stack = "group", [], []
        self.dirty = self.image_dirty = self.project_dirty = False
        self.preview_mode = False
        self.drag_template = self.drag_ghost = self.eyedrop_target = None
        self.drag_state = self.pan_state = None
        self.space_down, self.guides, self.cycle_state = False, [], None
        self.scale, self.pan_x, self.pan_y, self.saved_view = 1.0, 20.0, 20.0, None
        self.base_source, self.base_image, self.base_has_alpha = "image", None, False
        self.editable, self.tracking_ready = True, False
        self.source_path = image_path.resolve()
        self.managed_candidate = managed_candidate
        self.image_output_path: Path | None = self.source_path if managed_candidate else None
        self.project_image_path: Path | None = self.source_path if managed_candidate else None
        self.session_directory = Path(tempfile.mkdtemp(prefix="mdoc-image-editor-"))
        self.asset_directory = self.session_directory / "assets"
        self.record_path = self.snapshot_path = self.session_directory / "unused"
        self.allow_overwrite_var = tk.BooleanVar(value=managed_candidate)
        self._load_standalone_image()
        self.geometry("1540x930")
        self.minsize(1180, 720)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._refresh_templates()
        self.after(120, self._restore_or_fit)

    def _load_standalone_image(self) -> None:
        record, base, layers, skipped = load_project(self.source_path, self.fonts)
        self._load_image(base or self.source_path)
        if record and base:
            self.layers = layers
            self.saved_view = record.get("view")
            self.selected_layer_id = record.get("selected_layer_id") if any(layer.get("id") == record.get("selected_layer_id") for layer in layers) else None
            self.image_output_path = self.project_image_path = self.source_path
            source_assets = project_paths(self.source_path)[1]
            if source_assets.is_dir():
                shutil.copytree(source_assets, self.asset_directory, dirs_exist_ok=True)
        self.load_warning = (record or {}).get("warning")
        if skipped:
            self.load_warning = f"已跳过 {skipped} 个无法加载的图层。"

    def _build(self) -> None:
        top = ttk.Frame(self, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        ttk.Button(top, text="打开图片", command=self.open_image, state="disabled" if self.managed_candidate else "normal").pack(side="left")
        ttk.Button(top, text="导入共享模板", command=self.import_shared_templates).pack(side="left", padx=(4, 0))
        ttk.Button(top, text="撤销", command=self.undo).pack(side="left")
        ttk.Button(top, text="重做", command=self.redo).pack(side="left", padx=(4, 12))
        self.preview_button = ttk.Button(top, text="预览结果", command=self.toggle_preview)
        self.preview_button.pack(side="left")
        ttk.Button(top, text="适应窗口", command=self.fit).pack(side="left", padx=(12, 4))
        ttk.Button(top, text="100%", command=lambda: self.set_zoom(1.0)).pack(side="left")
        ttk.Button(top, text="−", command=lambda: self.zoom_by(1 / 1.2)).pack(side="left", padx=(12, 2))
        self.zoom_label = ttk.Label(top, width=7, anchor="center")
        self.zoom_label.pack(side="left")
        ttk.Button(top, text="+", command=lambda: self.zoom_by(1.2)).pack(side="left", padx=2)
        image_menu = tk.Menu(self, tearoff=False)
        image_menu.add_command(label="保存图片", command=self.save_image)
        if not self.managed_candidate:
            image_menu.add_command(label="图片另存为", command=lambda: self.save_image(save_as=True))
        ttk.Menubutton(top, text="保存图片 ▼", menu=image_menu).pack(side="right", padx=(4, 0))
        project_menu = tk.Menu(self, tearoff=False)
        project_menu.add_command(label="保存工程", command=self.save_project)
        if not self.managed_candidate:
            project_menu.add_command(label="工程另存为", command=lambda: self.save_project(save_as=True))
        ttk.Menubutton(top, text="保存工程 ▼", menu=project_menu).pack(side="right", padx=(4, 0))
        ttk.Button(top, text="关闭", command=self.close).pack(side="right", padx=(0, 8))
        ttk.Checkbutton(top, text="受管候选：保存即更新" if self.managed_candidate else "允许覆盖原图", variable=self.allow_overwrite_var, state="disabled" if self.managed_candidate else "normal").pack(side="right", padx=(0, 8))

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        left, right = ttk.Frame(body, padding=4), ttk.Frame(body, padding=4)
        body.add(left, weight=1); body.add(right, weight=4)
        self._build_left(left)
        self.canvas = tk.Canvas(right, background="#2B2B2B", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        for sequence, handler in (
            ("<Configure>", lambda _event: self.redraw()), ("<ButtonPress-1>", self.canvas_press), ("<B1-Motion>", self.canvas_motion),
            ("<ButtonRelease-1>", self.canvas_release), ("<ButtonPress-2>", self.pan_press), ("<B2-Motion>", self.pan_motion),
            ("<ButtonRelease-2>", self.pan_release), ("<MouseWheel>", self.wheel), ("<Button-3>", self.layer_menu), ("<Double-Button-1>", self.double_click),
        ):
            self.canvas.bind(sequence, handler)
        for sequence, handler in (
            ("<KeyPress-space>", lambda _event: setattr(self, "space_down", True)), ("<KeyRelease-space>", lambda _event: setattr(self, "space_down", False)),
            ("<Delete>", lambda _event: self.delete_layer()), ("<BackSpace>", lambda _event: self.delete_layer()), ("<Escape>", lambda _event: self.escape()),
            ("<Control-z>", lambda _event: self.undo()), ("<Control-y>", lambda _event: self.redo()), ("<Control-v>", self.paste_clipboard_image),
            ("<Control-0>", lambda _event: self.fit()), ("<Control-1>", lambda _event: self.set_zoom(1.0)), ("<Tab>", lambda _event: self.toggle_preview()),
            ("<Left>", lambda _event: self.nudge(-1, 0)), ("<Right>", lambda _event: self.nudge(1, 0)), ("<Up>", lambda _event: self.nudge(0, -1)),
            ("<Down>", lambda _event: self.nudge(0, 1)), ("<Shift-Left>", lambda _event: self.nudge(-10, 0)), ("<Shift-Right>", lambda _event: self.nudge(10, 0)),
            ("<Shift-Up>", lambda _event: self.nudge(0, -10)), ("<Shift-Down>", lambda _event: self.nudge(0, 10)),
        ):
            self.bind(sequence, handler)
        self._update_title()
        if self.load_warning:
            self.after(150, lambda: messagebox.showwarning("mdoc", self.load_warning, parent=self))

    def _restore_or_fit(self) -> None:
        super()._restore_or_fit()
        self.tracking_ready = True
        self.image_dirty = self.project_dirty = False
        self._update_title()

    def _update_title(self) -> None:
        status = " *" if self.image_dirty else ""
        if self.project_dirty:
            status += " [工程未保存]"
        self.title(f"mdoc Image Editor - {self.source_path}{status}")

    def available_templates(self) -> list[dict]:
        return [*self.store.system_items(), *self.session_templates, *self.store.all()]

    def import_shared_templates(self) -> None:
        selected = filedialog.askopenfilename(parent=self, title="导入共享模板配置", filetypes=(("mdoc 模板配置", "*.json"),))
        if not selected:
            return
        try:
            value = json.loads(Path(selected).read_text(encoding="utf-8"))
            templates = value.get("templates") if value.get("schema_version") == 1 else None
            if not isinstance(templates, list):
                raise ValueError("不是有效的 mdoc 图片模板配置")
            self.session_templates = []
            for item in templates:
                if not isinstance(item, dict) or not item.get("id") or item.get("kind") not in {"default", "manual"}:
                    continue
                template = copy.deepcopy(item)
                style = template.setdefault("style", {})
                for key, default in DEFAULT_STYLE.items():
                    style.setdefault(key, default_font(self.fonts) if key == "font" else default)
                if style.get("font") not in self.fonts:
                    style["font"] = default_font(self.fonts)
                template.setdefault("text", "")
                template.setdefault("sources", [])
                self.session_templates.append(template)
        except (OSError, json.JSONDecodeError, ValueError, AttributeError) as exc:
            messagebox.showerror("mdoc", f"无法导入共享模板配置：\n{exc}", parent=self)
            return
        self._refresh_templates()
        messagebox.showinfo("mdoc", f"已为当前会话导入 {len(self.session_templates)} 个共享模板。", parent=self)

    def mark_dirty(self) -> None:
        self.dirty = self.image_dirty = self.project_dirty = True
        self._update_title()

    def _mark_view_dirty(self) -> None:
        if self.tracking_ready:
            self.project_dirty = True
            self._update_title()

    def fit(self) -> None:
        super().fit(); self._mark_view_dirty()

    def set_zoom(self, scale: float) -> None:
        super().set_zoom(scale); self._mark_view_dirty()

    def zoom_by(self, factor: float, x: float | None = None, y: float | None = None) -> None:
        super().zoom_by(factor, x, y); self._mark_view_dirty()

    def pan_release(self, event=None) -> None:
        moved = self.pan_state is not None
        super().pan_release(event)
        if moved:
            self._mark_view_dirty()

    def _image_destination(self, title: str, initial: Path) -> Path | None:
        suffix = initial.suffix.casefold() if initial.suffix.casefold() in OUTPUT_TYPES else ".png"
        labels = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".bmp": "BMP", ".gif": "GIF", ".tif": "TIFF", ".tiff": "TIFF", ".webp": "WebP"}
        patterns = {".jpg": "*.jpg *.jpeg", ".jpeg": "*.jpg *.jpeg", ".tif": "*.tif *.tiff", ".tiff": "*.tif *.tiff"}
        current = (f"{labels[suffix]} 图片", patterns.get(suffix, f"*{suffix}"))
        filetypes = (current,) if suffix == ".png" else (current, ("PNG 图片", "*.png"))
        selected = filedialog.asksaveasfilename(parent=self, title=title, initialdir=initial.parent, initialfile=initial.name, defaultextension=suffix, filetypes=filetypes)
        return Path(selected).resolve() if selected else None

    def _write_image(self, output: Path) -> tuple[bool, bool]:
        image_format, options = OUTPUT_TYPES.get(output.suffix.casefold(), (None, None))
        if image_format is None:
            messagebox.showerror("mdoc", "不支持该输出格式，请使用 PNG、JPG、JPEG、BMP、GIF、TIFF 或 WebP。", parent=self)
            return False, False
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp")
        try:
            image = self.composite(); alpha = image.mode in {"RGBA", "LA"} and image.getextrema()[-1][0] < 255
            flattened = image_format in {"JPEG", "BMP", "GIF"} and alpha
            if flattened:
                background = Image.new("RGB", image.size, "white"); background.paste(image, mask=image.getchannel("A")); image = background
            elif image_format in {"JPEG", "BMP", "GIF"}: image = image.convert("RGB")
            elif image_format in {"PNG", "WEBP", "TIFF"} and alpha: image = image.convert("RGBA")
            image.save(temporary, format=image_format, **options)
            temporary.replace(output)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            messagebox.showerror("mdoc", f"无法保存图片：\n{exc}", parent=self)
            return False, False
        return True, flattened

    def _confirm_flattened_overwrite(self, output: Path) -> bool:
        if getattr(self, "managed_candidate", False) or output != self.source_path or output.suffix.casefold() not in {".gif", ".tif", ".tiff", ".webp"}:
            return True
        return messagebox.askokcancel(
            "覆盖原图",
            "覆盖后将保存为当前编辑画面的单帧扁平图片，动画、多页和原始元数据不会保留。",
            parent=self,
        )

    def save_image(self, save_as: bool = False) -> bool:
        output = None if save_as else self.image_output_path
        if output is None and self.allow_overwrite_var.get():
            output = self.source_path
        if output is None or (output == self.source_path and not self.allow_overwrite_var.get() and self.project_image_path is None):
            output = self._image_destination("图片另存为", self.source_path.with_name(f"{self.source_path.stem}-edited{self.source_path.suffix.lower()}"))
        if output is None or not self._confirm_flattened_overwrite(output):
            return False
        saved, flattened = self._write_image(output)
        if not saved:
            return False
        self.image_output_path = self.project_image_path = output
        self.source_path = output
        self.image_dirty = False
        self.dirty = self.project_dirty
        self._update_title()
        messagebox.showinfo("mdoc", f"图片已保存：\n{output}" + ("\n目标格式不支持完整透明通道，透明区域已使用白色背景合成。" if flattened else ""), parent=self)
        return True

    def save_project(self, save_as: bool = False) -> bool:
        output = None if save_as else self.project_image_path
        if output is None and self.allow_overwrite_var.get():
            output = self.source_path
        if output is None:
            initial = self.image_output_path or self.source_path.with_name(f"{self.source_path.stem}-edited{self.source_path.suffix.lower()}")
            output = self._image_destination("工程另存为", initial)
        if output is None or not self._confirm_flattened_overwrite(output):
            return False
        saved, flattened = self._write_image(output)
        if not saved:
            return False
        record_path, target_assets = project_paths(output)
        temporary_assets = target_assets.with_name(f".{target_assets.name}.tmp")
        if temporary_assets.exists():
            shutil.rmtree(temporary_assets)
        temporary_assets.mkdir(parents=True)
        if target_assets.is_dir():
            shutil.copytree(target_assets, temporary_assets, dirs_exist_ok=True)
        used = {str(layer.get("image_asset")) for layer in self.layers if layer.get("system_type") == PASTED_IMAGE_KIND}
        for name in used:
            source = self.asset_directory / name
            if source.is_file():
                shutil.copy2(source, temporary_assets / name)
        self.base_image.save(temporary_assets / "base.png", format="PNG")
        previous_assets = target_assets.with_name(f".{target_assets.name}.previous")
        if previous_assets.exists():
            shutil.rmtree(previous_assets)
        if target_assets.exists():
            target_assets.replace(previous_assets)
        temporary_assets.replace(target_assets)
        shutil.rmtree(previous_assets, ignore_errors=True)
        _atomic_json(record_path, {
            "schema_version": 1, "mode": "standalone", "image": output.name, "image_sha256": file_digest(output),
            "image_size": list(self.base_image.size), "base_snapshot": "base.png", "layers": self.layers,
            "view": self._view_record(), "selected_layer_id": self.selected_layer_id,
        })
        self.image_output_path = self.project_image_path = output
        self.source_path = output
        self.image_dirty = self.project_dirty = self.dirty = False
        self._update_title()
        messagebox.showinfo("mdoc", f"工程已保存：\n{output}\n{record_path}" + ("\n目标格式不支持完整透明通道，透明区域已使用白色背景合成。" if flattened else ""), parent=self)
        return True

    def _confirm_leave(self) -> bool:
        if self.image_dirty:
            answer = messagebox.askyesnocancel("未保存图片", "成品图片尚未保存。\n\n是否先保存图片？", parent=self)
            if answer is None or (answer and not self.save_image()):
                return False
        if self.project_dirty:
            answer = messagebox.askyesnocancel("工程未保存", "成品图片与编辑工程分开保存。\n当前编辑图层尚未保存为工程，是否保存工程？", parent=self)
            if answer is None or (answer and not self.save_project()):
                return False
        return True

    def open_image(self) -> None:
        if not self._confirm_leave():
            return
        selected = choose_image(self)
        if selected:
            shutil.rmtree(self.session_directory, ignore_errors=True)
            self.destroy()
            self.on_open(selected)

    def close(self) -> None:
        if self._confirm_leave():
            shutil.rmtree(self.session_directory, ignore_errors=True)
            self.destroy()


def choose_image(parent=None) -> Path | None:
    selected = filedialog.askopenfilename(parent=parent, title="选择要编辑的图片", filetypes=(("支持的图片", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp"), ("所有文件", "*.*")))
    return Path(selected).resolve() if selected else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Open the standalone mdoc image editor.")
    parser.add_argument("image", nargs="?", type=Path)
    parser.add_argument("--managed-candidate", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.managed_candidate and args.image is None:
        parser.error("--managed-candidate requires an image path")
    root = tk.Tk(); root.withdraw()

    def close_if_empty() -> None:
        if not any(isinstance(item, StandaloneImageEditor) for item in root.winfo_children()):
            root.destroy()

    def open_editor(path: Path) -> None:
        if path.suffix.casefold() not in SUPPORTED_INPUTS or not path.is_file():
            messagebox.showerror("mdoc", f"无法打开图片：\n{path}", parent=root)
            path = choose_image(root)
            if path is None:
                root.destroy(); return
        try:
            editor = StandaloneImageEditor(root, path, open_editor, args.managed_candidate)
        except OSError as exc:
            messagebox.showerror("mdoc", f"无法读取图片：\n{exc}", parent=root)
            root.destroy(); return
        editor.bind("<Destroy>", lambda event: root.after_idle(close_if_empty) if event.widget is editor else None)

    initial = args.image.resolve() if args.image else choose_image(root)
    if initial is None:
        root.destroy(); return 0
    open_editor(initial)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
