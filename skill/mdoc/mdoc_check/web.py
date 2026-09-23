from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import re
import secrets
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from .core import book_context, load_workspace
from .pdf_preview import PdfPreviewManager
from .feedback import SearchIndex
from .feedback_images import ImageCandidates
from .reports import file_record, file_records, global_record, load_generation, record_findings, source_changed, workspace_root
from .sources import source_path


ROOT = Path(__file__).resolve().parents[1]
MAX_EDIT_BYTES = 10 * 1024 * 1024
LOCAL_OPENER = build_opener(ProxyHandler({}))


def report_root(workspace: Path) -> Path:
    return workspace_root(workspace)


def _server_state_path(workspace: Path) -> Path:
    return workspace / ".mdoc" / "runtime" / "report-server.json"


def _reuse_server(workspace: Path, open_browser: bool, page: str = "check") -> str | None:
    state_path = _server_state_path(workspace)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        request = Request(state["url"] + "/api/server", headers={"X-Mdoc-Token": state["token"]})
        with LOCAL_OPENER.open(request, timeout=1) as response:
            status = json.loads(response.read().decode("utf-8"))
        if status.get("workspace") != str(workspace.resolve()): raise ValueError("报告服务工作区不匹配。")
        url = state["url"] + ("/feedback/?token=" if page == "feedback" else "/check/?token=") + state["token"]
        if open_browser: webbrowser.open(url)
        return url
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        state_path.unlink(missing_ok=True)
        return None


def create_launcher(workspace: Path) -> Path:
    from mdoc_core.launchers import refresh
    refresh(workspace)
    return workspace / ".mdoc" / "launchers" / "open_check_report.cmd"


def _report_path(workspace: Path, report_id: str) -> Path:
    root = report_root(workspace).resolve()
    path = (root / Path(*PurePosixPath(report_id).parts)).resolve(); path.relative_to(root)
    if path.name != "latest.json" or not path.is_file(): raise ValueError("未知报告。")
    return path


def _reports(workspace: Path) -> list[dict]:
    root = report_root(workspace); results = []
    for path in root.rglob("latest.meta.json") if root.is_dir() else []:
        try:
            report_path = path.with_name("latest.json")
            value = json.loads(path.read_text(encoding="utf-8")); report = json.loads(report_path.read_text(encoding="utf-8"))
            if not report.get("generation") or not (report_path.parent / "generations" / report["generation"]).is_dir(): continue
            value["id"] = report_path.relative_to(root).as_posix(); results.append(value)
        except (OSError, json.JSONDecodeError): continue
    return sorted(results, key=lambda item: item.get("created_at", 0), reverse=True)


def _report(workspace: Path, report_id: str) -> dict:
    path = _report_path(workspace, report_id)
    from .maintenance import touch_access
    touch_access(path.parent)
    return json.loads(path.read_text(encoding="utf-8"))


def _file_page(workspace: Path, report_id: str, offset: int = 0, limit: int = 20, query: str = "", severity: str = "", state: str = "active", paths: list[str] | None = None, query_scope: str = "all") -> dict:
    limit = min(100, max(10, limit if limit in {10, 20, 50, 100} else 20)); offset = max(0, offset); report_path = _report_path(workspace, report_id); _report_value, generation = load_generation(report_path); records = json.loads((generation / "files.json").read_text(encoding="utf-8"))["files"]; needle = query.casefold(); query_scope = query_scope if query_scope in {"all", "path", "rule", "message"} else "all"; items = []
    for record in records:
        if paths and record["path"] not in paths: continue
        if not paths and needle:
            path_match = query_scope in {"all", "path"} and needle in record["path"].casefold()
            finding_match = False
            if not path_match and query_scope != "path":
                keys = ("rule", "native_rule") if query_scope == "rule" else ("message", "native_message") if query_scope == "message" else ("rule", "native_rule", "message", "native_message")
                finding_match = any(needle in " ".join(str(item.get(key, "")) for key in keys).casefold() for item in record_findings(generation, record))
            if not (path_match or finding_match): continue
        counts = record["counts"]
        if not paths and ((severity == "error" and not counts["effective_errors"]) or (severity == "warning" and not counts["effective_warnings"])): continue
        if not paths and state == "active" and not (counts["effective_errors"] or counts["effective_warnings"]): continue
        if not paths and state == "ignored" and not counts["ignored"]: continue
        if not paths and state == "passed" and record["status"] != "passed": continue
        if not paths and state == "missing" and record["existence"] != "missing": continue
        items.append(record)
    return {"items": items[offset:offset + limit], "offset": offset, "limit": limit, "total": len(items)}


def _finding(workspace: Path, report_id: str, finding_id: str, display: str | None = None) -> tuple[dict, dict]:
    report_path = _report_path(workspace, report_id)
    if display:
        _meta, findings = file_record(report_path, display); found = next((item for item in findings if item.get("finding_id") == finding_id), None)
        if found: return _report(workspace, report_id), found
        raise ValueError("问题结果已更新，请重新选择。")
    for record in file_records(report_path):
        _meta, findings = file_record(report_path, record["path"]); found = next((item for item in findings if item.get("finding_id") == finding_id), None)
        if found: return _report(workspace, report_id), found
    raise ValueError("问题结果已更新，请重新选择。")


def _dictionary_context(workspace: Path, report_id: str, finding_id: str, display: str | None = None) -> dict:
    from .dictionaries import load

    report, issue = _finding(workspace, report_id, finding_id, display)
    if issue["rule"] != "spelling.unknown-word":
        raise ValueError("只有拼写检查问题可以加入自定义品牌或术语。")
    match = re.search(r"Unknown English word:\s*(\S+)", issue.get("native_message") or issue.get("message", ""))
    if not match:
        raise ValueError("无法从当前问题中识别待添加词语。")
    locale = issue["path"].split("/", 1)[0]
    book_id = issue.get("book") or report["scope"].get("book")
    language = load_workspace(workspace)["books"][book_id]["locales"][locale]["language"]
    language = "en" if language == "ja" else language
    return {"word": match.group(1), "language": language, "workspace": load(workspace), "language_words": load(workspace, language)}


def _source_path(workspace: Path, report: dict, display: str, finding_book: str | None = None) -> Path:
    return source_path(workspace, report, display, finding_book)


def _source(workspace: Path, report: dict, display: str, line: int = 1, before: int = 8, after: int = 8) -> dict:
    path = _source_path(workspace, report, display); lines = path.read_text(encoding="utf-8").splitlines(); first = max(1, line - before); last = min(len(lines), line + after)
    return {"path": display, "physical_path": str(path), "first_line": first, "total_lines": len(lines), "lines": lines[first - 1:last]}


def _resource_info(workspace: Path, report: dict, display: str) -> dict:
    path = _source_path(workspace, report, display)
    if not path.is_file():
        raise ValueError("资源文件不存在。")
    image = path.suffix.casefold() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
    value = {"path": display, "physical_path": str(path), "name": path.name, "extension": path.suffix, "size": path.stat().st_size, "image": image}
    if image:
        from PIL import Image
        with Image.open(path) as source:
            value.update({"width": source.width, "height": source.height, "format": source.format or path.suffix.lstrip(".").upper()})
    return value


def _editable_path(workspace: Path, report: dict, display: str) -> Path:
    path = _source_path(workspace, report, display)
    if path.suffix.casefold() not in {".md", ".markdown"} or not path.is_file():
        raise ValueError("只允许编辑当前报告中的 Markdown 文件。")
    if report.get("context") == "task":
        from .tasks import load_task

        _definition, _state, directory = load_task(workspace, report["scope"]["task"])
        try:
            path.resolve().relative_to((directory / "staging").resolve())
        except ValueError as exc:
            raise ValueError("任务报告只允许编辑 staging 中的 Markdown 文件。") from exc
    return path


def _file_state(path: Path) -> dict:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": str(stat.st_mtime_ns)}


