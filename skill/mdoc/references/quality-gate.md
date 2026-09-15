# Quality Gate

`mdoc check` is the single automatic checking engine for mdoc tasks and existing books.

## Profiles

- `basic`: all currently released Markdown, spelling, style, image, navigation, and mdoc domain checks.
- `full`: currently identical to `basic`; reserved for later optional checks.

Task publication runs the same task-scope checker automatically. `--skip-check` is task-only and must be explicitly requested. Old tasks frozen with `standard` or `release` are blocked and must be recreated.

## Findings

Findings keep severity and confidence separate:

- Severity: `error` or `warning`.
- Confidence: `exact`, `probable`, or `review`.

Only executed human review may produce `human_accepted`. A review finding that has not been performed remains `waiting_for_review` or `stale`; it must not be shown as passed.

## Reports And Fixes

Reports are stored under `.mdoc/reports/check/`. The report center supports file grouping, filtering, ignores, dictionaries, Markdown preview/editing, preview PDF generation, and user-confirmed automatic fixes. Automatic task verification does not silently edit staging or formal files.

Detailed report-center, local recheck, configuration, feedback revision, translation, image replacement, and cleanup behavior is documented in [Check And Feedback Tools](check-and-feedback.md).

## Build And PDF Checks

An explicitly configured generic build adapter remains an independent task condition and runs before and after publication. No adapter is required by `basic` or `full`.

PDF generation and structural checking use the single built-in `mdoc pdf` pipeline. They are not required by ordinary task publication in the current version; later opt-in Quality Gate integration must call that same pipeline rather than define another adapter.

## Command Examples

```powershell
mdoc check run --workspace <manual-repository-root> --scope task --task <task-id> --json
mdoc check run --workspace <manual-repository-root> --book <book-id> --locale <locale> --scope book --json
mdoc check report --workspace <manual-repository-root>
```
