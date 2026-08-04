# Screenshot Workflow

Support manual, assisted, and automated capture; assisted is the recommended default.

## State and workbooks

Keep `screenshots.yaml` as the machine state source. Generate one `screenshot-workbook.<locale>.md` for each locale that requires independent captures under `.work/greenvalley-manual/<task-id>/` in the bound manual repository. Do not generate a workbook for a locale whose screenshots are copied from another locale.

The workbook is the human capture interface. Put common rules, progress, and pending navigation at the top. Group cards by screenshot ID prefix and keep each card concise: ID/title, required flag, capture status, review status, target file, task-specific steps, expected state, optional warning, image link, and a preserved user-notes block.

## Capture synchronization

Run `scripts/screenshot_workbook.py sync <process-workspace> <task-id>`. It must:

- scan only the declared target `.png` path for each locale;
- treat file existence as captured without reading image contents;
- set ordinary missing captures back to pending and remove stale file mappings;
- preserve explicit `not-applicable`, `waived`, and `blocked` decisions while the target file is absent;
- prefer an actual target PNG over an earlier exception status;
- refresh workbook progress and cards while preserving user-note blocks;
- leave every individual `review.status` unchanged;
- never copy captures into the formal manual.

Human recapture may directly replace the formal target PNG. The agent must not silently overwrite captures itself.

## Status and exceptions

Capture statuses are `pending`, `captured`, `needs-retake`, `approved`, `not-applicable`, `waived`, and `blocked`. `not-applicable` and `waived` require explicit user confirmation. `blocked` remains incomplete. Missing optional screenshots do not block capture completion.

## Review and acceptance

Content review is independent and runs only when requested. It may update individual `review.status` values. Alternatively, after the user visually checks the workbooks, record aggregate acceptance in `state.yaml`; do not mark unreviewed individual screenshots approved. Either independent approval or current aggregate user acceptance permits publishing. Required missing screenshots still need an explicit waiver.

Aggregate acceptance must fingerprint all effective required captures and exception decisions. If a required file is replaced, added, deleted, or renamed, or a waiver/applicability decision changes, synchronization marks aggregate acceptance `stale`. Publishing then requires one renewed aggregate confirmation.

Never fabricate application screenshots. Use localized live interfaces for each locale that requires them. Preserve originals and create annotated copies separately. Pause for authentication, licenses, customer data, ambiguous dialogs, destructive actions, or unexpected application state.
