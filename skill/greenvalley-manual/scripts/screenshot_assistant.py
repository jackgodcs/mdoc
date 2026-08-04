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
    def __init__(self, parent: tk.Tk, image: Image.Image, bbox: tuple[int, int, int, int], done):
        self.parent, self.image, self.bbox, self.done = parent, image, bbox, done
        self.start = self.rect = self.mode = None
        self.win = tk.Toplevel(parent); self.win.overrideredirect(True); self.win.attributes("-topmost", True)
        x1, y1, x2, y2 = bbox; self.win.geometry(f"{x2-x1}x{y2-y1}{x1:+d}{y1:+d}")
        self.canvas = tk.Canvas(self.win, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.photo = ImageTk.PhotoImage(image); self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.shade = self.canvas.create_rectangle(0, 0, image.width, image.height, fill="black", stipple="gray50", outline="")
        self.info = self.canvas.create_text(12, 12, anchor="nw", fill="white", text="拖动选择区域；Enter/双击完成，Esc/右键取消", font=("Microsoft YaHei UI", 11, "bold"))
        self.canvas.bind("<ButtonPress-1>", self.press); self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release); self.canvas.bind("<Double-Button-1>", lambda e: self.finish())
        self.canvas.bind("<Button-3>", lambda e: self.cancel()); self.win.bind("<Escape>", lambda e: self.cancel())
        self.win.bind("<Return>", lambda e: self.finish()); self.win.bind("<Key>", self.key); self.win.focus_force()
        self.buttons = ttk.Frame(self.win); ttk.Button(self.buttons,text="重新选择",command=self.reset).pack(side="left"); ttk.Button(self.buttons,text="取消",command=self.cancel).pack(side="left",padx=4); ttk.Button(self.buttons,text="完成",command=self.finish).pack(side="left")
    def press(self, e):
        if self.rect and self.rect[0] <= e.x <= self.rect[2] and self.rect[1] <= e.y <= self.rect[3]:
            margin=8; edges=(abs(e.x-self.rect[0])<margin,abs(e.x-self.rect[2])<margin,abs(e.y-self.rect[1])<margin,abs(e.y-self.rect[3])<margin)
            self.mode=("resize",edges) if any(edges) else ("move",self.rect); self.start=(e.x,e.y)
        else: self.mode=("new",None); self.start=(e.x,e.y); self.rect=None
    def drag(self, e):
        if not self.start:return
        if self.mode[0]=="new": self.draw_box((self.start[0],self.start[1],e.x,e.y))
        elif self.mode[0]=="move":
            dx,dy=e.x-self.start[0],e.y-self.start[1]; old=self.mode[1]; self.draw_box((old[0]+dx,old[1]+dy,old[2]+dx,old[3]+dy))
        else:
            l,r,t,b=self.mode[1]; x1,y1,x2,y2=self.rect
            self.draw_box((e.x if l else x1,e.y if t else y1,e.x if r else x2,e.y if b else y2))
    def release(self, e): self.draw(e.x, e.y)
    def draw(self, x, y):
        if self.start and self.mode and self.mode[0]=="new": self.draw_box((self.start[0],self.start[1],x,y))
    def draw_box(self, raw):
        x1,y1,x2,y2=raw; box=(max(0,min(x1,x2)),max(0,min(y1,y2)),min(self.image.width,max(x1,x2)),min(self.image.height,max(y1,y2))); self.rect=box
        if hasattr(self, "selection"): self.canvas.coords(self.selection, *box)
        else: self.selection = self.canvas.create_rectangle(*box, outline="#00b7ff", width=2)
        self.canvas.itemconfigure(self.info, text=f"{box[2]-box[0]} × {box[3]-box[1]} px   Enter/双击完成；Esc取消")
        self.buttons.place(x=max(0,min(box[0],self.image.width-230)),y=max(0,box[1]-38))
    def key(self,e):
        if not self.rect or e.keysym not in {"Left","Right","Up","Down"}:return
        step=10 if e.state & 1 else 1; dx=(-step if e.keysym=="Left" else step if e.keysym=="Right" else 0); dy=(-step if e.keysym=="Up" else step if e.keysym=="Down" else 0); x1,y1,x2,y2=self.rect; self.draw_box((x1+dx,y1+dy,x2+dx,y2+dy))
    def reset(self):
        self.rect=self.start=self.mode=None
        if hasattr(self,"selection"): self.canvas.delete(self.selection); del self.selection
        self.buttons.place_forget(); self.canvas.itemconfigure(self.info,text="拖动选择区域；Enter/双击完成，Esc/右键取消")
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
        win=tk.Toplevel(self.root); win.title("设置截图状态"); win.transient(self.root); win.grab_set(); choice=tk.StringVar(value="blocked")
        for label,value in [("受阻","blocked"),("不适用","not-applicable"),("豁免","waived"),("恢复待截图","pending")]: ttk.Radiobutton(win,text=label,variable=choice,value=value).pack(anchor="w",padx=15,pady=3)
        ttk.Label(win,text="原因（异常状态必填）").pack(anchor="w",padx=15,pady=(8,2)); entry=ttk.Entry(win,width=55); entry.pack(padx=15); entry.focus_set()
        def save():
            try: state.set_locale_status(self.workspace,self.task_id,shot["id"],self.locale_var.get(),choice.get(),entry.get()); win.destroy(); self.refresh()
            except Exception as e: messagebox.showerror("无法保存",str(e),parent=win)
        ttk.Button(win,text="保存",command=save).pack(pady=12)
    def accept_all(self):
        if not messagebox.askyesno("接受全部截图","确认已目视核对当前所有必需截图，并接受它们用于发布？"):return
        try: state.accept(self.workspace,self.task_id); self.refresh(); prompt=f"使用 $greenvalley-manual 继续 {self.task_id}，截图已完成并已由用户目视接受，请同步状态并继续后续流程。"; self.root.clipboard_clear(); self.root.clipboard_append(prompt); messagebox.showinfo("已接受","已记录总体验收，并将继续任务提示复制到剪贴板。")
        except Exception as e: messagebox.showerror("无法接受",str(e))
    def close(self):
        try:self.save_local()
        finally:self.lock.release(); self.root.destroy()


def main():
    dpi_aware(); p=argparse.ArgumentParser(); p.add_argument("--workspace",type=Path); p.add_argument("--task"); p.add_argument("--repository",type=Path); p.add_argument("--check",action="store_true"); a=p.parse_args()
    try:
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
