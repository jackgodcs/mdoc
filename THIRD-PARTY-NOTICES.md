# Third-Party Notices

mdoc 本身采用 Apache-2.0。运行时依赖的精确版本、来源、SHA-256 和许可证复核记录在版本化 mdoc-toolchain Stable Catalog 中。

v1.3.7 源码仓库和产品 ZIP 不直接捆绑 Python 或 Python wheels。运行时 Toolchain 作为独立、逐项校验的发布资产维护；发布前必须复核每项许可证并生成 CycloneDX SBOM。PDF 问题页由固定版本的 `pypdfium2` 渲染，不要求单独安装 Poppler。

`requirements-ci.txt` 仅定义 GitHub Actions 测试环境，不会被打入 mdoc 技能 ZIP。
