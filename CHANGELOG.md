# Changelog

## 1.5.3 - 2026-09-16

- Added managed one-click uninstall from the release package, Start Menu, Windows Installed Apps, and `mdoc uninstall`.
- Added installation ownership manifests, safe PATH and registry cleanup, managed-Python ownership handling, and delayed cleanup for locked files.
- Renamed the release launchers to `install-mdoc.cmd` and `UnInstall-mdoc.cmd`, with English installer and uninstaller prompts.

## 1.5.2 - 2026-09-16

- Separated the feedback image list and image comparison area into independent scroll containers.
- Preserved each image-management scroll position across image selection, candidate refresh, save, discard, and busy-state rerenders.

## 1.5.1 - 2026-09-16

- Preserved the imported image format when saving from the standalone editor and the exact managed-candidate path and format when editing feedback images.
- Added format-correct PNG, JPEG, BMP, GIF, TIFF, and WebP output, atomic replacement, transparent-image handling, and overwrite warnings for flattened GIF, TIFF, and WebP originals.
- Removed legacy same-stem PNG candidates during feedback refresh so edited JPEG and other non-PNG candidates remain authoritative.

## 1.5.0 - 2026-09-15

- Replaced the former partial Quality Gate scanner with the validated `mdoc check` engine for page, section, book, workspace, and frozen-task checks.
- Added the local HTML report center, per-file refresh, ignores, dictionaries and brands, Markdown preview/editing, user-confirmed automatic fixes, and PDF previews.
- Added the separate manual feedback revision interface with strict text search, persistent incremental indexes, multilingual editing and translation review, and managed image replacement.
- Added markdownlint-cli2 0.23.2, markdownlint 0.41.1, markdown-it 15.0.1, CSpell 10.3.0, Vale 3.20.0, and Pillow 12.2.0 to Toolchain 2026.09.15.
- Changed Quality Gate profiles to `basic` and `full`; old `standard/release` tasks must be recreated, while old workspaces remain readable for explicit revision.
- Kept PDF generation and PDF human review optional, and preserved configured human reviews and generic build adapters as independent task conditions.

## 1.4.4 - 2026-09-13

- Reduced page and section PDF build time by materializing only selected Markdown files and their referenced images, styles, and fonts instead of the complete locale resource tree.
- Added prepare, image, outline, structural-check, output, cleanup, and total durations to workspace PDF build reports.
- Fixed page and section PDF builds for deeply nested `Summary.md` entries by rebuilding bookmarks from scope-relative levels while preserving full-book chapter numbers.
- Added `--summary-line` selection for repeated Summary targets and scope-aware `pdf check` validation.
- Added standalone Markdown PDF builds with `mdoc pdf build --file`, optional language, font, and PDF configuration overrides, unrestricted local relative resources, and atomic output replacement.
- Added standalone title and language detection, one-root bookmarks, resource findings, temporary output cleanup, and regression coverage for output preservation after failed builds.

## 1.4.3 - 2026-09-09

- Added a standalone image editor that reuses the screenshot editor canvas without requiring or modifying an mdoc workspace, task, screenshot acceptance, or publishing state.
- Added separate PNG-only save operations and reusable project save operations with sidecar JSON, base snapshot, and image-layer assets.
- Added `Open-mdoc-Image-Editor.cmd` for direct launch, file selection, and drag-and-drop image opening from both the release package and installed skill.

## 1.4.2 - 2026-09-04

- Fixed full-book PDF checks incorrectly rejecting embedded Type3 fonts that store glyph programs in `CharProcs` instead of `FontFile*` streams.
- Added regression coverage for Type3 font embedding detection.
- Increased the default PDF JPEG quality from 70 to 75 to preserve text clarity in large screenshots.
- Set the default maximum PDF image width to 1048 pixels and enabled smooth viewer interpolation for generated PDF images after readability validation.
- Relaxed the PDF batch memory guard to reserve 2 GiB and budget 4 GiB per concurrent build, allowing two builds from 10 GiB available memory and three from 14 GiB.
- Reduced the default PDF top and bottom margins to 36 points.
- Fixed PDF bookmark hierarchy when a `Summary.md` mixes tabs, two-space indentation, and four-space indentation, and added bookmark-level validation.
- Allowed PDF tables and code blocks to split at row or line boundaries while keeping each Markdown page's existing document boundary.

## 1.4.1 - 2026-09-04

- Fixed PDF builds failing with `cannot pickle 'mappingproxy' object` when merging the immutable workspace PDF defaults used by the real CLI.
- Added regression coverage for frozen workspace and book-level PDF configuration.

## 1.4.0 - 2026-09-04

- Added built-in PDF generation for individual pages, sections, locale books, and batch builds across book locales.
- Added deterministic chapter numbering, clickable TOC links, rebuilt PDF bookmarks, and the production `chapter-number-links` mode.
- Added generated-copy image optimization with a 20 KiB threshold, JPEG quality 70, and qpdf structural optimization and checks.
- Unified runtime installation around Toolchain 2026.09.1 with Node.js 24.18.0, HonKit 6.2.2, Calibre Portable 9.14.0, and qpdf 12.4.1.
- Kept PDF generation optional for normal manual authoring and publication workflows.

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
