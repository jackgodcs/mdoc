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

After an initial drag, the frozen capture overlay keeps the selection editable until explicit completion. Show four corner and four edge handles at all times. Each edge and corner supports directional resizing, the selection interior supports movement, and crossing an opposite edge reverses the active resize direction without ending the drag. Mouse release preserves the current selection; only Finish, Enter, or a double-click inside the selection captures it. Render the outside area with a precomputed, smoothly alpha-blended dark copy of the frozen image and place the original image crop over the selected area. Do not use Canvas bitmap stipple patterns for dimming.

During initial selection and edge or corner resizing, show a Canvas-based magnifier sampled from the original frozen image: 31 by 31 physical pixels, enlarged 6 times with nearest-neighbor scaling. Include a center crosshair, the active horizontal or vertical boundary, pointer coordinates, and selection dimensions. Place it near the pointer with right-bottom, left-bottom, right-top, then left-top fallback. Hide it while moving the whole selection and after mouse release. Overlay shading, borders, handles, labels, controls, and the magnifier must never be included in the saved PNG.

Use `Ctrl+Shift+Z` as the screenshot shortcut to avoid conflicts with common communication applications.

On Windows, register `Ctrl+Shift+Z` with `RegisterHotKey` and `MOD_NOREPEAT` while the assistant is running. Receive `WM_HOTKEY` on a background message thread and marshal capture requests to the Tk main thread through a queue. Registration failure or unexpected thread exit degrades to the toolbar and window-local shortcut without automatic retry. Always call `UnregisterHotKey` during normal shutdown; Windows reclaims the registration if the process terminates unexpectedly.

For a global trigger, record the current foreground window and pointer location, hide the assistant without activating it, wait 120 milliseconds for key release, and capture the pointer's monitor when the scope is Current Monitor. Successful capture restores and activates the assistant for the next requirement. Cancellation restores the assistant's prior visible, minimized, or hidden state without activation and attempts to return focus to the recorded foreground window. Use a shared busy flag, modal guard, and 300-millisecond deduplication window for global, local, and toolbar triggers.

Task dialogs opened by the assistant must be centered over the assistant window using absolute screen coordinates, including when the window is on a secondary monitor.

Aggregate acceptance must fingerprint all effective required captures and exception decisions. If a required file is replaced, added, deleted, or renamed, or a waiver/applicability decision changes, synchronization marks aggregate acceptance `stale`. Publishing then requires one renewed aggregate confirmation.

Never fabricate application screenshots. Use localized live interfaces for each locale that requires them. Preserve originals and create annotated copies separately. Pause for authentication, licenses, customer data, ambiguous dialogs, destructive actions, or unexpected application state.
