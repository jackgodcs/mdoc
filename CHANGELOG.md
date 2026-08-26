# Changelog

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
