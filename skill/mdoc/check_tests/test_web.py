from __future__ import annotations

import json
import socket
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import ProxyHandler, Request, build_opener
from urllib.error import HTTPError
from urllib.parse import quote

from PIL import Image
from ruamel.yaml import YAML

from mdoc_check.core import store
from mdoc_check.ignores import annotate
from mdoc_check.model import finding
from mdoc_check.web import _edit_source, _file_page, _reuse_server, _save_source, create_server, report_root
from mdoc_check.feedback import SearchIndex, image_references, load_markdown, save_markdown
from mdoc_check.web import _source

HTTP = build_opener(ProxyHandler({}))


class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "manual"
        locale = self.workspace / "Guide" / "en"
        (locale / "Main").mkdir(parents=True)
        (locale / "Main" / "Page.md").write_text("# Page\n\nBroken text.\n", encoding="utf-8")
        (locale / "Summary.md").write_text("# Summary\n", encoding="utf-8")
        (self.workspace / ".mdoc").mkdir()
        config = {"schema_version": 1, "workspace": {"id": f"web-test-{Path(self.temp.name).name}"}, "books": {"guide": {"root": "Guide", "source_locale": "en", "locales": {"en": {"root": "en", "language": "en"}}, "content_root": "Main", "assets_root": "images", "navigation": {"summary": "Summary.md"}}}}
        stream = __import__("io").StringIO(); YAML().dump(config, stream)
        (self.workspace / ".mdoc" / "workspace.yaml").write_text(stream.getvalue(), encoding="utf-8")
        findings = [finding("sample.rule", "error", "en/Main/Page.md", "Broken.", 3, 1)]
        annotate(findings, {"Main/Page.md": locale / "Main" / "Page.md"})
        report = {"schema_version": 1, "kind": "mdoc_check_report", "context": "book", "scope": {"kind": "page", "book": "guide", "locale": "en", "target": "Main/Page.md"}, "status": "blocked", "findings": findings, "inputs": {"markdown": ["en/Main/Page.md"]}, "counts": {"effective_errors": 1, "effective_warnings": 0}, "created_at": 1}
        store(report, self.workspace)
        self.server = create_server(self.workspace)
        self.token = self.server.mdoc_token
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        shutil.rmtree(report_root(self.workspace), ignore_errors=True)
        self.temp.cleanup()

    def get(self, path: str):
        request = Request(self.url + path, headers={"X-Mdoc-Token": self.token})
        with HTTP.open(request) as response:
            return response.headers.get_content_type(), response.read().decode("utf-8")

    def post(self, path: str, value: dict) -> dict:
        request = Request(self.url + path, data=json.dumps(value).encode("utf-8"), headers={"Content-Type": "application/json", "X-Mdoc-Token": self.token}, method="POST")
        with HTTP.open(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_report_center_lists_report_and_source_context(self) -> None:
        content_type, html = self.get("/")
        editor_ui = self.get("/static/editor-ui.js")[1]
        self.assertEqual("text/html", content_type)
        self.assertIn("mdoc 检查报告", html)
        self.assertIn("pageSize=document.getElementById('page-size')", html)
        reports = json.loads(self.get("/api/reports")[1])
        self.assertEqual("blocked", reports[0]["status"])
        report_id = reports[0]["id"].replace("/", "%2F")
        report = json.loads(self.get("/api/report?id=" + report_id)[1])
        self.assertIn("generation", report)
        page = json.loads(self.get("/api/files?report=" + report_id + "&state=all")[1])
        self.assertEqual("en/Main/Page.md", page["items"][0]["path"])
        detail = json.loads(self.get("/api/file?report=" + report_id + "&path=en%2FMain%2FPage.md")[1])
        self.assertEqual("sample.rule", detail["findings"][0]["rule"])
        self.assertEqual("暂无中文说明", detail["findings"][0]["help"]["title"])
        source = json.loads(self.get("/api/source?report=" + report_id + "&path=en%2FMain%2FPage.md&line=3")[1])
        self.assertEqual(1, source["first_line"])
        self.assertEqual("Broken text.", source["lines"][2])
        self.assertIn("Markdown 预览", html)
        self.assertIn("生成整册 PDF", html)
        self.assertIn("整册 PDF 操作", html)
        self.assertIn("let detailRequest=0", html)
        self.assertIn("fileRequest=0", html)
        self.assertIn('id="query-scope"', html)
        self.assertIn('<option value="path">文件路径</option>', html)
        self.assertIn('<option value="rule">规则编号</option>', html)
        self.assertIn('<option value="message">问题消息</option>', html)
        self.assertIn("query_scope:queryScope.value", html)
        self.assertIn("const request=++fileRequest", html)
        self.assertIn("if(request!==fileRequest)return", html)
        self.assertIn("if(request!==detailRequest)return", html)
        self.assertIn("查看规则原文", html)
        self.assertIn('class="help-head"', html)
        self.assertIn('class="help-grid"', html)
        self.assertIn('class="help-example"', html)
        self.assertIn("@media(max-width:900px)", html)
        self.assertIn('className=\'column-hit\'', html)
        self.assertIn("currentFinding.end_column||currentFinding.column", html)
        self.assertIn("columnObserver.observe(detail", html)
        self.assertIn("detail.scrollTop=0", html)
        self.assertIn("event.preventDefault()", html)
        self.assertIn("/api/preview-link?report=", html)
        self.assertIn("返回源文档", editor_ui)
        self.assertIn("previewOrigin = {path: source, back: () => renderPreview(source)}", editor_ui)
        self.assertIn("session.previewContent = null; renderPreview(link.path, link.fragment, {path: session.path, back: previewEditor})", editor_ui)
        self.assertIn("p.classList.add('standalone-image')", html)
        self.assertIn("p.classList.add('standalone-image')", editor_ui)
        self.assertIn(".standalone-image>img", html)
        self.assertIn(".standalone-image>img", editor_ui)
        self.assertIn("img:not([height]){height:auto}", html)
        self.assertIn("img:not([height]){height:auto}", editor_ui)
        self.assertNotIn("img{max-width:100%;height:auto}", html)
        self.assertNotIn("img{max-width:100%;height:auto}", editor_ui)
        self.assertNotIn("p>img:only-child", html)
        self.assertNotIn("p>img:only-child", editor_ui)
        self.assertNotIn("destroyEditor(); renderPreview(link.path, link.fragment)", editor_ui)
        self.assertNotIn("编辑文件", editor_ui)
        self.assertNotIn("showFileEditor", editor_ui)
        self.assertIn("session?.path !== path && dirty()", editor_ui)
        self.assertIn("打开并定位", html)
        self.assertNotIn("Markdown 编辑器已更新", html)
        self.assertIn('class="splitter"', html)
        self.assertIn("blockquote{", html)
        self.assertNotIn("background:#fff2c7", html)
        self.assertIn('id="previous-page"', html)
        self.assertIn('id="pager-info"', html)
        self.assertIn('id="next-page"', html)
        self.assertIn('id="clear-focus"', html)
        self.assertNotIn("document.createTextNode(`${p.total?", html)
        self.assertIn("mode==='remembered'&&e.message.includes('尚未选择有效的 Markdown 编辑器')", html)
        self.assertIn("focusPaths.add(x)", html)
        self.assertIn("openMenu.value=''", html)
        self.assertIn("document.getElementById('recheck').onclick=recheck", html)
        self.assertNotIn("请手动前往第", html)
        self.assertIn("f.ignore_id?'ignored'", html)
        self.assertIn("已忽略 ·", html)
        self.assertIn("取消忽略", html)
        self.assertIn("toggleIgnore", html)
        self.assertIn("function setChecking(active)", html)
        self.assertIn("if(checking)return", html)
        self.assertIn('id="notice"', html)
        self.assertIn('id="notice-close"', html)
        self.assertIn("document.getElementById('notice-close').onclick=closeNotice", html)
        self.assertIn("function notify(type,message,details='')", html)
        self.assertIn("function reportLabel(r=currentReportValue)", html)
        self.assertIn("function selectedLabel(paths)", html)
        self.assertIn("正在重新检查报告【", html)
        self.assertIn("原范围重新检查完成", html)
        self.assertIn("所选文件检查完成", html)
        self.assertIn('<script src="/static/editor.js?token=', html)
        self.assertIn('<script src="/static/editor-ui.js?token=', html)
        self.assertIn("editor-box", html)
        self.assertIn(".notice.success", html)
        self.assertIn(".notice.warning", html)
        self.assertIn(".notice.error", html)
        self.assertNotIn("alert(", html)
        self.assertNotIn("confirm(", html)
        editor_ui = (Path(__file__).parents[1] / "mdoc_check" / "static" / "editor-ui.js").read_text(encoding="utf-8")
        editor_entry = (Path(__file__).parents[1] / "tools" / "editor-entry.js").read_text(encoding="utf-8")
        self.assertIn("文件编辑", editor_ui)
        self.assertIn("源文件", editor_ui)
        self.assertIn("保存并检查当前文件", editor_ui)
        self.assertIn("保存并重新检查原范围", editor_ui)
        self.assertIn("当前文件有未保存修改", editor_ui)
        self.assertIn("磁盘已保存版本 · 只读", editor_ui)
        self.assertIn("readOnly: true", editor_ui)
        self.assertIn("select(comparison.editor, finding)", editor_ui)
        self.assertIn("comparison.editor.getSelection()", editor_ui)
        self.assertIn("session.editor.getSelection()", editor_ui)
        self.assertIn("mappedSelection", editor_ui)
        self.assertIn("自动修复当前文件", editor_ui)
        self.assertIn("自动修复后又有手动修改", editor_ui)
        self.assertIn("展开详情", editor_ui)
        self.assertIn("收起详情", editor_ui)
        self.assertIn("activeView", editor_ui)
        self.assertIn("view-more", editor_ui)
        self.assertIn("previewContent", editor_ui)
        self.assertIn("修改原因：", editor_ui)
        self.assertIn("修改方式：", editor_ui)
        self.assertIn("function destroyEditor() {\n    clearAutoFix();", editor_ui)
        self.assertIn("/api/editor/auto-fix", editor_ui)
        self.assertIn("cm-auto-fix", html)
        self.assertIn(".cm-content .cm-finding-hit{background:#ffd666!important", html)
        self.assertIn(".cm-content .cm-line.cm-finding-line{background:#ffe58a!important", html)
        self.assertIn("setFinding", editor_ui)
        self.assertIn("setCursorOffset", editor_entry)
        self.assertIn('\".cm-finding-hit\": {backgroundColor: \"#ffd666 !important\"', editor_entry)
        self.assertIn('\".cm-line.cm-finding-line\": {backgroundColor: \"#ffe58a !important\"', editor_entry)
        self.assertIn("position.from === position.to", editor_ui)
        self.assertIn("coords.top >= bounds.top && coords.bottom <= bounds.bottom", editor_entry)
        self.assertIn("fixing", editor_ui)
        self.assertIn("beforeunload", editor_ui)
        self.assertIn("PDF 预览", editor_ui)
        self.assertIn("/api/pdf/build", editor_ui)
        self.assertIn("返回发起位置", editor_ui)
        self.assertNotIn("Ctrl+S", editor_ui)

    def test_special_character_markdown_and_image_paths_load(self) -> None:
        locale = self.workspace / "Guide" / "en"
        markdown = locale / "Main" / "DBH&Height + File_Name.md"
        markdown.write_text("# Special\n", encoding="utf-8")
        image = locale / "images" / "Show_Hide + File.png"
        image.parent.mkdir()
        Image.new("RGB", (3, 2), "white").save(image)
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        source = json.loads(self.get("/api/editor/source?report=" + quote(report_id, safe="") + "&path=" + quote("en/Main/DBH&Height + File_Name.md", safe=""))[1])
        resource = json.loads(self.get("/api/resource-info?report=" + quote(report_id, safe="") + "&path=" + quote("en/images/Show_Hide + File.png", safe=""))[1])
        self.assertEqual("# Special\n", source["content"])
        self.assertEqual((True, 3, 2, "PNG"), (resource["image"], resource["width"], resource["height"], resource["format"]))
        request = Request(self.url + "/api/report-resource?report=" + quote(report_id, safe="") + "&path=" + quote("en/images/Show_Hide + File.png", safe=""), headers={"X-Mdoc-Token": self.token})
        with HTTP.open(request) as response:
            self.assertEqual("image/png", response.headers.get_content_type())
            self.assertEqual(image.read_bytes(), response.read())

    def test_editor_static_assets_are_served_locally(self) -> None:
        for name in ("editor.js", "editor-ui.js"):
            with HTTP.open(self.url + "/static/" + name + "?token=" + self.token) as response:
                self.assertEqual("text/javascript", response.headers.get_content_type())
                self.assertGreater(len(response.read()), 100)

    def test_pdf_preview_routes_delegate_to_report_manager(self) -> None:
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        manager = self.server.mdoc_pdf_preview
        with patch.object(manager, "context", return_value={"enabled": True, "key": "a" * 16}) as context:
            value = json.loads(self.get("/api/pdf/context?report=" + report_id.replace("/", "%2F") + "&path=en%2FMain%2FPage.md")[1])
        self.assertTrue(value["enabled"]); context.assert_called_once()
        with patch.object(manager, "start", return_value={"status": "waiting"}) as start:
            value = self.post("/api/pdf/build", {"report": report_id, "path": "en/Main/Page.md", "scope": "page", "occurrence": "0", "origin": {"view": "pdf"}})
        self.assertEqual("waiting", value["status"]); start.assert_called_once()

    def test_pdf_file_route_serves_only_manager_resolved_pdf(self) -> None:
        path = self.workspace / "preview.pdf"; path.write_bytes(b"%PDF-1.4\n")
        with patch.object(self.server.mdoc_pdf_preview, "pdf_path", return_value=path):
            request = Request(self.url + "/api/pdf/file?kind=file&key=" + "a" * 16, headers={"X-Mdoc-Token": self.token})
            with HTTP.open(request) as response:
                self.assertEqual("application/pdf", response.headers.get_content_type()); self.assertEqual("no-store", response.headers["Cache-Control"]); self.assertEqual(path.read_bytes(), response.read())

    def test_cancelled_preview_resource_request_does_not_report_server_error(self) -> None:
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        image = self.workspace / "Guide" / "en" / "Main" / "media" / "Large.png"
        image.parent.mkdir(); image.write_bytes(b"x" * (4 * 1024 * 1024))
        path = "/api/preview-resource?token=" + self.token + "&report=" + report_id.replace("/", "%2F") + "&path=en%2FMain%2FPage.md&resource=media%2FLarge.png"
        from http.server import ThreadingHTTPServer
        with patch.object(ThreadingHTTPServer, "handle_error") as handle_error:
            client = socket.create_connection(self.server.server_address)
            client.sendall(("GET " + path + " HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n").encode("ascii"))
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, __import__("struct").pack("ii", 1, 0)); client.close()
            __import__("time").sleep(.2)
        handle_error.assert_not_called()

    def test_running_report_server_can_be_reused_for_the_same_workspace(self) -> None:
        state = {"workspace": str(self.workspace.resolve()), "url": self.url, "token": self.token}
        state_path = self.workspace / ".mdoc" / "runtime" / "report-server.json"; state_path.parent.mkdir(parents=True)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with patch("mdoc_check.web.webbrowser.open") as opened:
            reused = _reuse_server(self.workspace, True)
        self.assertEqual(f"{self.url}/check/?token={self.token}", reused)
        opened.assert_called_once_with(reused)

    def test_report_server_status_identifies_its_workspace(self) -> None:
        status = json.loads(self.get("/api/server")[1])
        self.assertEqual(str(self.workspace.resolve()), status["workspace"])

    def test_editor_picker_runtime_error_returns_json_instead_of_dropping_connection(self) -> None:
        with patch("mdoc_check.editor.select_editor", side_effect=RuntimeError("main thread is not in main loop")):
            with self.assertRaises(HTTPError) as raised:
                self.post("/api/editor/select", {})
        self.assertEqual(500, raised.exception.code)
        self.assertEqual({"error": "操作执行失败，请重试。"}, json.loads(raised.exception.read().decode("utf-8")))

    def test_recheck_runs_and_stores_the_original_scope(self) -> None:
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        refreshed = {"schema_version": 1, "kind": "mdoc_check_report", "context": "book", "scope": {"kind": "page", "book": "guide", "locale": "en", "target": "Main/Page.md"}, "status": "passed", "findings": [], "inputs": {"markdown": ["en/Main/Page.md"]}, "counts": {"effective_errors": 0, "effective_warnings": 0}, "created_at": 2}
        with patch("mdoc_check.core.run", return_value=refreshed) as run, patch("mdoc_check.core.store", return_value={"path": "stored"}) as store_report:
            result = self.post("/api/recheck", {"report": report_id})
        run.assert_called_once_with(self.workspace.resolve(), "guide", "en", "page", "Main/Page.md", "basic", False, None, None, None, False)
        store_report.assert_called_once_with(refreshed, self.workspace.resolve())
        self.assertEqual({"status": "passed", "counts": {"effective_errors": 0, "effective_warnings": 0}}, result)

    def test_known_rule_has_chinese_help(self) -> None:
        from mdoc_check.web import _help

        issue = finding("markdown.md003", "error", "en/Main/Page.md", "Expected: atx; Actual: atx_closed", 1, 1)
        help_value = _help(issue)
        self.assertEqual("标题格式不符合要求", help_value["title"])
        self.assertEqual("# Annotation", help_value["after"])

    def test_all_current_rules_have_chinese_help_and_markdownlint_links(self) -> None:
        from mdoc_check.web import _help

        current = [
            "html.block-blank-line", "html.table-structure", "image.extension-matches-format",
            "markdown.md001", "markdown.md004", "markdown.md005", "markdown.md010", "markdown.md012",
            "markdown.md018", "markdown.md022", "markdown.md023", "markdown.md025", "markdown.md032",
            "markdown.md034", "markdown.md037", "markdown.md038", "markdown.md040",
            "navigation.duplicate-entry", "navigation.page-linked", "navigation.title-matches-h1",
            "path.ascii-only", "path.case-exact", "path.no-local-absolute", "path.resource-ascii-only", "resource.referenced",
            "terminology.brand-name",
        ]
        for rule in current:
            help_value = _help(finding(rule, "error", "en/Main/Page.md", "Example", 1, 1, checker="markdownlint" if rule.startswith("markdown.md") else "mdoc", native_rule=rule.removeprefix("markdown.").upper()))
            self.assertNotEqual("暂无中文说明", help_value["title"], rule)
            if rule.startswith("markdown.md"):
                self.assertEqual(f"https://github.com/DavidAnson/markdownlint/blob/main/doc/{rule.removeprefix('markdown.')}.md", help_value["url"])

    def test_unknown_markdownlint_rule_uses_official_rule_link(self) -> None:
        from mdoc_check.web import _help

        help_value = _help(finding("markdown.md999", "error", "en/Main/Page.md", "Example", 1, 1, checker="markdownlint", native_rule="MD999"))
        self.assertEqual("暂无中文说明", help_value["title"])
        self.assertEqual("https://github.com/DavidAnson/markdownlint/blob/main/doc/md999.md", help_value["url"])

    def test_markdown_preview_filters_scripts_and_keeps_static_html(self) -> None:
        from mdoc_check.preview import render

        page = self.workspace / "Guide" / "en" / "Main" / "Preview.md"
        page.write_text("# Preview\n\n> Note\n\n<div style=\"display:flex; gap:20px; justify-content:center\"><figure style=\"margin:0; text-align:center\"><img src=\"media/Sample.png\" style=\"width:100%\"><figcaption>Sample</figcaption></figure></div>\n\n<script>alert(1)</script>\n", encoding="utf-8")
        preview = render(page)
        self.assertIn("<blockquote", preview["html"])
        self.assertIn("<figure style=\"margin:0; text-align:center\">", preview["html"])
        self.assertIn("<figcaption>Sample</figcaption>", preview["html"])
        self.assertIn("style=\"display:flex; gap:20px; justify-content:center\"", preview["html"])
        self.assertIn("style=\"width:100%\"", preview["html"])
        self.assertNotIn("<script", preview["html"])
        self.assertGreater(preview["filtered"], 0)

    def test_markdown_preview_preserves_ordered_list_start(self) -> None:
        from mdoc_check.preview import render

        page = self.workspace / "Guide" / "en" / "Main" / "Ordered.md"
        page.write_text("1. **First**\n\n<div align=center>Image</div>\n\n2. **Second**\n", encoding="utf-8")
        preview = render(page)
        self.assertIn('<ol start="2"', preview["html"])

    def test_markdown_preview_adds_honkit_heading_anchor_ids(self) -> None:
        from mdoc_check.preview import render

        page = self.workspace / "Guide" / "en" / "Main" / "Anchor.md"
        page.write_text("# API\n\n## `classify_classify_by_csf`\n", encoding="utf-8")
        preview = render(page)
        self.assertIn('id="classify_classify_by_csf"', preview["html"])

    def test_markdown_preview_serves_relative_images(self) -> None:
        media = self.workspace / "Guide" / "en" / "Main" / "media"
        media.mkdir()
        image = media / "Sample.png"
        Image.new("RGB", (2, 2), "white").save(image)
        page = self.workspace / "Guide" / "en" / "Main" / "Page.md"
        page.write_text("# Page\n\n![Sample](media/Sample.png)\n", encoding="utf-8")
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        preview = json.loads(self.get("/api/preview?report=" + report_id.replace("/", "%2F") + "&path=en%2FMain%2FPage.md")[1])
        self.assertIn("media/Sample.png", preview["html"])
        resource = self.url + "/api/preview-resource?token=" + self.token + "&report=" + report_id.replace("/", "%2F") + "&path=en%2FMain%2FPage.md&resource=media%2FSample.png"
        with HTTP.open(resource) as response:
            self.assertEqual("image/png", response.headers.get_content_type())
            self.assertEqual(image.read_bytes(), response.read())
        with self.assertRaises(HTTPError) as raised:
            HTTP.open(self.url + "/api/preview-resource?token=" + self.token + "&report=" + report_id.replace("/", "%2F") + "&path=en%2FMain%2FPage.md&resource=..%2F..%2Foutside.png")
        self.assertEqual(400, raised.exception.code)

    def test_markdown_preview_resolves_local_pages_and_same_page_anchors(self) -> None:
        index = self.workspace / "Guide" / "en" / "Main" / "Index.md"
        index.write_text("# Index\n", encoding="utf-8")
        page = self.workspace / "Guide" / "en" / "Main" / "Page.md"
        page.write_text("# Page\n\n[Index](./Index.md)\n\n[Section](#section)\n\n## Section\n", encoding="utf-8")
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"].replace("/", "%2F")

        local = json.loads(self.get("/api/preview-link?report=" + report_id + "&path=en%2FMain%2FPage.md&target=.%2FIndex.md")[1])
        anchor = json.loads(self.get("/api/preview-link?report=" + report_id + "&path=en%2FMain%2FPage.md&target=%23section")[1])

        self.assertEqual({"kind": "markdown", "path": "en/Main/Index.md", "fragment": ""}, local)
        self.assertEqual({"kind": "anchor", "path": "en/Main/Page.md", "fragment": "section"}, anchor)

    def test_markdown_preview_rejects_links_outside_locale(self) -> None:
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"].replace("/", "%2F")
        with self.assertRaises(HTTPError) as raised:
            self.get("/api/preview-link?report=" + report_id + "&path=en%2FMain%2FPage.md&target=..%2F..%2Foutside.md")
        self.assertEqual(400, raised.exception.code)

    def test_files_are_paged_and_report_summary_excludes_full_list(self) -> None:
        reports = json.loads(self.get("/api/reports")[1])
        report_id = reports[0]["id"].replace("/", "%2F")
        summary = json.loads(self.get("/api/report?id=" + report_id)[1])
        self.assertNotIn("findings", summary)
        page = json.loads(self.get("/api/files?report=" + report_id + "&offset=0&limit=10&state=all")[1])
        self.assertEqual(1, page["total"])
        self.assertEqual(1, len(page["items"]))

    def test_file_page_supports_allowed_sizes_and_filters_results(self) -> None:
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        with patch("mdoc_check.web.file_record", side_effect=AssertionError("filter must not reload the file index per record")):
            page = json.loads(self.get("/api/files?report=" + report_id.replace("/", "%2F") + "&limit=100&severity=error&q=sample.rule&state=all")[1])
        self.assertEqual(100, page["limit"])
        self.assertEqual(1, page["total"])
        self.assertEqual("en/Main/Page.md", page["items"][0]["path"])

    def test_file_page_filters_by_explicit_query_scope(self) -> None:
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        self.assertEqual(1, _file_page(self.workspace, report_id, query="Page.md", state="all", query_scope="path")["total"])
        self.assertEqual(1, _file_page(self.workspace, report_id, query="sample.rule", state="all", query_scope="rule")["total"])
        self.assertEqual(1, _file_page(self.workspace, report_id, query="Broken", state="all", query_scope="message")["total"])
        self.assertEqual(0, _file_page(self.workspace, report_id, query="sample.rule", state="all", query_scope="path")["total"])
        self.assertEqual(0, _file_page(self.workspace, report_id, query="Broken", state="all", query_scope="rule")["total"])
        with patch("mdoc_check.web.record_findings", side_effect=AssertionError("path search must not load findings")):
            self.assertEqual(1, _file_page(self.workspace, report_id, query="Page.md", state="all", query_scope="path")["total"])
        api_page = json.loads(self.get("/api/files?report=" + report_id.replace("/", "%2F") + "&q=sample.rule&query_scope=rule&state=all")[1])
        self.assertEqual(1, api_page["total"])

    def test_file_page_can_focus_on_explicit_paths(self) -> None:
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        page = _file_page(self.workspace, report_id, query="not-present", severity="warning", state="passed", paths=["en/Main/Page.md"])
        self.assertEqual(["en/Main/Page.md"], [item["path"] for item in page["items"]])
        self.assertEqual(0, _file_page(self.workspace, report_id, state="all", paths=["en/Main/Other.md"])["total"])

    def test_file_page_can_filter_ignored_files(self) -> None:
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        detail = json.loads(self.get("/api/file?report=" + report_id.replace("/", "%2F") + "&path=en%2FMain%2FPage.md")[1])
        self.post("/api/ignore", {"report": report_id, "finding_id": detail["findings"][0]["finding_id"]})
        page = _file_page(self.workspace, report_id, state="ignored")
        self.assertEqual(["en/Main/Page.md"], [item["path"] for item in page["items"]])

    def test_stored_task_report_uses_file_generation_without_duplicate_payloads(self) -> None:
        issue = finding("sample.rule", "error", "en/Main/Page.md", "Broken.", 3, 1)
        report = {"schema_version": 1, "kind": "mdoc_check_report", "context": "task", "scope": {"kind": "task", "task": "compact", "book": "guide", "mode": "coordinator"}, "status": "blocked", "findings": [issue], "inputs": {"markdown": ["en/Main/Page.md"]}, "ignores": [], "units": [{"scope": {"locale": "en"}, "status": "blocked", "findings": [issue], "inputs": {"markdown": ["en/Main/Page.md"]}, "ignores": []}], "counts": {"effective_errors": 1, "effective_warnings": 0}, "created_at": 2}
        stored = store(report, self.workspace)
        archived = json.loads(Path(stored["path"]).read_text(encoding="utf-8"))
        self.assertNotIn("findings", archived)
        self.assertNotIn("units", archived)
        generation = Path(stored["path"]).parent / "generations" / archived["generation"]
        self.assertTrue((generation / "files.json").is_file())
        self.assertEqual([issue], stored["units"][0]["findings"])
        self.assertEqual({"markdown": ["en/Main/Page.md"]}, stored["units"][0]["inputs"])

    def test_task_editor_only_allows_staging_markdown(self) -> None:
        from mdoc_check.tasks import _digest

        directory = self.workspace / ".mdoc" / "tasks" / "edit-task"
        staged = directory / "staging" / "en" / "Main" / "Page.md"
        staged.parent.mkdir(parents=True)
        staged.write_text("# Staged\n", encoding="utf-8")
        definition = {"schema_version": 1, "task": {"id": "edit-task", "book": "guide"}, "manifest": [{"action": "update", "locale": "en", "path": "Main/Page.md", "kind": "page"}]}
        definition["definition_digest"] = _digest(definition)
        stream = __import__("io").StringIO(); YAML().dump(definition, stream)
        (directory / "task.yaml").write_text(stream.getvalue(), encoding="utf-8")
        (directory / "task-state.json").write_text(json.dumps({"schema_version": 1, "task_id": "edit-task", "definition_confirmation": {"digest": definition["definition_digest"]}}, indent=2), encoding="utf-8")
        report = {"context": "task", "scope": {"kind": "task", "task": "edit-task", "book": "guide"}}
        source = _edit_source(self.workspace, report, "en/Main/Page.md")
        _save_source(self.workspace, report, "en/Main/Page.md", "# Changed\n", source["state"])
        self.assertEqual("# Changed\n", staged.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "staging"):
            _edit_source(self.workspace, report, "en/Summary.md")

    def test_task_source_preview_does_not_rebuild_all_candidate_files(self) -> None:
        from mdoc_check.tasks import _digest

        directory = self.workspace / ".mdoc" / "tasks" / "preview-task"
        staged = directory / "staging" / "en" / "Main" / "Page.md"
        staged.parent.mkdir(parents=True)
        staged.write_text("# Staged\n", encoding="utf-8")
        definition = {"schema_version": 1, "task": {"id": "preview-task", "book": "guide"}, "manifest": [{"action": "update", "locale": "en", "path": "Main/Page.md", "kind": "page"}]}
        definition["definition_digest"] = _digest(definition)
        stream = __import__("io").StringIO(); YAML().dump(definition, stream)
        (directory / "task.yaml").write_text(stream.getvalue(), encoding="utf-8")
        (directory / "task-state.json").write_text(json.dumps({"schema_version": 1, "task_id": "preview-task", "definition_confirmation": {"digest": definition["definition_digest"]}}, indent=2), encoding="utf-8")
        report = {"context": "task", "scope": {"kind": "task", "task": "preview-task", "book": "guide"}}
        with patch("mdoc_check.tasks.candidate_files", side_effect=AssertionError("full candidate scan")):
            source = _source(self.workspace, report, "en/Main/Page.md")
        self.assertEqual(["# Staged"], source["lines"])

    def test_report_index_uses_metadata_without_reparsing_report(self) -> None:
        root = report_root(self.workspace)
        latest = next(root.rglob("latest.json"))
        latest.write_text("not json", encoding="utf-8")
        reports = json.loads(self.get("/api/reports")[1])
        self.assertEqual([], reports)

    def test_report_index_skips_old_report_protocol(self) -> None:
        root = report_root(self.workspace)
        old = root / "old"; old.mkdir()
        (old / "latest.json").write_text(json.dumps({"schema_version": 1, "kind": "mdoc_check_report", "status": "passed"}), encoding="utf-8")
        (old / "latest.meta.json").write_text(json.dumps({"schema_version": 1, "kind": "mdoc_check_report", "status": "passed"}), encoding="utf-8")
        reports = json.loads(self.get("/api/reports")[1])
        self.assertEqual(1, len(reports))
        self.assertNotIn("old/latest.json", [item["id"] for item in reports])

    def test_storing_report_does_not_refresh_workspace_launchers(self) -> None:
        self.assertFalse((self.workspace / ".mdoc" / "launchers").exists())

    def test_feedback_strict_search_and_format_preserving_save(self) -> None:
        page = self.workspace / "Guide" / "en" / "Main" / "Page.md"
        page.write_bytes(b"\xef\xbb\xbf# Page\r\n\r\nExact  phrase.\r\nExact  phrase.\r\n")
        index = SearchIndex(self.workspace); result = index.search("guide", "en", "Exact  phrase")
        self.assertEqual((1, 2), (result["file_count"], result["match_count"]))
        self.assertEqual((3, 1), (result["files"][0]["matches"][0]["line"], result["files"][0]["matches"][0]["column"]))
        self.assertEqual(0, index.search("guide", "en", "Exact phrase")["match_count"])
        source = load_markdown(self.workspace, "guide", "en", "main/page.MD")
        saved = save_markdown(self.workspace, "guide", "en", source["path"], source["content"].replace("phrase", "text"), source["state"])
        self.assertTrue(saved["saved"]); self.assertTrue(page.read_bytes().startswith(b"\xef\xbb\xbf")); self.assertIn(b"\r\n", page.read_bytes())

    def test_feedback_api_and_image_references(self) -> None:
        feedback_html = (Path(__file__).parents[1] / "mdoc_check" / "feedback_web.py").read_text(encoding="utf-8")
        self.assertIn(".settings-field input,.settings-field select{width:100%;height:40px", feedback_html)
        self.assertIn(".settings-actions button{height:38px;min-width:88px", feedback_html)
        self.assertIn(".settings-field.wide{grid-column:1/-1}", feedback_html)
        feedback_ui = (Path(__file__).parents[1] / "mdoc_check" / "static" / "feedback-ui.js").read_text(encoding="utf-8")
        self.assertIn("let revision=value.revision", feedback_ui)
        self.assertIn("revision=saved.revision", feedback_ui)
        self.assertIn("function initProviderModelSelector()", feedback_ui)
        self.assertIn("select.id='provider-model-options'", feedback_ui)
        self.assertIn("已读取 '+models.length+' 个模型：'+models.join('、')", feedback_ui)
        self.assertIn("function initSplitters()", feedback_ui)
        self.assertIn("layout.style.setProperty('--feedback-files-width'", feedback_ui)
        self.assertIn("layout.style.setProperty('--feedback-matches-width'", feedback_ui)
        self.assertNotIn("layout.style.gridTemplateColumns", feedback_ui)
        self.assertIn("function clearWorkbench(", feedback_ui)
        self.assertIn("function syncDirty()", feedback_ui)
        self.assertIn("editor.getValue()!==source?.content", feedback_ui)
        self.assertIn("source.content=content", feedback_ui)
        self.assertIn("p.classList.add('standalone-image')", feedback_ui)
        self.assertIn(".standalone-image>img{display:block;margin:auto}", feedback_ui)
        self.assertIn("p img{vertical-align:middle}", feedback_ui)
        self.assertIn("img:not([height]){height:auto}", feedback_ui)
        self.assertIn(".translation-markdown img:not([height]){height:auto}", feedback_html)
        self.assertNotIn("p>img:only-child", feedback_ui)
        self.assertIn("pageSize = 50", feedback_ui)
        self.assertIn("toolbar file-toolbar", feedback_ui)
        self.assertNotIn("<div class=\"pager\">", feedback_ui)
        self.assertIn("if(matches.length)selectMatch(selectedFile.matches[0],matches[0])", feedback_ui)
        self.assertIn("单击左侧文件即可打开编辑，中间匹配项用于定位", feedback_ui)
        self.assertIn("/api/feedback/translation-prepare", feedback_ui)
        self.assertIn("多语言翻译对照", feedback_ui)
        self.assertIn("renderTranslationWorkbench", feedback_ui)
        self.assertIn("Math.min(2,queue.length)", feedback_ui)
        self.assertIn("current.dataset.translationCurrent", feedback_ui)
        self.assertIn("draft.compareTarget", feedback_ui)
        self.assertIn("index===active", feedback_ui)
        self.assertIn("!target.candidate&&!target.ignored&&!target.manual&&!target.externalChanged", feedback_ui)
        self.assertIn("translationPromptContext", feedback_ui)
        self.assertIn("translationContextMarkup", feedback_ui)
        self.assertIn("sourceSelection.insertAdjacentHTML('afterend'", feedback_ui)
        self.assertIn("<b>待翻译原文</b>", feedback_ui)
        self.assertIn("classList.toggle('translation-mode',translating)", feedback_ui)
        self.assertIn('<button id="translation-tab">返回翻译对照</button>', feedback_ui)
        self.assertNotIn('id="back-translation"', feedback_ui)
        self.assertIn(".layout.translation-mode>#files", feedback_html)
        self.assertIn(".layout.image-mode>#matches", feedback_html)
        self.assertIn(".layout.image-mode{grid-template-columns:var(--feedback-files-width,330px)", feedback_html)
        self.assertIn("classList.toggle('image-mode',images)", feedback_ui)
        self.assertIn('<button id="images-tab">图片管理</button>', feedback_ui)
        self.assertIn("async function openImages()", feedback_ui)
        self.assertIn("activeView==='images'", feedback_ui)
        self.assertIn("image-workspace", feedback_html)
        self.assertIn(".image-body{min-width:860px;min-height:0;display:grid;grid-template-columns:var(--image-list-width,240px) 6px minmax(0,1fr);overflow:hidden}", feedback_html)
        self.assertIn("#image-entries{min-width:0;min-height:0;overflow:auto}", feedback_html)
        self.assertIn(".image-compare{min-width:640px;min-height:0;display:grid;grid-template-columns:minmax(320px,var(--image-original-width,1fr)) 6px minmax(320px,1fr);overflow:auto}", feedback_html)
        self.assertIn(",listScroll=byId('image-entries'),compareScroll=document.querySelector('.image-compare')", feedback_ui)
        self.assertIn("requestAnimationFrame(()=>{const list=byId('image-entries'),compare=document.querySelector('.image-compare')", feedback_ui)
        self.assertIn("data-image-resize", feedback_ui)
        self.assertIn("完整 Markdown 原文", feedback_ui)
        self.assertIn("imagePending", feedback_ui)
        self.assertIn("async function runImageAction(action)", feedback_ui)
        self.assertIn("api('/api/feedback/image-pending')", feedback_ui)
        self.assertIn("canvas.onwheel=event=>", feedback_ui)
        self.assertIn("event.deltaY<0?1.15:1/1.15", feedback_ui)
        self.assertIn("image-format-warning", feedback_html)
        self.assertIn("value.warning?' · '+value.warning", feedback_ui)
        self.assertIn("hydrateTranslationContext", feedback_ui)
        self.assertIn("host.querySelectorAll('.translation-markdown p')", feedback_ui)
        self.assertIn("p.standalone-image>img", feedback_html)
        self.assertIn("translation-context-before", feedback_html)
        self.assertIn("translation-context-current", feedback_html)
        self.assertIn("translation-context-after", feedback_html)
        self.assertIn("重新读取目标文件", feedback_ui)
        self.assertIn("translation-progress-track", feedback_html)
        self.assertIn("正在生成 '+esc(target.locale)+' 翻译候选", feedback_ui)
        self.assertIn("target.status==='running'?'disabled'", feedback_ui)
        self.assertIn("target.expanded=e.target.open", feedback_ui)
        self.assertIn("validateTranslation(target,panel)", feedback_ui)
        self.assertIn("target.validationRequest", feedback_ui)
        self.assertIn("translationLocked(target)", feedback_ui)
        self.assertIn("保存所有语言翻译", feedback_ui)
        self.assertIn("以下语言没有翻译候选", feedback_ui)
        self.assertIn("实际翻译与保存范围", feedback_ui)
        self.assertIn("target.reason||'目标块映射不可靠。'", feedback_ui)
        self.assertIn("AbortController", feedback_ui)
        self.assertIn("翻译请求超过", feedback_ui)
        self.assertIn("querySelectorAll('[data-confirm]').forEach(item=>item.remove())", feedback_ui)
        self.assertIn("button.disabled=false", feedback_ui)
        self.assertIn("已人工处理", feedback_ui)
        self.assertIn("忽略此语言", feedback_ui)
        self.assertIn("translationDraft&&!translationDraft.completed", feedback_ui)
        self.assertIn("function translationDraftBlocksNavigation", feedback_ui)
        self.assertIn("translationDraftBlocksNavigation(next.path)", feedback_ui)
        self.assertIn("if(translationDraftBlocksNavigation())return", feedback_ui)
        self.assertIn("请先保存所有语言翻译或放弃草稿", feedback_ui)
        self.assertIn("else openEditor(item)", feedback_ui)
        self.assertNotIn("id=\"open-edit\"", feedback_ui)
        self.assertNotIn("button.ondblclick", feedback_ui)
        image = self.workspace / "Guide" / "en" / "Main" / "media" / "Shot.png"; image.parent.mkdir(); Image.new("RGB", (2, 2)).save(image)
        page = self.workspace / "Guide" / "en" / "Main" / "Page.md"; page.write_text("# Page\n\n![one](media/Shot.png)\n<img src=\"media/Shot.png\">\n", encoding="utf-8")
        refs = image_references(self.workspace, "guide", "en", "Main/Page.md")
        self.assertEqual([3, 4], refs[0]["lines"]); self.assertTrue(refs[0]["editable"])
        context = json.loads(self.get("/api/feedback/context")[1]); self.assertEqual("guide", context["books"][0]["id"])
        self.assertEqual(120, context["config"]["translation"]["timeout_seconds"])
        capabilities = json.loads(self.get("/api/feedback/translation-capabilities")[1]); self.assertIn("methods", capabilities)
        searched = self.post("/api/feedback/search", {"book":"guide", "locale":"en", "query":"Shot.png"})
        self.assertEqual(2, searched["match_count"])
        prepared = self.post("/api/feedback/translation-prepare", {"book":"guide","source_locale":"en","path":"Main/Page.md","from":0,"to":6})
        self.assertEqual([], prepared["targets"])
        self.assertFalse(json.loads(self.get("/api/feedback/image-pending")[1])["pending"])
        candidate = self.post("/api/feedback/image-candidate", {"session":"00000000-0000-4000-8000-000000000004","book":"guide","locale":"en","path":"Main/Page.md","reference":"media/Shot.png"})
        self.assertTrue(json.loads(self.get("/api/feedback/image-pending")[1])["pending"]); self.assertTrue(self.post("/api/feedback/image-discard", {"key":candidate["key"]})["discarded"]); self.assertFalse(json.loads(self.get("/api/feedback/image-pending")[1])["pending"])
        self.assertTrue(self.post("/api/feedback/translation-validate", {"source":"text","candidate":"translated"})["valid"])
        html = self.get("/feedback/")[1]; self.assertIn("手册反馈修订", html); self.assertIn("feedback-ui.js", html)

    def test_ignore_can_be_added_removed_and_becomes_stale_after_source_change(self) -> None:
        reports = json.loads(self.get("/api/reports")[1])
        report_id = reports[0]["id"]
        detail = json.loads(self.get("/api/file?report=" + report_id.replace("/", "%2F") + "&path=en%2FMain%2FPage.md")[1])
        ignored = self.post("/api/ignore", {"report": report_id, "finding_id": detail["findings"][0]["finding_id"]})
        self.assertEqual("用户通过 mdoc 检查报告忽略此问题", ignored["reason"])
        ignored_detail = json.loads(self.get("/api/file?report=" + report_id.replace("/", "%2F") + "&path=en%2FMain%2FPage.md")[1])
        self.assertEqual(ignored["id"], ignored_detail["findings"][0]["ignore_id"])
        from mdoc_check.ignores import apply, load

        rules, _ = load(self.workspace, "en")
        findings = [dict(detail["findings"][0])]
        states = apply(findings, rules, "en")
        self.assertEqual("active", states[0]["status"])
        findings[0]["finding_id"] = "changed"
        self.assertEqual("stale", apply(findings, rules, "en")[0]["status"])
        self.post("/api/ignore/delete", {"report": report_id, "ignore_id": ignored["id"]})
        self.assertEqual([], load(self.workspace, "en")[0])
        restored_detail = json.loads(self.get("/api/file?report=" + report_id.replace("/", "%2F") + "&path=en%2FMain%2FPage.md")[1])
        self.assertNotIn("ignore_id", restored_detail["findings"][0])
        self.assertEqual("inactive", restored_detail["findings"][0]["suppression"])

    def test_changed_source_is_marked_and_rejects_ignore(self) -> None:
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        page = self.workspace / "Guide" / "en" / "Main" / "Page.md"
        page.write_text("# Page\n\nChanged text with a different size.\n", encoding="utf-8")
        detail = json.loads(self.get("/api/file?report=" + report_id.replace("/", "%2F") + "&path=en%2FMain%2FPage.md")[1])
        self.assertTrue(detail["source_changed"])
        with self.assertRaises(HTTPError) as raised:
            self.post("/api/ignore", {"report": report_id, "finding_id": detail["findings"][0]["finding_id"]})
        self.assertEqual(400, raised.exception.code)
        self.assertIn("先重新检查该文件", raised.exception.read().decode("utf-8"))

    def test_markdown_can_be_loaded_and_saved_without_changing_file_format(self) -> None:
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        report = json.loads(self.get("/api/report?id=" + report_id.replace("/", "%2F"))[1])
        page = self.workspace / "Guide" / "en" / "Main" / "Page.md"
        page.write_bytes(b"\xef\xbb\xbf# Page\r\n\r\nOld.\r\n")
        source = _edit_source(self.workspace, report, "en/Main/Page.md")
        self.assertEqual(("# Page\n\nOld.\n", "crlf", True, True), (source["content"], source["newline"], source["trailing_newline"], source["bom"]))
        saved = _save_source(self.workspace, report, "en/Main/Page.md", "# Page\n\nNew.\n", source["state"])
        self.assertTrue(saved["saved"])
        self.assertEqual(b"\xef\xbb\xbf# Page\r\n\r\nNew.\r\n", page.read_bytes())
        source = _edit_source(self.workspace, report, "en/Main/Page.md")
        _save_source(self.workspace, report, "en/Main/Page.md", "# Page\n\nNo trailing newline", source["state"], False)
        self.assertFalse(page.read_bytes().endswith(b"\r\n"))
        source = _edit_source(self.workspace, report, "en/Main/Page.md")
        _save_source(self.workspace, report, "en/Main/Page.md", source["content"] + "\n", source["state"], True)
        self.assertTrue(page.read_bytes().endswith(b"\r\n"))

    def test_markdown_save_rejects_external_changes_and_large_content(self) -> None:
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        report = json.loads(self.get("/api/report?id=" + report_id.replace("/", "%2F"))[1])
        source = _edit_source(self.workspace, report, "en/Main/Page.md")
        page = self.workspace / "Guide" / "en" / "Main" / "Page.md"
        page.write_text("# External\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "外部程序"):
            _save_source(self.workspace, report, "en/Main/Page.md", "# Edited\n", source["state"])
        fresh = _edit_source(self.workspace, report, "en/Main/Page.md")
        with self.assertRaisesRegex(ValueError, "10 MiB"):
            _save_source(self.workspace, report, "en/Main/Page.md", "x" * (10 * 1024 * 1024 + 1), fresh["state"])

    def test_editor_api_loads_and_saves_markdown(self) -> None:
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        source = json.loads(self.get("/api/editor/source?report=" + report_id.replace("/", "%2F") + "&path=en%2FMain%2FPage.md")[1])
        saved = self.post("/api/editor/save", {"report": report_id, "path": "en/Main/Page.md", "content": "# Saved\n", "state": source["state"]})
        self.assertTrue(saved["saved"])
        saved_again = self.post("/api/editor/save", {"report": report_id, "path": "en/Main/Page.md", "content": "# Saved twice\n", "state": saved["state"]})
        self.assertTrue(saved_again["saved"])
        self.assertEqual("# Saved twice\n", (self.workspace / "Guide" / "en" / "Main" / "Page.md").read_text(encoding="utf-8"))
        preview = self.post("/api/editor/preview", {"report": report_id, "path": "en/Main/Page.md", "content": "# Unsaved\n"})
        self.assertIn("Unsaved", preview["html"])

    def test_editor_auto_fix_api_returns_unsaved_result(self) -> None:
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        source = json.loads(self.get("/api/editor/source?report=" + report_id.replace("/", "%2F") + "&path=en%2FMain%2FPage.md")[1])
        content = "#Page  \n\n\nText"
        with patch("mdoc_check.autofix.vale", return_value=[]), patch("mdoc_check.autofix.cspell", return_value=[]):
            result = self.post("/api/editor/auto-fix", {"report": report_id, "path": "en/Main/Page.md", "content": content, "state": source["state"]})
        self.assertTrue(result["changed"]); self.assertIn("# Page", result["content"]); self.assertTrue(result["ranges"])
        self.assertEqual("# Page\n\nBroken text.\n", (self.workspace / "Guide" / "en" / "Main" / "Page.md").read_text(encoding="utf-8"))

    def test_editor_auto_fix_can_be_disabled_without_disabling_save(self) -> None:
        (self.workspace / ".mdoc" / "check.yaml").write_text("auto_fix:\n  enabled: false\n", encoding="utf-8")
        config = json.loads(self.get("/api/editor/auto-fix-config")[1])
        self.assertFalse(config["enabled"])
        report_id = json.loads(self.get("/api/reports")[1])[0]["id"]
        source = json.loads(self.get("/api/editor/source?report=" + report_id.replace("/", "%2F") + "&path=en%2FMain%2FPage.md")[1])
        with self.assertRaises(HTTPError) as raised:
            self.post("/api/editor/auto-fix", {"report": report_id, "path": "en/Main/Page.md", "content": source["content"], "state": source["state"]})
        self.assertEqual(400, raised.exception.code)
        saved = self.post("/api/editor/save", {"report": report_id, "path": "en/Main/Page.md", "content": "# Saved\n", "state": source["state"]})
        self.assertTrue(saved["saved"])

    def test_mandatory_finding_cannot_be_ignored(self) -> None:
        from mdoc_check.ignores import add

        issue = finding("locale.en-no-han", "error", "en/Main/Page.md", "Han.", 3, 1, mandatory=True)
        issue["finding_id"] = "mandatory"
        with self.assertRaisesRegex(ValueError, "Mandatory"):
            add(self.workspace, issue)

    def test_duplicate_findings_receive_distinct_stable_ids(self) -> None:
        from mdoc_check.ignores import annotate

        page = self.workspace / "Guide" / "en" / "Main" / "Page.md"
        issues = [finding("sample.rule", "error", "en/Main/Page.md", "Same.", 3, 1), finding("sample.rule", "error", "en/Main/Page.md", "Same.", 3, 1)]
        annotate(issues, {"Main/Page.md": page})
        self.assertNotEqual(issues[0]["finding_id"], issues[1]["finding_id"])

    def test_same_finding_in_different_books_has_distinct_id(self) -> None:
        from mdoc_check.ignores import annotate

        page = self.workspace / "Guide" / "en" / "Main" / "Page.md"
        first = finding("sample.rule", "error", "en/Main/Page.md", "Same.", 3, 1); first["book"] = "first"
        second = finding("sample.rule", "error", "en/Main/Page.md", "Same.", 3, 1); second["book"] = "second"
        annotate([first, second], {"Main/Page.md": page})
        self.assertNotEqual(first["finding_id"], second["finding_id"])

    def test_dictionary_words_can_be_added_and_removed(self) -> None:
        from mdoc_check.dictionaries import add, load, remove

        self.assertEqual(["Gvscript"], add(self.workspace, "Gvscript"))
        self.assertEqual(["Mlsworkbench"], add(self.workspace, "Mlsworkbench", "en"))
        self.assertEqual(["Gvscript"], load(self.workspace))
        self.assertEqual([], remove(self.workspace, "Gvscript"))

    def test_spelling_finding_dictionary_is_available_in_report_center(self) -> None:
        from mdoc_check.reports import store_full

        issue = finding("spelling.unknown-word", "warning", "en/Main/Page.md", "Unknown English word: Gvscript", 3, 1, checker="cspell", native_rule="unknown-word")
        annotate([issue], {"Main/Page.md": self.workspace / "Guide" / "en" / "Main" / "Page.md"})
        report = {"schema_version": 1, "kind": "mdoc_check_report", "context": "book", "scope": {"kind": "page", "book": "guide", "locale": "en", "target": "Main/Page.md"}, "status": "passed", "findings": [issue], "inputs": {"markdown": ["en/Main/Page.md"]}, "counts": {"effective_errors": 0, "effective_warnings": 1}, "created_at": 3}
        stored = store_full(report, self.workspace); report_id = Path(stored["path"]).relative_to(report_root(self.workspace)).as_posix().replace("/", "%2F")
        with patch("mdoc_check.web.file_records", side_effect=AssertionError("direct lookup must not scan the report")):
            context = json.loads(self.get(f"/api/dictionary?report={report_id}&finding={issue['finding_id']}&path=en%2FMain%2FPage.md")[1])
        self.assertEqual(("Gvscript", "en"), (context["word"], context["language"]))
        self.post("/api/dictionary/add", {"report": report_id.replace("%2F", "/"), "finding_id": issue["finding_id"], "path": "en/Main/Page.md", "scope": "workspace", "word": "ignored"})
        self.assertEqual(["Gvscript"], context["workspace"] + __import__("mdoc_check.dictionaries", fromlist=["load"]).load(self.workspace))
        self.post("/api/dictionary/remove", {"report": report_id.replace("%2F", "/"), "finding_id": issue["finding_id"], "path": "en/Main/Page.md", "scope": "workspace", "word": "Gvscript"})
        self.assertEqual([], __import__("mdoc_check.dictionaries", fromlist=["load"]).load(self.workspace))
        html = self.get("/")[1]
        self.assertIn("自定义品牌/术语", html)
        self.assertIn("管理已有词条", html)
        self.assertIn("'/api/dictionary/'+action", html)
        self.assertIn("changeDictionary('add'", html)
        self.assertIn("changeDictionary('remove'", html)
        self.assertNotIn("Promise.all([api('/api/source", html)
        self.assertIn("if(f.rule==='spelling.unknown-word')loadDictionary(f,request)", html)
        self.assertIn("&path='+encodeURIComponent(f.path)", html)
        self.assertIn("自定义品牌/术语加载失败", html)

    def test_html_and_static_assets_require_the_loopback_token(self) -> None:
        for path in ("/check/", "/feedback/", "/static/editor.js"):
            with self.assertRaises(HTTPError) as raised: HTTP.open(self.url + path)
            self.assertEqual(403, raised.exception.code)

    def test_ignore_state_is_only_reported_for_its_locale(self) -> None:
        from mdoc_check.ignores import apply

        rules = [{"id": "one", "finding_id": "missing", "path": "en/Main/Page.md", "rule": "sample"}]
        self.assertEqual([], apply([], rules, "ja"))


if __name__ == "__main__":
    unittest.main()
