# Releasing mdoc

正式版本通过不含预发布后缀的版本标签自动公开发布。RC 标签只执行构建和验证，不创建公开 Release；普通 CI 只有 `contents: read`，正式发布 Job 才授予 `contents: write`。

1. 更新 `VERSION`、`CHANGELOG.md`、工具清单和第三方许可证。
2. 运行完整测试、`scripts/release_check.py`、中立 Atlas 夹具 E2E 和受控真实书册验收。
3. 生成确定性 `mdoc-1.3.4-windows-x64.zip`；包内必须包含 `PACKAGE-MANIFEST.json`、CycloneDX SBOM、安装器、共享事务和运行时修复组件。正式版本标签自动发布；带预发布后缀的 RC 标签只运行验证，不创建 Release。
4. 如需候选验证，推送带预发布后缀的 RC 标签，并在干净 Windows 用户目录完成在线/离线安装、任务、Quality Gate、PDF Check、更新和卸载闭环；不要为 RC 创建公开 Release。
5. 推送正式版本标签并确认自动发布成功；发布后清理最近 PDF 检查资源，仅保留必要记录。
