#!/usr/bin/env python3
"""Thin human screenshot UI over the mdoc task state machine."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import ImageGrab, ImageTk

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from mdoc_core.config import load_task, load_workspace
from mdoc_core.screenshots import declared, synchronize
from mdoc_core.state import load_state

LABELS = {
    "pending": "待截图",
    "captured": "已捕获",
    "needs_retake": "需重拍",
    "waived": "已豁免",
    "not_applicable": "不适用",
    "accepted": "已验收",
}


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
    def __init__(self, repository: Path, task_id: str):
        self.repository = repository.resolve()
        self.task_id = task_id
        self.workspace = load_workspace(self.repository)
        self.task = load_task(self.workspace, task_id)
        self.root = tk.Tk()
        self.root.title(f"mdoc 截图助手 — {task_id}")
        self.root.geometry("900x560")
        self.items = {}
        self.preview_image = None
        self.build()
        self.refresh()

    def build(self):
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="刷新", command=self.refresh).pack(side="left")
        ttk.Button(toolbar, text="截取区域", command=self.capture).pack(side="left", padx=6)
        ttk.Button(toolbar, text="需重拍", command=lambda: self.set_status("needs_retake")).pack(side="left")
        ttk.Button(toolbar, text="豁免", command=lambda: self.set_status("waived")).pack(side="left", padx=6)
        ttk.Button(toolbar, text="不适用", command=lambda: self.set_status("not_applicable")).pack(side="left")
        ttk.Button(toolbar, text="恢复待截图", command=lambda: self.set_status("pending")).pack(side="left", padx=6)
        ttk.Button(toolbar, text="验收当前全部截图", command=self.accept).pack(side="right")

        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=2)
        body.add(right, weight=3)
        self.tree = ttk.Treeview(left, columns=("locale", "status", "target"), show="headings")
        for key, text, width in (("locale", "语言", 70), ("status", "状态", 90), ("target", "目标 PNG", 360)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, stretch=key == "target")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.preview())
        self.preview = ttk.Label(right, text="选择一项查看预览", anchor="center")
        self.preview.pack(fill="both", expand=True)
        self.detail = ttk.Label(right, wraplength=460, justify="left")
        self.detail.pack(fill="x", pady=8)

    def command(self, *arguments):
        complete = subprocess.run([sys.executable, str(SCRIPT_DIR / "mdoc.py"), *arguments, "--repository", str(self.repository), "--no-gui"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
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
        paths = {key: capture for _requirement, key, _locale, capture in declared(self.task)}
        for key, item in manifest["items"].items():
            self.items[key] = (item, paths[key])
            self.tree.insert("", "end", iid=key, values=(item["locale"], LABELS[item["status"]], str(paths[key])))
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
        item, path = self.items[key]
        self.detail.configure(text=f"{key}\n状态：{LABELS[item['status']]}\n目标：{path}\n可直接把有效 PNG 放到该路径，然后点击刷新。")
        if not path.is_file():
            self.preview.configure(image="", text="尚无截图")
            return
        try:
            from PIL import Image
            image = Image.open(path)
            image.thumbnail((500, 420))
            self.preview_image = ImageTk.PhotoImage(image)
            self.preview.configure(image=self.preview_image, text="")
        except Exception:
            self.preview.configure(image="", text="PNG 无法预览")

    def capture(self):
        key = self.selected()
        if not key:
            return
        _item, path = self.items[key]
        self.root.withdraw()
        self.root.update_idletasks()
        selector = RegionSelector(self.root)
        self.root.deiconify()
        if selector.result is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        selector.result.save(path, format="PNG")
        self.command("task", "continue", "--task", self.task_id)
        self.refresh()

    def set_status(self, status):
        key = self.selected()
        if key:
            self.command("task", "screenshot-status", "--task", self.task_id, "--item", key, "--status", status)
            self.refresh()

    def accept(self):
        result = self.command("task", "accept-screenshots", "--task", self.task_id)
        if result is not None:
            messagebox.showinfo("mdoc", "截图已验收，任务可以继续编写。")
            self.refresh()

    def run(self):
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    Assistant(args.repository, args.task).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
