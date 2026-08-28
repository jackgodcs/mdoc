#!/usr/bin/env python3
"""Interactive, task-scoped text replacement editor for mdoc screenshots."""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser, messagebox, simpledialog, ttk

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageTk

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from mdoc_core.locking import task_lock
from mdoc_core.paths import staged_target
from mdoc_core.screenshots import accept as accept_screenshots
from mdoc_core.screenshots import png_info, synchronize
from mdoc_core.state import load_state, save_state


DEFAULT_STYLE = {
    "font": "Segoe UI", "font_size": 9.0, "text_color": "#000000",
    "bg_color": "#FFFFFF", "padding": 0.0, "align": "left", "line_spacing": 1.2,
}
COVERAGE_GUTTER = 0.0
HANDLE_RADIUS = 5
MIN_FONT_SIZE = 1.0
MIN_BOX_SIZE = 2.0
SNAP_SCREEN_DISTANCE = 4.0


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def _hex(pixel: object) -> str:
    if isinstance(pixel, int):
        return f"#{pixel:02X}{pixel:02X}{pixel:02X}"
    values = tuple(pixel)
    return "#{:02X}{:02X}{:02X}".format(*values[:3])


def _font_directory() -> Path:
    return Path(os.environ.get("WINDIR") or os.environ.get("SystemRoot") or "") / "Fonts"


def system_fonts() -> dict[str, Path]:
    """Return usable Windows font faces, including installed variants."""
    result: dict[str, Path] = {}
    directory = _font_directory()
    if directory.is_dir():
        for path in sorted((*directory.glob("*.ttf"), *directory.glob("*.otf"), *directory.glob("*.ttc"))):
            try:
                face = ImageFont.truetype(path, 12)
                family, style = face.getname()
                label = family if style.lower() in {"regular", "roman", ""} else f"{family} {style}"
                if label in result:
                    label = f"{label} ({path.stem})"
                result[label] = path
            except OSError:
                continue
    if not result:
        result["Arial"] = Path("Arial")
    return result


def default_font(fonts: dict[str, Path]) -> str:
    for preferred in ("Segoe UI", "Segoe UI Regular", "Arial", "Arial Regular"):
        if preferred in fonts:
            return preferred
    return next(iter(fonts))


class TemplateStore:
    def __init__(self, fonts: dict[str, Path], workspace_control: Path):
        self.fonts = fonts
        self.shared_path = workspace_control / "image-text-editor.json"
        self.user_path = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "mdoc" / "image-text-editor.json"
        self.path = self.shared_path if self.shared_path.is_file() else self.user_path
        self.value = self._read()
        self._merge_defaults()

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value.get("schema_version") == 1 and isinstance(value.get("templates"), list):
                return value
            if value.get("schema_version") == 1:
                legacy = []
                for text, style in (value.get("default_styles") or {}).items():
                    legacy.append({"id": f"legacy:{uuid.uuid4().hex}", "kind": "default", "text": text, "sources": [], "style": style})
                for item in value.get("manual_templates") or []:
                    legacy.append({"id": item.get("id") or f"manual:{uuid.uuid4().hex}", "kind": "manual", "text": item.get("text", ""), "sources": [], "style": item.get("style") or {}})
                return {"schema_version": 1, "templates": legacy}
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        return {"schema_version": 1, "templates": []}

    def _merge_defaults(self) -> None:
        fallback = default_font(self.fonts)
        templates = self.value.setdefault("templates", [])
        for template in templates:
            style = template.setdefault("style", {})
            for key, value in DEFAULT_STYLE.items():
                style.setdefault(key, fallback if key == "font" else value)
            if style.get("font") not in self.fonts:
                style["font"] = fallback
            style["padding"] = 0.0
            template.setdefault("kind", "manual")
            template.setdefault("sources", [])
        self.save()

    def save(self) -> None:
        _atomic_json(self.path, self.value)

    def all(self) -> list[dict]:
        return [copy.deepcopy(item) for item in self.value["templates"]]

    def update_style(self, template: dict, style: dict) -> None:
        for item in self.value["templates"]:
            if item["id"] == template["id"]:
                item["style"] = copy.deepcopy(style)
                break
        self.save()

    def add(self, text: str) -> dict:
        item = {"id": f"manual:{uuid.uuid4().hex}", "kind": "manual", "text": text, "sources": [], "style": {**DEFAULT_STYLE, "font": default_font(self.fonts)}}
        self.value["templates"].append(item)
        self.save()
        return copy.deepcopy(item)

    def rename(self, template: dict, text: str) -> None:
        for item in self.value["templates"]:
            if item["id"] == template["id"]:
                item["text"] = text
                self.save()
                return

    def delete(self, template: dict) -> None:
        self.value["templates"] = [item for item in self.value["templates"] if item["id"] != template["id"]]
        self.save()


