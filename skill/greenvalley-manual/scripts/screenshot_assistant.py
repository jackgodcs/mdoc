#!/usr/bin/env python3
"""Human screenshot assistant for GreenValley manual tasks."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from PIL import Image, ImageGrab, ImageTk

import screenshot_state as state

STATUS_LABELS = {
    "pending": "待截图", "captured": "已截图", "needs-retake": "需重拍",
    "blocked": "受阻", "not-applicable": "不适用", "waived": "豁免",
}
def dpi_aware() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass


def monitor_for_window(root: tk.Tk) -> tuple[int, int, int, int]:
    if sys.platform != "win32":
        return (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    class INFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]
    user32 = ctypes.windll.user32
    monitor = user32.MonitorFromWindow(root.winfo_id(), 2)
    info = INFO(ctypes.sizeof(INFO))
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    r = info.rcMonitor
    return (r.left, r.top, r.right, r.bottom)


def virtual_screen() -> tuple[int, int, int, int]:
    if sys.platform != "win32":
        return (0, 0, ImageGrab.grab().width, ImageGrab.grab().height)
    u = ctypes.windll.user32
    x, y = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
    return (x, y, x + u.GetSystemMetrics(78), y + u.GetSystemMetrics(79))


def center_on_parent(dialog: tk.Toplevel, parent: tk.Misc) -> None:
    """Center a laid-out dialog over its parent using screen coordinates."""
    parent.update_idletasks(); dialog.update_idletasks()
    width, height = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
    # geometry() and winfo_x/y use top-level window coordinates.  Mixing them
    # with winfo_rootx/y introduces the Windows non-client border offset.
    x = parent.winfo_x() + (parent.winfo_width() - width) // 2
    y = parent.winfo_y() + (parent.winfo_height() - height) // 2
    dialog.geometry(f"{width}x{height}{x:+d}{y:+d}")


class InstanceLock:
    def __init__(self, path: Path): self.path, self.owned = path, False
    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(), "created_at": state.now()}).encode())
            os.close(fd); self.owned = True; return True
        except FileExistsError:
            try:
                pid = int(json.loads(self.path.read_text(encoding="utf-8"))["pid"])
                if sys.platform == "win32":
                    alive = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                    if alive: ctypes.windll.kernel32.CloseHandle(alive); return False
                else:
                    os.kill(pid, 0); return False
            except Exception: pass
            try: self.path.unlink()
            except OSError: return False
            return self.acquire()
    def release(self):
        if self.owned:
            try: self.path.unlink()
            except OSError: pass


class CaptureOverlay:
    HANDLE_RADIUS = 4
    HIT_MARGIN = 8
    BLUE = "#168cff"
    DIM_AMOUNT = 0.45
    CURSORS = {
        "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
        "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
        "nw": "size_nw_se", "se": "size_nw_se",
        "ne": "size_ne_sw", "sw": "size_ne_sw",
        "move": "fleur", "new": "crosshair",
    }

    def __init__(self, parent: tk.Tk, image: Image.Image, bbox: tuple[int, int, int, int], done):
        self.parent, self.image, self.bbox, self.done = parent, image, bbox, done
        self.start = self.rect = self.mode = self.drag_rect = self.resize_axes = None
        self.win = tk.Toplevel(parent); self.win.overrideredirect(True); self.win.attributes("-topmost", True)
        x1, y1, x2, y2 = bbox; self.win.geometry(f"{x2-x1}x{y2-y1}{x1:+d}{y1:+d}")
        self.canvas = tk.Canvas(self.win, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.dimmed = self.dim_image(image)
        self.dimmed_photo = ImageTk.PhotoImage(self.dimmed); self.canvas.create_image(0, 0, anchor="nw", image=self.dimmed_photo)
        self.selection_photo = None
        self.selection_image = self.canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.info = self.canvas.create_text(12, 12, anchor="nw", fill="white", text="拖动选择区域；Enter/双击完成，Esc/右键取消", font=("Microsoft YaHei UI", 11, "bold"))
        self.canvas.bind("<ButtonPress-1>", self.press); self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release); self.canvas.bind("<Double-Button-1>", self.double_click); self.canvas.bind("<Motion>", self.hover)
        self.canvas.bind("<Button-3>", lambda e: self.cancel()); self.win.bind("<Escape>", lambda e: self.cancel())
        self.win.bind("<Return>", lambda e: self.finish()); self.win.bind("<Key>", self.key); self.win.focus_force()
        self.buttons = ttk.Frame(self.win); ttk.Button(self.buttons,text="重新选择",command=self.reset).pack(side="left"); ttk.Button(self.buttons,text="取消",command=self.cancel).pack(side="left",padx=4); ttk.Button(self.buttons,text="完成",command=self.finish).pack(side="left")
        self.handles = [self.canvas.create_oval(0, 0, 0, 0, fill=self.BLUE, outline="white", width=1, state="hidden") for _ in range(8)]
        self.size_bg = self.canvas.create_rectangle(0, 0, 0, 0, fill="white", outline="#b5b5b5", state="hidden")
        self.size_text = self.canvas.create_text(0, 0, anchor="nw", fill="#222222", font=("Microsoft YaHei UI", 9), state="hidden")
        self.lens_bg = self.canvas.create_rectangle(0, 0, 0, 0, fill="#202020", outline=self.BLUE, width=2, state="hidden")
        self.lens_item = self.canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.lens_h = self.canvas.create_line(0, 0, 0, 0, fill=self.BLUE, width=1, state="hidden")
        self.lens_v = self.canvas.create_line(0, 0, 0, 0, fill=self.BLUE, width=1, state="hidden")
        self.lens_text = self.canvas.create_text(0, 0, anchor="nw", fill="white", font=("Consolas", 9), state="hidden")
        self.lens_photo = None

    @staticmethod
    def normalize(raw):
        x1, y1, x2, y2 = raw
        return (int(min(x1, x2)), int(min(y1, y2)), int(max(x1, x2)), int(max(y1, y2)))

    @classmethod
    def dim_image(cls, image):
        source=image.convert("RGB")
        return Image.blend(source,Image.new("RGB",source.size,"black"),cls.DIM_AMOUNT)

    @staticmethod
    def resize_result(rect, axes, x, y):
        x1,y1,x2,y2=rect; raw=[x1,y1,x2,y2]
        if "w" in axes:raw[0]=x
        if "e" in axes:raw[2]=x
        if "n" in axes:raw[1]=y
        if "s" in axes:raw[3]=y
        horizontal = "w" if (("w" in axes and x < x2) or ("e" in axes and x < x1)) else "e"
        vertical = "n" if (("n" in axes and y < y2) or ("s" in axes and y < y1)) else "s"
        mode = horizontal if axes in {"w","e"} else (vertical if axes in {"n","s"} else vertical+horizontal)
        return CaptureOverlay.normalize(raw), mode

    def points(self):
        x1, y1, x2, y2 = self.rect; mx, my = (x1+x2)//2, (y1+y2)//2
        return [(x1,y1),(mx,y1),(x2,y1),(x2,my),(x2,y2),(mx,y2),(x1,y2),(x1,my)]

    def hit_test(self, x, y):
        if not self.rect: return "new"
        x1,y1,x2,y2=self.rect; m=self.HIT_MARGIN
        near_l,near_r,near_t,near_b=abs(x-x1)<=m,abs(x-x2)<=m,abs(y-y1)<=m,abs(y-y2)<=m
        within_x=x1-m<=x<=x2+m; within_y=y1-m<=y<=y2+m
        if near_l and near_t:return "nw"
        if near_r and near_t:return "ne"
        if near_r and near_b:return "se"
        if near_l and near_b:return "sw"
        if near_t and within_x:return "n"
        if near_b and within_x:return "s"
        if near_l and within_y:return "w"
        if near_r and within_y:return "e"
        if x1<x<x2 and y1<y<y2:return "move"
        return "new"

    def set_cursor(self, mode):
        try:self.canvas.config(cursor=self.CURSORS.get(mode,"crosshair"))
        except tk.TclError:self.canvas.config(cursor="crosshair")

    def hover(self,e):
        if self.start:return
        mode=self.hit_test(e.x,e.y); self.set_cursor(mode); self.highlight(mode)

    def highlight(self,mode):
        active={"nw":{0},"n":{1},"ne":{2},"e":{3},"se":{4},"s":{5},"sw":{6},"w":{7}}.get(mode,set())
        if not self.rect:return
        for index,(item,(x,y)) in enumerate(zip(self.handles,self.points())):
            r=self.HANDLE_RADIUS+(2 if index in active else 0); self.canvas.coords(item,x-r,y-r,x+r,y+r)

    def press(self, e):
        self.mode=self.hit_test(e.x,e.y); self.start=(e.x,e.y); self.drag_rect=self.rect; self.resize_axes=self.mode
        if self.mode=="new":self.rect=None; self.drag_rect=(e.x,e.y,e.x,e.y)
        self.set_cursor(self.mode)
    def drag(self, e):
        if not self.start:return
        x1,y1,x2,y2=self.drag_rect; mx,my=e.x,e.y
        if self.mode=="new":raw=(self.start[0],self.start[1],mx,my)
        elif self.mode=="move":
            dx,dy=mx-self.start[0],my-self.start[1]; w,h=x2-x1,y2-y1
            nx=max(0,min(self.image.width-w,x1+dx)); ny=max(0,min(self.image.height-h,y1+dy)); raw=(nx,ny,nx+w,ny+h)
        else:
            raw,self.mode=self.resize_result(self.drag_rect,self.resize_axes,mx,my)
        self.draw_box(raw)
        if self.mode!="move":self.show_lens(mx,my,self.mode)
    def release(self, e):
        self.hide_lens(); self.start=self.drag_rect=self.resize_axes=None
        if self.rect:self.set_cursor(self.hit_test(e.x,e.y))
    def draw_box(self, raw):
        box=self.normalize(raw); box=(max(0,box[0]),max(0,box[1]),min(self.image.width,box[2]),min(self.image.height,box[3])); self.rect=box
        if hasattr(self, "selection"): self.canvas.coords(self.selection, *box)
        else:self.selection=self.canvas.create_rectangle(*box,outline=self.BLUE,width=2)
        x1,y1,x2,y2=box; w,h=x2-x1,y2-y1
        if w>0 and h>0:
            self.selection_photo=ImageTk.PhotoImage(self.image.crop(box)); self.canvas.coords(self.selection_image,x1,y1); self.canvas.itemconfigure(self.selection_image,image=self.selection_photo,state="normal")
        else:self.canvas.itemconfigure(self.selection_image,state="hidden")
        for item,(x,y) in zip(self.handles,self.points()):
            r=self.HANDLE_RADIUS; self.canvas.coords(item,x-r,y-r,x+r,y+r); self.canvas.itemconfigure(item,state="normal"); self.canvas.tag_raise(item)
        label=f"{w} × {h}"; lx=max(2,min(x1,self.image.width-80)); ly=y1-26 if y1>=28 else min(self.image.height-24,y1+8)
        self.canvas.coords(self.size_bg,lx,ly,lx+76,ly+22); self.canvas.coords(self.size_text,lx+7,ly+3); self.canvas.itemconfigure(self.size_text,text=label,state="normal"); self.canvas.itemconfigure(self.size_bg,state="normal")
        self.canvas.tag_raise(self.selection)
        for item in self.handles:self.canvas.tag_raise(item)
        self.canvas.tag_raise(self.size_bg); self.canvas.tag_raise(self.size_text)
        self.canvas.itemconfigure(self.info,text="拖动边框或控制点调整；拖动内部平移；Enter/双击完成")
        by=y1-42 if y1>=46 else min(self.image.height-34,y2+8); self.buttons.place(x=max(0,min(x1,self.image.width-230)),y=by)

    def show_lens(self,x,y,mode):
        sample=31; half=sample//2; left,top=x-half,y-half
        patch=Image.new("RGB",(sample,sample),(0,0,0)); crop_box=(max(0,left),max(0,top),min(self.image.width,left+sample),min(self.image.height,top+sample))
        if crop_box[2]>crop_box[0] and crop_box[3]>crop_box[1]:patch.paste(self.image.crop(crop_box),(crop_box[0]-left,crop_box[1]-top))
        zoom=patch.resize((186,186),Image.Resampling.NEAREST); self.lens_photo=ImageTk.PhotoImage(zoom)
        width,height=196,222; gap=24; candidates=[(x+gap,y+gap),(x-width-gap,y+gap),(x+gap,y-height-gap),(x-width-gap,y-height-gap)]
        fitting=[(cx,cy) for cx,cy in candidates if 0<=cx<=self.image.width-width and 0<=cy<=self.image.height-height]
        def overlap(candidate):
            if not self.rect:return 0
            cx,cy=candidate; x1,y1,x2,y2=self.rect
            return max(0,min(cx+width,x2)-max(cx,x1))*max(0,min(cy+height,y2)-max(cy,y1))
        lx,ly=min(fitting,key=overlap) if fitting else (max(0,min(self.image.width-width,x+gap)),max(0,min(self.image.height-height,y+gap)))
        self.canvas.coords(self.lens_bg,lx,ly,lx+width,ly+height); self.canvas.coords(self.lens_item,lx+5,ly+5); self.canvas.itemconfigure(self.lens_item,image=self.lens_photo,state="normal")
        center=lx+5+93; middle=ly+5+93
        self.canvas.coords(self.lens_h,lx+5,middle,lx+191,middle); self.canvas.coords(self.lens_v,center,ly+5,center,ly+191)
        self.canvas.itemconfigure(self.lens_h,state="normal" if any(c in mode for c in "ns") or mode=="new" else "hidden")
        self.canvas.itemconfigure(self.lens_v,state="normal" if any(c in mode for c in "ew") or mode=="new" else "hidden")
        w,h=(self.rect[2]-self.rect[0],self.rect[3]-self.rect[1]) if self.rect else (0,0); self.canvas.coords(self.lens_text,lx+7,ly+194); self.canvas.itemconfigure(self.lens_text,text=f"坐标: {x}, {y}   选区: {w} × {h}",state="normal")
        self.canvas.itemconfigure(self.lens_bg,state="normal")
        for item in (self.lens_bg,self.lens_item,self.lens_h,self.lens_v,self.lens_text):self.canvas.tag_raise(item)

    def hide_lens(self):
        for item in (self.lens_bg,self.lens_item,self.lens_h,self.lens_v,self.lens_text):self.canvas.itemconfigure(item,state="hidden")
    def key(self,e):
        if not self.rect or e.keysym not in {"Left","Right","Up","Down"}:return
        step=10 if e.state & 1 else 1; dx=(-step if e.keysym=="Left" else step if e.keysym=="Right" else 0); dy=(-step if e.keysym=="Up" else step if e.keysym=="Down" else 0); x1,y1,x2,y2=self.rect; w,h=x2-x1,y2-y1; nx=max(0,min(self.image.width-w,x1+dx)); ny=max(0,min(self.image.height-h,y1+dy)); self.draw_box((nx,ny,nx+w,ny+h))
    def reset(self):
        self.rect=self.start=self.mode=self.drag_rect=self.resize_axes=None; self.hide_lens()
        if hasattr(self,"selection"): self.canvas.delete(self.selection); del self.selection
        for item in self.handles:self.canvas.itemconfigure(item,state="hidden")
        for item in (self.size_bg,self.size_text):self.canvas.itemconfigure(item,state="hidden")
        self.canvas.itemconfigure(self.selection_image,state="hidden"); self.selection_photo=None
        self.buttons.place_forget(); self.canvas.itemconfigure(self.info,text="拖动选择区域；Enter/双击完成，Esc/右键取消"); self.set_cursor("new")
    def double_click(self,e):
        if self.rect and self.rect[0]<e.x<self.rect[2] and self.rect[1]<e.y<self.rect[3]:self.finish()
    def finish(self):
        if not self.rect or self.rect[2]-self.rect[0] < 2 or self.rect[3]-self.rect[1] < 2: return
        cropped = self.image.crop(self.rect); self.win.destroy(); self.done(cropped)
    def cancel(self): self.win.destroy(); self.done(None)


class Assistant:
    def __init__(self, root: tk.Tk, workspace: Path, task_id: str, check=False):
        self.root, self.workspace, self.task_id = root, workspace, task_id
        self.paths = state.task_paths(workspace, task_id); self.lock = InstanceLock(self.paths["lock"])
        if not self.lock.acquire(): raise RuntimeError("此任务的截图助手已经在运行。")
        self.root.protocol("WM_DELETE_WINDOW", self.close); self.root.title(f"GreenValley 截图助手 — {task_id}")
        self.root.geometry("1180x760"); self.root.minsize(900, 600)
        self.preview_photo = None; self.visible = []
        self.load_local(); self.build(); self.refresh()
        if check:
            original = self.locale_var.get()
            for locale in self.manifest["locales"]:
                self.locale_var.set(locale["id"]); self.populate_list()
            self.locale_var.set(original); self.populate_list()
            self.root.after(300, self.close)
    def load_local(self):
        try: self.local = json.loads(self.paths["local"].read_text(encoding="utf-8"))
        except Exception: self.local = {"schema_version":1, "preferences":{}, "notes":{}}
        p = self.local.setdefault("preferences", {})
        self.locale_var = tk.StringVar(value=p.get("selected_locale", "zh"))
        self.scope_var = tk.StringVar(value=p.get("capture_scope", "current_monitor")); self.auto_var = tk.BooleanVar(value=p.get("auto_advance", True))
    def save_local(self):
        self.local["preferences"] = {"selected_locale":self.locale_var.get(), "capture_scope":self.scope_var.get(), "auto_advance":self.auto_var.get()}
        state.atomic_json(self.paths["local"], self.local)
    def build(self):
        top = ttk.Frame(self.root, padding=8); top.pack(fill="x")
        ttk.Label(top, text="语言").pack(side="left"); self.locale_box = ttk.Combobox(top, textvariable=self.locale_var, state="readonly", width=14); self.locale_box.pack(side="left", padx=(5,12))
        ttk.Button(top, text="刷新 F5", command=self.refresh).pack(side="left", padx=5)
        ttk.Button(top, text="接受当前全部截图", command=self.accept_all).pack(side="right")
        pane = ttk.Panedwindow(self.root, orient="horizontal"); pane.pack(fill="both", expand=True, padx=8, pady=(0,8))
        left = ttk.Frame(pane); right = ttk.Panedwindow(pane, orient="vertical"); pane.add(left, weight=1); pane.add(right, weight=3)
        self.tree = ttk.Treeview(left, columns=("status",), show="tree headings", selectmode="browse"); self.tree.heading("#0", text="截图项"); self.tree.heading("status", text="状态"); self.tree.column("status", width=78, anchor="center"); self.tree.pack(fill="both", expand=True)
        req = ttk.LabelFrame(right, text="截图要求", padding=8); prev = ttk.LabelFrame(right, text="图片预览", padding=8); right.add(req, weight=1); right.add(prev, weight=3)
        self.requirements = tk.Text(req, height=10, wrap="word", state="disabled", font=("Microsoft YaHei UI",10)); self.requirements.pack(fill="both", expand=True)
        bar=ttk.Frame(prev); bar.pack(fill="x")
        ttk.Button(bar,text="截图 Ctrl+Shift+Z",command=self.capture).pack(side="left"); ttk.Button(bar,text="打开图片",command=self.open_image).pack(side="left",padx=4); ttk.Button(bar,text="打开目录",command=self.open_folder).pack(side="left")
        ttk.Button(bar,text="异常状态…",command=self.exception).pack(side="left",padx=4); ttk.Checkbutton(bar,text="保存后自动下一项",variable=self.auto_var).pack(side="right")
        ttk.Radiobutton(bar,text="当前屏幕",variable=self.scope_var,value="current_monitor").pack(side="right"); ttk.Radiobutton(bar,text="全部屏幕",variable=self.scope_var,value="all_monitors").pack(side="right")
        self.preview = ttk.Label(prev, text="尚未截图", anchor="center"); self.preview.pack(fill="both", expand=True, pady=(8,0))
        self.status = ttk.Label(self.root, anchor="w", padding=(8,2)); self.status.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self.show_selected); self.locale_box.bind("<<ComboboxSelected>>", self.locale_changed)
        self.root.bind("<F5>",lambda e:self.refresh()); self.root.bind("<Control-Shift-Z>",lambda e:self.capture()); self.root.bind("<Control-o>",lambda e:self.open_image()); self.root.bind("<Control-Shift-O>",lambda e:self.open_folder()); self.root.bind("<Control-Return>",lambda e:self.accept_all())
    def refresh(self):
        self.manifest = state.synchronize(self.workspace, self.task_id, Path(__file__).resolve().parent.parent)
        labels = [f"{x['label']} ({x['id']})" for x in self.manifest["locales"]]; ids=[x["id"] for x in self.manifest["locales"]]
        self.locale_box["values"] = labels
        current=self.locale_var.get(); idx=ids.index(current) if current in ids else 0; self.locale_box.current(idx); self.locale_var.set(ids[idx])
        self.populate_list()
    def locale_changed(self,e=None):
        index=self.locale_box.current()
        if index >= 0: self.locale_var.set(self.manifest["locales"][index]["id"])
        self.populate_list()
    def populate_list(self):
        locale=self.locale_var.get(); selected=self.selected_id(); self.tree.delete(*self.tree.get_children()); self.visible=[]
        for shot in self.manifest["screenshots"]:
            s=shot["locales"][locale]["status"]
            title=f"{shot['id']}  {shot['filename']}"; self.tree.insert("","end",iid=shot["id"],text=title,values=(STATUS_LABELS.get(s,s),)); self.visible.append(shot)
        choose=selected if selected and self.tree.exists(selected) else (self.visible[0]["id"] if self.visible else None)
        if choose: self.tree.selection_set(choose); self.tree.focus(choose); self.show_selected()
        else: self.clear_details()
        complete=sum(x["locales"][locale]["status"] in state.COMPLETE_STATUSES for x in self.manifest["screenshots"] if x["required"]); total=sum(x["required"] for x in self.manifest["screenshots"]); self.status.config(text=f"{locale}: 必需截图 {complete}/{total}；总体验收：{self.manifest['acceptance']['status']}")
        self.save_local()
        if len(self.visible) != len(self.manifest["screenshots"]):
            raise RuntimeError("左侧截图列表不完整")
    def selected_id(self):
        s=self.tree.selection(); return s[0] if s else None
    def selected(self): return next((x for x in self.manifest["screenshots"] if x["id"]==self.selected_id()),None)
    def clear_details(self):
        self.requirements.config(state="normal"); self.requirements.delete("1.0","end"); self.requirements.config(state="disabled"); self.preview.config(image="",text="没有符合筛选条件的截图项"); self.preview_photo=None
    def show_selected(self,e=None):
        shot=self.selected(); locale=self.locale_var.get()
        if not shot:return
        d=shot["locales"][locale]; lines=[f"{shot['id']} · {'必需' if shot['required'] else '可选'} · {STATUS_LABELS.get(d['status'],d['status'])}",f"目标：{d['absolute_target']}"]
        if shot["entry_steps"]: lines += ["","操作："]+[f"  {i+1}. {x}" for i,x in enumerate(shot["entry_steps"])]
        if shot["preconditions"]: lines += ["","准备："]+[f"  • {x}" for x in shot["preconditions"]]
        if shot["expected_state"]: lines += ["","画面必须包含："]+[f"  • {x}" for x in shot["expected_state"]]
        if d.get("reason"): lines += ["",f"原因：{d['reason']}"]
        self.requirements.config(state="normal"); self.requirements.delete("1.0","end"); self.requirements.insert("1.0","\n".join(lines)); self.requirements.config(state="disabled"); self.update_preview(Path(d["absolute_target"]))
    def update_preview(self,path:Path):
        if not path.is_file(): self.preview.config(image="",text="尚未截图"); self.preview_photo=None; return
        try:
            im=Image.open(path); self.root.update_idletasks(); w=max(300,self.preview.winfo_width()-20); h=max(220,self.preview.winfo_height()-20); im.thumbnail((w,h),Image.Resampling.LANCZOS); self.preview_photo=ImageTk.PhotoImage(im.copy()); self.preview.config(image=self.preview_photo,text="")
        except Exception as e: self.preview.config(image="",text=f"无法预览：{e}")
    def capture(self):
        shot=self.selected();
        if not shot:return
        target=Path(shot["locales"][self.locale_var.get()]["absolute_target"]); bbox=virtual_screen() if self.scope_var.get()=="all_monitors" else monitor_for_window(self.root)
        self.root.withdraw(); self.root.after(250,lambda:self.start_capture(bbox,target))
    def start_capture(self,bbox,target):
        try: image=ImageGrab.grab(bbox=bbox,all_screens=True)
        except Exception as e: self.root.deiconify(); messagebox.showerror("截图失败",str(e)); return
        CaptureOverlay(self.root,image,bbox,lambda result:self.finish_capture(result,target))
    def finish_capture(self,image,target):
        self.root.deiconify(); self.root.lift();
        if image is None:return
        if target.exists() and not messagebox.askyesno("覆盖截图",f"目标文件已存在，是否覆盖？\n{target}"): return
        target.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=target.stem+".",suffix=".png",dir=target.parent); os.close(fd)
        try: image.save(tmp,"PNG"); os.replace(tmp,target)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        current=self.selected_id(); self.refresh()
        if self.auto_var.get() and current:
            children=self.tree.get_children();
            if children:
                try:i=children.index(current); nxt=children[min(i+1,len(children)-1)]
                except ValueError:nxt=children[0]
                self.tree.selection_set(nxt); self.tree.focus(nxt); self.tree.see(nxt); self.show_selected()
    def open_image(self):
        shot=self.selected();
        if shot:
            p=Path(shot["locales"][self.locale_var.get()]["absolute_target"]);
            if p.exists(): os.startfile(p)
    def open_folder(self):
        shot=self.selected();
        if shot:
            p=Path(shot["locales"][self.locale_var.get()]["absolute_target"]); p.parent.mkdir(parents=True,exist_ok=True); os.startfile(p.parent)
    def exception(self):
        shot=self.selected();
        if not shot:return
        win=tk.Toplevel(self.root); win.title("设置截图状态"); win.transient(self.root); win.withdraw(); choice=tk.StringVar(value="blocked")
        for label,value in [("受阻","blocked"),("不适用","not-applicable"),("豁免","waived"),("恢复待截图","pending")]: ttk.Radiobutton(win,text=label,variable=choice,value=value).pack(anchor="w",padx=15,pady=3)
        ttk.Label(win,text="原因（异常状态必填）").pack(anchor="w",padx=15,pady=(8,2)); entry=ttk.Entry(win,width=55); entry.pack(padx=15); entry.focus_set()
        def save():
            try: state.set_locale_status(self.workspace,self.task_id,shot["id"],self.locale_var.get(),choice.get(),entry.get()); win.destroy(); self.refresh()
            except Exception as e: messagebox.showerror("无法保存",str(e),parent=win)
        ttk.Button(win,text="保存",command=save).pack(pady=12)
        center_on_parent(win,self.root); win.deiconify(); win.grab_set(); win.lift(); entry.focus_set()
    def accept_all(self):
        if not messagebox.askyesno("接受全部截图","确认已目视核对当前所有必需截图，并接受它们用于发布？"):return
        try: state.accept(self.workspace,self.task_id); self.refresh(); prompt=f"使用 $greenvalley-manual 继续 {self.task_id}，截图已完成并已由用户目视接受，请同步状态并继续后续流程。"; self.root.clipboard_clear(); self.root.clipboard_append(prompt); messagebox.showinfo("已接受","已记录总体验收，并将继续任务提示复制到剪贴板。")
        except Exception as e: messagebox.showerror("无法接受",str(e))
    def close(self):
        try:self.save_local()
        finally:self.lock.release(); self.root.destroy()


def main():
    dpi_aware(); p=argparse.ArgumentParser(); p.add_argument("--workspace",type=Path); p.add_argument("--task"); p.add_argument("--repository",type=Path); p.add_argument("--check",action="store_true"); p.add_argument("--overlay-check",action="store_true"); a=p.parse_args()
    try:
        if a.overlay_check:
            dimmed=CaptureOverlay.dim_image(Image.new("RGB",(2,2),(200,100,50)))
            assert all(dimmed.getpixel((x,y)) == dimmed.getpixel((0,0)) for x in range(2) for y in range(2))
            assert dimmed.getpixel((0,0)) == (110,55,27)
            assert CaptureOverlay.normalize((40,30,10,5)) == (10,5,40,30)
            assert CaptureOverlay.resize_result((10,10,100,100),"e",120,50) == ((10,10,120,100),"e")
            assert CaptureOverlay.resize_result((10,10,100,100),"e",5,50) == ((5,10,10,100),"w")
            assert CaptureOverlay.resize_result((10,10,100,100),"se",5,4) == ((5,4,10,10),"nw")
            print("overlay checks passed"); return 0
        workspace, task_id = a.workspace, a.task
        if not workspace:
            found=state.discover((a.repository or Path.cwd()).resolve()); workspace=Path(found["workspace"])
            if task_id is None and len(found["tasks"]) == 1: task_id=found["tasks"][0]
            elif task_id is None: raise ValueError("请用 --task 指定截图任务；当前可用任务：" + ", ".join(found["tasks"]))
        if not task_id: raise ValueError("缺少 --task")
        workspace=workspace.resolve(); state.synchronize(workspace,task_id,Path(__file__).resolve().parent.parent); root=tk.Tk(); Assistant(root,workspace,task_id,a.check); root.mainloop(); return 0
    except Exception as e:
        try: messagebox.showerror("截图助手",str(e))
        except Exception: print(f"ERROR: {e}",file=sys.stderr)
        return 1
if __name__=="__main__": sys.exit(main())
