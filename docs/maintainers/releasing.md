# Releasing mdoc

正式版本通过不含预发布后缀的版本标签自动公开发布。RC 标签只执行构建和验证，不创建公开 Release；普通 CI 只有 `contents: read`，正式发布 Job 才授予 `contents: write`。

1. 先发布 `bootstrap/toolchain-bootstrap.json` 指定的 Toolchain 版本，上传并校验 Catalog、六个组件 ZIP 和完整 Toolchain ZIP。
2. 更新 `VERSION`、`CHANGELOG.md`、工具清单、安装说明及 Toolchain URL/SHA-256。
3. 运行完整测试、`scripts/release_check.py`、中立 Atlas 夹具 E2E 和受控真实书册验收；PDF 视觉检查只在版本稳定前按需执行。
4. 生成确定性 `mdoc-<version>-windows-x64.zip`；包内必须包含 `PACKAGE-MANIFEST.json`、CycloneDX SBOM、安装器、共享事务和运行时修复组件。
5. 如需候选验证，推送带预发布后缀的 RC 标签，并在干净 Windows 用户目录完成在线/离线安装、任务、Quality Gate、PDF、更新和卸载闭环；不要为 RC 创建公开 Release。
6. 确认 Toolchain Release 可下载且哈希匹配后，推送正式 mdoc 版本标签并确认自动发布成功。
