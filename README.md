# mdoc

`mdoc` 是面向 Windows 10/11 x64 的多语言 Markdown 产品手册工作流。它把正式手册仓库与本机流程工作区分离，并在每次操作中明确显示“活动书册”和“本次操作书册”，避免任务误写到其他版本。

Copyright 2026 cshuan. Licensed under Apache-2.0. 该许可证只覆盖 mdoc 源码和随附通用模板，不自动覆盖用户手册、截图、PDF 或项目数据。

## 第一次安装

推荐从公开 GitHub Stable Release 只下载 `mdoc-1.1.0-windows-x64.zip`，完整解压后双击“安装 mdoc.cmd”。安装器默认安装到 `%USERPROFILE%\.codex\skills\mdoc`；用户同意后，它会下载并校验固定版本的 Toolchain，创建独立运行环境，并把 mdoc 命令目录加入当前用户 PATH。

如果已安装 Codex，可让 AI 在你明确同意联网和下载后执行安装。AI 必须先展示下载地址、目标目录、版本、SHA-256 和许可证，再进行下载。官方来源：

- mdoc：`https://github.com/jackgodcs/mdoc/releases`
- Python：`https://www.python.org/downloads/windows/`
- mdoc Toolchain：`https://github.com/jackgodcs/mdoc-toolchain/releases`

基础运行需要 CPython 3.12、`ruamel.yaml` 和 `jsonschema`。PDF Check 还需要 `pdfplumber`、`pypdf`、`pypdfium2` 和 Pillow；截图助手需要 Pillow、Tk/Tcl。安装器优先校验用户指定或机器已有 Python，并始终为 mdoc 创建独立 venv，不修改外部 Python 的全局包。缺失时可运行 `mdoc doctor --repair --allow-network-download`，或使用 `mdoc doctor --repair --toolkit <完整Toolchain ZIP>`。

## 首次使用

```powershell
python skill/mdoc/scripts/mdoc.py setup --repository D:\manuals --workspace D:\manuals-manual-workspace --book Product_V1
python skill/mdoc/scripts/mdoc.py status --workspace D:\manuals-manual-workspace
python skill/mdoc/scripts/mdoc.py new-task --workspace D:\manuals-manual-workspace --id add-search --operation add_feature --title Search
```

也可双击流程工作区内的 `open-mdoc.cmd` 查看当前活动书册。全新项目默认源语言为简体中文，初始目标语言为英语；初始化只建立语言目录，首个模块任务才创建内容结构。

## 常用命令

```text
mdoc setup | status | new-task | tasks | resume | switch-book
mdoc configure | bind-local | doctor | doctor --repair
mdoc check | pdf-check | screenshots | diagnose
mdoc update | uninstall
```

Quality Gate 默认是建议性能力，不是发布必选项。只有配置 `validation.mode: required` 且 `publish_policy.required_before_publish: true` 时才阻止发布。PDF Check 只展示问题页、PDF 页码和 Markdown 源位置，用户修改源文档后重新检查；机器规则确有缺陷时，可以在用户明确确认后对单项强制通过。

正式手册默认只修改本地工作副本。Git/SVN 提交、推送和发布必须单独获得用户明确授权。v1.1.0 支持本地磁盘和 Windows 局域网共享，局域网场景仅支持轮流写入，不提供并发锁。正式图片支持 PNG/JPEG/JPG，不支持 SVG/GIF/WebP。

开发与发布说明见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [docs/maintainers/releasing.md](docs/maintainers/releasing.md)。
