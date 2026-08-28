# mdoc

`mdoc` 是面向 Windows 10/11 x64 的多语言 Markdown 产品手册工作流工具。产品版本是 `1.3.1`；新版工作区和任务协议统一使用 `schema_version: 1`。

新版 mdoc 是一次干净重构：不识别、不迁移、不兼容旧工作区、旧配置、旧任务或旧状态文件。所有流程状态都由同一个 Python CLI 写入，正式手册内容只由发布事务修改；代理和人工编写只能先进入任务的受控 `staging/`。

Copyright 2026 cshuan. Licensed under Apache-2.0. 该许可证只覆盖 mdoc 源码和随附通用模板，不自动覆盖用户手册、截图、PDF 或项目数据。

## 安装

从 GitHub Stable Release 下载 `mdoc-1.3.1-windows-x64.zip`，完整解压后双击“安装 mdoc.cmd”。安装器默认安装到当前用户的 Codex skills 目录，并为 mdoc 创建独立运行环境；它不会修改外部 Python 的全局包。

官方来源：

- mdoc：`https://github.com/jackgodcs/mdoc/releases`
- Python：`https://www.python.org/downloads/windows/`
- mdoc Toolchain：`https://github.com/jackgodcs/mdoc-toolchain/releases`

基础运行需要 CPython 3.12、`ruamel.yaml` 和 `jsonschema`。PDF 检查还需要 `pdfplumber`、`pypdf`、`pypdfium2` 和 Pillow；截图助手需要 Pillow、Tk/Tcl。

## 从 Git 获取最新版

协作者可从 Git 服务拉取本仓库的最新 `main`，在仓库根目录运行以下命令构建 Windows 安装包：

```powershell
python scripts/release_check.py
python scripts/build_release.py
```

构建结果位于 `dist/mdoc-<version>-windows-x64.zip`。解压后双击“安装 mdoc.cmd”即可为当前 Windows 用户安装独立的 mdoc、Python 运行时和截图助手依赖。每台协作者电脑只需安装一次；实际手册工作区、任务、截图与项目模板仍应保留在共享手册目录中，不应提交到本仓库。

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

Quality Gate 是唯一检查引擎，服务任务验证和独立书册审计。任务发布前至少通过 `standard` 档位；`full` 和 `release` 逐级增加人工复核、构建和 PDF 检查。独立书册检查默认返回报告，只有显式 `--enforce` 且存在阻断项时才返回非零。

```powershell
mdoc quality check --workspace <manual-repository-root> --book user-guide
mdoc quality check --workspace <manual-repository-root> --book user-guide --enforce
mdoc quality check --workspace <manual-repository-root> --task add-search
```

## 开发

开发与发布说明见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [docs/maintainers/releasing.md](docs/maintainers/releasing.md)。提交前至少运行：

```powershell
python -m unittest discover -s skill/mdoc/tests -v
python -m unittest discover -s skill/mdoc/scripts -p "test_*.py" -v
python scripts/release_check.py
```