def _edit_source(workspace: Path, report: dict, display: str) -> dict:
    path = _editable_path(workspace, report, display)
    raw = path.read_bytes()
    if len(raw) > MAX_EDIT_BYTES:
        raise ValueError("Markdown 文件超过 10 MiB，不能在报告中编辑。")
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw[3 if bom else 0:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Markdown 文件不是有效的 UTF-8。") from exc
    newline = "crlf" if "\r\n" in text else "lf"
    trailing = text.endswith(("\n", "\r"))
    content = text.replace("\r\n", "\n").replace("\r", "\n")
    return {"path": display, "physical_path": str(path), "content": content, "state": _file_state(path), "newline": newline, "trailing_newline": trailing, "bom": bom, "summary": PurePosixPath(display).name.casefold() == "summary.md"}


def _save_source(workspace: Path, report: dict, display: str, content: str, state: dict, trailing_newline: bool | None = None) -> dict:
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_EDIT_BYTES:
        raise ValueError("Markdown 内容不能超过 10 MiB。")
    path = _editable_path(workspace, report, display)
    current = _file_state(path)
    if current != state:
        raise ValueError("文件已被外部程序修改，当前内容未保存。请重新加载文件后再编辑。")
    original = _edit_source(workspace, report, display)
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    trailing = original["trailing_newline"] if trailing_newline is None else trailing_newline
    normalized = normalized.rstrip("\n") + "\n" if trailing else normalized.rstrip("\n")
    text = normalized.replace("\n", "\r\n") if original["newline"] == "crlf" else normalized
    data = (b"\xef\xbb\xbf" if original["bom"] else b"") + text.encode("utf-8")
    if data == path.read_bytes():
        return {"saved": False, "state": current}
    mode = path.stat().st_mode
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, mode); os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"saved": True, "state": _file_state(path)}


def _preview_resource_path(workspace: Path, report: dict, display: str, resource: str) -> Path:
    locale, logical = display.split("/", 1)
    parsed = urlsplit(resource)
    if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith(("/", "\\")):
        raise ValueError("预览资源必须使用相对路径。")
    combined = posixpath.normpath(posixpath.join(PurePosixPath(logical).parent.as_posix(), unquote(parsed.path).replace("\\", "/")))
    if combined == ".." or combined.startswith("../"):
        raise ValueError("预览资源路径越界。")
    path = _source_path(workspace, report, f"{locale}/{combined}")
    if path.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        raise ValueError("预览仅支持静态位图资源。")
    if not path.is_file():
        raise ValueError("预览资源不存在。")
    return path


def _preview_link(workspace: Path, report: dict, display: str, target: str) -> dict:
    locale, logical = display.split("/", 1)
    parsed = urlsplit(target)
    if parsed.scheme in {"http", "https"}:
        return {"kind": "external", "url": target}
    if parsed.scheme or parsed.netloc or parsed.path.startswith(("/", "\\")):
        raise ValueError("预览链接必须使用相对路径。")
    if not parsed.path:
        return {"kind": "anchor", "path": display, "fragment": unquote(parsed.fragment)}
    combined = posixpath.normpath(posixpath.join(PurePosixPath(logical).parent.as_posix(), unquote(parsed.path).replace("\\", "/")))
    if combined == ".." or combined.startswith("../"):
        raise ValueError("预览链接路径越界。")
    path = _source_path(workspace, report, f"{locale}/{combined}")
    if path.suffix.casefold() not in {".md", ".markdown"}:
        raise ValueError("预览仅支持跳转到 Markdown 页面。")
    if not path.is_file():
        raise ValueError("预览链接目标不存在。")
    return {"kind": "markdown", "path": f"{locale}/{combined}", "fragment": unquote(parsed.fragment)}


def _help(finding: dict) -> dict:
    catalog = json.loads((Path(__file__).with_name("rule_help.zh-CN.json")).read_text(encoding="utf-8")); value = catalog.get(finding["rule"], {"title": "暂无中文说明", "description": "此规则尚未提供中文说明。", "suggestion": "请结合原始信息和规则编号进行处理。"})
    if "url" not in value and finding.get("checker") == "markdownlint" and re.fullmatch(r"MD\d{3}", finding.get("native_rule", ""), re.IGNORECASE):
        value = {**value, "url": f"https://github.com/DavidAnson/markdownlint/blob/main/doc/{finding['native_rule'].lower()}.md"}
    if finding["rule"] == "markdown.md009":
        match = re.search(r"Expected: (.*?); Actual: (\d+)", finding.get("message", ""))
        if match: value = {**value, "current": f"当前行尾有 {match.group(2)} 个空格，期望为 {match.group(1)}。"}
    if finding["rule"] == "terminology.brand-name":
        details = finding.get("details") or {}
        value = {**value, "current": f"当前写法：{details.get('actual', '')}；标准写法：{details.get('expected', '')}；配置来源：{details.get('configuration', '')}", "before": details.get("actual", ""), "after": details.get("expected", "")}
    return {**value, "severity_label": "错误" if finding["severity"] == "error" else "警告", "original": finding.get("native_message") or finding.get("message")}


HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>mdoc 检查报告</title><style>
.column-hit{padding:1px 0;background:#ffe58a;box-shadow:0 0 0 1px #d5a400}
.resource-info{padding:12px 0}.resource-preview{display:block;max-width:100%;max-height:calc(100vh - 300px);margin:0 auto;border:1px solid #d8dee4;object-fit:contain}
.dictionary{margin:8px 0;padding:8px 10px;border:1px solid #d9dedb}.dictionary-head,.dictionary-actions,.word-list{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.dictionary-head{margin-bottom:6px}.dictionary-head b{flex:1}.dictionary-scope{margin-top:6px;font-size:12px;color:#687078}.word{display:inline-flex;align-items:center;gap:3px;padding:2px 5px;background:#eef1ef}.word button{border:0;background:transparent;color:#687078;cursor:pointer;padding:0 2px}
.help{padding:9px 12px}.help-head{display:flex;align-items:center;gap:10px;margin-bottom:6px}.help-head h2{flex:1;margin:0;font-size:17px;line-height:1.3}.help-rule{white-space:nowrap}.help-grid,.help-example{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px 16px}.help-item{margin:0}.help-item b,.help-example b{display:block;margin-bottom:2px}.help-current{margin-top:7px}.help-example{margin-top:7px}.help-example code{display:block;padding:5px 7px;white-space:pre-wrap;overflow-wrap:anywhere;background:#eef1ef;font:12px/1.45 Consolas,monospace}.help-original{margin-top:7px;padding-top:6px;border-top:1px solid #dde2df;overflow-wrap:anywhere}@media(max-width:900px){.help-head{align-items:flex-start;flex-wrap:wrap}.help-grid,.help-example{grid-template-columns:1fr}}
.editor-box{height:calc(100vh - 260px);min-height:360px;border:1px solid #d8dee4}.edit-toolbar{height:42px;display:flex;align-items:center;gap:7px;padding:6px;border-bottom:1px solid #d8dee4}.edit-status{margin-left:auto;color:#687078}.edit-status.warning{color:#8a5200}.edit-status.error{color:#b3261e}#edit-surface,#source-surface{height:calc(100% - 42px)}.edit-confirm{display:flex;align-items:center;gap:8px;padding:10px 12px;color:#8a5200;background:#fef7e0;border:1px solid #f3d37a}.edit-confirm span{flex:1}.auto-fix-summary{padding:8px 10px;background:#fef7e0;border-bottom:1px solid #f3d37a}.auto-fix-head{display:flex;align-items:center;gap:8px}.auto-fix-head span{flex:1}.auto-fix-details{max-height:180px;overflow:auto;padding-top:7px}.auto-fix-rules button{margin:2px 6px 2px 0}.auto-fix-help{display:flex;align-items:flex-start;gap:12px;margin-top:7px;padding-top:7px;border-top:1px solid #e7ca73}.auto-fix-help span{flex:1}.auto-fix-help a{white-space:nowrap}.cm-auto-fix{background:#fff2a8;box-shadow:inset 0 -1px #d5a400}.cm-content .cm-finding-hit{background:#ffd666!important;box-shadow:inset 0 -1px #b7791f!important}.cm-content .cm-line.cm-finding-line{background:#ffe58a!important;box-shadow:inset 4px 0 #b7791f!important}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 "Segoe UI","Microsoft YaHei",sans-serif;color:#202124}button,input,select{font:inherit}header{height:58px;display:flex;align-items:center;gap:12px;padding:0 18px;border-bottom:1px solid #ddd}header h1{font-size:18px;margin:0}.notice{margin:10px 14px;padding:12px 14px;border:1px solid;border-radius:8px;display:flex;align-items:center;gap:12px}.notice span{flex:1}.notice button{border:0;background:transparent;color:inherit;font-size:18px;line-height:1;cursor:pointer}.notice.success{color:#137333;background:#e6f4ea;border-color:#a8dab5}.notice.warning{color:#8a5200;background:#fef7e0;border-color:#f3d37a}.notice.error{color:#b3261e;background:#fce8e6;border-color:#efaaa3}main{display:grid;grid-template-columns:var(--reports-width,300px) 6px var(--files-width,500px) 6px minmax(320px,1fr);height:calc(100vh - 58px - var(--notice-height,0px))}aside,#files-pane,#detail{min-width:0;overflow:auto}.splitter{cursor:col-resize;background:#e6e9e7;border-left:1px solid #d2d6d4;border-right:1px solid #d2d6d4}.splitter:hover,.splitter.dragging{background:#9bb4a8}.report,.file,.finding{display:block;width:100%;text-align:left;border:0;border-bottom:1px solid #eee;background:#fff;padding:11px;cursor:pointer}.active,.report:hover,.file:hover,.finding:hover{background:#eef5f1}.tools{position:sticky;top:0;background:#fff;padding:8px;border-bottom:1px solid #ddd;display:flex;gap:6px;z-index:2}.tools input{min-width:0;flex:1}.meta{font-size:12px;color:#687078}.error{color:#b3261e}.warning{color:#8a5200}.group{border-bottom:1px solid #ccd}.findings{padding-left:16px}.finding.ignored,.help.ignored{color:#7a7f7d;background:#f2f3f3;text-decoration:line-through}.finding.ignored .error,.finding.ignored .warning{color:#7a7f7d}.hidden{display:none}.tabs{display:flex;gap:4px;padding:10px}.view-tabs{position:sticky;top:0;z-index:5;flex-wrap:nowrap;overflow:hidden;background:#fff;border-bottom:1px solid #ddd}.view-tabs button.active{color:#137333;background:#e6f4ea;border-color:#79b98c}#view-more{display:none;margin-left:auto}.view-tabs.compact #open-source,.view-tabs.compact #ignore-finding,.view-tabs.compact #open-menu{display:none}.view-tabs.compact #view-more{display:block}.panel{padding:0 14px 20px}.line{display:grid;grid-template-columns:50px 1fr;font:13px/1.55 Consolas,monospace;white-space:pre-wrap}.hit{box-shadow:inset 3px 0 #d5a400}.line-no{text-align:right;color:#888;padding-right:10px}.help{padding:9px 12px;background:#f7f8f8;border-left:3px solid #4f7664}.preview{width:100%;height:calc(100vh - 180px);border:0}.pager{padding:8px;display:flex;gap:8px;align-items:center}.selection{font-weight:600}
</style></head><body><header><h1>mdoc 检查报告</h1><span id="summary">正在加载...</span><button id="recheck">重新检查原范围</button></header><main><aside id="reports"></aside><div class="splitter" data-resize="reports" title="拖动调整报告列表宽度"></div><section id="files-pane"><div class="tools"><select id="severity"><option value="">全部级别</option><option value="error">错误</option><option value="warning">警告</option></select><select id="state"><option value="active">有效问题</option><option value="ignored">已忽略</option><option value="passed">已通过</option><option value="missing">缺失</option><option value="all">全部</option></select><select id="query-scope"><option value="all">全部范围</option><option value="path">文件路径</option><option value="rule">规则编号</option><option value="message">问题消息</option></select><input id="search" placeholder="路径、规则或消息"><button id="filter">筛选</button><button id="clear-focus" class="hidden">返回筛选结果</button></div><div class="tools"><button id="select-page">选择当前页</button><button id="clear-selection">清空选择</button><span class="selection" id="selected">已选择 0</span><button id="check-selected">检查所选文件</button></div><div id="files"></div><div class="pager" id="pager"><button id="previous-page">上一页</button><span id="pager-info"></span><button id="next-page">下一页</button><select id="page-size"><option>10</option><option selected>20</option><option>50</option><option>100</option></select></div></section><div class="splitter" data-resize="files" title="拖动调整文件列表和预览宽度"></div><section id="detail"><div class="panel">选择一个问题以查看中文说明和源文件。</div></section></main><script>
document.querySelector('header').insertAdjacentHTML('afterend','<div id="notice" class="notice hidden" role="status"><span id="notice-text"></span><button id="notice-close" title="关闭">×</button></div>');const TOKEN='__TOKEN__',selectedPaths=new Set(),focusPaths=new Set(),reports=document.getElementById('reports'),summary=document.getElementById('summary'),files=document.getElementById('files'),pageSize=document.getElementById('page-size'),search=document.getElementById('search'),queryScope=document.getElementById('query-scope'),severity=document.getElementById('severity'),state=document.getElementById('state'),detail=document.getElementById('detail'),selected=document.getElementById('selected'),filter=document.getElementById('filter'),clearFocus=document.getElementById('clear-focus'),notice=document.getElementById('notice'),noticeText=document.getElementById('notice-text');let currentReport=null,currentReportValue=null,currentOffset=0,currentFinding=null,currentSourceChanged=false,checking=false;let detailRequest=0,fileRequest=0;const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));function notify(type,message,details=''){notice.className='notice '+type;noticeText.textContent=message;noticeText.title=details;document.documentElement.style.setProperty('--notice-height',notice.offsetHeight+20+'px')}function closeNotice(){notice.className='notice hidden';noticeText.title='';document.documentElement.style.setProperty('--notice-height','0px')}document.getElementById('notice-close').onclick=closeNotice;function reportLabel(r=currentReportValue){const scope=r?.scope||{};return [scope.task||scope.book||r?.context,scope.locale,scope.kind==='book'?'整册':scope.kind==='page'?'页面':scope.kind==='section'?'章节':scope.kind==='workspace'?'工作区':scope.kind==='task'?'任务':scope.kind].filter(Boolean).join(' / ')||currentReport||'未知报告'}function selectedLabel(paths){return paths.length<=5?paths.join('；'):paths.slice(0,5).join('；')+'；另有 '+(paths.length-5)+' 个文件'}async function api(url,opt={}){opt.headers={...(opt.headers||{}),'X-Mdoc-Token':TOKEN};const r=await fetch(url,opt);if(!r.ok)throw new Error((await r.json()).error||r.statusText);return r.status===204?{}:r.json()}
async function loadReports(){const data=await api('/api/reports');reports.innerHTML=data.map((r,i)=>`<button class="report ${i?'':'active'}" data-id="${esc(r.id)}"><b>${esc(r.scope?.task||r.scope?.book||r.context)}</b><div>${esc(r.scope?.mode||r.scope?.locale||r.scope?.kind)} · ${esc(r.status)}</div><div class="meta">${r.counts?.effective_errors||0} 错误 · ${r.counts?.effective_warnings||0} 警告</div></button>`).join('');document.querySelectorAll('.report').forEach(b=>b.onclick=()=>loadReport(b.dataset.id,b));if(data[0])loadReport(data[0].id,document.querySelector('.report'))}
async function loadReport(id,b,keepSelection=false){currentReport=id;currentOffset=0;if(!keepSelection)selectedPaths.clear();updateSelected();document.querySelectorAll('.report').forEach(x=>x.classList.remove('active'));b?.classList.add('active');const r=await api('/api/report?id='+encodeURIComponent(id));currentReportValue=r;summary.textContent=`${r.status} · ${r.counts?.effective_errors||0} 个有效错误 · ${r.counts?.effective_warnings||0} 个警告 · 完整检查 ${r.full_check?.status||r.status}`;document.getElementById('recheck').title='重新检查报告【'+reportLabel(r)+'】的原范围';await loadFiles()}
async function loadFiles(){const request=++fileRequest,p=new URLSearchParams({report:currentReport,offset:currentOffset,limit:pageSize.value,q:search.value,query_scope:queryScope.value,severity:severity.value,state:state.value});focusPaths.forEach(path=>p.append('path',path));clearFocus.classList.toggle('hidden',!focusPaths.size);const page=await api('/api/files?'+p);if(request!==fileRequest)return;files.innerHTML=page.items.map(x=>`<div class="group"><button class="file" data-path="${esc(x.path)}"><input class="pick" type="checkbox" data-path="${esc(x.path)}" ${selectedPaths.has(x.path)?'checked':''}><b>${esc(x.path)}</b><div class="meta">${x.counts.effective_errors} 个错误 · ${x.counts.effective_warnings} 个警告 · ${x.counts.ignored} 个已忽略 · ${esc(x.existence)}</div></button><div class="findings hidden"></div></div>`).join('')||'<div class="panel">没有匹配文件。</div>';document.querySelectorAll('.pick').forEach(x=>x.onclick=e=>{e.stopPropagation();x.checked?selectedPaths.add(x.dataset.path):selectedPaths.delete(x.dataset.path);updateSelected()});document.querySelectorAll('.file').forEach(x=>x.onclick=e=>{if(!e.target.classList.contains('pick'))openFile(x.dataset.path,x.nextElementSibling)});renderPager(page)}
async function openFile(path,box){const request=++detailRequest,data=await api('/api/file?report='+encodeURIComponent(currentReport)+'&path='+encodeURIComponent(path));if(request!==detailRequest)return;currentSourceChanged=data.source_changed;document.querySelectorAll('.findings').forEach(x=>x.classList.add('hidden'));box.classList.remove('hidden');box.innerHTML=(data.source_changed?'<div class="panel warning">源文件已变化，请重新检查此文件后再处理旧问题。</div>':'')+(data.findings.map((f,i)=>`<button class="finding ${f.ignore_id?'ignored':''}" data-index="${i}"><b class="${f.severity}">${f.ignore_id?'已忽略 · ':''}${f.severity==='error'?'错误':'警告'} · ${esc(f.rule)}</b><div>${f.line}:${f.column}</div><div class="meta">${esc(f.help.title)}</div></button>`).join('')||'<div class="panel">此文件当前没有问题。</div>');box.querySelectorAll('.finding').forEach(x=>x.onclick=()=>showFinding(data.findings[+x.dataset.index],x))}
async function showFinding(f,button){const request=++detailRequest;currentFinding=f;document.querySelectorAll('.finding').forEach(x=>x.classList.remove('active'));button?.classList.add('active');const s=await api('/api/source?report='+encodeURIComponent(currentReport)+'&path='+encodeURIComponent(f.path)+'&line='+f.line);if(request!==detailRequest)return;detail.innerHTML=`<div class="tabs"><button id="source-tab">源码定位</button><button id="preview-tab">Markdown 预览</button><button id="open-source">打开并定位</button><button id="ignore-finding" ${currentSourceChanged||f.mandatory?'disabled':''}>${f.ignore_id?'取消忽略':'忽略此问题'}</button><select id="open-menu"><option value="">打开方式</option><option value="default">Windows 默认程序</option><option value="select">重新选择编辑器...</option></select></div><div class="panel">${currentSourceChanged?'<p class="warning">源文件已变化。当前定位仅供参考，请先重新检查该文件。</p>':''}${f.ignore_id?'<p class="meta">此问题已被忽略，可点击“取消忽略”恢复。</p>':''}<div class="help ${f.ignore_id?'ignored':''}"><div class="help-head"><h2>${esc(f.help.title)}</h2><span class="meta help-rule">${esc(f.rule)}</span>${f.help.url?`<a href="${esc(f.help.url)}" target="_blank" rel="noopener noreferrer">查看规则原文</a>`:''}</div><div class="help-grid"><p class="help-item"><b>问题说明</b>${esc(f.help.description)}</p><p class="help-item"><b>修改建议</b>${esc(f.help.suggestion)}</p></div>${f.help.current?`<p class="help-item help-current"><b>当前情况</b>${esc(f.help.current)}</p>`:''}${f.help.before?`<div class="help-example"><div><b>修改前</b><code>${esc(f.help.before)}</code></div><div><b>修改后</b><code>${esc(f.help.after)}</code></div></div>`:''}<div class="meta help-original">原始信息：${esc(f.help.original)}</div></div>${f.rule==='spelling.unknown-word'?'<div id="dictionary-box" class="dictionary"><span class="meta">正在加载自定义品牌/术语...</span></div>':''}<div id="source-lines">${s.lines.map((line,i)=>`<div class="line ${s.first_line+i>=f.line&&s.first_line+i<=(f.end_line||f.line)?'hit':''}"><span class="line-no">${s.first_line+i}</span><span>${esc(line)||' '}</span></div>`).join('')}</div><div id="preview-box" class="hidden"></div></div>`;detail.scrollTop=0;if(f.rule==='spelling.unknown-word')loadDictionary(f,request);document.getElementById('open-source').onclick=()=>openSource('remembered');document.getElementById('ignore-finding').onclick=toggleIgnore;const openMenu=document.getElementById('open-menu');openMenu.onchange=async e=>{const value=e.target.value;openMenu.value='';if(value==='default')await openSource('windows-default');else if(value==='select')await selectEditor()};document.getElementById('preview-tab').onclick=showPreview;document.getElementById('source-tab').onclick=()=>{document.getElementById('source-lines').classList.remove('hidden');document.getElementById('preview-box').classList.add('hidden')}}
function dictionaryWords(items,scope){return items.map(word=>`<span class="word">${esc(word)}<button data-word="${esc(word)}" data-scope="${scope}" title="删除此词">×</button></span>`).join('')||'无'}
async function loadDictionary(f,request=detailRequest){const box=document.getElementById('dictionary-box');if(!box)return;try{const d=await api('/api/dictionary?report='+encodeURIComponent(currentReport)+'&finding='+encodeURIComponent(f.finding_id)+'&path='+encodeURIComponent(f.path));if(request!==detailRequest||currentFinding?.finding_id!==f.finding_id)return;box.innerHTML=`<div class="dictionary-head"><b>自定义品牌/术语：${esc(d.word)}</b><div class="dictionary-actions"><button data-add="workspace">加入工作区词典</button><button data-add="language">加入 ${esc(d.language)} 词典</button></div></div><details><summary>管理已有词条（工作区 ${d.workspace.length}，${esc(d.language)} ${d.language_words.length}）</summary><div class="dictionary-scope">工作区通用</div><div class="word-list">${dictionaryWords(d.workspace,'workspace')}</div><div class="dictionary-scope">${esc(d.language)} 语言</div><div class="word-list">${dictionaryWords(d.language_words,'language')}</div></details>`;box.querySelectorAll('[data-add]').forEach(x=>x.onclick=()=>changeDictionary('add',x.dataset.add,d.word));box.querySelectorAll('[data-word]').forEach(x=>x.onclick=()=>changeDictionary('remove',x.dataset.scope,x.dataset.word))}catch(e){if(request===detailRequest&&currentFinding?.finding_id===f.finding_id)box.innerHTML=`<span class="warning">自定义品牌/术语加载失败：${esc(e.message)}</span>`}}
async function changeDictionary(action,scope,word){try{await api('/api/dictionary/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({report:currentReport,finding_id:currentFinding.finding_id,path:currentFinding.path,scope,word})});notify('success',`${word} 已${action==='add'?'加入':'从'}${scope==='workspace'?'工作区':'语言'}词典${action==='remove'?'删除':''}，重新检查文件后生效。`);await loadDictionary(currentFinding)}catch(e){notify('error','词典操作失败：'+e.message)}}
async function showPreview(){return renderPreview(currentFinding.path)}
async function renderPreview(path,fragment=''){const p=await api('/api/preview?report='+encodeURIComponent(currentReport)+'&path='+encodeURIComponent(path));const doc=new DOMParser().parseFromString(p.html,'text/html');doc.querySelectorAll('p').forEach(p=>{if(!p.textContent.trim()&&p.children.length&&[...p.children].every(child=>child.tagName==='IMG'))p.classList.add('standalone-image')});doc.querySelectorAll('img[src]').forEach(img=>{const src=img.getAttribute('src');if(src&&!/^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i.test(src))img.setAttribute('src','/api/preview-resource?token='+encodeURIComponent(TOKEN)+'&report='+encodeURIComponent(currentReport)+'&path='+encodeURIComponent(path)+'&resource='+encodeURIComponent(src))});document.getElementById('source-lines').classList.add('hidden');const box=document.getElementById('preview-box');box.classList.remove('hidden');box.innerHTML=p.filtered?'<p>部分不安全 HTML 已过滤。</p>':'';const frame=document.createElement('iframe');frame.className='preview';frame.setAttribute('sandbox','allow-same-origin');frame.onload=()=>{const previewDocument=frame.contentDocument;previewDocument.addEventListener('click',event=>{const link=event.target.closest('a[href]');if(!link)return;event.preventDefault();followPreviewLink(path,link.getAttribute('href'),frame)});if(fragment){const target=previewDocument.getElementById(fragment)||previewDocument.querySelector(`[name="${CSS.escape(fragment)}"]`);target?.scrollIntoView()}};const highlight=path===currentFinding.path?`[data-source-line='${currentFinding.line}']{box-shadow:inset 3px 0 #d5a400}`:'';frame.srcdoc=`<style>*{box-sizing:border-box}body{margin:0 auto;padding:24px 32px 60px;max-width:1100px;color:#24292f;background:#fff;font:16px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif}h1,h2,h3,h4,h5,h6{margin:1.35em 0 .55em;padding:0;font-weight:650;line-height:1.3}h1{font-size:2em;border-bottom:1px solid #d8dee4;padding-bottom:.3em}h2{font-size:1.5em;border-bottom:1px solid #d8dee4;padding-bottom:.25em}h3{font-size:1.25em}h4{font-size:1em}p{margin:.75em 0}blockquote{margin:1em 0;padding:.6em 1em;color:#57606a;background:#f6f8fa;border-left:4px solid #8c959f}blockquote>:first-child{margin-top:0}blockquote>:last-child{margin-bottom:0}ul,ol{margin:.75em 0;padding-left:2em}li+li{margin-top:.25em}img{max-width:100%}img:not([height]){height:auto}.standalone-image>img,div[align=center]>img,div[align='center']>img{display:block;margin:1em auto}p img{vertical-align:middle}figure{max-width:100%}figcaption{margin-top:.35em;color:#57606a;font-size:.9em}table{display:block;width:max-content;max-width:100%;overflow:auto;border-collapse:collapse;margin:1em 0}td,th{border:1px solid #d0d7de;padding:6px 13px}th{background:#f6f8fa}code{padding:.15em .35em;background:#eff1f3;border-radius:3px;font:85% Consolas,monospace}pre{overflow:auto;background:#f6f8fa;padding:16px;border:1px solid #d8dee4}pre code{padding:0;background:transparent}${highlight}</style>${doc.body.innerHTML}`;box.appendChild(frame)}
async function followPreviewLink(path,target,frame){try{const link=await api('/api/preview-link?report='+encodeURIComponent(currentReport)+'&path='+encodeURIComponent(path)+'&target='+encodeURIComponent(target));if(link.kind==='anchor'){const previewDocument=frame.contentDocument,node=previewDocument.getElementById(link.fragment)||previewDocument.querySelector(`[name="${CSS.escape(link.fragment)}"]`);if(node)node.scrollIntoView();else notify('warning','预览页内未找到对应位置。')}else if(link.kind==='markdown')await renderPreview(link.path,link.fragment);else if(link.kind==='external')window.open(link.url,'_blank','noopener')}catch(e){notify('error','预览链接打开失败：'+e.message)}}
async function openSource(mode){try{await api('/api/editor/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({report:currentReport,path:currentFinding.path,line:currentFinding.line,column:currentFinding.column,mode})})}catch(e){if(mode==='remembered'&&e.message.includes('尚未选择有效的 Markdown 编辑器')){try{const chosen=await selectEditor();if(chosen.status==='selected')return openSource('remembered')}catch(selectError){notify('error',selectError.message)}}else notify('error',e.message)}}async function selectEditor(){return api('/api/editor/select',{method:'POST'})}
async function toggleIgnore(){const path=currentFinding.path;if(currentFinding.ignore_id)await api('/api/ignore/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({report:currentReport,ignore_id:currentFinding.ignore_id})});else await api('/api/ignore',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({report:currentReport,finding_id:currentFinding.finding_id})});detail.innerHTML=`<div class="panel">问题已${currentFinding.ignore_id?'取消忽略':'忽略'}。</div>`;await loadReport(currentReport,null,true);const button=document.querySelector(`.file[data-path="${CSS.escape(path)}"]`);if(button)await openFile(path,button.nextElementSibling)}
function setChecking(active){checking=active;document.getElementById('recheck').disabled=active;document.getElementById('check-selected').disabled=active}
async function recheck(){if(checking)return;const button=document.getElementById('recheck'),label=button.textContent,report=reportLabel();setChecking(true);button.textContent='正在重新检查...';notify('warning','正在重新检查报告【'+report+'】的原范围...');try{const result=await api('/api/recheck',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({report:currentReport})});focusPaths.clear();selectedPaths.clear();await loadReports();notify(result.counts?.effective_errors?'warning':'success','报告【'+report+'】原范围重新检查完成：'+(result.counts?.effective_errors||0)+' 个错误，'+(result.counts?.effective_warnings||0)+' 个警告。')}catch(e){notify('error','报告【'+report+'】原范围重新检查失败：'+e.message)}finally{button.textContent=label;setChecking(false)}}
function renderPager(p){const prev=document.getElementById('previous-page'),next=document.getElementById('next-page');document.getElementById('pager-info').textContent=`${p.total?Math.floor(p.offset/p.limit)+1:0}/${Math.ceil(p.total/p.limit)} 页，共 ${p.total} 个文件`;prev.disabled=!p.offset;prev.onclick=()=>{currentOffset=Math.max(0,currentOffset-p.limit);loadFiles()};next.disabled=p.offset+p.limit>=p.total;next.onclick=()=>{currentOffset+=p.limit;loadFiles()}}function updateSelected(){selected.textContent=`已选择 ${selectedPaths.size}`}
filter.onclick=()=>{focusPaths.clear();currentOffset=0;loadFiles()};clearFocus.onclick=()=>{focusPaths.clear();currentOffset=0;loadFiles()};document.getElementById('recheck').onclick=recheck;pageSize.onchange=()=>{currentOffset=0;api('/api/preferences',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({files_per_page:+pageSize.value})});loadFiles()};document.getElementById('select-page').onclick=()=>{document.querySelectorAll('.pick').forEach(x=>{x.checked=true;selectedPaths.add(x.dataset.path)});updateSelected()};document.getElementById('clear-selection').onclick=()=>{selectedPaths.clear();loadFiles();updateSelected()};document.getElementById('check-selected').onclick=async()=>{if(checking)return;if(!selectedPaths.size)return notify('warning','报告【'+reportLabel()+'】下尚未选择文件。');const paths=[...selectedPaths],report=reportLabel(),selection=selectedLabel(paths),details=paths.join('\n');setChecking(true);notify('warning','正在检查报告【'+report+'】下所选的 '+paths.length+' 个文件：'+selection,details);try{const result=await api('/api/check-selected',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({report:currentReport,files:paths})});const failed=new Set(result.failures||[]);focusPaths.clear();paths.forEach(x=>{if(failed.has(x))selectedPaths.add(x);else{selectedPaths.delete(x);focusPaths.add(x)}});updateSelected();await loadReport(currentReport,null,true);notify(result.failed_files?'error':'success','报告【'+report+'】所选文件检查完成：已更新 '+result.updated_files+' 个文件'+(result.failed_files?'，'+result.failed_files+' 个文件检查失败并保留选择':'')+'。检查范围：'+selection,details)}catch(e){notify('error','报告【'+report+'】所选文件检查失败：'+e.message+'。检查范围：'+selection,details)}finally{setChecking(false)}};loadReports().catch(e=>notify('error','检查报告加载失败：'+e.message))
document.querySelectorAll('.splitter').forEach(handle=>handle.onpointerdown=e=>{handle.setPointerCapture(e.pointerId);handle.classList.add('dragging');const kind=handle.dataset.resize,start=e.clientX,main=document.querySelector('main'),initial=kind==='reports'?document.getElementById('reports').getBoundingClientRect().width:document.getElementById('files-pane').getBoundingClientRect().width;handle.onpointermove=event=>{const width=Math.max(kind==='reports'?180:300,Math.min(kind==='reports'?520:900,initial+event.clientX-start));main.style.setProperty(kind==='reports'?'--reports-width':'--files-width',width+'px')};handle.onpointerup=()=>{handle.classList.remove('dragging');handle.onpointermove=null}})
const columnObserver=new MutationObserver(()=>{const target=detail.querySelector('.hit span:last-child');if(!target||target.querySelector('.column-hit')||!currentFinding)return;const text=target.textContent,start=Math.max(0,(currentFinding.column||1)-1),end=Math.min(text.length,Math.max(start+1,currentFinding.end_column||currentFinding.column||1));if(start>=text.length)return;target.textContent='';target.append(document.createTextNode(text.slice(0,start)));const mark=document.createElement('mark');mark.className='column-hit';mark.textContent=text.slice(start,end);target.append(mark,document.createTextNode(text.slice(end)))});columnObserver.observe(detail,{childList:true,subtree:true});
</script><script src="/static/editor.js?token=__TOKEN__"></script><script src="/static/editor-ui.js?token=__TOKEN__"></script></body></html>'''


PDF_STYLE = ".pdf-box{min-height:420px}.pdf-toolbar,.pdf-meta,.pdf-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:8px 0}.pdf-meta{color:#687078;border-bottom:1px solid #ddd}.pdf-frame{width:100%;height:calc(100vh - 280px);min-height:420px;border:1px solid #d8dee4}.pdf-log{max-height:180px;overflow:auto;white-space:pre-wrap;background:#f6f8fa;padding:8px;font:12px/1.45 Consolas,monospace}.notice-actions{display:flex;gap:6px}.notice-actions button{font-size:14px;border:1px solid currentColor;background:transparent;padding:3px 8px}"


def create_server(workspace: Path, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    workspace = workspace.resolve(); token = secrets.token_urlsafe(24); pdf_preview = PdfPreviewManager(workspace); feedback_index = SearchIndex(workspace); feedback_images = ImageCandidates(workspace)
    def disconnected(exc: BaseException) -> bool: return isinstance(exc,(BrokenPipeError,ConnectionAbortedError,ConnectionResetError)) or getattr(exc,"winerror",None) in {10053,10054}
    class Server(ThreadingHTTPServer):
        def handle_error(self,request,client_address):
            if not disconnected(sys.exc_info()[1]): super().handle_error(request,client_address)
    class Handler(BaseHTTPRequestHandler):
        def allowed(self) -> bool:
            if self.headers.get("X-Mdoc-Token") != token and parse_qs(urlsplit(self.path).query).get("token", [""])[0] != token: return False
            host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").casefold()
            if host not in {"127.0.0.1", "localhost", "::1"}: return False
            origin = self.headers.get("Origin")
            return not origin or urlsplit(origin).hostname in {"127.0.0.1", "localhost", "::1"}
        def send_json(self, value, status=200): data=json.dumps(value,ensure_ascii=False).encode("utf-8");self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            parsed=urlsplit(self.path);q=parse_qs(parsed.query)
            if parsed.path in {"/static/editor.js", "/static/editor-ui.js", "/static/feedback-ui.js"}:
                if not self.allowed(): self.send_json({"error":"会话已失效。"},403);return
                path=ROOT / "mdoc_check" / "static" / parsed.path.rsplit("/",1)[1]
                if not path.is_file(): self.send_json({"error":"编辑器组件不存在。"},404);return
                data=path.read_bytes();self.send_response(200);self.send_header("Content-Type","text/javascript; charset=utf-8");self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data);return
            if parsed.path in {"/", "/check", "/check/"}:
                if not self.allowed(): self.send_json({"error":"会话已失效。"},403);return
                data=HTML.replace("</style>",PDF_STYLE+"</style>").replace('<button id="recheck">重新检查原范围</button>','<button id="recheck">重新检查原范围</button><button id="build-book-pdf">生成整册 PDF</button><select id="book-pdf-actions"><option value="">整册 PDF 操作</option><option value="browser">浏览器打开上次结果</option><option value="default">Windows 默认程序</option><option value="save">另存为</option><option value="folder">打开所在文件夹</option></select><a href="/feedback/?token=__TOKEN__">手册反馈修订</a>').replace("__TOKEN__",token).encode("utf-8");self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data);return
            if parsed.path in {"/feedback", "/feedback/"}:
                if not self.allowed(): self.send_json({"error":"会话已失效。"},403);return
                from .feedback_web import HTML as feedback_html
                data=feedback_html.replace("__TOKEN__",token).encode("utf-8");self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data);return
            if not self.allowed(): self.send_json({"error":"会话已失效。"},403);return
            try:
                report_id=q.get("report",q.get("id",[""]))[0]
                if parsed.path=="/api/server": value={"workspace":str(workspace)}
                elif parsed.path=="/api/feedback/context":
                    from .feedback import ensure_configuration,feedback_context
                    ensure_configuration(workspace);value=feedback_context(workspace)
                elif parsed.path=="/api/feedback/file":
                    from .feedback import load_markdown
                    value=load_markdown(workspace,q["book"][0],q["locale"][0],q["path"][0])
                elif parsed.path=="/api/feedback/locales":
                    from .feedback import locale_files
                    value=locale_files(workspace,q["book"][0],q["path"][0])
                elif parsed.path=="/api/feedback/images":
                    value=feedback_images.list(q["book"][0],q["locale"][0],q["path"][0])
                elif parsed.path=="/api/feedback/image-pending": value={"pending":feedback_images.has_pending()}
                elif parsed.path=="/api/feedback/image-candidate": value=feedback_images.info(q["key"][0])
                elif parsed.path=="/api/feedback/image-candidate-file":
                    path=feedback_images.path(q["key"][0]);data=path.read_bytes();content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream";self.send_response(200);self.send_header("Content-Type",content_type);self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data);return
                elif parsed.path=="/api/feedback/providers":
                    from .feedback_translation import load_providers
                    value=load_providers(workspace)
                elif parsed.path=="/api/feedback/translation-capabilities":
                    from .feedback_translation_draft import capabilities
                    value=capabilities(workspace)
                elif parsed.path=="/api/feedback/provider-models":
                    from .feedback_translation import provider_models
                    value=provider_models(workspace,q["provider"][0])
                elif parsed.path=="/api/feedback/pdf-context": value=pdf_preview.feedback_context(q["book"][0],q["locale"][0],q["path"][0])
                elif parsed.path=="/api/feedback/resource":
                    from .feedback import resource_path
                    path=resource_path(workspace,q["book"][0],q["locale"][0],q["path"][0],q["resource"][0]);data=path.read_bytes();content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                    self.send_response(200);self.send_header("Content-Type",content_type);self.send_header("Content-Length",str(len(data)));self.send_header("X-Content-Type-Options","nosniff");self.end_headers();self.wfile.write(data);return
                elif parsed.path=="/api/reports": value=_reports(workspace)
                elif parsed.path=="/api/report": value=_report(workspace,report_id)
                elif parsed.path=="/api/files": value=_file_page(workspace,report_id,int(q.get("offset",["0"])[0]),int(q.get("limit",["20"])[0]),q.get("q",[""])[0],q.get("severity",[""])[0],q.get("state",["active"])[0],q.get("path"),q.get("query_scope",["all"])[0])
                elif parsed.path=="/api/file": record,findings=file_record(_report_path(workspace,report_id),q["path"][0]);value={**record,"source_changed":source_changed(workspace,_report(workspace,report_id),record),"findings":[{**f,"help":_help(f)} for f in findings]}
                elif parsed.path=="/api/source": value=_source(workspace,_report(workspace,report_id),q["path"][0],int(q.get("line",["1"])[0]))
                elif parsed.path=="/api/resource-info": value=_resource_info(workspace,_report(workspace,report_id),q["path"][0])
                elif parsed.path=="/api/report-resource":
                    path=_source_path(workspace,_report(workspace,report_id),q["path"][0]);data=path.read_bytes();content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                    self.send_response(200);self.send_header("Content-Type",content_type);self.send_header("Content-Length",str(len(data)));self.send_header("X-Content-Type-Options","nosniff");self.end_headers();self.wfile.write(data);return
                elif parsed.path=="/api/editor/source": value=_edit_source(workspace,_report(workspace,report_id),q["path"][0])
                elif parsed.path=="/api/editor/auto-fix-config":
                    from .autofix import configuration
                    try: value={"enabled":configuration(workspace)["enabled"]}
                    except ValueError as exc:value={"enabled":False,"error":str(exc)}
                elif parsed.path=="/api/preview":
                    from .preview import render
                    value=render(_source_path(workspace,_report(workspace,report_id),q["path"][0]))
                elif parsed.path=="/api/preview-link": value=_preview_link(workspace,_report(workspace,report_id),q["path"][0],q["target"][0])
                elif parsed.path=="/api/preview-resource":
                    path=_preview_resource_path(workspace,_report(workspace,report_id),q["path"][0],q["resource"][0]);data=path.read_bytes();content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                    self.send_response(200);self.send_header("Content-Type",content_type);self.send_header("Content-Length",str(len(data)));self.send_header("X-Content-Type-Options","nosniff");self.end_headers();self.wfile.write(data);return
                elif parsed.path=="/api/global": value=global_record(_report_path(workspace,report_id))
                elif parsed.path=="/api/dictionary": value=_dictionary_context(workspace,report_id,q["finding"][0],q.get("path",[None])[0])
                elif parsed.path=="/api/pdf/environment": value=pdf_preview.doctor(q.get("refresh",["0"])[0]=="1")
                elif parsed.path=="/api/pdf/context": value=pdf_preview.context(report_id,_report(workspace,report_id),q["path"][0])
                elif parsed.path=="/api/pdf/book-context": value=pdf_preview.book_preview_context(report_id,_report(workspace,report_id))
                elif parsed.path=="/api/pdf/status": value=pdf_preview.status()
                elif parsed.path=="/api/pdf/file":
                    path=pdf_preview.pdf_path(q["kind"][0],q["key"][0]);data=path.read_bytes();self.send_response(200);self.send_header("Content-Type","application/pdf");self.send_header("Content-Disposition",f'inline; filename="{path.name}"');self.send_header("Cache-Control","no-store");self.send_header("X-Content-Type-Options","nosniff");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data);return
                else: self.send_json({"error":"未找到接口。"},404);return
                self.send_json(value)
            except OSError as exc:
                if not disconnected(exc): self.send_json({"error":str(exc)},400)
            except (KeyError,ValueError,json.JSONDecodeError) as exc:self.send_json({"error":str(exc)},400)
            except Exception:self.send_json({"error":"操作执行失败，请重试。"},500)
        def do_POST(self):
            if not self.allowed(): self.send_json({"error":"会话已失效。"},403);return
            try:
                length=int(self.headers.get("Content-Length","0"))
                if length>30*1024*1024:raise ValueError("请求内容过大。")
                body=json.loads(self.rfile.read(length) or b"{}")
                if self.path=="/api/editor/select":
                    from .editor import select_editor
                    value=select_editor()
                elif self.path=="/api/feedback/search": value=feedback_index.search(body["book"],body["locale"],body["query"],rebuild=bool(body.get("rebuild")))
                elif self.path=="/api/feedback/save":
                    from .feedback import save_markdown
                    value=save_markdown(workspace,body["book"],body["locale"],body["path"],body["content"],body["state"],bool(body.get("force")))
                    if value.get("saved"): feedback_index.update(body["book"],body["locale"],body["path"]);pdf_preview.mark_stale(f'{body["locale"]}/{body["path"]}')
                elif self.path=="/api/feedback/preview":
                    from .feedback import load_markdown
                    from .preview import render_text
                    load_markdown(workspace,body["book"],body["locale"],body["path"]);value=render_text(body["content"])
                elif self.path=="/api/feedback/image-candidate": value=feedback_images.create(body["session"],body["book"],body["locale"],body["path"],body["reference"])
                elif self.path=="/api/feedback/image-copy-locale": value=feedback_images.copy_locale(body["session"],body["book"],body["locale"],body["path"],body["reference"],body["source_locale"])
                elif self.path=="/api/feedback/image-upload":
                    import base64
                    data=base64.b64decode(body["data"],validate=True);value=feedback_images.upload(body["session"],body["book"],body["locale"],body["path"],body["reference"],data,body["suffix"])
                elif self.path=="/api/feedback/image-editor": value=feedback_images.open_editor(body["key"])
                elif self.path=="/api/feedback/image-discard": value=feedback_images.discard(body["key"])
                elif self.path=="/api/feedback/image-save": value=feedback_images.save(body["key"]);pdf_preview.mark_stale(f'{body["locale"]}/{body["path"]}')
                elif self.path=="/api/feedback/image-revert": value=feedback_images.revert(body["book"],body["locale"],body["path"],body["reference"],bool(body.get("force")));pdf_preview.mark_stale(f'{body["locale"]}/{body["path"]}')
                elif self.path=="/api/feedback/providers":
                    from .feedback_translation import save_providers
                    value=save_providers(workspace,body,body.get("revision"))
                elif self.path=="/api/feedback/translate":
                    from .feedback_translation import codex_translate,translate
                    from .feedback import configuration
                    timeout=int(configuration(workspace)["translation"]["timeout_seconds"]);value=codex_translate(body["source_locale"],body["targets"],body["text"],timeout,title=body.get("title",""),context=body.get("context","")) if body.get("method")=="codex" else translate(workspace,body["provider"],body["source_locale"],body["targets"],body["text"],body.get("title",""),body.get("context",""),timeout)
                elif self.path=="/api/feedback/translation-prepare":
                    from .feedback_translation_draft import prepare
                    value=prepare(workspace,body["book"],body["source_locale"],body["path"],int(body["from"]),int(body["to"]))
                elif self.path=="/api/feedback/translation-validate":
                    from .feedback_translation_draft import validate
                    value=validate(body["source"],body["candidate"])
                elif self.path=="/api/feedback/translation-method":
                    from .feedback_translation_draft import remember_method
                    value=remember_method(workspace,body["method"])
                elif self.path=="/api/feedback/translation-commit":
                    from .feedback_translation_draft import commit
                    value=commit(workspace,body["book"],body["source"],body["targets"]);[feedback_index.update(body["book"],item["locale"],item["path"]) for item in value["files"]]
                elif self.path=="/api/feedback/provider-test":
                    from .feedback_translation import test_provider
                    value=test_provider(workspace,body["provider"])
                elif self.path=="/api/feedback/pdf-build": value=pdf_preview.start_feedback(body["book"],body["locale"],body["path"],body.get("origin",{}))
                elif self.path=="/api/editor/save":
                    report=_report(workspace,body["report"]);value=_save_source(workspace,report,body["path"],body["content"],body["state"],body.get("trailing_newline"))
                    if value["saved"]: pdf_preview.mark_stale(body["path"])
                elif self.path=="/api/editor/auto-fix":
                    from .autofix import run as auto_fix
                    report=_report(workspace,body["report"]);path=_editable_path(workspace,report,body["path"]);_record,findings=file_record(_report_path(workspace,body["report"]),body["path"]);active=[item for item in findings if item.get("ignore_id")]
                    value=auto_fix(workspace,report,body["path"],path,body["content"],body["state"],_file_state(path),active,findings)
                elif self.path=="/api/editor/preview":
                    from .preview import render_text
                    if not isinstance(body.get("content"),str) or len(body["content"].encode("utf-8"))>MAX_EDIT_BYTES:raise ValueError("Markdown 内容不能超过 10 MiB。")
                    _editable_path(workspace,_report(workspace,body["report"]),body["path"]);value=render_text(body["content"])
                elif self.path=="/api/editor/open":
                    from .editor import open_source
                    source=_source_path(workspace,_report(workspace,body["report"]),body["path"]);value=open_source(source,int(body.get("line",1)),int(body.get("column",1)),body.get("mode","remembered"))
                elif self.path=="/api/resource/open-default":
                    from .editor import open_default
                    source=_source_path(workspace,_report(workspace,body["report"]),body["path"]);value=open_default(source)
                elif self.path=="/api/preferences":
                    from .editor import load_preferences,save_preferences
                    value=load_preferences();value.setdefault("ui",{})["files_per_page"]=body["files_per_page"];value=save_preferences(value)
                elif self.path in {"/api/dictionary/add","/api/dictionary/remove"}:
                    from .dictionaries import add,remove
                    context=_dictionary_context(workspace,body["report"],body["finding_id"],body.get("path"));language=context["language"] if body["scope"]=="language" else None
                    if body["scope"] not in {"workspace","language"}:raise ValueError("未知词典范围。")
                    word=context["word"] if self.path.endswith("/add") else body["word"]
                    value={"words":add(workspace,word,language) if self.path.endswith("/add") else remove(workspace,word,language)}
                elif self.path=="/api/recheck":
                    from .core import run,store
                    from .maintenance import activity, report_root as maintenance_root
                    report=_report(workspace,body["report"]);scope=report["scope"];kind=scope["kind"]
                    with activity(maintenance_root(workspace)/".check.active.json","recheck"):
                        value=run(workspace,scope.get("book"),scope.get("locale"),kind,scope.get("target"),report.get("level","basic"),report.get("options",{}).get("internal_only",False),None,scope.get("task") if kind=="task" else None,None,False);store(value,workspace);value={"status":value["status"],"counts":value["counts"]}
                elif self.path=="/api/check-selected":
                    from .core import run_selected
                    from .maintenance import activity, report_root as maintenance_root
                    report=_report(workspace,body["report"]);selection={"schema_version":1,"kind":"mdoc_check_file_selection","source_report":body["report"],"base_revision":report["revision"],"files":body["files"]}
                    with activity(maintenance_root(workspace)/".check.active.json","selected-check"):
                        with tempfile.TemporaryDirectory(prefix="selection-",dir=_report_path(workspace,body["report"]).parent) as temporary:
                            path=Path(temporary)/"selection.json";path.write_text(json.dumps(selection,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");value=run_selected(workspace,path,report.get("level","basic"),report.get("options",{}).get("internal_only",False))
                elif self.path=="/api/ignore":
                    from .ignores import add
                    from .reports import set_suppression
                    report,finding=_finding(workspace,body["report"],body["finding_id"]);language=None
                    record=next(item for item in file_records(_report_path(workspace,body["report"])) if item["path"]==finding["path"])
                    if source_changed(workspace,report,record): raise ValueError("源文件在本次检查后已发生变化，请先重新检查该文件。")
                    if body.get("language_override"):
                        locale=finding["path"].split("/",1)[0];book_id=finding.get("book") or report["scope"].get("book");language=load_workspace(workspace)["books"][book_id]["locales"][locale]["language"]
                    ignored=add(workspace,finding,language,body.get("reason"));set_suppression(_report_path(workspace,body["report"]),finding["finding_id"],True,ignored["id"]);value=ignored
                elif self.path=="/api/ignore/delete":
                    from .ignores import remove
                    from .reports import set_suppression
                    report_id=body["report"];report_path=_report_path(workspace,report_id);target=None
                    for record in file_records(report_path):
                        _meta,findings=file_record(report_path,record["path"]);target=next((item for item in findings if item.get("ignore_id")==body["ignore_id"]),None)
                        if target:break
                    remove(workspace,body["ignore_id"])
                    if target:set_suppression(report_path,target["finding_id"],False)
                    value={"status":"deleted"}
                elif self.path=="/api/pdf/build":
                    report=_report(workspace,body["report"]);value=pdf_preview.start(body["report"],report,body.get("path"),body["scope"],body.get("occurrence"),body.get("origin",{}))
                elif self.path=="/api/pdf/doctor": value=pdf_preview.doctor(True)
                elif self.path=="/api/pdf/open-default": value=pdf_preview.open_default(body["kind"],body["key"])
                elif self.path=="/api/pdf/open-folder": value=pdf_preview.open_folder(body["kind"],body["key"])
                elif self.path=="/api/pdf/save-as": value=pdf_preview.save_as(body["kind"],body["key"])
                else:self.send_json({"error":"未找到接口。"},404);return
                self.send_json(value)
            except OSError as exc:
                if not disconnected(exc): self.send_json({"error":str(exc)},400)
            except (KeyError,ValueError,json.JSONDecodeError) as exc:self.send_json({"error":str(exc)},400)
            except Exception:self.send_json({"error":"操作执行失败，请重试。"},500)
        def log_message(self,*_):pass
    server=Server((host,port),Handler);server.mdoc_token=token;server.mdoc_pdf_preview=pdf_preview;server.mdoc_feedback_index=feedback_index;server.mdoc_feedback_images=feedback_images;return server


def serve(workspace: Path, host: str, port: int, open_browser: bool = True, page: str = "check") -> str:
    workspace=workspace.resolve()
    from .maintenance import cleanup, configuration
    maintained=cleanup(workspace,automatic=True); threshold=configuration(workspace)["notify_freed_bytes"]
    if maintained["bytes"]>=threshold: print(f"缓存维护完成：删除 {maintained['directories']} 个目录、{maintained['files']} 个文件，释放 {maintained['bytes']/1048576:.2f} MiB。",flush=True)
    for warning in maintained["warnings"]: print("缓存维护警告："+warning,flush=True)
    reused=_reuse_server(workspace,open_browser,page)
    if reused: print(f"已复用正在运行的报告服务: {reused}",flush=True);return reused
    server=create_server(workspace,host,port);base_url=f"http://{host}:{server.server_address[1]}";url=f"{base_url}/{'feedback/' if page == 'feedback' else 'check/'}?token={server.mdoc_token}";state_path=_server_state_path(workspace);state_path.parent.mkdir(parents=True,exist_ok=True);state_path.write_text(json.dumps({"workspace":str(workspace),"url":base_url,"token":server.mdoc_token},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("mdoc 检查报告服务已启动",flush=True);print(f"工作区: {workspace}",flush=True);print(f"报告目录: {report_root(workspace)}",flush=True);print(f"访问地址: {url}",flush=True);print("关闭此窗口或按 Ctrl+C 可停止报告服务。",flush=True)
    if open_browser: threading.Timer(.2,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.server_close();server.mdoc_feedback_images.close()
        try:
            state=json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("token")==server.mdoc_token:state_path.unlink(missing_ok=True)
        except (OSError,json.JSONDecodeError):pass
    return url
