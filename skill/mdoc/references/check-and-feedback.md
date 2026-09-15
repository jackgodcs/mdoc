# Check And Feedback Tools

`mdoc check` and `mdoc feedback` are separate manual-maintenance tools. They share one loopback-only local service and common Markdown, PDF-preview, translation, and image-editing components, but keep their workflows and state separate.

## Automatic Checks

Run a check against one page, one Summary section, one locale book, the registered workspace, or a frozen task:

```powershell
mdoc check run --workspace <manual-root> --book <book-id> --locale <locale> --scope page --target <page.md>
mdoc check run --workspace <manual-root> --book <book-id> --locale <locale> --scope section --target <page.md>
mdoc check run --workspace <manual-root> --book <book-id> --locale <locale> --scope book
mdoc check run --workspace <manual-root> --scope workspace
mdoc check run --workspace <manual-root> --scope task --task <task-id>
```

Checks combine locked markdownlint, CSpell, Vale, Pillow, navigation, resource, locale, terminology, and mdoc domain rules. Japanese pages are classified from their first visible content lines: pages containing Japanese characters skip English spelling, while English pages inside a Japanese book use English CSpell. English pages containing Han characters remain a mandatory error.

Results are `passed`, `blocked`, `incomplete`, or `skipped`. Exit codes are `0` for passed or skipped, `3` for blocked, and `4` for incomplete or invalid check invocation. `--json` emits the stable machine report. Task-only `--skip-check` skips automatic Markdown checking but does not bypass configured human reviews or generic build adapters.

Workspace configuration layers are:

- `.mdoc/check.yaml`: common workspace rules.
- `.mdoc/check.<language>.yaml`: language override with highest priority.
- `.mdoc/check-ignores.yaml` and `.mdoc/check-ignores.<language>.yaml`: active finding ignores.
- `.mdoc/check-words.txt` and `.mdoc/check-words.<language>.txt`: CSpell dictionaries.
- `.mdoc/check-brands.yaml` and `.mdoc/check-brands.<language>.yaml`: canonical brand names and accepted variants.

There is no book-level check override. Mandatory rules cannot be disabled or ignored. Rule severity, disabled rules, checker concurrency, timeouts, language detection, image limits, automatic fixes, PDF previews, and retention are configured in `check.yaml`; use the installed `config/mdoc.yaml` as the default-field reference.

## Check Report Center

Open the current workspace reports with:

```powershell
mdoc check report --workspace <manual-root>
```

Workspace initialization and later mdoc commands maintain `.mdoc/launchers/open_check_report.cmd`. The launcher reuses an existing server for the same workspace when possible and shows which workspace the console serves. Reports are stored under `.mdoc/reports/check/`; generated launchers and runtime files remain under `.mdoc/` rather than polluting the manual root.

The report center groups findings by file, displays error, warning, and ignored counts, pages the file list, and filters by path, rule, or message. Selecting a finding updates the full-file read-only source, editable source, and Markdown preview at the corresponding location. Local Markdown links and images resolve inside the selected locale root.

Users may recheck the report's original scope or recheck an explicitly selected file list. A local recheck updates those files inside the current complete report; it does not create independent partial-report histories. During recheck, execution buttons are disabled to prevent duplicate runs. Deleted files are removed from the bounded checked scope, and newly selected files are added only when explicitly included.

Ordinary findings can be ignored at workspace or language level and later restored. Ignore identity includes the source-line anchor; changed or no-longer-matching ignores are displayed as stale with distinct gray, struck-through styling and may be deleted. Unknown Latin words can be added to or removed from the appropriate dictionary. Brand rules can likewise be maintained from their workspace or language configuration.

The built-in editor never saves automatically. User-confirmed automatic repair operates only on the current file and only for enabled deterministic Markdown or simple mdoc text fixes. The proposed content, changed ranges, rule explanations, and temporary highlights remain in memory until the user saves or discards them. Saving clears temporary repair state; source changes made by another program cause a conflict instead of an implicit overwrite.

