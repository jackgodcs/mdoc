# Changelog

## 1.3.12 - 2026-09-03

- Automatically create or refresh a task-specific one-click screenshot assistant launcher when a confirmed task declares screenshots, including already completed tasks continued after upgrading.
- Improved screenshot reference handling for new multilingual manual content and retained the generic capture workflow alongside image editing.
- Added reusable image layers, external clipboard image import, and reference-image copying to the screenshot editor workflow.

## 1.3.11 - 2026-08-31

- Added the publish-conflict approval state field to the released task-state schema and runtime, so contributor installations recognize tasks created with the current shared-workspace workflow.
- Added the coordinator command `mdoc task approve-publish-conflict --confirm` for changed `update` targets after an already reviewed staging result encounters a publishing conflict.

## 1.3.10 - 2026-08-28

- Fixed the GitHub Actions Windows release check so diagnostics containing Chinese file names are emitted as UTF-8 instead of failing under the legacy `cp1252` console encoding.
- Reissued the 1.3.9 release metadata under 1.3.10 after the original tag omitted the matching version marker in the Windows installation guide.

## 1.3.9 - 2026-08-28

- Restored the full generic screenshot workflow: frozen-screen capture, editable selection handles, magnifier, window selection, monitor scope, global shortcut, automatic next-item selection, original/new comparison divider, exception reasons, and task-local duplicate-assistant protection.
- Kept the OCR replacement workflow additive: original-image copy, external image import, text editing, JPEG support, controlled capture paths, and contributor submission remain available.
- Kept assistant display preferences local to each user so shared-workspace contributors do not overwrite one another's capture settings.

## 1.3.8 - 2026-08-28

- Added **原图作为新截图** for confirmed OCR false positives. It creates a validated, byte-identical capture from the original reference image and clears stale text-editor layers for that item.

## 1.3.7 - 2026-08-28

- Fixed contributor screenshot launchers so a Windows UNC share is mapped before its workspace path is passed to the assistant; this prevents a trailing UNC separator from consuming the task argument.

## 1.3.6 - 2026-08-28

- Made contributor screenshot launchers work from Windows UNC shares and prevented the assistant subprocess from using a UNC current directory.

## 1.3.5 - 2026-08-28

- Allowed installers to automatically verify and use a manually downloaded Toolchain ZIP placed beside the installer, avoiding network download on unreliable machines.

## 1.3.4 - 2026-08-28

- Retried interrupted toolchain downloads, discarded partial files, and used Windows curl as a verified fallback when Invoke-WebRequest cannot complete the download.

## 1.3.3 - 2026-08-28

- Isolated Windows Python capability probes from PowerShell native stderr handling so incompatible Python candidates are skipped instead of aborting installation.

## 1.3.2 - 2026-08-28

- Fixed Windows PowerShell 5.1 package-manifest decoding for packages containing Chinese filenames.

## 1.3.1 - 2026-08-28

- Fixed Windows PowerShell installer parsing and installer-console encoding on systems using the default Windows PowerShell 5.1 host.

## 1.3.0 - 2026-08-26

- Completed the schema_version 1 refactor, including strict workspace and task definition lifecycles, frozen manifests, controlled authoring staging, screenshot acceptance, and transactional publishing.
- Added a unified Quality Gate for candidate-book, published-task, and independent book checks, with isolated generator and build adapter execution.
- Added end-to-end lifecycle coverage for workspace governance, task revisions, screenshots, generators, locking, transactions, virtual books, and Quality Gate behavior.

## 1.2.0 - 2026-08-11

- Rebuilt mdoc around the schema_version 1 workspace and task model: one `.mdoc/` control directory, explicit book registry, task manifest freezing, controlled staging, and transactional publishing.
- Made `mdoc task continue --workspace <manual-repository-root> --task <id>` the normal idempotent state-machine entrypoint through definition confirmation, screenshots, authoring, Quality Gate, publishing, and final review.
- Unified task verification and existing-book audit under Quality Gate, including release build adapters and PDF Check blocking for effective PDF errors.
- Replaced old public docs, templates, examples, and release hygiene with product-neutral schema_version 1 materials.

## 1.2.0-rc.1 - 2026-08-10

- Added deterministic Windows x64 package assembly with a package manifest, runtime contract, and install/update transaction verification.
- Added shared runtime repair planning, CPython source classification, and package-bounded install/update records.
- Added PDF Check runtime dependencies and local viewer support for release validation.
- Kept the public release surface to one deterministic Windows x64 ZIP.

## 1.1.0 - 2026-08-08

- First stable release of mdoc.
- Windows-first Codex workflow for multilingual Markdown manuals.
- Workspace and task management, screenshots, Quality Gate, and PDF Check.
- Verified offline installation, verified-package update, diagnostics, and release packaging.
- Single-package Windows installer with an explicitly authorized, SHA-256-verified Toolchain repair flow.
- PDF problem-page rendering through pinned `pypdfium2`, without a separate Poppler installation.
