# mdoc

`mdoc` 是面向 Windows 10/11 x64 的多语言 Markdown 产品手册工作流工具。产品版本是 `1.5.3`；工作区和任务协议使用 `schema_version: 1`。

新版 mdoc 是一次干净重构：不识别、不迁移、不兼容旧工作区、旧配置、旧任务或旧状态文件。所有流程状态都由同一个 Python CLI 写入，正式手册内容只由发布事务修改；代理和人工编写只能先进入任务的受控 `staging/`。

Copyright 2026 cshuan. Licensed under Apache-2.0. 该许可证只覆盖 mdoc 源码和随附通用模板，不自动覆盖用户手册、截图、PDF 或项目数据。

## 安装

从 GitHub Stable Release 下载 `mdoc-1.5.3-windows-x64.zip`，完整解压后双击 `install-mdoc.cmd`。安装器默认安装到当前用户的 Codex skills 目录，并为 mdoc 创建独立运行环境；它不会修改外部 Python 的全局包。

安装后可双击 `%USERPROFILE%\.codex\skills\mdoc\Open-mdoc-Image-Editor.cmd` 独立编辑图片，也可以将一张图片拖到该 CMD 上。独立编辑器不需要 mdoc workspace 或 task，不修改截图任务状态；成品默认保持导入图片格式，也可另存为 PNG，“保存工程”额外输出同名 `.mdoc-image-edit.json` 和 `.mdoc-image-edit-assets`。

网络不稳定或离线安装时，下载 [mdoc Toolchain 2026.09.15](https://github.com/jackgodcs/mdoc-toolchain/releases/download/v2026.09.15/mdoc-toolchain-2026.09.15-windows-x64.zip)，将其保留原文件名或重命名为 `mdoc-toolchain.zip`，并放在已解压的 mdoc 安装包根目录、与 `install-mdoc.cmd` 同级。双击安装器后会自动使用本地 Toolchain，校验 SHA-256 后安装，不会再联网下载。

卸载时双击 `UnInstall-mdoc.cmd`，或运行 `mdoc uninstall`；自动化场景可使用 `mdoc uninstall --confirm --json`。卸载只删除 mdoc 管理的 skill、Runtime、Toolchain、PATH、开始菜单和 Installed Apps 登记，不删除任何手册工作区及其 `.mdoc` 数据。

官方来源：

- mdoc：`https://github.com/jackgodcs/mdoc/releases`
- Python：`https://www.python.org/downloads/windows/`
- mdoc Toolchain：`https://github.com/jackgodcs/mdoc-toolchain/releases`

Toolchain 采用单一全包，包含 CPython 3.12、Python 检查与截图依赖、Node.js 24.18.0、markdownlint-cli2 0.23.2、CSpell 10.3.0、Vale 3.20.0、HonKit 6.2.2、Calibre Portable 9.14.0 和 qpdf 12.4.1。

PDF 生成是独立能力，不是普通手册修改、发布或 Quality Gate 的默认必选项。安装完整 Toolchain 后可按需运行 `mdoc pdf init`、`mdoc pdf doctor`、`mdoc pdf build`、`mdoc pdf check` 和 `mdoc pdf clean`。

工作区模式可按页面、章节或整册生成 PDF；独立文件模式可直接对任意 Markdown 路径运行 `mdoc pdf build --file <file.md>`，无需 workspace、`Summary.md` 或 `book.json`。详细参数见 [PDF 参考](skill/mdoc/references/pdf.md)。

## 从 Git 获取最新版

协作者可从 Git 服务拉取本仓库的最新 `main`，在仓库根目录运行以下命令构建 Windows 安装包：

```powershell
python scripts/release_check.py
python scripts/build_release.py
```

构建结果位于 `dist/mdoc-<version>-windows-x64.zip`。解压后双击 `install-mdoc.cmd` 即可为当前 Windows 用户安装独立的 mdoc、Python 运行时和截图助手依赖。每台协作者电脑只需安装一次；实际手册工作区、任务、截图与项目模板仍应保留在共享手册目录中，不应提交到本仓库。

## 核心流程

mdoc 直接绑定正式手册仓库根目录，并在其中使用 `.mdoc/` 控制目录。没有全局活动书册；每个任务必须显式声明一个书册。

```powershell
mdoc workspace init --workspace <manual-repository-root>
mdoc workspace apply --workspace <manual-repository-root>
mdoc workspace confirm --workspace <manual-repository-root>

mdoc task create --workspace <manual-repository-root> --task add-search --book user-guide --intent add_feature
mdoc task define --workspace <manual-repository-root> --task add-search
mdoc task confirm-definition --workspace <manual-repository-root> --task add-search
mdoc task continue --workspace <manual-repository-root> --task add-search
mdoc task confirm-final --workspace <manual-repository-root> --task add-search
```

正常推进只使用幂等的 `mdoc task continue`。它会停在下一个人工等待点，或在通过 Quality Gate 后自动执行普通增量发布。需要人工判断的情况包括定义确认、截图验收、最终成品验收、删除确认、目标冲突、基线变化、证据不足、人工复核未完成和发布异常。

## 配置与任务

工作区权威配置只有 `.mdoc/workspace.yaml`，本机配置只有 `.mdoc/workspace.local.yaml`。草稿必须先 `apply` 生成候选，再 `confirm` 写入权威文件；候选会绑定草稿哈希和当前权威配置哈希，防止并发覆盖。

任务权威制品只有 `.mdoc/tasks/<task-id>/task.yaml` 和 `.mdoc/tasks/<task-id>/task-state.json`。任务进入定义确认后会冻结 manifest；确认后的范围变化必须重新修订、定义并确认。

## Quality Gate

`mdoc check` 是唯一自动检查引擎，支持页面、章节、整册、工作区和冻结任务范围。检查等级为 `basic` 和 `full`，当前执行相同的 Markdown、拼写、风格、图片与 mdoc 领域规则；`full` 为后续可选检查预留。任务发布自动执行检查，也可在明确需要时使用任务范围的 `--skip-check` 临时跳过。人工审核仍由任务状态机单独收敛，PDF 生成和 PDF 人工检查均不是普通发布必选项。

```powershell
mdoc check run --workspace <manual-repository-root> --book user-guide --locale en --scope book
mdoc check run --workspace <manual-repository-root> --scope task --task add-search
mdoc check report --workspace <manual-repository-root>
mdoc feedback open --workspace <manual-repository-root>
```

检查报告支持按文件分组、筛选、局部复查、忽略与恢复、词典和品牌维护、Markdown 预览编辑、确认后自动修复，以及按页面或章节生成可选 PDF 预览。手册反馈修订是独立界面，支持严格短语定位、多语言文字对照与统一保存、AI 或网页翻译候选，以及通过临时候选安全替换不同语言的同名图片。详细规则和本地配置路径见 [检查与反馈修订参考](skill/mdoc/references/check-and-feedback.md)。

旧工作区的 `standard/release` 配置仍可读取和修订，但不能创建新任务或执行新检查；请先将工作区改为 `basic/full`。使用旧 profile 冻结的任务不升级，必须重新创建。

## 开发

开发与发布说明见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [docs/maintainers/releasing.md](docs/maintainers/releasing.md)。提交前至少运行：

```powershell
python -m unittest discover -s skill/mdoc/tests -v
python -m unittest discover -s skill/mdoc/scripts -p "test_*.py" -v
python scripts/release_check.py
```
