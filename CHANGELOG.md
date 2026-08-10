# Changelog

## 1.2.0-rc.1 - 2026-08-10

- Added explicit workspace inspection, adoption, schema v1-to-v2 migration, cleanup, registry management, and repair commands.
- Added immutable plan/apply confirmation flows with SHA-256 input revalidation and compact latest records.
- Added global Doctor operation without a workspace and stable workspace launchers through `%LOCALAPPDATA%\mdoc\bin\mdoc.cmd`.
- Added shared package-bounded install/update transactions, active/stale locks, rollback, runtime cancellation, and embedded package-manifest verification.
- Added CPython source classification, including compatible Codex runtime adoption and rejection of temporary/E2E runtimes.
- Kept the public release surface to one deterministic Windows x64 ZIP.

## 1.1.0 - 2026-08-08

- First stable release of mdoc.
- Windows-first Codex workflow for multilingual Markdown manuals.
- Workspace and task management, screenshots, Quality Gate, and PDF Check.
- Verified offline installation, verified-package update, diagnostics, and release packaging.
- Single-package Windows installer with an explicitly authorized, SHA-256-verified Toolchain repair flow.
- PDF problem-page rendering through pinned `pypdfium2`, without a separate Poppler installation.
