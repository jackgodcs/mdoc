# Screenshot Workflow

Support manual, assisted, and automated capture; assisted is the recommended default.

## State and assistant files

Keep `screenshots.yaml` as the only authoritative screenshot state. `screenshot-assistant.json` is a generated read model and `screenshot-assistant.local.json` stores only GUI preferences and local notes. Generate `open-screenshot-assistant.cmd` under `.work/greenvalley-manual/<task-id>/`. Do not generate Markdown screenshot workbooks.

The GUI is the human capture interface. For every independent locale, the left pane always shows the task's complete screenshot list in the same manifest order. Locale-specific completion and exception states are shown in the status column and must never hide list items. The GUI also shows concise requirements, previews the current target PNG, and records exception states.

## Capture synchronization

Run `scripts/screenshot_state.py sync <process-workspace> <task-id>`. It must:

- scan only the declared target `.png` path for each locale;
- treat file existence as captured without reading image contents;
- set ordinary missing captures back to pending and remove stale file mappings;
- preserve explicit `not-applicable`, `waived`, and `blocked` decisions while the target file is absent;
- prefer an actual target PNG over an earlier exception status;
- refresh the generated GUI manifest while preserving local preferences;
- leave every individual `review.status` unchanged;
- never copy captures into the formal manual.

Human recapture may directly replace the formal target PNG. The agent must not silently overwrite captures itself.

## Status and exceptions

Capture statuses are `pending`, `captured`, `needs-retake`, `approved`, `not-applicable`, `waived`, and `blocked`. `not-applicable` and `waived` require explicit user confirmation. `blocked` remains incomplete. Missing optional screenshots do not block capture completion.

## Review and acceptance

Content review is independent and runs only when requested. It may update individual `review.status` values. Alternatively, after the user visually checks the previews in the assistant, record aggregate acceptance in `state.yaml`; do not mark unreviewed individual screenshots approved. Either independent approval or current aggregate user acceptance permits publishing. Required missing screenshots still need an explicit waiver.

## GUI capture behavior

The assistant captures the monitor containing its window by default; the user may choose the whole virtual desktop. It hides itself before freezing the desktop, supports mixed-DPI and negative monitor coordinates, and saves the selected physical-pixel rectangle directly to the declared PNG target. Existing targets always require overwrite confirmation. Saving uses a temporary PNG followed by atomic replacement. A task-local lock prevents two assistant instances from updating the same task.

Use `Ctrl+Shift+Z` as the screenshot shortcut to avoid conflicts with common communication applications.

Task dialogs opened by the assistant must be centered over the assistant window using absolute screen coordinates, including when the window is on a secondary monitor.

Aggregate acceptance must fingerprint all effective required captures and exception decisions. If a required file is replaced, added, deleted, or renamed, or a waiver/applicability decision changes, synchronization marks aggregate acceptance `stale`. Publishing then requires one renewed aggregate confirmation.

Never fabricate application screenshots. Use localized live interfaces for each locale that requires them. Preserve originals and create annotated copies separately. Pause for authentication, licenses, customer data, ambiguous dialogs, destructive actions, or unexpected application state.
