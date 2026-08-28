#!/usr/bin/env python3
"""Thin human screenshot UI over the mdoc task state machine."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tkinter as tk
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
from mdoc_core.screenshots import declared, png_info, synchronize
from mdoc_core.state import load_state
from image_text_editor import ImageTextEditor

LABELS = {
    "pending": "待截图",
    "captured": "已捕获",
    "needs_retake": "需重拍",
    "waived": "已豁免",
    "not_applicable": "不适用",
    "accepted": "已验收",
}


def image_format(path: Path) -> str:
    return "JPEG" if path.suffix.casefold() in {".jpg", ".jpeg"} else "PNG"


def image_save_options(path: Path) -> dict:
    return {"quality": 95, "subsampling": 0} if image_format(path) == "JPEG" else {}

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


class RegionSelector:
    def __init__(self, owner: tk.Tk):
        self.owner = owner
        self.result = None
        self.image = ImageGrab.grab()
        self.window = tk.Toplevel(owner)
        self.window.attributes("-fullscreen", True)
        self.window.attributes("-topmost", True)
        self.photo = ImageTk.PhotoImage(self.image)
        self.canvas = tk.Canvas(self.window, cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.start = None
        self.rectangle = None
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        self.window.grab_set()
        self.window.wait_window()

    def press(self, event):
        self.start = (event.x, event.y)
        self.rectangle = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#00a7ff", width=2)

    def drag(self, event):
        if self.start:
            self.canvas.coords(self.rectangle, self.start[0], self.start[1], event.x, event.y)

    def release(self, event):
        if not self.start:
            return
        left, right = sorted((self.start[0], event.x))
        top, bottom = sorted((self.start[1], event.y))
        if right - left >= 2 and bottom - top >= 2:
            self.result = self.image.crop((left, top, right, bottom))
        self.window.destroy()


class Assistant:
    def __init__(self, workspace_path: Path, task_id: str, *, contributor: bool = False):
        self.repository = workspace_path.resolve()
        self.task_id = task_id
        self.contributor = contributor
        self.workspace = load_workspace(self.repository)
        self.task = load_task(self.workspace, task_id)
        self.root = tk.Tk()
        title = "mdoc 协作者截图助手" if contributor else "mdoc 截图助手"
        self.root.title(f"{title} — {task_id}")
        self.root.geometry("1320x720")
        self.items = {}
        self.original_preview_image = None
        self.capture_preview_image = None
        self.original_menu = tk.Menu(self.root, tearoff=False)
        self.original_menu.add_command(label="复制原图到剪贴板", command=self.copy_original)
        self.build()
        self.refresh()

    def build(self):
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="刷新", command=self.refresh).pack(side="left")
        ttk.Button(toolbar, text="编辑当前项", command=self.edit_current).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="截取区域", command=self.capture).pack(side="left", padx=6)
        ttk.Button(toolbar, text="导入已编辑图片", command=self.import_image).pack(side="left")
        ttk.Button(toolbar, text="用默认程序打开原图", command=lambda: self.open_image("original")).pack(side="left", padx=6)
        ttk.Button(toolbar, text="用默认程序打开新截图", command=lambda: self.open_image("capture")).pack(side="left", padx=6)
        ttk.Button(toolbar, text="需重拍", command=lambda: self.set_status("needs_retake")).pack(side="left")
        ttk.Button(toolbar, text="豁免", command=lambda: self.set_status("waived")).pack(side="left", padx=6)
        ttk.Button(toolbar, text="不适用", command=lambda: self.set_status("not_applicable")).pack(side="left")
        ttk.Button(toolbar, text="恢复待截图", command=lambda: self.set_status("pending")).pack(side="left", padx=6)
        if self.contributor:
            ttk.Button(toolbar, text="提交截图成果", command=self.submit).pack(side="right")
        else:
            ttk.Button(toolbar, text="验收当前全部截图", command=self.accept).pack(side="right")

        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=2)
        body.add(right, weight=3)
        self.tree = ttk.Treeview(left, columns=("locale", "status", "target"), show="headings")
        for key, text, width in (("locale", "语言", 70), ("status", "状态", 90), ("target", "原图位置", 360)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, stretch=key == "target")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.preview())
        self.tree.bind("<Double-Button-1>", lambda _event: self.edit_current())

        comparison = ttk.Frame(right)
        comparison.pack(fill="both", expand=True)
        original_frame = ttk.Labelframe(comparison, text="原手册图片（只读对照）", padding=6)
        original_frame.pack(side="left", fill="both", expand=True, padx=(0, 4))
        capture_frame = ttk.Labelframe(comparison, text="新截图（受控收集区）", padding=6)
        capture_frame.pack(side="left", fill="both", expand=True, padx=(4, 0))
        self.original_label = ttk.Label(original_frame, text="选择一项查看原图", anchor="center")
        self.original_label.pack(fill="both", expand=True)
        self.original_label.bind("<Button-3>", self.show_original_menu)
        original_frame.bind("<Button-3>", self.show_original_menu)
        self.capture_label = ttk.Label(capture_frame, text="尚无新截图", anchor="center")
        self.capture_label.pack(fill="both", expand=True)
        self.detail = ttk.Label(right, wraplength=760, justify="left")
        self.detail.pack(fill="x", pady=8)

    def command(self, *arguments):
        complete = subprocess.run([sys.executable, str(SCRIPT_DIR / "mdoc.py"), *arguments, "--workspace", str(self.repository), "--no-gui", "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        if complete.returncode not in {0, 3}:
            messagebox.showerror("mdoc", complete.stdout or complete.stderr)
            return None
        try:
            return json.loads(complete.stdout)
        except json.JSONDecodeError:
            return None

    def refresh(self):
        state = load_state(self.task.directory / "task-state.json", self.task_id)
        manifest = synchronize(self.task, state)
        selected = self.tree.selection()
        selected_key = selected[0] if selected else None
        self.tree.delete(*self.tree.get_children())
        self.items = {}
        paths = {}
        for requirement, key, locale, capture in declared(self.task):
            destination = requirement["destinations"][locale]
            book = self.workspace.config["books"][self.task.definition["task"]["book"]]
            locale_root = self.repository / book["root"] / book["locales"][locale]["root"]
            original = (locale_root / destination).resolve()
            try:
                original.relative_to(locale_root.resolve())
            except ValueError:
                raise RuntimeError(f"截图目标超出语言目录：{destination}")
            paths[key] = (requirement, capture, original, destination)
        for key, item in manifest["items"].items():
            requirement, capture, original, destination = paths[key]
            self.items[key] = {
                "item": item,
                "requirement": requirement,
                "capture": capture,
                "original": original,
                "destination": destination,
            }
            self.tree.insert("", "end", iid=key, values=(item["locale"], LABELS[item["status"]], destination))
        if selected_key in self.items:
            self.tree.selection_set(selected_key)
        elif self.items:
            first = next(iter(self.items))
            self.tree.selection_set(first)
        self.preview()

    def selected(self):
        values = self.tree.selection()
        return values[0] if values else None

    def preview(self):
        key = self.selected()
        if not key:
            return
        entry = self.items[key]
        item = entry["item"]
        requirement = entry["requirement"]
        capture = entry["capture"]
        original = entry["original"]
        description = requirement.get("description", "")
        contribution = ""
        if self.contributor:
            state = load_state(self.task.directory / "task-state.json", self.task_id)
            submitted = state.get("screenshot_submission") or {}
            label = {"submitted": "已提交", "stale": "提交已过期"}.get(submitted.get("status"), "未提交")
            contribution = f"\n协作者提交：{label}。完成后点击“提交截图成果”，由主控机验收和发布。"
        self.detail.configure(
            text=(
                f"{key}  |  状态：{LABELS[item['status']]}\n"
                f"原图：{original}\n"
                f"新截图保存位置：{capture}\n"
                f"处理要求：{description}{contribution}"
            )
        )
        self.show_image(self.original_label, original, "original_preview_image", "原手册图片无法预览")
        self.show_image(self.capture_label, capture, "capture_preview_image", "尚无新截图")

    def show_image(self, label, path: Path, attribute: str, empty_text: str):
        if not path.is_file():
            setattr(self, attribute, None)
            label.configure(image="", text=empty_text)
            return
        try:
            with Image.open(path) as source:
                image = source.convert("RGB")
            image.thumbnail((520, 500))
            preview_image = ImageTk.PhotoImage(image)
            setattr(self, attribute, preview_image)
            label.configure(image=preview_image, text="")
        except Exception:
            setattr(self, attribute, None)
            label.configure(image="", text="图片无法预览")

    def open_image(self, kind: str):
        key = self.selected()
        if not key:
            messagebox.showwarning("mdoc", "请先选择一项截图任务。")
            return
        entry = self.items[key]
        path = entry["original"] if kind == "original" else entry["capture"]
        if not path.is_file():
            label = "原手册图片" if kind == "original" else "新截图"
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
        source = self.items[key]["original"]
        if not source.is_file():
            messagebox.showwarning("mdoc", f"原手册图片不存在：\n{source}")
            return
        try:
            copy_image_to_clipboard(source)
        except OSError as exc:
            messagebox.showerror("mdoc", f"无法复制原图到剪贴板：\n{exc}")
            return
        self.detail.configure(text=self.detail.cget("text") + "\n原图已复制到剪贴板，可在图片编辑软件中直接粘贴。")

    def import_image(self):
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
        original = self.items[key]["original"]
        source_info = png_info(source)
        original_info = png_info(original)
        if not original_info:
            messagebox.showerror("mdoc", f"无法读取原图尺寸：\n{original}")
            return
        if source_info["width"] != original_info["width"] or source_info["height"] != original_info["height"]:
            messagebox.showerror(
                "mdoc",
                f"导入图片尺寸必须与原图一致。\n原图：{original_info['width']} × {original_info['height']}\n"
                f"导入图：{source_info['width']} × {source_info['height']}",
            )
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            temporary = target.with_name(f".{target.name}.tmp")
            with Image.open(source) as edited:
                converted = edited.convert("RGB") if image_format(target) == "JPEG" else edited.copy()
                converted.save(temporary, format=image_format(target), **image_save_options(target))
            if png_info(temporary) is None:
                temporary.unlink(missing_ok=True)
                raise OSError("导入后的文件不是有效图片")
            temporary.replace(target)
        except OSError as exc:
            messagebox.showerror("mdoc", f"无法导入图片：\n{exc}")
            return
        if not self.contributor:
            self.command("task", "continue", "--task", self.task_id)
        self.refresh()

    def edit_current(self):
        key = self.selected()
        if not key:
            messagebox.showwarning("mdoc", "请先选择一项截图任务。")
            return
        editor = ImageTextEditor(self.root, self.task, self.items[key], on_saved=self.refresh, contributor=self.contributor)
        editor.transient(self.root)
        editor.grab_set()

    def capture(self):
        key = self.selected()
        if not key:
            return
        path = self.items[key]["capture"]
        self.root.withdraw()
        self.root.update_idletasks()
        selector = RegionSelector(self.root)
        self.root.deiconify()
        if selector.result is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        captured = selector.result.convert("RGB") if image_format(path) == "JPEG" else selector.result
        captured.save(path, format=image_format(path), **image_save_options(path))
        if not self.contributor:
            self.command("task", "continue", "--task", self.task_id)
        self.refresh()

    def set_status(self, status):
        key = self.selected()
        if key:
            arguments = ["task", "screenshots", "set-status", "--task", self.task_id, "--item", key, "--status", status]
            if self.contributor:
                arguments.append("--contributor")
            self.command(*arguments)
            self.refresh()

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

    def run(self):
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--contributor", action="store_true")
    args = parser.parse_args()
    Assistant(args.workspace, args.task, contributor=args.contributor).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
