#!/usr/bin/env python3
"""Thin human screenshot UI over the mdoc task state machine."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import shutil
import subprocess
import sys
import time
import tkinter as tk
from collections.abc import Mapping
from ctypes import wintypes
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageGrab, ImageTk

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from mdoc_core.config import load_task, load_workspace
from image_text_editor import ImageTextEditor, clear_image_edit_artifacts
from mdoc_core.locking import task_lock
from mdoc_core.screenshots import accept as accept_screenshots
from mdoc_core.screenshots import declared, png_info, synchronize
from mdoc_core.state import load_state, save_state
from screenshot_interaction import CaptureOverlay, GlobalHotkey, InstanceLock, dpi_aware, monitor_for_point, monitor_for_window, virtual_screen, window_snapshot

LABELS = {
    "pending": "待截图",
    "captured": "已捕获",
    "blocked": "受阻",
    "needs_retake": "需重拍",
    "waived": "已豁免",
    "not_applicable": "不适用",
    "accepted": "已验收",
}
EXCEPTION_STATUSES = {"blocked", "needs_retake", "waived", "not_applicable"}
SCREENSHOT_EDIT_BLOCKED_TASK_STATUSES = {"ready_for_review", "accepted", "cancelled"}


def image_format(path: Path) -> str:
    return "JPEG" if path.suffix.casefold() in {".jpg", ".jpeg"} else "PNG"


def image_save_options(path: Path) -> dict:
    return {"quality": 95, "subsampling": 0} if image_format(path) == "JPEG" else {}


def screenshot_changes_allowed(state: dict) -> bool:
    """Allow post-acceptance corrections, but preserve final task boundaries."""
    return state.get("status") not in SCREENSHOT_EDIT_BLOCKED_TASK_STATUSES


def screenshot_locales(items: dict) -> list[str]:
    """Return declared screenshot locales in their manifest order."""
    return list(dict.fromkeys(item.get("locale", "") for item in items.values() if item.get("locale")))


def items_for_locale(items: dict, locale: str) -> dict:
    """Keep the screenshot manifest order while showing one locale at a time."""
    return {key: item for key, item in items.items() if item.get("locale") == locale}


def screenshot_action(definition: dict, locale: str, destination: str) -> str:
    """Return the declared action for one screenshot destination."""
    match = next((item for item in definition["manifest"] if item["kind"] == "asset" and item["locale"] == locale and item["path"] == destination), None)
    return match["action"] if match else "update"


def screenshot_reference_locale(definition: dict, locale: str) -> str:
    """Resolve the reference locale for a newly created screenshot."""
    strategy = definition["locale_plan"]["targets"].get(locale, {}).get("screenshots")
    if isinstance(strategy, Mapping) and strategy.get("copy_from"):
        return strategy["copy_from"]
    return locale if locale == "zh" else "zh"


def copy_reference_to_capture(source: Path, target: Path) -> dict:
    """Copy a valid reference image byte-for-byte into a declared capture slot."""
    source_info = png_info(source)
    if source_info is None:
        raise OSError("参考图片不是有效的 PNG 或 JPEG 文件。")
    if source_info["format"] != image_format(target):
        raise OSError("参考图片格式与当前截图目标格式不一致，不能原样复制。")
    if source.resolve() == target.resolve():
        return source_info
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        shutil.copyfile(source, temporary)
        target_info = png_info(temporary)
        if (
            target_info is None
            or target_info["format"] != source_info["format"]
            or target_info["width"] != source_info["width"]
            or target_info["height"] != source_info["height"]
            or target_info["sha256"] != source_info["sha256"]
        ):
            raise OSError("参考图复制后的校验结果不一致。")
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return source_info


CF_DIB = 8
GMEM_MOVEABLE = 0x0002


def copy_image_to_clipboard(path: Path) -> None:
    """Copy a full-resolution image as a Windows DIB for editor paste support."""
    with Image.open(path) as source:
        image = source.convert("RGB")
    stream = BytesIO()
    image.save(stream, format="BMP")
    dib = stream.getvalue()[14:]

    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    user32.SetClipboardData.restype = wintypes.HANDLE

    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
    if not handle:
        raise OSError("无法为剪贴板图像分配内存。")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise OSError("无法写入剪贴板图像数据。")
    try:
        ctypes.memmove(pointer, dib, len(dib))
    finally:
        kernel32.GlobalUnlock(handle)

    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        raise OSError("剪贴板正被其他程序占用。")
    try:
        if not user32.EmptyClipboard():
            raise OSError("无法清空剪贴板。")
        if not user32.SetClipboardData(CF_DIB, handle):
            raise OSError("无法将原图复制到剪贴板。")
        handle = None  # Clipboard owns the allocated memory after SetClipboardData.
    finally:
        user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)


class Assistant:
    def __init__(self, workspace_path: Path, task_id: str, *, contributor: bool = False):
        dpi_aware()
        self.repository = workspace_path.resolve()
        self.task_id = task_id
        self.contributor = contributor
        self.workspace = load_workspace(self.repository)
        self.task = load_task(self.workspace, task_id)
        self.root = tk.Tk()
        title = "mdoc 协作者截图助手" if contributor else "mdoc 截图助手"
        self.root.title(f"{title} — {task_id}")
        self.root.geometry("1440x860")
        self.root.minsize(1040, 620)
        self.items = {}
        self.original_preview_image = self.capture_preview_image = None
        self.preview_resize_job = None
        self.preview_dragging = False
        self.capture_in_progress = False
        self.last_capture_request = 0.0
        self.capture_context = None
        self.capture_entry = None
        self.modal_count = 0
        self.hotkey_text = "正在注册全局截图快捷键…"
        self.events = queue.Queue()
        self.hotkey = GlobalHotkey(self.events)
        local_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "mdoc" / "screenshot-assistant"
        self.local_path = local_root / f"{task_id}.json"
        self.load_local()
        self.lock = InstanceLock(self.workspace.control / "locks" / f"screenshot-assistant-{task_id}.lock")
        if not self.lock.acquire():
            self.root.destroy()
            raise RuntimeError("此任务的截图助手已经在运行。")
        self.original_menu = tk.Menu(self.root, tearoff=False)
        self.original_menu.add_command(label="复制参考图到剪贴板", command=self.copy_original)
        self.original_menu.add_command(label="用默认程序打开参考图", command=lambda: self.open_image("reference"))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        try:
            self.build()
            self.refresh()
            self.hotkey.start()
            self.root.after(50, self.poll_events)
        except Exception:
            self.lock.release()
            self.root.destroy()
            raise

    def load_local(self):
        try:
            value = json.loads(self.local_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            value = {}
        preferences = value.get("preferences", {}) if isinstance(value, dict) else {}
        try:
            ratio = float(preferences.get("preview_split_ratio", 0.42))
        except (TypeError, ValueError):
            ratio = 0.42
        self.scope_var = tk.StringVar(value=preferences.get("capture_scope", "current_monitor"))
        self.auto_var = tk.BooleanVar(value=preferences.get("auto_advance", True))
        self.locale_var = tk.StringVar(value=preferences.get("selected_locale", ""))
        self.preview_split_ratio = max(0.2, min(0.8, ratio))

    def save_local(self):
        value = {
            "schema_version": 1,
            "preferences": {
                "capture_scope": self.scope_var.get(),
                "auto_advance": bool(self.auto_var.get()),
                "selected_locale": self.locale_var.get(),
                "preview_split_ratio": self.preview_split_ratio,
            },
        }
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.local_path.with_name(f".{self.local_path.name}.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.local_path)

    def build(self):
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="刷新", command=self.refresh).pack(side="left")
        ttk.Button(toolbar, text="编辑当前项", command=self.edit_current).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="截取区域", command=lambda: self.request_capture("local")).pack(side="left", padx=6)
        ttk.Button(toolbar, text="导入已编辑图片", command=self.import_image).pack(side="left")
        ttk.Button(toolbar, text="复制参考图为新截图", command=self.use_original_as_capture).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="打开截图目录", command=self.open_folder).pack(side="left", padx=6)
        if self.contributor:
            ttk.Button(toolbar, text="提交截图成果", command=self.submit).pack(side="right")
        else:
            ttk.Button(toolbar, text="验收当前全部截图", command=self.accept).pack(side="right")

        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        left = ttk.Frame(body)
        right = ttk.Panedwindow(body, orient="vertical")
        body.add(left, weight=2)
        body.add(right, weight=6)
        locale_bar = ttk.Frame(left)
        locale_bar.pack(fill="x", pady=(0, 6))
        ttk.Label(locale_bar, text="语言：").pack(side="left")
        self.locale_selector = ttk.Combobox(locale_bar, textvariable=self.locale_var, state="readonly", width=10)
        self.locale_selector.pack(side="left", fill="x", expand=True)
        self.locale_selector.bind("<<ComboboxSelected>>", self.change_locale)
        self.tree = ttk.Treeview(left, columns=("status", "target"), show="headings")
        for key, text, width in (("status", "状态", 90), ("target", "原图位置", 430)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, stretch=key == "target")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.preview)
        self.tree.bind("<Double-Button-1>", lambda _event: self.edit_current())
        self.tree.bind("<F5>", lambda _event: self.refresh())

        requirement_frame = ttk.Labelframe(right, text="截图要求", padding=8)
        comparison = ttk.Labelframe(right, text="参考图 / 新截图对照", padding=8)
        right.add(requirement_frame, weight=1)
        right.add(comparison, weight=4)
        self.detail = tk.Text(requirement_frame, height=8, wrap="word", state="disabled", font=("Microsoft YaHei UI", 10))
        self.detail.pack(fill="both", expand=True)

        controls = ttk.Frame(comparison)
        controls.pack(fill="x")
        ttk.Radiobutton(controls, text="当前屏幕", variable=self.scope_var, value="current_monitor", command=self.save_local).pack(side="left")
        ttk.Radiobutton(controls, text="全部屏幕", variable=self.scope_var, value="all_monitors", command=self.save_local).pack(side="left", padx=4)
        ttk.Checkbutton(controls, text="保存后自动下一项", variable=self.auto_var, command=self.save_local).pack(side="left", padx=8)
        ttk.Button(controls, text="异常状态…", command=self.exception).pack(side="left")
        ttk.Button(controls, text="恢复对照分隔条", command=self.reset_preview_split).pack(side="right")

        self.preview_area = ttk.Frame(comparison)
        self.preview_area.pack(fill="both", expand=True, pady=(8, 0))
        self.preview_reference_frame = ttk.Frame(self.preview_area)
        self.preview_current_frame = ttk.Frame(self.preview_area)
        self.preview_divider = tk.Canvas(self.preview_area, width=10, highlightthickness=0, bd=0, cursor="sb_h_double_arrow", background="#f0f0f0")
        self.preview_divider_line = self.preview_divider.create_rectangle(4, 0, 6, 1, fill="#c8c8c8", outline="")
        self.preview_divider_dots = [self.preview_divider.create_oval(3, 0, 7, 4, fill="#8a8a8a", outline="") for _ in range(3)]
        self.reference_title = ttk.Label(self.preview_reference_frame, text="参考手册图片（只读对照）")
        self.reference_title.pack(anchor="w")
        self.capture_title = ttk.Label(self.preview_current_frame, text="新截图（受控收集区）")
        self.capture_title.pack(anchor="w")
        self.original_label = ttk.Label(self.preview_reference_frame, text="选择一项查看原图", anchor="center")
        self.original_label.pack(fill="both", expand=True, pady=(4, 0))
        self.original_label.bind("<Button-3>", self.show_original_menu)
        self.preview_reference_frame.bind("<Button-3>", self.show_original_menu)
        self.capture_label = ttk.Label(self.preview_current_frame, text="尚无新截图", anchor="center")
        self.capture_label.pack(fill="both", expand=True, pady=(4, 0))
        ttk.Button(self.preview_reference_frame, text="打开图片", command=lambda: self.open_image("reference")).pack(pady=(4, 0))
        ttk.Button(self.preview_current_frame, text="打开图片", command=lambda: self.open_image("capture")).pack(pady=(4, 0))
        self.preview_area.bind("<Configure>", self.preview_resized)
        self.preview_divider.bind("<Enter>", lambda _event: self.paint_preview_divider("drag" if self.preview_dragging else "hover"))
        self.preview_divider.bind("<Leave>", lambda _event: self.paint_preview_divider("drag" if self.preview_dragging else "normal"))
        self.preview_divider.bind("<ButtonPress-1>", self.preview_divider_press)
        self.preview_divider.bind("<B1-Motion>", self.preview_divider_drag)
        self.preview_divider.bind("<ButtonRelease-1>", self.preview_divider_release)
        self.preview_divider.bind("<Double-Button-1>", self.reset_preview_split)
        self.root.after(150, self.layout_preview_split)
        self.status = ttk.Label(self.root, anchor="w", padding=(8, 2))
        self.status.pack(fill="x")
        self.root.bind("<F5>", lambda _event: self.refresh())
        self.root.bind("<Control-Shift-Z>", lambda _event: self.request_capture("local"))
        self.root.bind("<Control-o>", lambda _event: self.open_image("capture"))
        self.root.bind("<Control-Shift-O>", lambda _event: self.open_folder())

    def command(self, *arguments):
        complete = subprocess.run([sys.executable, str(SCRIPT_DIR / "mdoc.py"), *arguments, "--workspace", str(self.repository), "--no-gui", "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        if complete.returncode not in {0, 3}:
            messagebox.showerror("mdoc", complete.stdout or complete.stderr, parent=self.root)
            return None
        try:
            return json.loads(complete.stdout)
        except json.JSONDecodeError:
            return None

    def refresh(self):
        state = load_state(self.task.directory / "task-state.json", self.task_id)
        manifest = synchronize(self.task, state)
        manifest_items = manifest["items"]
        locales = screenshot_locales(manifest_items)
        selected_locale = self.locale_var.get()
        if selected_locale not in locales:
            selected_locale = locales[0] if locales else ""
            self.locale_var.set(selected_locale)
        self.locale_selector.configure(values=locales)
        visible_items = items_for_locale(manifest_items, selected_locale)
        selected = self.tree.selection()
        selected_key = selected[0] if selected else None
        self.tree.delete(*self.tree.get_children())
        self.items = {}
        self.manifest_items = manifest_items
        paths = {}
        captures = {(locale, requirement["id"]): capture for requirement, _key, locale, capture in declared(self.task)}
        for requirement, key, locale, capture in declared(self.task):
            destination = requirement["destinations"][locale]
            book = self.workspace.config["books"][self.task.definition["task"]["book"]]
            locale_root = self.repository / book["root"] / book["locales"][locale]["root"]
            action = screenshot_action(self.task.definition, locale, destination)
            reference_locale = screenshot_reference_locale(self.task.definition, locale) if action == "create" else locale
            if action == "create" and (reference_locale, requirement["id"]) not in captures:
                reference_locale = locale
            reference = captures[(reference_locale, requirement["id"])] if action == "create" else (locale_root / destination).resolve()
            try:
                if action != "create":
                    reference.relative_to(locale_root.resolve())
            except ValueError:
                raise RuntimeError(f"截图目标超出语言目录：{destination}")
            reference_label = f"{reference_locale} 参考图片" if action == "create" else "参考手册图片"
            paths[key] = (requirement, capture, reference, reference_label, destination, action)
        for key, item in visible_items.items():
            requirement, capture, reference, reference_label, destination, action = paths[key]
            self.items[key] = {
                "key": key,
                "item": item,
                "requirement": requirement,
                "capture": capture,
                "original": reference,
                "reference": reference,
                "reference_label": reference_label,
                "capture_label": f"{item['locale']} 新截图",
                "action": action,
                "destination": destination,
            }
            self.tree.insert("", "end", iid=key, values=(LABELS.get(item["status"], item["status"]), destination))
        if selected_key in self.items:
            self.tree.selection_set(selected_key)
        elif self.items:
            first = next(iter(self.items))
            self.tree.selection_set(first)
        self.preview()
        self.update_status()

    def change_locale(self, _event=None):
        self.save_local()
        self.refresh()

    def selected(self):
        values = self.tree.selection()
        return values[0] if values else None

    def preview(self, _event=None):
        key = self.selected()
        if not key or key not in self.items:
            self.detail.configure(state="normal")
            self.detail.delete("1.0", "end")
            self.detail.configure(state="disabled")
            return
        entry = self.items[key]
        item = entry["item"]
        requirement = entry["requirement"]
        capture = entry["capture"]
        reference = entry["reference"]
        lines = [
            f"{key}  |  状态：{LABELS.get(item['status'], item['status'])}",
            f"参考图：{reference}",
            f"新截图保存位置：{capture}",
            f"正式手册位置：{entry['destination']}",
        ]
        description = requirement.get("description", "")
        if description:
            lines.extend(["", "处理要求：", description])
        if item.get("reason"):
            lines.extend(["", f"状态原因：{item['reason']}"])
        if self.contributor:
            state = load_state(self.task.directory / "task-state.json", self.task_id)
            submitted = state.get("screenshot_submission") or {}
            label = {"submitted": "已提交", "stale": "提交已过期"}.get(submitted.get("status"), "未提交")
            lines.extend(["", f"协作者提交：{label}。完成后点击“提交截图成果”，由主控机验收。"])
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", "\n".join(lines))
        self.detail.configure(state="disabled")
        self.reference_title.configure(text=f"{entry['reference_label']}（只读对照） · {reference.name}")
        self.capture_title.configure(text=f"{entry['capture_label']}（受控收集区） · {LABELS.get(item['status'], item['status'])}")
        self.show_image(self.original_label, reference, "original_preview_image", "参考图片尚不存在")
        self.show_image(self.capture_label, capture, "capture_preview_image", "尚无新截图")

    def show_image(self, label, path: Path, attribute: str, empty_text: str, fast: bool = False):
        if not path.is_file():
            setattr(self, attribute, None)
            label.configure(image="", text=empty_text)
            return
        try:
            with Image.open(path) as source:
                image = source.convert("RGB")
            self.root.update_idletasks()
            width = max(120, label.winfo_width() - 16)
            height = max(120, label.winfo_height() - 16)
            image.thumbnail((width, height), Image.Resampling.BILINEAR if fast else Image.Resampling.LANCZOS)
            preview_image = ImageTk.PhotoImage(image)
            setattr(self, attribute, preview_image)
            label.configure(image=preview_image, text="")
        except Exception:
            setattr(self, attribute, None)
            label.configure(image="", text="图片无法预览")

    def paint_preview_divider(self, state):
        colors = {
            "normal": ("#f0f0f0", "#c8c8c8", "#8a8a8a"),
            "hover": ("#e5f2fb", "#78b8e6", "#4798d0"),
            "drag": ("#dcefff", "#168cff", "#168cff"),
        }
        background, line, dot = colors[state]
        self.preview_divider.configure(background=background)
        self.preview_divider.itemconfigure(self.preview_divider_line, fill=line)
        for item in self.preview_divider_dots:
            self.preview_divider.itemconfigure(item, fill=dot)

    def layout_preview_split(self):
        self.root.update_idletasks()
        width, height = self.preview_area.winfo_width(), self.preview_area.winfo_height()
        if width <= 12 or height <= 1:
            return
        usable = width - 10
        left = int(usable * self.preview_split_ratio)
        self.preview_reference_frame.place(x=0, y=0, width=left, height=height)
        self.preview_divider.place(x=left, y=0, width=10, height=height)
        self.preview_current_frame.place(x=left + 10, y=0, width=usable - left, height=height)
        self.preview_divider.coords(self.preview_divider_line, 4, 0, 6, height)
        middle = height // 2
        for item, y in zip(self.preview_divider_dots, (middle - 10, middle - 2, middle + 6)):
            self.preview_divider.coords(item, 3, y, 7, y + 4)

    def preview_resized(self, _event=None):
        self.layout_preview_split()
        if self.preview_resize_job:
            self.root.after_cancel(self.preview_resize_job)
        self.preview_resize_job = self.root.after(120, self.finish_preview_resize)

    def finish_preview_resize(self):
        self.preview_resize_job = None
        self.layout_preview_split()
        self.preview()

    def preview_divider_press(self, _event):
        self.preview_dragging = True
        self.paint_preview_divider("drag")

    def preview_divider_drag(self, event):
        width = self.preview_area.winfo_width() - 10
        if width <= 1:
            return
        self.preview_split_ratio = max(0.2, min(0.8, (self.preview_divider.winfo_x() + event.x) / width))
        self.layout_preview_split()
        if self.preview_resize_job:
            self.root.after_cancel(self.preview_resize_job)
        self.preview_resize_job = self.root.after(50, self.preview)

    def preview_divider_release(self, _event=None):
        self.preview_dragging = False
        self.paint_preview_divider("hover")
        if self.preview_resize_job:
            self.root.after_cancel(self.preview_resize_job)
            self.preview_resize_job = None
        self.save_local()
        self.preview()

    def reset_preview_split(self, _event=None):
        self.preview_split_ratio = 0.42
        self.layout_preview_split()
        self.save_local()
        self.preview()

    def update_status(self):
        if not hasattr(self, "status"):
            return
        completed_statuses = {"captured", "accepted", "waived", "not_applicable"}
        required = [entry["item"] for entry in self.items.values() if entry["item"]["required"]]
        complete = sum(item["status"] in completed_statuses for item in required)
        all_required = [item for item in getattr(self, "manifest_items", {}).values() if item["required"]]
        all_complete = sum(item["status"] in completed_statuses for item in all_required)
        state = load_state(self.task.directory / "task-state.json", self.task_id)
        acceptance = (state.get("screenshot_acceptance") or {}).get("status", "未验收")
        scope = "全部屏幕" if self.scope_var.get() == "all_monitors" else "当前屏幕"
        self.status.configure(text=f"{self.locale_var.get()}：必需截图 {complete}/{len(required)}；全部：{all_complete}/{len(all_required)}；总体验收：{acceptance}；截图范围：{scope}；{self.hotkey_text}")

    def poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "capture":
                    self.request_capture("global", payload)
                elif kind == "hotkey-status":
                    self.hotkey_text = {
                        "registered": "全局截图快捷键：Ctrl+Shift+Z",
                        "failed": "全局快捷键注册失败，工具栏截图仍可用",
                        "stopped": "全局截图快捷键已停止，重启截图助手可恢复",
                        "unsupported": "当前系统不支持全局截图快捷键",
                    }.get(payload, "全局截图快捷键状态未知")
                    self.update_status()
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(50, self.poll_events)

    def open_image(self, kind: str):
        key = self.selected()
        if not key:
            messagebox.showwarning("mdoc", "请先选择一项截图任务。")
            return
        entry = self.items[key]
        path = entry["reference"] if kind == "reference" else entry["capture"]
        if not path.is_file():
            label = entry["reference_label"] if kind == "reference" else entry["capture_label"]
            messagebox.showwarning("mdoc", f"{label}不存在：\n{path}")
            return
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror("mdoc", f"无法用默认程序打开图片：\n{path}\n\n{exc}")

    def show_original_menu(self, event):
        self.original_menu.tk_popup(event.x_root, event.y_root)
        self.original_menu.grab_release()

    def copy_original(self):
        key = self.selected()
        if not key:
            messagebox.showwarning("mdoc", "请先选择一项截图任务。")
            return
        source = self.items[key]["reference"]
        if not source.is_file():
            messagebox.showwarning("mdoc", f"参考图片不存在：\n{source}")
            return
        try:
            copy_image_to_clipboard(source)
        except OSError as exc:
            messagebox.showerror("mdoc", f"无法复制参考图到剪贴板：\n{exc}")
            return
        self.show_status_message("参考图已复制到剪贴板，可在图片编辑软件中直接粘贴。")

    def show_status_message(self, text):
        self.status.configure(text=text)
        self.root.after(3000, self.update_status)

    def ensure_editable(self):
        state = load_state(self.task.directory / "task-state.json", self.task_id)
        if screenshot_changes_allowed(state):
            return True
        messagebox.showerror("mdoc", "任务已经进入最终审核或终态，不能继续修改截图。请创建修订任务。", parent=self.root)
        return False

    def open_folder(self):
        key = self.selected()
        if not key:
            return
        folder = self.items[key]["capture"].parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))
        except OSError as exc:
            messagebox.showerror("mdoc", f"无法打开截图目录：\n{folder}\n\n{exc}", parent=self.root)

    def record_capture(self, key, *, image=None, source=None):
        """Save one controlled capture and preserve coordinator acceptance when valid."""
        entry = self.items[key]
        target = entry["capture"]
        with task_lock(self.task):
            state_path = self.task.directory / "task-state.json"
            state = load_state(state_path, self.task_id)
            was_accepted = bool(state.get("screenshot_acceptance"))
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.tmp")
            try:
                if source is not None:
                    copy_reference_to_capture(source, target)
                else:
                    if image is None:
                        raise OSError("没有可保存的截图内容。")
                    captured = image.convert("RGB") if image_format(target) == "JPEG" else image
                    captured.save(temporary, format=image_format(target), **image_save_options(target))
                    if png_info(temporary) is None:
                        raise OSError("生成的截图不是有效的 PNG 或 JPEG 文件。")
                    temporary.replace(target)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            clear_image_edit_artifacts(self.task, entry)
            synchronize(self.task, state)
            state["screenshots"][key]["status"] = "pending"
            state["screenshots"][key].pop("reason", None)
            synchronize(self.task, state)
            if was_accepted and not self.contributor:
                accept_screenshots(self.task, state)
            save_state(state_path, state)

    def use_original_as_capture(self):
        if not self.ensure_editable():
            return
        key = self.selected()
        if not key:
            messagebox.showwarning("mdoc", "请先选择一项截图任务。")
            return
        entry = self.items[key]
        source = entry["reference"]
        target = entry["capture"]
        if not source.is_file():
            messagebox.showwarning("mdoc", f"参考图片不存在：\n{source}")
            return
        if target.is_file() and not messagebox.askyesno(
            "替换当前截图",
            "将复制参考图为当前新截图，并清除该项之前的图片编辑记录。\n\n是否继续？",
            parent=self.root,
        ):
            return
        try:
            self.record_capture(key, source=source)
        except (OSError, RuntimeError) as exc:
            messagebox.showerror("mdoc", f"无法复制参考图为当前新截图：\n{exc}")
            return
        self.refresh()
        self.advance_after_completion(key)

    def import_image(self):
        if not self.ensure_editable():
            return
        key = self.selected()
        if not key:
            messagebox.showwarning("mdoc", "请先选择一项截图任务。")
            return
        source_name = filedialog.askopenfilename(
            parent=self.root,
            title="选择已编辑的图片",
            filetypes=[("支持的图片", "*.png *.jpg *.jpeg"), ("所有文件", "*.*")],
        )
        if not source_name:
            return
        source = Path(source_name)
        if source.suffix.casefold() not in {".png", ".jpg", ".jpeg"} or png_info(source) is None:
            messagebox.showerror("mdoc", "只能导入有效的 PNG 或 JPEG 图片。")
            return
        target = self.items[key]["capture"]
        original = self.items[key]["reference"]
        source_info = png_info(source)
        original_info = png_info(original) or png_info(target)
        if not original_info:
            messagebox.showerror("mdoc", "参考图片和现有新截图都不存在，无法校验导入图片尺寸。")
            return
        if source_info["width"] != original_info["width"] or source_info["height"] != original_info["height"]:
            messagebox.showerror(
                "mdoc",
                f"导入图片尺寸必须与当前底图一致。\n底图：{original_info['width']} × {original_info['height']}\n"
                f"导入图：{source_info['width']} × {source_info['height']}",
            )
            return
        try:
            with Image.open(source) as edited:
                self.record_capture(key, image=edited.copy())
        except (OSError, RuntimeError) as exc:
            messagebox.showerror("mdoc", f"无法导入图片：\n{exc}")
            return
        self.refresh()
        self.advance_after_completion(key)

    def edit_current(self):
        key = self.selected()
        if not key:
            messagebox.showwarning("mdoc", "请先选择一项截图任务。")
            return
        entry = self.items[key]
        if not entry["reference"].is_file() and not entry["capture"].is_file():
            messagebox.showwarning("mdoc", "参考图片和新截图都不存在，无法打开编辑窗口。", parent=self.root)
            return
        self.modal_count += 1
        released = False

        def release_modal(event=None):
            nonlocal released
            if event is not None and event.widget is not editor:
                return
            if released:
                return
            released = True
            self.modal_count = max(0, self.modal_count - 1)

        try:
            editor = ImageTextEditor(self.root, self.task, self.items[key], on_saved=self.refresh, contributor=self.contributor)
            editor.transient(self.root)
            editor.grab_set()
            editor.bind("<Destroy>", release_modal, add="+")
        except Exception:
            release_modal()
            raise

    def request_capture(self, source, payload=None):
        if not self.ensure_editable():
            return
        now = time.monotonic()
        if self.capture_in_progress or self.modal_count or now - self.last_capture_request < 0.3:
            return
        key = self.selected()
        if not key:
            return
        self.last_capture_request = now
        self.capture_in_progress = True
        payload = payload or {}
        if source == "global":
            cursor = payload.get("cursor", (0, 0))
            bbox = virtual_screen() if self.scope_var.get() == "all_monitors" else monitor_for_point(*cursor)
            delay = 120
        else:
            bbox = virtual_screen() if self.scope_var.get() == "all_monitors" else monitor_for_window(self.root)
            delay = 250
        root_state = self.root.state()
        self.capture_context = {
            "source": source,
            "foreground": payload.get("foreground", 0),
            "window_state": root_state,
            "visible": root_state not in {"withdrawn", "iconic"},
        }
        self.capture_entry = key
        self.root.withdraw()
        self.root.after(delay, lambda: self.start_capture(bbox, key))

    def start_capture(self, bbox, key):
        try:
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
            windows = window_snapshot(bbox, {int(self.root.winfo_id())})
        except Exception as exc:
            self.activate_assistant()
            self.capture_in_progress = False
            self.capture_entry = None
            messagebox.showerror("截图失败", str(exc), parent=self.root)
            return
        CaptureOverlay(self.root, image, bbox, lambda result: self.finish_capture(result, key), windows)

    def finish_capture(self, image, key):
        context = self.capture_context or {}
        self.capture_context = None
        self.capture_entry = None
        if image is None:
            self.restore_after_cancel(context)
            self.capture_in_progress = False
            return
        self.activate_assistant()
        target = self.items[key]["capture"]
        if target.is_file() and not messagebox.askyesno("覆盖截图", f"目标文件已存在，是否覆盖？\n{target}", parent=self.root):
            self.capture_in_progress = False
            return
        try:
            self.record_capture(key, image=image)
            self.refresh()
            self.advance_after_completion(key)
        except (OSError, RuntimeError) as exc:
            messagebox.showerror("截图保存失败", str(exc), parent=self.root)
        finally:
            self.capture_in_progress = False

    def activate_assistant(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        if sys.platform == "win32":
            try:
                ctypes.windll.user32.SetForegroundWindow(self.root.winfo_id())
            except Exception:
                pass

    def restore_after_cancel(self, context):
        if context.get("source") != "global":
            self.activate_assistant()
            return
        if context.get("visible"):
            self.root.deiconify()
            self.root.lower()
        elif context.get("window_state") == "iconic":
            self.root.iconify()
        else:
            self.root.withdraw()
        foreground = context.get("foreground", 0)
        if sys.platform == "win32" and foreground and ctypes.windll.user32.IsWindow(foreground):
            try:
                ctypes.windll.user32.SetForegroundWindow(foreground)
            except Exception:
                pass

    def advance_after_completion(self, current):
        if not self.auto_var.get():
            return
        children = list(self.tree.get_children())
        if not children:
            return
        try:
            index = children.index(current)
        except ValueError:
            index = -1
        next_item = children[min(index + 1, len(children) - 1)]
        self.tree.selection_set(next_item)
        self.tree.focus(next_item)
        self.tree.see(next_item)
        self.preview()

    def exception(self):
        if not self.ensure_editable():
            return
        key = self.selected()
        if not key:
            return
        self.modal_count += 1
        item = self.items[key]["item"]
        current = item.get("status", "pending")
        restore = "captured" if self.items[key]["capture"].is_file() else "pending"
        win = tk.Toplevel(self.root)
        win.title("设置截图状态")
        win.transient(self.root)
        choice = tk.StringVar(value=current if current in EXCEPTION_STATUSES else "blocked")
        options = (
            ("受阻", "blocked"),
            ("需重拍", "needs_retake"),
            ("不适用", "not_applicable"),
            ("豁免", "waived"),
            ("恢复为已捕获" if restore == "captured" else "恢复为待截图", restore),
        )
        for label, value in options:
            ttk.Radiobutton(win, text=label, variable=choice, value=value).pack(anchor="w", padx=16, pady=3)
        ttk.Label(win, text="原因（异常状态必填）").pack(anchor="w", padx=16, pady=(8, 2))
        reason = ttk.Entry(win, width=60)
        reason.pack(padx=16)
        if item.get("reason"):
            reason.insert(0, item["reason"])

        closed = False

        def close_dialog():
            nonlocal closed
            if closed:
                return
            closed = True
            self.modal_count = max(0, self.modal_count - 1)
            win.destroy()

        def save_status():
            status = choice.get()
            if status in EXCEPTION_STATUSES and not reason.get().strip():
                messagebox.showwarning("mdoc", "异常状态需要填写原因。", parent=win)
                return
            arguments = ["task", "screenshots", "set-status", "--task", self.task_id, "--item", key, "--status", status, "--reason", reason.get().strip()]
            if self.contributor:
                arguments.append("--contributor")
            if self.command(*arguments) is not None:
                close_dialog()
                self.refresh()

        ttk.Button(win, text="保存", command=save_status).pack(pady=12)
        win.protocol("WM_DELETE_WINDOW", close_dialog)
        win.grab_set()
        win.focus_force()

    def accept(self):
        result = self.command("task", "screenshots", "accept", "--task", self.task_id)
        if result is not None:
            messagebox.showinfo("mdoc", "截图已验收，任务可以继续编写。")
            self.refresh()

    def submit(self):
        result = self.command("task", "screenshots", "submit", "--task", self.task_id)
        if result is not None:
            messagebox.showinfo("mdoc", "截图成果已提交，等待主控机验收。")
            self.refresh()

    def close(self):
        try:
            self.save_local()
        finally:
            self.hotkey.stop()
            self.lock.release()
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--contributor", action="store_true")
    args = parser.parse_args()
    try:
        Assistant(args.workspace, args.task, contributor=args.contributor).run()
        return 0
    except Exception as exc:
        try:
            messagebox.showerror("截图助手", str(exc))
        except Exception:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
