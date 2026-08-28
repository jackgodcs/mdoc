"""Windows capture interaction shared by all mdoc screenshot tasks."""
from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageGrab, ImageTk


def dpi_aware() -> None:
    """Use physical-pixel coordinates for Windows screen capture."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def virtual_screen() -> tuple[int, int, int, int]:
    if sys.platform != "win32":
        image = ImageGrab.grab()
        return (0, 0, image.width, image.height)
    user32 = ctypes.windll.user32
    left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    return (left, top, left + user32.GetSystemMetrics(78), top + user32.GetSystemMetrics(79))


def monitor_for_window(root: tk.Tk) -> tuple[int, int, int, int]:
    if sys.platform != "win32":
        return (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())

    class Rect(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", Rect), ("rcWork", Rect), ("dwFlags", ctypes.c_ulong)]

    user32 = ctypes.windll.user32
    monitor = user32.MonitorFromWindow(root.winfo_id(), 2)
    info = MonitorInfo(ctypes.sizeof(MonitorInfo))
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return virtual_screen()
    rect = info.rcMonitor
    return (rect.left, rect.top, rect.right, rect.bottom)


def monitor_for_point(x: int, y: int) -> tuple[int, int, int, int]:
    if sys.platform != "win32":
        return virtual_screen()

    class Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class Rect(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", Rect), ("rcWork", Rect), ("dwFlags", ctypes.c_ulong)]

    user32 = ctypes.windll.user32
    monitor = user32.MonitorFromPoint(Point(x, y), 2)
    info = MonitorInfo(ctypes.sizeof(MonitorInfo))
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return virtual_screen()
    rect = info.rcMonitor
    return (rect.left, rect.top, rect.right, rect.bottom)


def window_snapshot(bbox: tuple[int, int, int, int], excluded: set[int] | None = None) -> list[dict]:
    """Capture visible top-level-window bounds in frozen-image coordinates."""
    if sys.platform != "win32":
        return []
    excluded = excluded or set()
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    left, top, right, bottom = bbox
    records: list[dict] = []
    ignored = {"Progman", "WorkerW", "Shell_TrayWnd", "#32768", "tooltips_class32", "SysShadow", "MSCTFIME UI", "IME"}

    class Rect(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    def title(hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value.strip()

    def class_name(hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value

    def process_name(hwnd: int) -> str:
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).stem
        finally:
            kernel32.CloseHandle(handle)
        return ""

    def callback(hwnd, _lparam):
        hwnd = int(hwnd)
        if hwnd in excluded or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        cls = class_name(hwnd)
        if cls in ignored or user32.GetWindowLongW(hwnd, -20) & 0x20:
            return True
        rect = Rect()
        ok = False
        try:
            ok = ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0
        except Exception:
            pass
        if not ok and not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        if rect.right - rect.left < 32 or rect.bottom - rect.top < 32:
            return True
        clipped = (max(left, rect.left), max(top, rect.top), min(right, rect.right), min(bottom, rect.bottom))
        if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
            return True
        records.append({
            "hwnd": hwnd,
            "rect": (clipped[0] - left, clipped[1] - top, clipped[2] - left, clipped[3] - top),
            "title": title(hwnd) or process_name(hwnd) or cls,
            "clipped": clipped != (rect.left, rect.top, rect.right, rect.bottom),
        })
        return True

    procedure = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(callback)
    user32.EnumWindows(procedure, 0)
    return records


class InstanceLock:
    """Prevent two assistants from editing the same task at once."""

    def __init__(self, path: Path):
        self.path = path
        self.owned = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, json.dumps({"pid": os.getpid(), "created_at": int(time.time())}).encode("utf-8"))
            os.close(descriptor)
            self.owned = True
            return True
        except FileExistsError:
            try:
                pid = int(json.loads(self.path.read_text(encoding="utf-8"))["pid"])
                if sys.platform == "win32":
                    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                    if process:
                        ctypes.windll.kernel32.CloseHandle(process)
                        return False
                else:
                    os.kill(pid, 0)
                    return False
            except Exception:
                pass
            try:
                self.path.unlink()
            except OSError:
                return False
            return self.acquire()

    def release(self) -> None:
        if self.owned:
            try:
                self.path.unlink()
            except OSError:
                pass


class GlobalHotkey:
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_NOREPEAT = 0x4000
    VK_Z = 0x5A

    def __init__(self, events: queue.Queue):
        self.events = events
        self.thread = None
        self.thread_id = 0

    def start(self) -> None:
        if sys.platform != "win32":
            self.events.put(("hotkey-status", "unsupported"))
            return
        self.thread = threading.Thread(target=self.run, name="mdoc-global-hotkey", daemon=True)
        self.thread.start()

    def run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self.thread_id = kernel32.GetCurrentThreadId()
        registered = bool(user32.RegisterHotKey(None, 1, self.MOD_CONTROL | self.MOD_SHIFT | self.MOD_NOREPEAT, self.VK_Z))
        self.events.put(("hotkey-status", "registered" if registered else "failed"))
        if not registered:
            return

        class Point(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class Message(ctypes.Structure):
            _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_ulong), ("pt", Point), ("lPrivate", ctypes.c_ulong)]

        message = Message()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == self.WM_HOTKEY:
                    point = Point()
                    user32.GetCursorPos(ctypes.byref(point))
                    self.events.put(("capture", {"foreground": int(user32.GetForegroundWindow() or 0), "cursor": (point.x, point.y)}))
        finally:
            user32.UnregisterHotKey(None, 1)
            self.events.put(("hotkey-status", "stopped"))

    def stop(self) -> None:
        if self.thread_id and self.thread and self.thread.is_alive():
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)
            self.thread.join(timeout=1)


class CaptureOverlay:
    """Frozen-image selector with window snap, handles, and precision magnifier."""

    HANDLE_RADIUS = 4
    HIT_MARGIN = 8
    BLUE = "#168cff"
    DIM_AMOUNT = 0.45
    CURSORS = {"n": "sb_v_double_arrow", "s": "sb_v_double_arrow", "e": "sb_h_double_arrow", "w": "sb_h_double_arrow", "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw", "move": "fleur", "new": "crosshair"}

    def __init__(self, parent: tk.Tk, image: Image.Image, bbox: tuple[int, int, int, int], done, windows: list[dict] | None = None):
        self.parent, self.image, self.bbox, self.done = parent, image, bbox, done
        self.start = self.rect = self.mode = self.drag_rect = self.resize_axes = None
        self.windows = windows or []
        self.hover_candidate = self.preview_window = None
        self.hover_job = None
        self.finished = False
        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        left, top, right, bottom = bbox
        self.win.geometry(f"{right - left}x{bottom - top}{left:+d}{top:+d}")
        self.canvas = tk.Canvas(self.win, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.dimmed_photo = ImageTk.PhotoImage(self.dim_image(image))
        self.canvas.create_image(0, 0, anchor="nw", image=self.dimmed_photo)
        self.selection_photo = self.preview_photo = self.lens_photo = None
        self.selection_image = self.canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.preview_image = self.canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.preview_rect = self.canvas.create_rectangle(0, 0, 0, 0, outline=self.BLUE, width=2, state="hidden")
        self.preview_bg = self.canvas.create_rectangle(0, 0, 0, 0, fill="white", outline="#b5b5b5", state="hidden")
        self.preview_text = self.canvas.create_text(0, 0, anchor="nw", fill="#222222", font=("Microsoft YaHei UI", 9), state="hidden")
        self.info = self.canvas.create_text(12, 12, anchor="nw", fill="white", text="悬停选择窗口，或拖动自由框选；Esc/右键取消", font=("Microsoft YaHei UI", 11, "bold"))
        self.handles = [self.canvas.create_oval(0, 0, 0, 0, fill=self.BLUE, outline="white", width=1, state="hidden") for _ in range(8)]
        self.size_bg = self.canvas.create_rectangle(0, 0, 0, 0, fill="white", outline="#b5b5b5", state="hidden")
        self.size_text = self.canvas.create_text(0, 0, anchor="nw", fill="#222222", font=("Microsoft YaHei UI", 9), state="hidden")
        self.lens_bg = self.canvas.create_rectangle(0, 0, 0, 0, fill="#202020", outline=self.BLUE, width=2, state="hidden")
        self.lens_item = self.canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.lens_h = self.canvas.create_line(0, 0, 0, 0, fill=self.BLUE, width=1, state="hidden")
        self.lens_v = self.canvas.create_line(0, 0, 0, 0, fill=self.BLUE, width=1, state="hidden")
        self.lens_text = self.canvas.create_text(0, 0, anchor="nw", fill="white", font=("Consolas", 9), state="hidden")
        self.buttons = ttk.Frame(self.win)
        ttk.Button(self.buttons, text="重新选择", command=self.reset).pack(side="left")
        ttk.Button(self.buttons, text="取消", command=self.cancel).pack(side="left", padx=4)
        ttk.Button(self.buttons, text="完成", command=self.finish).pack(side="left")
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.canvas.bind("<Double-Button-1>", self.double_click)
        self.canvas.bind("<Motion>", self.hover)
        self.canvas.bind("<Button-3>", lambda _event: self.cancel())
        self.win.bind("<Escape>", lambda _event: self.cancel())
        self.win.bind("<Return>", lambda _event: self.finish())
        self.win.bind("<Key>", self.key)
        self.win.focus_force()

    @staticmethod
    def normalize(raw):
        left, top, right, bottom = raw
        return (int(min(left, right)), int(min(top, bottom)), int(max(left, right)), int(max(top, bottom)))

    @classmethod
    def dim_image(cls, image: Image.Image) -> Image.Image:
        source = image.convert("RGB")
        return Image.blend(source, Image.new("RGB", source.size, "black"), cls.DIM_AMOUNT)

    @staticmethod
    def resize_result(rect, axes, x, y):
        left, top, right, bottom = rect
        raw = [left, top, right, bottom]
        if "w" in axes: raw[0] = x
        if "e" in axes: raw[2] = x
        if "n" in axes: raw[1] = y
        if "s" in axes: raw[3] = y
        horizontal = "w" if (("w" in axes and x < right) or ("e" in axes and x < left)) else "e"
        vertical = "n" if (("n" in axes and y < bottom) or ("s" in axes and y < top)) else "s"
        mode = horizontal if axes in {"w", "e"} else (vertical if axes in {"n", "s"} else vertical + horizontal)
        return CaptureOverlay.normalize(raw), mode

    def points(self):
        left, top, right, bottom = self.rect
        middle_x, middle_y = (left + right) // 2, (top + bottom) // 2
        return [(left, top), (middle_x, top), (right, top), (right, middle_y), (right, bottom), (middle_x, bottom), (left, bottom), (left, middle_y)]

    def hit_test(self, x, y):
        if not self.rect:
            return "new"
        left, top, right, bottom = self.rect
        margin = self.HIT_MARGIN
        near_left, near_right, near_top, near_bottom = abs(x - left) <= margin, abs(x - right) <= margin, abs(y - top) <= margin, abs(y - bottom) <= margin
        within_x, within_y = left - margin <= x <= right + margin, top - margin <= y <= bottom + margin
        if near_left and near_top: return "nw"
        if near_right and near_top: return "ne"
        if near_right and near_bottom: return "se"
        if near_left and near_bottom: return "sw"
        if near_top and within_x: return "n"
        if near_bottom and within_x: return "s"
        if near_left and within_y: return "w"
        if near_right and within_y: return "e"
        if left < x < right and top < y < bottom: return "move"
        return "new"

    def window_at(self, x, y):
        return next((item for item in self.windows if item["rect"][0] <= x < item["rect"][2] and item["rect"][1] <= y < item["rect"][3]), None)

    def set_cursor(self, mode):
        try:
            self.canvas.configure(cursor=self.CURSORS.get(mode, "crosshair"))
        except tk.TclError:
            self.canvas.configure(cursor="crosshair")

    def hover(self, event):
        if self.start:
            return
        if self.rect:
            mode = self.hit_test(event.x, event.y)
            self.set_cursor(mode)
            self.highlight(mode)
            return
        candidate = self.window_at(event.x, event.y)
        if candidate is self.hover_candidate:
            return
        self.hover_candidate = candidate
        if self.hover_job:
            self.win.after_cancel(self.hover_job)
            self.hover_job = None
        if candidate:
            self.hover_job = self.win.after(80, lambda hwnd=candidate["hwnd"]: self.stabilize_window(hwnd))
        else:
            self.hide_preview()

    def stabilize_window(self, hwnd):
        self.hover_job = None
        if self.hover_candidate and self.hover_candidate["hwnd"] == hwnd:
            self.show_preview(self.hover_candidate)

    def show_preview(self, item):
        self.preview_window = item
        left, top, right, bottom = item["rect"]
        self.preview_photo = ImageTk.PhotoImage(self.image.crop(item["rect"]))
        self.canvas.coords(self.preview_image, left, top)
        self.canvas.itemconfigure(self.preview_image, image=self.preview_photo, state="normal")
        self.canvas.coords(self.preview_rect, left, top, right, bottom)
        self.canvas.itemconfigure(self.preview_rect, state="normal")
        title = item["title"] if len(item["title"]) <= 40 else item["title"][:39] + "..."
        label = f"{title}\n{right - left} x {bottom - top}" + (" · 已裁剪" if item["clipped"] else "")
        label_x = max(2, min(left, self.image.width - 300))
        label_y = top - 44 if top >= 46 else min(self.image.height - 42, top + 4)
        self.canvas.coords(self.preview_text, label_x + 7, label_y + 3)
        self.canvas.itemconfigure(self.preview_text, text=label, state="normal")
        self.canvas.update_idletasks()
        bounds = self.canvas.bbox(self.preview_text)
        self.canvas.coords(self.preview_bg, bounds[0] - 5, bounds[1] - 3, bounds[2] + 5, bounds[3] + 3)
        self.canvas.itemconfigure(self.preview_bg, state="normal")
        for element in (self.preview_image, self.preview_rect, self.preview_bg, self.preview_text):
            self.canvas.tag_raise(element)
        self.canvas.itemconfigure(self.info, text="单击选择窗口；双击完成；拖动可自由框选")
        self.set_cursor("new")

    def hide_preview(self):
        self.canvas.itemconfigure(self.preview_image, image="", state="hidden")
        for item in (self.preview_rect, self.preview_bg, self.preview_text):
            self.canvas.itemconfigure(item, state="hidden")
        self.preview_window = self.preview_photo = None

    def highlight(self, mode):
        active = {"nw": {0}, "n": {1}, "ne": {2}, "e": {3}, "se": {4}, "s": {5}, "sw": {6}, "w": {7}}.get(mode, set())
        if not self.rect:
            return
        for index, (item, (x, y)) in enumerate(zip(self.handles, self.points())):
            radius = self.HANDLE_RADIUS + (2 if index in active else 0)
            self.canvas.coords(item, x - radius, y - radius, x + radius, y + radius)

    def press(self, event):
        if not self.rect and self.preview_window and self.window_at(event.x, event.y) is self.preview_window:
            self.mode, self.start, self.drag_rect, self.resize_axes = "window", (event.x, event.y), self.preview_window["rect"], None
            return
        self.mode = self.hit_test(event.x, event.y)
        self.start, self.drag_rect, self.resize_axes = (event.x, event.y), self.rect, self.mode
        if self.mode == "new":
            self.rect = None
            self.drag_rect = (event.x, event.y, event.x, event.y)
        self.set_cursor(self.mode)

    def drag(self, event):
        if not self.start:
            return
        left, top, right, bottom = self.drag_rect
        if self.mode == "window":
            if max(abs(event.x - self.start[0]), abs(event.y - self.start[1])) <= 4:
                return
            self.hide_preview()
            self.mode, self.drag_rect = "new", (self.start[0], self.start[1], self.start[0], self.start[1])
            raw = (self.start[0], self.start[1], event.x, event.y)
        elif self.mode == "new":
            raw = (self.start[0], self.start[1], event.x, event.y)
        elif self.mode == "move":
            width, height = right - left, bottom - top
            new_left = max(0, min(self.image.width - width, left + event.x - self.start[0]))
            new_top = max(0, min(self.image.height - height, top + event.y - self.start[1]))
            raw = (new_left, new_top, new_left + width, new_top + height)
        else:
            raw, self.mode = self.resize_result(self.drag_rect, self.resize_axes, event.x, event.y)
        self.draw_box(raw)
        if self.mode != "move":
            self.show_lens(event.x, event.y, self.mode)

    def release(self, event):
        if self.mode == "window" and self.preview_window:
            selected = self.preview_window["rect"]
            self.hide_preview()
            self.draw_box(selected)
        self.hide_lens()
        self.start = self.drag_rect = self.resize_axes = None
        if self.rect:
            self.set_cursor(self.hit_test(event.x, event.y))

    def draw_box(self, raw):
        left, top, right, bottom = self.normalize(raw)
        self.rect = (max(0, left), max(0, top), min(self.image.width, right), min(self.image.height, bottom))
        left, top, right, bottom = self.rect
        if hasattr(self, "selection"):
            self.canvas.coords(self.selection, *self.rect)
        else:
            self.selection = self.canvas.create_rectangle(*self.rect, outline=self.BLUE, width=2)
        width, height = right - left, bottom - top
        if width > 0 and height > 0:
            self.selection_photo = ImageTk.PhotoImage(self.image.crop(self.rect))
            self.canvas.coords(self.selection_image, left, top)
            self.canvas.itemconfigure(self.selection_image, image=self.selection_photo, state="normal")
        else:
            self.canvas.itemconfigure(self.selection_image, state="hidden")
        for item, (x, y) in zip(self.handles, self.points()):
            radius = self.HANDLE_RADIUS
            self.canvas.coords(item, x - radius, y - radius, x + radius, y + radius)
            self.canvas.itemconfigure(item, state="normal")
            self.canvas.tag_raise(item)
        label_x, label_y = max(2, min(left, self.image.width - 80)), top - 26 if top >= 28 else min(self.image.height - 24, top + 8)
        self.canvas.coords(self.size_bg, label_x, label_y, label_x + 76, label_y + 22)
        self.canvas.coords(self.size_text, label_x + 7, label_y + 3)
        self.canvas.itemconfigure(self.size_text, text=f"{width} x {height}", state="normal")
        self.canvas.itemconfigure(self.size_bg, state="normal")
        self.canvas.tag_raise(self.selection)
        for item in self.handles:
            self.canvas.tag_raise(item)
        self.canvas.tag_raise(self.size_bg)
        self.canvas.tag_raise(self.size_text)
        self.canvas.itemconfigure(self.info, text="拖动边框或控制点调整；拖动内部平移；Enter/双击完成")
        button_y = top - 42 if top >= 46 else min(self.image.height - 34, bottom + 8)
        self.buttons.place(x=max(0, min(left, self.image.width - 230)), y=button_y)

    def show_lens(self, x, y, mode):
        sample, half = 31, 15
        left, top = x - half, y - half
        patch = Image.new("RGB", (sample, sample), "black")
        crop = (max(0, left), max(0, top), min(self.image.width, left + sample), min(self.image.height, top + sample))
        if crop[2] > crop[0] and crop[3] > crop[1]:
            patch.paste(self.image.crop(crop), (crop[0] - left, crop[1] - top))
        self.lens_photo = ImageTk.PhotoImage(patch.resize((186, 186), Image.Resampling.NEAREST))
        width, height, gap = 196, 222, 24
        candidates = [(x + gap, y + gap), (x - width - gap, y + gap), (x + gap, y - height - gap), (x - width - gap, y - height - gap)]
        fitting = [(left, top) for left, top in candidates if 0 <= left <= self.image.width - width and 0 <= top <= self.image.height - height]
        lens_x, lens_y = fitting[0] if fitting else (max(0, min(self.image.width - width, x + gap)), max(0, min(self.image.height - height, y + gap)))
        self.canvas.coords(self.lens_bg, lens_x, lens_y, lens_x + width, lens_y + height)
        self.canvas.coords(self.lens_item, lens_x + 5, lens_y + 5)
        self.canvas.itemconfigure(self.lens_item, image=self.lens_photo, state="normal")
        center, middle = lens_x + 98, lens_y + 98
        self.canvas.coords(self.lens_h, lens_x + 5, middle, lens_x + 191, middle)
        self.canvas.coords(self.lens_v, center, lens_y + 5, center, lens_y + 191)
        self.canvas.itemconfigure(self.lens_h, state="normal" if any(axis in mode for axis in "ns") or mode == "new" else "hidden")
        self.canvas.itemconfigure(self.lens_v, state="normal" if any(axis in mode for axis in "ew") or mode == "new" else "hidden")
        selection_width = self.rect[2] - self.rect[0] if self.rect else 0
        selection_height = self.rect[3] - self.rect[1] if self.rect else 0
        self.canvas.coords(self.lens_text, lens_x + 7, lens_y + 194)
        self.canvas.itemconfigure(self.lens_text, text=f"坐标: {x}, {y}   选区: {selection_width} x {selection_height}", state="normal")
        self.canvas.itemconfigure(self.lens_bg, state="normal")
        for item in (self.lens_bg, self.lens_item, self.lens_h, self.lens_v, self.lens_text):
            self.canvas.tag_raise(item)

    def hide_lens(self):
        for item in (self.lens_bg, self.lens_item, self.lens_h, self.lens_v, self.lens_text):
            self.canvas.itemconfigure(item, state="hidden")

    def key(self, event):
        if not self.rect or event.keysym not in {"Left", "Right", "Up", "Down"}:
            return
        step = 10 if event.state & 1 else 1
        delta_x = -step if event.keysym == "Left" else step if event.keysym == "Right" else 0
        delta_y = -step if event.keysym == "Up" else step if event.keysym == "Down" else 0
        left, top, right, bottom = self.rect
        width, height = right - left, bottom - top
        new_left = max(0, min(self.image.width - width, left + delta_x))
        new_top = max(0, min(self.image.height - height, top + delta_y))
        self.draw_box((new_left, new_top, new_left + width, new_top + height))

    def reset(self):
        self.rect = self.start = self.mode = self.drag_rect = self.resize_axes = None
        self.hide_lens()
        if self.hover_job:
            self.win.after_cancel(self.hover_job)
            self.hover_job = None
        if hasattr(self, "selection"):
            self.canvas.delete(self.selection)
            del self.selection
        for item in self.handles + [self.size_bg, self.size_text]:
            self.canvas.itemconfigure(item, state="hidden")
        self.canvas.itemconfigure(self.selection_image, state="hidden")
        self.selection_photo = None
        self.hover_candidate = None
        self.hide_preview()
        self.buttons.place_forget()
        self.canvas.itemconfigure(self.info, text="悬停选择窗口，或拖动自由框选；Esc/右键取消")
        self.set_cursor("new")

    def double_click(self, event):
        if self.rect and self.rect[0] < event.x < self.rect[2] and self.rect[1] < event.y < self.rect[3]:
            self.finish()

    def finish(self):
        if not self.rect or self.rect[2] - self.rect[0] < 2 or self.rect[3] - self.rect[1] < 2:
            return
        self.complete(self.image.crop(self.rect))

    def cancel(self):
        self.complete(None)

    def complete(self, result):
        if self.finished:
            return
        self.finished = True
        self.win.destroy()
        self.done(result)