PDF preview is optional. It uses the single built-in mdoc PDF pipeline to build the current page or Summary section, keeps only the latest preview for that Markdown file, labels its scope, locale, time, and stale state, and can save the generated PDF elsewhere. Building one preview replaces the previous page/section preview and cleans its obsolete temporary work. It does not make PDF generation or PDF human review mandatory for task publication.

## Feedback Revision

Open the separate feedback-revision interface with:

```powershell
mdoc feedback open --workspace <manual-root>
```

The maintained launcher is `.mdoc/launchers/open_book_feedback_modify.cmd`. Feedback revision starts after a user or tester has extracted an exact phrase from an external PDF or issue screenshot. It performs a strict, space-preserving search only in the selected book and source language. The first search builds an incremental index under `.mdoc/cache/feedback/`; later searches refresh only changed files. Other languages are associated by case-insensitive matching of the same relative Markdown and image names, not by translated-text search.

Selecting a result opens the complete Markdown file; selecting another match moves the editor to that exact occurrence. The interface provides file editing, Markdown preview, page PDF preview, translation comparison, and a separate image-management view. Unsaved edits or an unfinished translation draft block switching to another file until the user saves or discards the current work. Undoing edits back to the original bytes removes the dirty state.

Saving Markdown preserves its existing UTF-8 BOM state, CRLF or LF line endings, and final-newline state. If the file changed externally, the user must reload or explicitly choose forced overwrite. Feedback revision does not run Markdown checks; validation remains centralized in the check report center.

## Translation Review

The user selects source text in the current Markdown file and chooses the target languages. All locales registered for that book are discovered dynamically; a book is not assumed to contain only `zh`, `en`, and `ja`. During translation review, source context, the selected source range, and each target file's corresponding Markdown block are shown together. The original selected source remains specially highlighted.

Translation methods are preferred in this order when available: configured AI provider, Codex CLI, then Google or Bing web translation with user-pasted output. AI providers use OpenAI-compatible Completions or Chat Completions endpoints. Provider configuration is local and stored in `.mdoc/config/translation-providers.json`; API keys are plaintext, are never returned to the browser after saving, and that file must remain ignored by version control. Terminology and protected brand names are stored in `.mdoc/feedback-translation-terms.yaml`.

Each target language must end in exactly one resolved state: accepted candidate, manually handled, or explicitly ignored. Manual and ignored states are mutually exclusive. Saving is a single all-language transaction; partial language writes are not allowed. The interface validates Markdown structure and target-block mapping before saving, shows generated candidates for comparison, permits manual edits, and permits undoing one or more language candidates before the final save.

## Image Revision

Image management is separate from translation review. It lists Markdown images and HTML `img` references for the current file, with draggable panes for the list, original image, and candidate image. Mouse-wheel zoom is available in both image views. Original Markdown context can be expanded when needed.

An image candidate may come from a selected or pasted file, another locale's same relative image name, or the managed mdoc image editor. The editor opens the candidate directly; saving it refreshes that candidate rather than modifying the formal image. Supported image formats are inherited from the mdoc editor and are not converted to PNG merely for comparison. A content/extension mismatch is shown as a warning rather than blocking the candidate.

Only an explicit save replaces the formal image. Image dimensions are limited to `4096 x 4096` and the file size to `20 MB`; size or aspect-ratio changes are reported during save without requiring a second confirmation. A forced overwrite remains available for a verified external-change conflict. Candidate files are temporary and are removed after apply, discard, replacement, service shutdown, or maintenance cleanup. Feedback screenshots supplied as problem evidence are not persisted by mdoc.

## Maintenance

Run maintenance explicitly with:

```powershell
mdoc check clean --workspace <manual-root>
```

Checks, report opening, feedback search, and PDF previews also trigger throttled maintenance. Fixed report scopes keep only their current generation. Workspace, book, and active-task reports are retained; inactive page, section, and task reports expire according to `retention.inactive_report_days`. PDF preview age and capacity are controlled by `retention.pdf_preview_days` and `retention.pdf_preview_max_bytes`. Cleanup never removes manual Markdown, formal images, workspace authority, ignore rules, dictionaries, provider configuration, formal PDFs, or PDFs explicitly saved by the user.
