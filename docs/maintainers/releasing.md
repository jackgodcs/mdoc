# Releasing mdoc

Stable 发布只能从 `workflow_dispatch` 启动，并使用受保护的 `release` Environment 进行单维护者批准。普通 CI 只有 `contents: read`，发布 Job 才授予 `contents: write`。

1. 更新 `VERSION`、`CHANGELOG.md`、工具清单和第三方许可证。
2. 运行完整测试、`scripts/release_check.py`、中立 Atlas 夹具 E2E 和受控真实书册验收。
3. 生成确定性 `mdoc-1.2.0-rc.1-windows-x64.zip`；包内必须包含 `PACKAGE-MANIFEST.json`、CycloneDX SBOM、安装器、共享事务和运行时修复组件。
4. 先发布 RC，在干净 Windows 用户目录完成在线/离线安装、任务、Quality Gate、PDF Check、更新和卸载闭环。
5. 人工批准 Stable，发布后清理最近 PDF 检查资源，仅保留必要记录。
