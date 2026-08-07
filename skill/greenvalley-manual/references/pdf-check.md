# PDF Check

PDF Check is a local visual-problem finder for generated manual PDFs. It is not an approval or sign-off workflow. Run it, inspect the highlighted PDF location, open the mapped Markdown source, edit the manual, and rerun until no effective exact errors remain.

## Commands

Task mode reads PDF artifacts from `workspace.local.yaml`:

```powershell
python scripts/manual_pdf_check.py open --workspace <process-workspace> --task <task-id>
python scripts/manual_pdf_check.py check --workspace <process-workspace> --task <task-id>
```

After either task command runs successfully, it also writes a Windows one-click launcher to `<manual-repository>/.work/greenvalley-manual/<task-id>/open-pdf-check.cmd`. Double-click it for subsequent check-and-open cycles. The launcher contains machine-local paths and must not be committed.

Standalone existing-PDF mode:

```powershell
python scripts/manual_pdf_check.py open --book-root <book-root> --pdf <manual.pdf> --output <report-directory>
```

Other commands are `serve`, `verify`, `doctor`, `finalize`, and `clean`. Task mode is accepted by all lifecycle commands, for example `finalize --workspace <process-workspace> --task <task-id>`. Exit codes are 0 for no effective errors, 1 for remaining effective errors, 2 for execution failure, 3 for stale or missing current resources, 4 for missing required capability, and 130 for cancellation.

## PDF sources

Configure machine paths only in `workspace.local.yaml`:

```yaml
validation:
  pdf_check:
    artifacts:
      - id: pdf-en
        locale: en
        required: true
        source:
          mode: existing-pdf
          path: D:/output/manual-en.pdf
```

For controlled builds use `mode: build`, `protocol: pdf-check-v1`, and a command array. The adapter receives `GV_MANUAL_SOURCE_ROOT`, `GV_MANUAL_OUTPUT_PATH`, `GV_MANUAL_LOCALE`, `GV_MANUAL_BUILD_ROLE`, `GV_MANUAL_INSTRUMENTED`, and `GV_MANUAL_BUILD_MANIFEST`. Legacy build commands are accepted but mapping falls back to title and content matching.

## Viewer and ignores

The viewer binds only to `127.0.0.1`, displays rendered problem pages, and accepts only current finding IDs. On first use, select **Windows Open With** to choose a registered application, or **Select editor .exe** to browse for an executable. The choice is remembered in `%LOCALAPPDATA%/GreenValley/manual-tools/pdf-check-preferences.json`; later **Open corresponding Markdown** uses it directly. A confirmed ignore requires a reason and applies only to the exact finding fingerprint and current PDF/rule version. It remains visible as `ignored-by-user` and is never presented as passed.

The deterministic first version reports marker leakage, replacement glyphs, text outside the page, unexpected blank pages, page-geometry changes, sparse pages, and very small text. Complex semantic layout judgments such as whether a heading is orphaned, a caption is separated from its image, or a table split is editorially acceptable are intentionally not promoted to exact errors without reliable evidence. Inspect the rendered problem pages for those cases.

## Scope and cleanup

All PDF pages are scanned. Task mode classifies mapped findings as task or book-existing; artifact-level failures always block required checks. Standalone mode treats the whole book as scope. Temporary instrumented sources and mapping PDFs are deleted immediately. Only the latest check PDF and problem-page previews remain under `.pdf-check/current`; publishing calls `finalize` to delete them while retaining the small JSON and Markdown reports.