class ImageTextEditor(tk.Toplevel):
    def __init__(self, owner, task, entry: dict, on_saved=None, contributor: bool = False):
        super().__init__(owner)
        self.owner = owner
        self.task = task
        self.entry = entry
        self.on_saved = on_saved
        self.contributor = contributor
        self.fonts = system_fonts()
        self.store = TemplateStore(self.fonts, task.workspace.control)
        self.templates: dict[str, dict] = {}
        self.selected_template_id: str | None = None
        self.layers: list[dict] = []
        self.selected_layer_id: str | None = None
        self.selected_part = "group"
        self.undo_stack: list[list[dict]] = []
        self.redo_stack: list[list[dict]] = []
        self.dirty = False
        self.preview_mode = False
        self.drag_template: dict | None = None
        self.drag_ghost = None
        self.eyedrop_target: tuple[str, str] | None = None
        self.drag_state = None
        self.pan_state = None
        self.space_down = False
        self.guides: list[tuple[str, float]] = []
        self.cycle_state: tuple[float, float, list[str], int] | None = None
        self.scale = 1.0
        self.pan_x = 20.0
        self.pan_y = 20.0
        self.saved_view = None
        self.base_source = "original"
        self.base_path = Path(entry["original"])
        self.base_image = None
        self.base_has_alpha = False
        self.editable = True
        self.record_path, self.snapshot_path = self._record_paths()
        self.title(f"图片文字编辑器 — {entry['item']['id']}")
        self.geometry("1540x930")
        self.minsize(1180, 720)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._load_record()
        self._build()
        self._refresh_templates()
        self._refresh_base_choice()
        self.after(120, self._restore_or_fit)

    def _record_paths(self) -> tuple[Path, Path]:
        locale = self.entry["item"]["locale"]
        identifier = self.entry["item"]["id"].replace("/", "_").replace(":", "_")
        directory = self.task.directory / "image-edits" / locale
        return directory / f"{identifier}.json", directory / f"{identifier}.base.png"

    def _load_image(self, path: Path) -> None:
        with Image.open(path) as source:
            self.base_has_alpha = source.mode in {"RGBA", "LA"} or "transparency" in source.info
            self.base_image = source.convert("RGBA")
        self.base_path = path

    def _load_record(self) -> None:
        state = load_state(self.task.directory / "task-state.json", self.task.task_id)
        self.editable = state.get("status") not in {"ready_for_review", "accepted", "cancelled"}
        record = None
        try:
            record = json.loads(self.record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        if record and record.get("schema_version") == 1 and self.snapshot_path.is_file():
            self.base_source = record.get("base_source", "original")
            self._load_image(self.snapshot_path)
            self.layers = list(record.get("layers", []))
            self.saved_view = record.get("view")
            selected = record.get("selected_layer_id")
            self.selected_layer_id = selected if any(layer.get("id") == selected for layer in self.layers) else None
        else:
            self._load_image(Path(self.entry["original"]))

    def _build(self) -> None:
        top = ttk.Frame(self, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        ttk.Label(top, text="底图").pack(side="left")
        self.base_var = tk.StringVar()
        self.base_combo = ttk.Combobox(top, textvariable=self.base_var, state="readonly", width=20)
        self.base_combo.pack(side="left", padx=(4, 12))
        self.base_combo.bind("<<ComboboxSelected>>", self._switch_base)
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
        ttk.Button(top, text="保存", command=self.save).pack(side="right", padx=(4, 0))
        ttk.Button(top, text="保存并关闭", command=lambda: self.save(close=True)).pack(side="right")
        ttk.Button(top, text="关闭", command=self.close).pack(side="right", padx=(0, 12))

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        left = ttk.Frame(body, padding=4)
        right = ttk.Frame(body, padding=4)
        body.add(left, weight=1)
        body.add(right, weight=4)
        self._build_left(left)
        self.canvas = tk.Canvas(right, background="#2B2B2B", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self.canvas_press)
        self.canvas.bind("<B1-Motion>", self.canvas_motion)
        self.canvas.bind("<ButtonRelease-1>", self.canvas_release)
        self.canvas.bind("<ButtonPress-2>", self.pan_press)
        self.canvas.bind("<B2-Motion>", self.pan_motion)
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Button-3>", self.layer_menu)
        self.canvas.bind("<Double-Button-1>", self.double_click)
        self.bind("<KeyPress-space>", lambda _event: setattr(self, "space_down", True))
        self.bind("<KeyRelease-space>", lambda _event: setattr(self, "space_down", False))
        self.bind("<Delete>", lambda _event: self.delete_layer())
        self.bind("<BackSpace>", lambda _event: self.delete_layer())
        self.bind("<Escape>", lambda _event: self.escape())
        self.bind("<Control-z>", lambda _event: self.undo())
        self.bind("<Control-y>", lambda _event: self.redo())
        self.bind("<Control-0>", lambda _event: self.fit())
        self.bind("<Control-1>", lambda _event: self.set_zoom(1.0))
        self.bind("<Tab>", lambda _event: self.toggle_preview())
        self.bind("<Left>", lambda _event: self.nudge(-1, 0))
        self.bind("<Right>", lambda _event: self.nudge(1, 0))
        self.bind("<Up>", lambda _event: self.nudge(0, -1))
        self.bind("<Down>", lambda _event: self.nudge(0, 1))
        self.bind("<Shift-Left>", lambda _event: self.nudge(-10, 0))
        self.bind("<Shift-Right>", lambda _event: self.nudge(10, 0))
        self.bind("<Shift-Up>", lambda _event: self.nudge(0, -10))
        self.bind("<Shift-Down>", lambda _event: self.nudge(0, 10))

    def _build_left(self, parent) -> None:
        ttk.Label(parent, text="字符串模板").pack(anchor="w")
        search_row = ttk.Frame(parent)
        search_row.pack(fill="x", pady=(2, 4))
        self.search_var = tk.StringVar()
        search = ttk.Entry(search_row, textvariable=self.search_var)
        search.pack(side="left", fill="x", expand=True)
        self.search_var.trace_add("write", lambda *_args: self._refresh_templates())
        ttk.Button(search_row, text="+", width=3, command=self.add_template).pack(side="left", padx=(4, 0))
        self.template_tree = ttk.Treeview(parent, show="tree", height=15, selectmode="browse")
        self.template_tree.pack(fill="both", expand=True)
        self.template_tree.bind("<<TreeviewSelect>>", self.template_selected)
        self.template_tree.bind("<ButtonPress-1>", self.template_press)
        self.template_tree.bind("<B1-Motion>", self.template_motion)
        self.template_tree.bind("<ButtonRelease-1>", self.template_release)
        self.template_tree.bind("<Button-3>", self.template_menu)
        self.template_menu_widget = tk.Menu(self, tearoff=False)
        self.template_menu_widget.add_command(label="设置文字颜色", command=lambda: self.choose_color("template", "text_color"))
        self.template_menu_widget.add_command(label="选择背景色", command=lambda: self.choose_color("template", "bg_color"))
        self.template_menu_widget.add_command(label="背景透明", command=lambda: self.set_transparent("template"))
        self.template_menu_widget.add_command(label="从底图吸取背景色", command=self.eyedrop_template)
        self.template_menu_widget.add_separator()
        self.template_menu_widget.add_command(label="重命名", command=self.rename_template)
        self.template_menu_widget.add_command(label="删除", command=self.delete_template)

        props = ttk.Labelframe(parent, text="当前模板 / 当前图层", padding=6)
        props.pack(fill="x", pady=(8, 0))
        self.style_target = ttk.Label(props, text="选择模板或图层")
        self.style_target.pack(anchor="w")
        self.font_var = tk.StringVar()
        self.font_box = ttk.Combobox(props, textvariable=self.font_var, values=sorted(self.fonts), width=28)
        self.font_box.pack(fill="x", pady=(4, 2))
        self.font_box.bind("<<ComboboxSelected>>", lambda _event: self.change_style("font", self.font_var.get()))
        self.font_box.bind("<KeyRelease>", self.filter_fonts)
        number_row = ttk.Frame(props)
        number_row.pack(fill="x", pady=2)
        ttk.Label(number_row, text="字号").pack(side="left")
        self.size_var = tk.StringVar(value="9")
        size = ttk.Spinbox(number_row, from_=1, to=500, increment=1, textvariable=self.size_var, width=7, command=lambda: self.change_style("font_size", self.float_value(self.size_var, 9)))
        size.pack(side="left", padx=(4, 12))
        size.bind("<FocusOut>", lambda _event: self.change_style("font_size", self.float_value(self.size_var, 9)))
        ttk.Label(number_row, text="内边距").pack(side="left")
        self.padding_var = tk.StringVar(value="2")
        padding = ttk.Spinbox(number_row, from_=0, to=200, increment=1, textvariable=self.padding_var, width=7, command=lambda: self.change_style("padding", self.float_value(self.padding_var, 2)))
        padding.pack(side="left", padx=4)
        padding.bind("<FocusOut>", lambda _event: self.change_style("padding", self.float_value(self.padding_var, 2)))
        options = ttk.Frame(props)
        options.pack(fill="x", pady=2)
        self.align_var = tk.StringVar(value="left")
        ttk.Combobox(options, textvariable=self.align_var, values=("left", "center", "right"), state="readonly", width=9).pack(side="left")
        options.winfo_children()[-1].bind("<<ComboboxSelected>>", lambda _event: self.change_style("align", self.align_var.get()))
        self.line_var = tk.StringVar(value="1.2")
        ttk.Label(options, text="行距").pack(side="left", padx=(8, 2))
        line = ttk.Spinbox(options, from_=0.8, to=2.0, increment=0.1, textvariable=self.line_var, width=6, command=lambda: self.change_style("line_spacing", self.float_value(self.line_var, 1.2)))
        line.pack(side="left")
        line.bind("<FocusOut>", lambda _event: self.change_style("line_spacing", self.float_value(self.line_var, 1.2)))
        color_row = ttk.Frame(props)
        color_row.pack(fill="x", pady=(4, 0))
        ttk.Button(color_row, text="文字颜色", command=lambda: self.choose_color("selected", "text_color")).pack(side="left")
        ttk.Button(color_row, text="背景颜色", command=lambda: self.choose_color("selected", "bg_color")).pack(side="left", padx=4)
        ttk.Button(color_row, text="背景透明", command=lambda: self.set_transparent("selected")).pack(side="left", padx=4)
        ttk.Button(color_row, text="吸取底色", command=self.eyedrop_selected).pack(side="left")
        ttk.Button(props, text="扩展遮罩到替换前文字", command=self.expand_background_to_source).pack(anchor="w", pady=(4, 0))
        self.part_label = ttk.Label(props, text="拖动模式：整体（文字与背景一起移动）")
        self.part_label.pack(anchor="w", pady=(4, 0))

    def _refresh_base_choice(self) -> None:
        choices = ["原手册图片"]
        if Path(self.entry["capture"]).is_file():
            choices.append("新截图")
        self.base_combo["values"] = choices
        self.base_var.set("新截图" if self.base_source == "capture" and len(choices) > 1 else "原手册图片")
        if not self.editable:
            for child in self.winfo_children():
                pass

    def _refresh_templates(self) -> None:
        query = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        selected = self.selected_template_id
        self.template_tree.delete(*self.template_tree.get_children())
        self.templates = {item["id"]: item for item in self.store.all()}
        groups = (("default-group", "共享模板", "default"), ("manual-group", "我的模板", "manual"))
        for group_id, label, kind in groups:
            group = self.template_tree.insert("", "end", iid=group_id, text=label, open=True)
            for item in sorted((item for item in self.templates.values() if item["kind"] == kind and query in item["text"].lower()), key=lambda value: value["text"].lower()):
                tag = f"font:{item['id']}"
                face = item["style"].get("font", default_font(self.fonts))
                try:
                    self.template_tree.tag_configure(tag, font=(face, max(7, round(float(item["style"].get("font_size", 9))))))
                except tk.TclError:
                    self.template_tree.tag_configure(tag, font=(default_font(self.fonts), max(7, round(float(item["style"].get("font_size", 9))))))
                self.template_tree.insert(group, "end", iid=item["id"], text=item["text"], tags=(tag,))
        if selected in self.templates:
            self.template_tree.selection_set(selected)
        elif self.templates:
            first = next(iter(self.templates))
            self.selected_template_id = first
            self.template_tree.selection_set(first)
        self.template_selected()

    def template_selected(self, _event=None) -> None:
        selected = self.template_tree.selection()
        if not selected or selected[0] not in self.templates:
            return
        self.selected_template_id = selected[0]
        self.selected_layer_id = None
        self.selected_part = "group"
        self._sync_style_controls(self.templates[selected[0]]["style"], f"模板：{self.templates[selected[0]]['text']}")
        self.redraw()

    def template_press(self, event) -> None:
        item = self.template_tree.identify_row(event.y)
        self.drag_template = self.templates.get(item)

    def template_motion(self, event) -> None:
        if not self.drag_template:
            return
        x = self.winfo_pointerx() - self.canvas.winfo_rootx()
        y = self.winfo_pointery() - self.canvas.winfo_rooty()
        if 0 <= x <= self.canvas.winfo_width() and 0 <= y <= self.canvas.winfo_height():
            self._draw_ghost(x, y, self.drag_template["text"])

    def template_release(self, _event) -> None:
        if not self.drag_template:
            return
        x = self.winfo_pointerx() - self.canvas.winfo_rootx()
        y = self.winfo_pointery() - self.canvas.winfo_rooty()
        template = self.drag_template
        self.drag_template = None
        self._clear_ghost()
        if 0 <= x <= self.canvas.winfo_width() and 0 <= y <= self.canvas.winfo_height():
            ix, iy = self.screen_to_image(x, y)
            self.add_layer(template, ix, iy)

    def _draw_ghost(self, x: float, y: float, text: str) -> None:
        self._clear_ghost()
        self.drag_ghost = self.canvas.create_text(x, y, text=text, fill="#59BFFF", anchor="nw", stipple="gray50", tags="ghost")

    def _clear_ghost(self) -> None:
        self.canvas.delete("ghost")
        self.drag_ghost = None

    def template_menu(self, event) -> None:
        item = self.template_tree.identify_row(event.y)
        if item not in self.templates:
            return
        self.template_tree.selection_set(item)
        self.template_selected()
        template = self.templates[item]
        state = "normal" if template["kind"] == "manual" else "disabled"
        self.template_menu_widget.entryconfigure("重命名", state=state)
        self.template_menu_widget.entryconfigure("删除", state=state)
        self.template_menu_widget.tk_popup(event.x_root, event.y_root)
        self.template_menu_widget.grab_release()

    def add_template(self) -> None:
        text = simpledialog.askstring("添加模板", "字符串内容：", parent=self)
        if text and text.strip():
            item = self.store.add(text.strip())
            self.selected_template_id = item["id"]
            self._refresh_templates()

    def rename_template(self) -> None:
        template = self.current_template()
        if not template or template["kind"] != "manual":
            return
        text = simpledialog.askstring("重命名模板", "字符串内容：", initialvalue=template["text"], parent=self)
        if text and text.strip():
            self.store.rename(template, text.strip())
            self._refresh_templates()

    def delete_template(self) -> None:
        template = self.current_template()
        if not template or template["kind"] != "manual":
            return
        if messagebox.askyesno("删除模板", f"删除“{template['text']}”？", parent=self):
            self.store.delete(template)
            self.selected_template_id = None
            self._refresh_templates()

    def filter_fonts(self, _event=None) -> None:
        query = self.font_var.get().lower()
        self.font_box["values"] = [name for name in sorted(self.fonts) if query in name.lower()]

    def float_value(self, variable: tk.StringVar, fallback: float) -> float:
        try:
            return float(variable.get())
        except ValueError:
            variable.set(str(fallback))
            return fallback

    def current_template(self) -> dict | None:
        return self.templates.get(self.selected_template_id or "")

    def selected_layer(self) -> dict | None:
        return next((layer for layer in self.layers if layer["id"] == self.selected_layer_id), None)

    def _sync_style_controls(self, style: dict, label: str) -> None:
        self.style_target.configure(text=label)
        self.font_var.set(style.get("font", default_font(self.fonts)))
        self.size_var.set(str(style.get("font_size", 9)))
        self.padding_var.set(str(style.get("padding", 2)))
        self.align_var.set(style.get("align", "left"))
        self.line_var.set(str(style.get("line_spacing", 1.2)))
        if hasattr(self, "part_label"):
            labels = {
                "group": "拖动模式：整体（文字与背景一起移动）",
                "text": "拖动模式：仅文字",
                "bg": "拖动模式：仅背景遮罩",
            }
            self.part_label.configure(text=labels.get(self.selected_part, labels["group"]))

    def change_style(self, key: str, value) -> None:
        layer = self.selected_layer()
        if layer:
            self.push_undo()
            layer[key] = value
            if key == "align":
                self.align_text_in_background(layer)
            if key in {"font_size", "padding", "line_spacing", "text"}:
                layer["text_attached"] = False
            self.mark_dirty()
            self.redraw()
            return
        template = self.current_template()
        if template:
            template["style"][key] = value
            self.store.update_style(template, template["style"])
            self._refresh_templates()

    def choose_color(self, scope: str, key: str) -> None:
        if scope == "template":
            target = self.current_template()
            style = target["style"] if target else None
        else:
            target = self.selected_layer()
            style = target
        if not style:
            return
        color = colorchooser.askcolor(style.get(key) or "#FFFFFF", parent=self, title="选择颜色")[1]
        if color:
            self.change_style(key, color)

    def set_transparent(self, scope: str) -> None:
        target = self.current_template() if scope == "template" else self.selected_layer()
        if target:
            self.change_style("bg_color", None)

    def eyedrop_template(self) -> None:
        template = self.current_template()
        if template:
            self.eyedrop_target = (template["id"], "template")
            self.canvas.configure(cursor="tcross")

    def eyedrop_selected(self) -> None:
        layer = self.selected_layer()
        if layer:
            self.eyedrop_target = (layer["id"], "layer")
            self.canvas.configure(cursor="tcross")

    def _switch_base(self, _event=None) -> None:
        wanted = "capture" if self.base_var.get() == "新截图" else "original"
        if wanted == self.base_source and self.snapshot_path.is_file():
            return
        path = Path(self.entry["capture"] if wanted == "capture" else self.entry["original"])
        if not path.is_file():
            messagebox.showwarning("mdoc", "当前没有可用的新截图。", parent=self)
            self.base_var.set("原手册图片")
            return
        info = png_info(path)
        original = png_info(Path(self.entry["original"]))
        if not info or not original or info["width"] != original["width"] or info["height"] != original["height"]:
            messagebox.showerror("mdoc", "新截图尺寸必须与原手册图片一致，不能作为编辑底图。", parent=self)
            self.base_var.set("新截图" if self.base_source == "capture" else "原手册图片")
            return
        keep = messagebox.askyesnocancel("切换底图", "是否保留现有文字图层？\n“是”保留，“否”清空，“取消”不切换。", parent=self)
        if keep is None:
            self.base_var.set("新截图" if self.base_source == "capture" else "原手册图片")
            return
        self.push_undo()
        self.base_source = wanted
        self._load_image(path)
        if not keep:
            self.layers = []
            self.selected_layer_id = None
        self.mark_dirty()
        self.fit()

    def add_layer(self, template: dict, x: float, y: float) -> None:
        self.push_undo()
        style = copy.deepcopy(template["style"])
        layer = {
            "id": uuid.uuid4().hex, "text": template["text"], **style,
            "text_x": x, "text_y": y, "text_attached": False,
            "source_x": x, "source_y": y,
            "source_candidates": list(template.get("sources", [])),
        }
        geometry = self.layer_geometry(layer)
        padding = float(layer["padding"])
        source_text, source_w, source_h = self.source_coverage(layer)
        layer["source_text"] = source_text
        gutter = COVERAGE_GUTTER
        layer.update({
            "bg_x": x - padding - gutter,
            "bg_y": y - padding - gutter,
            "bg_w": max(geometry["text_w"], source_w) + (padding + gutter) * 2,
            "bg_h": max(geometry["text_h"], source_h) + (padding + gutter) * 2,
        })
        # The template remains white by default. On an actual UI image its initial
        # mask is matched to the surrounding pixels unless the user chose a color.
        if self.uses_default_background(style):
            layer["bg_color"] = self.sample_background_color(layer)
        self.layers.append(layer)
        self.selected_layer_id = layer["id"]
        self.selected_part = "group"
        self._sync_style_controls(layer, "图层样式")
        self.mark_dirty()
        self.redraw()

    def uses_default_background(self, style: dict) -> bool:
        return str(style.get("bg_color") or "").upper() == DEFAULT_STYLE["bg_color"]

    def source_coverage(self, layer: dict) -> tuple[str | None, float, float]:
        """Return the largest known old label that this replacement may need to mask."""
        replacement = str(layer.get("text", "")).casefold()
        candidates = tuple(layer.get("source_candidates", ()))
        source_text = max(candidates, key=len, default=None)
        if not source_text:
            geometry = self.layer_geometry(layer)
            return None, geometry["text_w"], geometry["text_h"]
        metrics = self.text_metrics(layer, source_text)
        return source_text, metrics["text_w"], metrics["text_h"]

    def text_metrics(self, layer: dict, text: str) -> dict:
        font = self.font_for(layer)
        probe = Image.new("RGBA", (1, 1))
        draw = ImageDraw.Draw(probe)
        spacing = max(0, round((float(layer.get("line_spacing", 1.2)) - 1) * float(layer.get("font_size", 9))))
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing)
        return {
            "text_w": max(1.0, float(bbox[2] - bbox[0])),
            "text_h": max(1.0, float(bbox[3] - bbox[1])),
            "bbox": bbox,
            "spacing": spacing,
        }

    def source_rect(self, layer: dict) -> tuple[float, float, float, float] | None:
        source_text = layer.get("source_text")
        if not source_text:
            source_text, _width, _height = self.source_coverage(layer)
        if not source_text:
            return None
        metrics = self.text_metrics(layer, source_text)
        padding = float(layer.get("padding", 2)) + COVERAGE_GUTTER
        x = float(layer.get("source_x", layer.get("text_x", 0)))
        y = float(layer.get("source_y", layer.get("text_y", 0)))
        return x - padding, y - padding, x + metrics["text_w"] + padding, y + metrics["text_h"] + padding

    def sample_background_color(self, layer: dict) -> str:
        """Sample a perimeter around the target label, avoiding the text itself."""
        source = self.source_rect(layer)
        if not source or self.base_image is None:
            return DEFAULT_STYLE["bg_color"]
        left, top, right, bottom = source
        pixels: list[tuple[int, int, int]] = []
        for offset in (2, 4, 6):
            for x in range(max(0, round(left) - offset), min(self.base_image.width, round(right) + offset + 1)):
                for y in (round(top) - offset, round(bottom) + offset):
                    if 0 <= y < self.base_image.height:
                        pixel = self.base_image.getpixel((x, y))
                        pixels.append(tuple(pixel[:3]))
            for y in range(max(0, round(top) - offset), min(self.base_image.height, round(bottom) + offset + 1)):
                for x in (round(left) - offset, round(right) + offset):
                    if 0 <= x < self.base_image.width:
                        pixel = self.base_image.getpixel((x, y))
                        pixels.append(tuple(pixel[:3]))
        if not pixels:
            return DEFAULT_STYLE["bg_color"]
        buckets: dict[tuple[int, int, int], list[tuple[int, int, int]]] = {}
        for pixel in pixels:
            bucket = tuple(channel // 8 for channel in pixel)
            buckets.setdefault(bucket, []).append(pixel)
        dominant = max(buckets.values(), key=len)
        average = tuple(round(sum(pixel[index] for pixel in dominant) / len(dominant)) for index in range(3))
        return "#{:02X}{:02X}{:02X}".format(*average)

    def expand_background_to_source(self) -> None:
        layer = self.selected_layer()
        if not layer:
            return
        source = self.source_rect(layer)
        if not source:
            messagebox.showinfo("mdoc", "当前字符串没有已知的替换前文本，请直接拖动橙色背景框调整遮罩范围。", parent=self)
            return
        self.push_undo()
        geometry = self.layer_geometry(layer)
        left = min(geometry["bg_x"], source[0])
        top = min(geometry["bg_y"], source[1])
        right = max(geometry["bg_x"] + geometry["bg_w"], source[2])
        bottom = max(geometry["bg_y"] + geometry["bg_h"], source[3])
        layer.update({"bg_x": left, "bg_y": top, "bg_w": right - left, "bg_h": bottom - top})
        self.selected_part = "group"
        self.mark_dirty()
        self.redraw()

    def font_for(self, layer: dict) -> ImageFont.FreeTypeFont:
        path = self.fonts.get(layer.get("font", ""))
        try:
            return ImageFont.truetype(path, max(1, round(float(layer.get("font_size", 9)))))
        except (OSError, TypeError, AttributeError):
            return ImageFont.truetype(self.fonts[default_font(self.fonts)], max(1, round(float(layer.get("font_size", 9)))))

    def layer_geometry(self, layer: dict) -> dict:
        metrics = self.text_metrics(layer, str(layer.get("text", "")))
        tx, ty = float(layer.get("text_x", 0)), float(layer.get("text_y", 0))
        return {"text_x": tx, "text_y": ty, **metrics,
                "bg_x": float(layer.get("bg_x", tx)), "bg_y": float(layer.get("bg_y", ty)), "bg_w": float(layer.get("bg_w", metrics["text_w"])), "bg_h": float(layer.get("bg_h", metrics["text_h"]))}

    def align_text_in_background(self, layer: dict) -> None:
        geometry = self.layer_geometry(layer)
        padding = float(layer.get("padding", 2))
        content_x = geometry["bg_x"] + padding
        content_w = max(0, geometry["bg_w"] - padding * 2)
        align = layer.get("align", "left")
        x = content_x if align == "left" else content_x + (content_w - geometry["text_w"]) / (2 if align == "center" else 1)
        y = geometry["bg_y"] + (geometry["bg_h"] - geometry["text_h"]) / 2
        layer.update({"text_x": x, "text_y": y, "text_attached": False})

    def composite(self) -> Image.Image:
        image = self.base_image.copy()
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for layer in self.layers:
            geometry = self.layer_geometry(layer)
            bg = layer.get("bg_color")
            if bg:
                color = ImageColor.getrgb(bg) + (255,)
                draw.rectangle((round(geometry["bg_x"]), round(geometry["bg_y"]), round(geometry["bg_x"] + geometry["bg_w"]), round(geometry["bg_y"] + geometry["bg_h"])), fill=color)
            font = self.font_for(layer)
            bbox = geometry["bbox"]
            draw.multiline_text((round(geometry["text_x"] - bbox[0]), round(geometry["text_y"] - bbox[1])), layer.get("text", ""), font=font, fill=layer.get("text_color", "#000000"), spacing=geometry["spacing"])
        return Image.alpha_composite(image, overlay)

    def redraw(self) -> None:
        if not hasattr(self, "canvas") or self.base_image is None:
            return
        self.canvas.delete("all")
        image = self.composite()
        width = max(1, round(image.width * self.scale))
        height = max(1, round(image.height * self.scale))
        display = image.resize((width, height), Image.Resampling.LANCZOS)
        self.canvas_image = ImageTk.PhotoImage(display)
        self.canvas.create_image(self.pan_x, self.pan_y, image=self.canvas_image, anchor="nw", tags="image")
        if not self.preview_mode:
            self._draw_overlays()
        self.zoom_label.configure(text=f"{round(self.scale * 100)}%")

    def _draw_overlays(self) -> None:
        x0, y0 = self.pan_x, self.pan_y
        x1 = x0 + self.base_image.width * self.scale
        y1 = y0 + self.base_image.height * self.scale
        self.canvas.create_rectangle(x0, y0, x1, y1, outline="#E05050", width=1, tags="overlay")
        layer = self.selected_layer()
        if layer:
            geo = self.layer_geometry(layer)
            part = self.selected_part
            if part == "text":
                rect = (geo["text_x"], geo["text_y"], geo["text_x"] + geo["text_w"], geo["text_y"] + geo["text_h"])
                color = "#00A7FF"
            elif part == "bg":
                rect = (geo["bg_x"], geo["bg_y"], geo["bg_x"] + geo["bg_w"], geo["bg_y"] + geo["bg_h"])
                color = "#F6B73C"
            else:
                rect = self.group_rect(geo)
                color = "#47D16C"
            sx0, sy0 = self.image_to_screen(rect[0], rect[1])
            sx1, sy1 = self.image_to_screen(rect[2], rect[3])
            self.canvas.create_rectangle(sx0, sy0, sx1, sy1, outline=color, width=2, tags="overlay")
            for name, hx, hy in self.handles(sx0, sy0, sx1, sy1):
                self.canvas.create_rectangle(hx - HANDLE_RADIUS, hy - HANDLE_RADIUS, hx + HANDLE_RADIUS, hy + HANDLE_RADIUS, fill=color, outline="#FFFFFF", tags=("overlay", f"handle:{name}"))
            if not self.text_inside_bg(geo):
                tx0, ty0 = self.image_to_screen(geo["text_x"], geo["text_y"])
                tx1, ty1 = self.image_to_screen(geo["text_x"] + geo["text_w"], geo["text_y"] + geo["text_h"])
                self.canvas.create_rectangle(tx0, ty0, tx1, ty1, outline="#FF3B30", dash=(4, 3), tags="overlay")
        for orientation, value in self.guides:
            if orientation == "x":
                sx, _ = self.image_to_screen(value, 0)
                self.canvas.create_line(sx, y0, sx, y1, fill="#3AA7FF", dash=(3, 2), tags="overlay")
            else:
                _, sy = self.image_to_screen(0, value)
                self.canvas.create_line(x0, sy, x1, sy, fill="#3AA7FF", dash=(3, 2), tags="overlay")

    def text_inside_bg(self, geo: dict) -> bool:
        return geo["text_x"] >= geo["bg_x"] and geo["text_y"] >= geo["bg_y"] and geo["text_x"] + geo["text_w"] <= geo["bg_x"] + geo["bg_w"] and geo["text_y"] + geo["text_h"] <= geo["bg_y"] + geo["bg_h"]

    def group_rect(self, geo: dict) -> tuple[float, float, float, float]:
        return (
            min(geo["bg_x"], geo["text_x"]),
            min(geo["bg_y"], geo["text_y"]),
            max(geo["bg_x"] + geo["bg_w"], geo["text_x"] + geo["text_w"]),
            max(geo["bg_y"] + geo["bg_h"], geo["text_y"] + geo["text_h"]),
        )

    def handles(self, x0, y0, x1, y1):
        return (("nw", x0, y0), ("n", (x0 + x1) / 2, y0), ("ne", x1, y0), ("e", x1, (y0 + y1) / 2), ("se", x1, y1), ("s", (x0 + x1) / 2, y1), ("sw", x0, y1), ("w", x0, (y0 + y1) / 2))

    def image_to_screen(self, x: float, y: float) -> tuple[float, float]:
        return self.pan_x + x * self.scale, self.pan_y + y * self.scale

    def screen_to_image(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.pan_x) / self.scale, (y - self.pan_y) / self.scale

    def _restore_or_fit(self) -> None:
        if self.saved_view:
            self.scale = min(8.0, max(0.1, float(self.saved_view.get("scale", 1))))
            center = self.saved_view.get("center", [self.base_image.width / 2, self.base_image.height / 2])
            self.pan_x = self.canvas.winfo_width() / 2 - float(center[0]) * self.scale
            self.pan_y = self.canvas.winfo_height() / 2 - float(center[1]) * self.scale
            self.redraw()
        else:
            self.fit()

    def fit(self) -> None:
        if self.canvas.winfo_width() < 2 or self.canvas.winfo_height() < 2:
            self.after(50, self.fit)
            return
        self.scale = min(8.0, max(0.1, min((self.canvas.winfo_width() - 40) / self.base_image.width, (self.canvas.winfo_height() - 40) / self.base_image.height)))
        self.pan_x = (self.canvas.winfo_width() - self.base_image.width * self.scale) / 2
        self.pan_y = (self.canvas.winfo_height() - self.base_image.height * self.scale) / 2
        self.redraw()

    def set_zoom(self, scale: float) -> None:
        center_x = self.canvas.winfo_width() / 2
        center_y = self.canvas.winfo_height() / 2
        ix, iy = self.screen_to_image(center_x, center_y)
        self.scale = min(8.0, max(0.1, scale))
        self.pan_x, self.pan_y = center_x - ix * self.scale, center_y - iy * self.scale
        self.redraw()

    def zoom_by(self, factor: float, x: float | None = None, y: float | None = None) -> None:
        x = self.canvas.winfo_width() / 2 if x is None else x
        y = self.canvas.winfo_height() / 2 if y is None else y
        ix, iy = self.screen_to_image(x, y)
        self.scale = min(8.0, max(0.1, self.scale * factor))
        self.pan_x, self.pan_y = x - ix * self.scale, y - iy * self.scale
        self.redraw()

    def wheel(self, event) -> None:
        self.zoom_by(1.15 if event.delta > 0 else 1 / 1.15, event.x, event.y)

    def pan_press(self, event) -> None:
        self.pan_state = (event.x, event.y, self.pan_x, self.pan_y)

    def pan_motion(self, event) -> None:
        if self.pan_state:
            sx, sy, px, py = self.pan_state
            self.pan_x, self.pan_y = px + event.x - sx, py + event.y - sy
            self.redraw()

    def canvas_press(self, event) -> None:
        self.focus_set()
        if self.eyedrop_target:
            ix, iy = self.screen_to_image(event.x, event.y)
            ix, iy = min(max(round(ix), 0), self.base_image.width - 1), min(max(round(iy), 0), self.base_image.height - 1)
            color = _hex(self.base_image.getpixel((ix, iy)))
            target, kind = self.eyedrop_target
            self.eyedrop_target = None
            self.canvas.configure(cursor="crosshair")
            if kind == "template" and target in self.templates:
                self.templates[target]["style"]["bg_color"] = color
                self.store.update_style(self.templates[target], self.templates[target]["style"])
                self._refresh_templates()
            else:
                layer = next((item for item in self.layers if item["id"] == target), None)
                if layer:
                    self.push_undo()
                    layer["bg_color"] = color
                    self.mark_dirty()
            self.redraw()
            return
        if self.space_down:
            self.pan_press(event)
            return
        hit = self.handle_hit(event.x, event.y)
        if hit:
            self.push_undo()
            self.drag_state = {"kind": "resize", "handle": hit, "start": (event.x, event.y), "before": copy.deepcopy(self.selected_layer()), "part": self.selected_part}
            return
        ix, iy = self.screen_to_image(event.x, event.y)
        target = self.hit_layer(ix, iy, bool(event.state & 0x0008))
        if target:
            layer, part = target
            self.selected_layer_id, self.selected_part = layer["id"], part
            self._sync_style_controls(layer, "图层样式")
            self.push_undo()
            self.drag_state = {"kind": "move", "start": (event.x, event.y), "before": copy.deepcopy(layer), "part": part, "alt": bool(event.state & 0x0008)}
            self.redraw()
        else:
            self.selected_layer_id = None
            self.guides = []
            self.redraw()

    def canvas_motion(self, event) -> None:
        if self.pan_state:
            self.pan_motion(event)
            return
        if not self.drag_state:
            return
        layer = self.selected_layer()
        if not layer:
            return
        dx, dy = (event.x - self.drag_state["start"][0]) / self.scale, (event.y - self.drag_state["start"][1]) / self.scale
        before = self.drag_state["before"]
        # Pointer movement is measured from the press point. Reapply that absolute
        # offset to the press snapshot so repeated Tk motion events never compound.
        layer.clear()
        layer.update(copy.deepcopy(before))
        if self.drag_state["kind"] == "move":
            geo = self.layer_geometry(before)
            part = self.drag_state["part"]
            if part == "group":
                rect = self.group_rect(geo)
                x, y = self.snap(before, rect[0] + dx, rect[1] + dy, rect[2] - rect[0], rect[3] - rect[1], event.state)
                self.move_layer_by(layer, "group", x - rect[0], y - rect[1])
            elif part == "text":
                x, y = self.snap(before, geo["text_x"] + dx, geo["text_y"] + dy, geo["text_w"], geo["text_h"], event.state)
                self.move_layer_by(layer, "text", x - geo["text_x"], y - geo["text_y"])
            else:
                x, y = self.snap(before, geo["bg_x"] + dx, geo["bg_y"] + dy, geo["bg_w"], geo["bg_h"], event.state)
                self.move_layer_by(layer, "bg", x - geo["bg_x"], y - geo["bg_y"])
        else:
            self.guides = []
            self.resize_layer(layer, before, self.drag_state["part"], self.drag_state["handle"], dx, dy)
        self.mark_dirty()
        self.redraw()

    def canvas_release(self, _event) -> None:
        self.drag_state = None
        self.pan_state = None
        self.guides = []
        self.redraw()

    def handle_hit(self, x: float, y: float) -> str | None:
        layer = self.selected_layer()
        if not layer or self.preview_mode:
            return None
        geo = self.layer_geometry(layer)
        if self.selected_part == "text":
            rect = (geo["text_x"], geo["text_y"], geo["text_x"] + geo["text_w"], geo["text_y"] + geo["text_h"])
        elif self.selected_part == "bg":
            rect = (geo["bg_x"], geo["bg_y"], geo["bg_x"] + geo["bg_w"], geo["bg_y"] + geo["bg_h"])
        else:
            rect = self.group_rect(geo)
        sx0, sy0 = self.image_to_screen(rect[0], rect[1])
        sx1, sy1 = self.image_to_screen(rect[2], rect[3])
        for name, hx, hy in self.handles(sx0, sy0, sx1, sy1):
            if abs(x - hx) <= HANDLE_RADIUS + 2 and abs(y - hy) <= HANDLE_RADIUS + 2:
                return name
        return None

    def hit_layer(self, x: float, y: float, cycle: bool) -> tuple[dict, str] | None:
        hits = []
        for layer in self.layers:
            geo = self.layer_geometry(layer)
            in_bg = geo["bg_x"] <= x <= geo["bg_x"] + geo["bg_w"] and geo["bg_y"] <= y <= geo["bg_y"] + geo["bg_h"]
            in_text = geo["text_x"] <= x <= geo["text_x"] + geo["text_w"] and geo["text_y"] <= y <= geo["text_y"] + geo["text_h"]
            if in_bg or in_text:
                part = self.selected_part if layer["id"] == self.selected_layer_id and self.selected_part in {"text", "bg"} else "group"
                hits.append((layer, part))
        if not hits:
            return None
        if cycle:
            ids = [layer["id"] for layer, _part in hits]
            same = self.cycle_state and abs(self.cycle_state[0] - x) < 3 and abs(self.cycle_state[1] - y) < 3 and self.cycle_state[2] == ids
            index = (self.cycle_state[3] + 1) % len(hits) if same else 0
            self.cycle_state = (x, y, ids, index)
            return hits[-1 - index]
        self.cycle_state = None
        return hits[-1]

    def move_layer_by(self, layer: dict, part: str, dx: float, dy: float) -> None:
        """Translate an entire editable layer, or one deliberate sub-part."""
        if part == "group":
            for x_key, y_key in (("text_x", "text_y"), ("bg_x", "bg_y"), ("source_x", "source_y")):
                fallback_x = float(layer.get("text_x", 0)) if x_key == "source_x" else 0.0
                fallback_y = float(layer.get("text_y", 0)) if y_key == "source_y" else 0.0
                layer[x_key] = float(layer.get(x_key, fallback_x)) + dx
                layer[y_key] = float(layer.get(y_key, fallback_y)) + dy
            layer["text_attached"] = False
        elif part == "text":
            layer["text_x"] = float(layer.get("text_x", 0)) + dx
            layer["text_y"] = float(layer.get("text_y", 0)) + dy
            layer["text_attached"] = False
        else:
            layer["bg_x"] = float(layer.get("bg_x", 0)) + dx
            layer["bg_y"] = float(layer.get("bg_y", 0)) + dy

    def set_selected_part(self, part: str) -> None:
        layer = self.selected_layer()
        if not layer:
            return
        self.selected_part = part
        self._sync_style_controls(layer, "图层样式")
        self.redraw()

    def resize_layer(self, layer: dict, before: dict, part: str, handle: str, dx: float, dy: float) -> None:
        geo = self.layer_geometry(before)
        if part == "text":
            ratios = []
            if "e" in handle: ratios.append((geo["text_w"] + dx) / geo["text_w"])
            if "w" in handle: ratios.append((geo["text_w"] - dx) / geo["text_w"])
            if "s" in handle: ratios.append((geo["text_h"] + dy) / geo["text_h"])
            if "n" in handle: ratios.append((geo["text_h"] - dy) / geo["text_h"])
            ratio = max(0.1, sum(ratios) / max(1, len(ratios)))
            layer["font_size"] = max(MIN_FONT_SIZE, float(before["font_size"]) * ratio)
            layer["padding"] = max(0.0, float(before.get("padding", 2)) * ratio)
            layer["text_attached"] = False
            if "w" in handle:
                layer["text_x"] = geo["text_x"] + geo["text_w"] - self.layer_geometry(layer)["text_w"]
            if "n" in handle:
                layer["text_y"] = geo["text_y"] + geo["text_h"] - self.layer_geometry(layer)["text_h"]
            return
        if part == "group":
            x0, y0, x1, y1 = self.group_rect(geo)
            old_width, old_height = max(MIN_BOX_SIZE, x1 - x0), max(MIN_BOX_SIZE, y1 - y0)
            if "w" in handle: x0 = min(x1 - MIN_BOX_SIZE, x0 + dx)
            if "e" in handle: x1 = max(x0 + MIN_BOX_SIZE, x1 + dx)
            if "n" in handle: y0 = min(y1 - MIN_BOX_SIZE, y0 + dy)
            if "s" in handle: y1 = max(y0 + MIN_BOX_SIZE, y1 + dy)
            scale_x = (x1 - x0) / old_width
            scale_y = (y1 - y0) / old_height
            ratios = []
            if "e" in handle or "w" in handle: ratios.append(scale_x)
            if "n" in handle or "s" in handle: ratios.append(scale_y)
            font_ratio = max(0.1, sum(ratios) / max(1, len(ratios)))
            layer["font_size"] = max(MIN_FONT_SIZE, float(before["font_size"]) * font_ratio)
            layer["padding"] = max(0.0, float(before.get("padding", 2)) * font_ratio)
            for x_key, y_key in (("text_x", "text_y"), ("bg_x", "bg_y"), ("source_x", "source_y")):
                layer[x_key] = x0 + (float(before.get(x_key, 0)) - self.group_rect(geo)[0]) * scale_x
                layer[y_key] = y0 + (float(before.get(y_key, 0)) - self.group_rect(geo)[1]) * scale_y
            layer["bg_w"] = max(MIN_BOX_SIZE, float(before.get("bg_w", 0)) * scale_x)
            layer["bg_h"] = max(MIN_BOX_SIZE, float(before.get("bg_h", 0)) * scale_y)
            layer["text_attached"] = False
            return
        x0, y0, x1, y1 = geo["bg_x"], geo["bg_y"], geo["bg_x"] + geo["bg_w"], geo["bg_y"] + geo["bg_h"]
        if "w" in handle: x0 = min(x1 - MIN_BOX_SIZE, x0 + dx)
        if "e" in handle: x1 = max(x0 + MIN_BOX_SIZE, x1 + dx)
        if "n" in handle: y0 = min(y1 - MIN_BOX_SIZE, y0 + dy)
        if "s" in handle: y1 = max(y0 + MIN_BOX_SIZE, y1 + dy)
        layer.update({"bg_x": x0, "bg_y": y0, "bg_w": x1 - x0, "bg_h": y1 - y0})

    def snap(self, moving: dict, x: float, y: float, width: float, height: float, state: int) -> tuple[float, float]:
        if state & 0x0008:
            self.guides = []
            return x, y
        # Snap distance is a visual affordance, so it stays constant in screen
        # pixels instead of becoming an oversized magnetic area while zoomed in.
        threshold = SNAP_SCREEN_DISTANCE / max(0.1, float(self.scale))
        candidates_x = [0.0, self.base_image.width / 2, float(self.base_image.width)]
        candidates_y = [0.0, self.base_image.height / 2, float(self.base_image.height)]
        for layer in self.layers:
            if layer["id"] == moving["id"]:
                continue
            geo = self.layer_geometry(layer)
            candidates_x.extend((geo["bg_x"], geo["bg_x"] + geo["bg_w"] / 2, geo["bg_x"] + geo["bg_w"]))
            candidates_y.extend((geo["bg_y"], geo["bg_y"] + geo["bg_h"] / 2, geo["bg_y"] + geo["bg_h"]))
        guides = []
        for edge in (x, x + width / 2, x + width):
            nearby = next((value for value in candidates_x if abs(value - edge) <= threshold), None)
            if nearby is not None:
                x += nearby - edge
                guides.append(("x", nearby))
                break
        for edge in (y, y + height / 2, y + height):
            nearby = next((value for value in candidates_y if abs(value - edge) <= threshold), None)
            if nearby is not None:
                y += nearby - edge
                guides.append(("y", nearby))
                break
        self.guides = guides
        return x, y

    def double_click(self, event) -> None:
        ix, iy = self.screen_to_image(event.x, event.y)
        hit = self.hit_layer(ix, iy, False)
        if hit:
            layer, _part = hit
            geo = self.layer_geometry(layer)
            in_text = geo["text_x"] <= ix <= geo["text_x"] + geo["text_w"] and geo["text_y"] <= iy <= geo["text_y"] + geo["text_h"]
            if not in_text:
                return
            text = simpledialog.askstring("编辑文字", "文字内容：", initialvalue=layer["text"], parent=self)
            if text is not None:
                self.push_undo()
                layer["text"] = text
                layer["text_attached"] = False
                self.selected_layer_id, self.selected_part = layer["id"], "group"
                self.mark_dirty()
                self.redraw()

    def layer_menu(self, event) -> None:
        ix, iy = self.screen_to_image(event.x, event.y)
        hit = self.hit_layer(ix, iy, False)
        if not hit:
            return
        layer, part = hit
        self.selected_layer_id, self.selected_part = layer["id"], part
        self._sync_style_controls(layer, "图层样式")
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="整体移动（文字与背景）", command=lambda: self.set_selected_part("group"))
        menu.add_command(label="仅移动文字", command=lambda: self.set_selected_part("text"))
        menu.add_command(label="仅移动背景遮罩", command=lambda: self.set_selected_part("bg"))
        menu.add_separator()
        menu.add_command(label="编辑文字", command=lambda: self.edit_selected_text())
        menu.add_command(label="复制图层", command=self.copy_layer)
        menu.add_command(label="删除图层", command=self.delete_layer)
        menu.add_separator()
        menu.add_command(label="设置文字颜色", command=lambda: self.choose_color("selected", "text_color"))
        menu.add_command(label="设置背景色", command=lambda: self.choose_color("selected", "bg_color"))
        menu.add_command(label="背景透明", command=lambda: self.set_transparent("selected"))
        menu.add_command(label="从底图吸取背景色", command=self.eyedrop_selected)
        menu.add_command(label="扩展遮罩到替换前文字", command=self.expand_background_to_source)
        menu.add_separator()
        menu.add_command(label="置于顶层", command=self.bring_front)
        menu.add_command(label="置于底层", command=self.send_back)
        menu.tk_popup(event.x_root, event.y_root)
        menu.grab_release()
        self.redraw()

    def edit_selected_text(self) -> None:
        layer = self.selected_layer()
        if not layer:
            return
        text = simpledialog.askstring("编辑文字", "文字内容：", initialvalue=layer["text"], parent=self)
        if text is not None:
            self.push_undo()
            layer["text"] = text
            layer["text_attached"] = False
            self.selected_part = "group"
            self.mark_dirty()
            self.redraw()

    def copy_layer(self) -> None:
        layer = self.selected_layer()
        if not layer:
            return
        self.push_undo()
        duplicate = copy.deepcopy(layer)
        duplicate["id"] = uuid.uuid4().hex
        for key in ("text_x", "text_y", "bg_x", "bg_y"):
            duplicate[key] = float(duplicate.get(key, 0)) + 12
        self.layers.append(duplicate)
        self.selected_layer_id = duplicate["id"]
        self.mark_dirty()
        self.redraw()

    def delete_layer(self) -> None:
        layer = self.selected_layer()
        if not layer:
            return
        self.push_undo()
        self.layers = [item for item in self.layers if item["id"] != layer["id"]]
        self.selected_layer_id = None
        self.mark_dirty()
        self.redraw()

    def bring_front(self) -> None:
        layer = self.selected_layer()
        if layer:
            self.push_undo()
            self.layers = [item for item in self.layers if item["id"] != layer["id"]] + [layer]
            self.mark_dirty(); self.redraw()

    def send_back(self) -> None:
        layer = self.selected_layer()
        if layer:
            self.push_undo()
            self.layers = [layer] + [item for item in self.layers if item["id"] != layer["id"]]
            self.mark_dirty(); self.redraw()

    def nudge(self, dx: float, dy: float) -> None:
        layer = self.selected_layer()
        if not layer or self.preview_mode:
            return
        self.push_undo()
        self.move_layer_by(layer, self.selected_part, dx, dy)
        self.mark_dirty(); self.redraw()

    def push_undo(self) -> None:
        self.undo_stack.append(copy.deepcopy(self.layers))
        self.undo_stack = self.undo_stack[-100:]
        self.redo_stack.clear()

    def undo(self) -> None:
        if self.undo_stack:
            self.redo_stack.append(copy.deepcopy(self.layers))
            self.layers = self.undo_stack.pop()
            self.selected_layer_id = self.layers[-1]["id"] if self.layers else None
            self.mark_dirty(); self.redraw()

    def redo(self) -> None:
        if self.redo_stack:
            self.undo_stack.append(copy.deepcopy(self.layers))
            self.layers = self.redo_stack.pop()
            self.selected_layer_id = self.layers[-1]["id"] if self.layers else None
            self.mark_dirty(); self.redraw()

    def escape(self) -> None:
        if self.eyedrop_target:
            self.eyedrop_target = None
            self.canvas.configure(cursor="crosshair")
        else:
            self.selected_layer_id = None
            self.guides = []
            self.redraw()

    def toggle_preview(self) -> None:
        self.preview_mode = not self.preview_mode
        self.preview_button.configure(text="退出预览" if self.preview_mode else "预览结果")
        self.redraw()

    def mark_dirty(self) -> None:
        self.dirty = True

    def _view_record(self) -> dict:
        center = self.screen_to_image(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2)
        return {"scale": self.scale, "center": [center[0], center[1]]}

    def save(self, close: bool = False) -> bool:
        if not self.editable:
            messagebox.showerror("mdoc", "任务已经进入最终审核或终态，不能继续修改。请创建修订任务。", parent=self)
            return False
        result = self.composite()
        output = Path(self.entry["capture"])
        if result.size != self.base_image.size:
            messagebox.showerror("mdoc", "导出尺寸与原图不一致，已取消保存。", parent=self)
            return False
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.base_image.convert("RGBA" if self.base_has_alpha else "RGB").save(self.snapshot_path, format="PNG")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp")
        result.convert("RGBA" if self.base_has_alpha else "RGB").save(temporary, format="PNG")
        if png_info(temporary) is None:
            temporary.unlink(missing_ok=True)
            messagebox.showerror("mdoc", "生成的 PNG 无效，未保存。", parent=self)
            return False
        temporary.replace(output)
        _atomic_json(self.record_path, {
            "schema_version": 1, "base_source": self.base_source, "base_snapshot": self.snapshot_path.name,
            "layers": self.layers, "view": self._view_record(), "selected_layer_id": self.selected_layer_id,
        })
        self._sync_mdoc_after_save()
        self.dirty = False
        if self.on_saved:
            self.on_saved()
        if close:
            self.destroy()
        else:
            self._refresh_base_choice()
            messagebox.showinfo("mdoc", "编辑结果已保存到当前截图项。", parent=self)
        return True

    def _sync_mdoc_after_save(self) -> None:
        with task_lock(self.task):
            state_path = self.task.directory / "task-state.json"
            state = load_state(state_path, self.task.task_id)
            had_acceptance = bool(state.get("screenshot_acceptance"))
            synchronize(self.task, state)
            if had_acceptance and not self.contributor:
                accept_screenshots(self.task, state)
            save_state(state_path, state)

    def close(self) -> None:
        if self.dirty:
            answer = messagebox.askyesnocancel("未保存修改", "是否保存当前编辑？\n“是”保存并关闭，“否”放弃修改，“取消”继续编辑。", parent=self)
            if answer is None:
                return
            if answer:
                self.save(close=True)
                return
        self.destroy()
