# Screenshot Workflow

Support manual, assisted, and automated capture; assisted is the recommended default.

## State and assistant files

Keep `screenshots.yaml` as the only authoritative screenshot state. `screenshot-assistant.json` is a generated read model and `screenshot-assistant.local.json` stores only GUI preferences and local notes. Generate `open-screenshot-assistant.cmd` under `.work/greenvalley-manual/<task-id>/`. Do not generate Markdown screenshot workbooks.

The GUI is the human capture interface. For every independent locale, the left pane always shows the task's complete screenshot list in the same manifest order. Locale-specific completion and exception states are shown in the status column and must never hide list items. The GUI also shows concise requirements, previews the current target PNG, and records exception states.

Split the image preview horizontally into a read-only Chinese reference pane and a current-locale target pane. Use an initial 40/60 width ratio and a custom full-height divider with a 10-pixel hit area, centered 2-pixel line, three grip dots, horizontal-resize cursor, hover/drag highlighting, and double-click reset to 40/60. Constrain the ratio to 20/80 through 80/20 and persist it only in `screenshot-assistant.local.json`. During drag, refresh at roughly 50 milliseconds with bilinear scaling; cancel any pending fast refresh and render with Lanczos on release. Both panes always refer to the same screenshot ID: the left pane loads `zh`, while the right pane loads the currently selected locale. Screenshot, exception, open-image, and open-directory actions continue to target only the current locale; provide a separate read-only Open Reference Image action for the Chinese pane. Refresh and independently fit both images after selection, locale, capture, status, divider, or window-size changes.

When the current locale is not `zh`, show `Copy to <locale>` in the Chinese reference header. Hide the button for `zh`. Enable it only when the Chinese PNG exists and has `usable_in_manual: true`. Copy the original PNG bytes to the selected locale's declared target with a same-directory temporary file and atomic replacement; never re-encode it. Confirm before replacing an existing target. If the target locale is under an exception or retake state, combine the overwrite and status-restoration details into one confirmation. On success, set the target locale to `captured`, clear its exception reason, add a history record even for `captured` to `captured`, make aggregate acceptance stale, refresh both previews, show a temporary status message, and honor auto-advance.

## Capture synchronization

Run `scripts/screenshot_state.py sync <process-workspace> <task-id>`. It must:

- scan only the declared target `.png` path for each locale;
- treat file existence as captured without reading image contents only when no explicit exception or retake decision exists;
- set ordinary missing captures back to pending and remove stale file mappings;
- preserve explicit `not-applicable`, `waived`, `blocked`, and `needs-retake` decisions whether or not the target PNG exists;
- treat human status decisions as authoritative over file existence; an existing PNG under an exception state is reference-only and must not be written into a manual;
- refresh the generated GUI manifest while preserving local preferences;
- leave every individual `review.status` unchanged;
- never copy captures into the formal manual.

Human recapture may directly replace the target PNG. The agent must not silently overwrite captures itself. Successful recapture of an exception or retake item changes that locale to `captured`, clears its exception reason, restores its manual-use eligibility, and makes aggregate acceptance stale.

## Status and exceptions

Capture statuses are `pending`, `captured`, `needs-retake`, `approved`, `not-applicable`, `waived`, and `blocked`. Exception and retake decisions require a reason and are locale-specific. `not-applicable` and `waived` count as explicit completion decisions; `blocked` and `needs-retake` remain incomplete. Missing optional screenshots do not block capture completion. Only an existing PNG whose locale state is `captured` or compatible `approved` is eligible for manual writing.

An existing PNG may remain at its target path under `not-applicable`, `waived`, `blocked`, or `needs-retake`. Keep it available as historical reference, omit it from the `capture.files` map and generated `manual_assets`, and never copy or reference it in staging or formal manuals. Restoring an item chooses `captured` when the target PNG exists and `pending` when it does not.

## Review and acceptance

Content review is independent and runs only when requested. It may update individual `review.status` values. Alternatively, after the user visually checks the previews in the assistant, record aggregate acceptance in `state.yaml`; do not mark unreviewed individual screenshots approved. Either independent approval or current aggregate user acceptance permits publishing. Required missing screenshots still need an explicit waiver. Required `blocked` and `needs-retake` items prevent acceptance even when an older PNG remains on disk.

## GUI capture behavior

The assistant captures the monitor containing its window by default; the user may choose the whole virtual desktop. It hides itself before freezing the desktop, supports mixed-DPI and negative monitor coordinates, and saves the selected physical-pixel rectangle directly to the declared PNG target. Existing targets always require overwrite confirmation. Saving uses a temporary PNG followed by atomic replacement. A task-local lock prevents two assistant instances from updating the same task.

After an initial drag, the frozen capture overlay keeps the selection editable until explicit completion. Show four corner and four edge handles at all times. Each edge and corner supports directional resizing, the selection interior supports movement, and crossing an opposite edge reverses the active resize direction without ending the drag. Mouse release preserves the current selection; only Finish, Enter, or a double-click inside the selection captures it. Render the outside area with a precomputed, smoothly alpha-blended dark copy of the frozen image and place the original image crop over the selected area. Do not use Canvas bitmap stipple patterns for dimming.

During initial selection and edge or corner resizing, show a Canvas-based magnifier sampled from the original frozen image: 31 by 31 physical pixels, enlarged 6 times with nearest-neighbor scaling. Include a center crosshair, the active horizontal or vertical boundary, pointer coordinates, and selection dimensions. Place it near the pointer with right-bottom, left-bottom, right-top, then left-top fallback. Hide it while moving the whole selection and after mouse release. Overlay shading, borders, handles, labels, controls, and the magnifier must never be included in the saved PNG.

At desktop freeze time, also enumerate a Z-ordered snapshot of eligible Windows top-level windows. Prefer `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)` and fall back to `GetWindowRect`; store visible bounds, title or process name, clipping state, and handle in frozen-image coordinates. Exclude the assistant, overlay, invisible, minimized, click-through, undersized, desktop, taskbar, tooltip, IME, and `#32768` menu windows. Never query live window geometry after the overlay appears.

Before a formal selection exists, resolve the topmost frozen window under the pointer. Require an 80-millisecond stable hover before showing its bright preview, blue border, title, dimensions, and optional clipped marker. A click locks it as the editable selection; a double-click completes it. Pointer movement beyond four physical pixels after press switches to free rectangular selection from the press point. Reset returns to stable-hover window selection. A window extending outside the frozen capture is clipped without changing capture scope or taking a new desktop image.

Use `Ctrl+Shift+Z` as the screenshot shortcut to avoid conflicts with common communication applications.

On Windows, register `Ctrl+Shift+Z` with `RegisterHotKey` and `MOD_NOREPEAT` while the assistant is running. Receive `WM_HOTKEY` on a background message thread and marshal capture requests to the Tk main thread through a queue. Registration failure or unexpected thread exit degrades to the toolbar and window-local shortcut without automatic retry. Always call `UnregisterHotKey` during normal shutdown; Windows reclaims the registration if the process terminates unexpectedly.

For a global trigger, record the current foreground window and pointer location, hide the assistant without activating it, wait 120 milliseconds for key release, and capture the pointer's monitor when the scope is Current Monitor. Successful capture restores and activates the assistant for the next requirement. Cancellation restores the assistant's prior visible, minimized, or hidden state without activation and attempts to return focus to the recorded foreground window. Use a shared busy flag, modal guard, and 300-millisecond deduplication window for global, local, and toolbar triggers.

Task dialogs opened by the assistant must be centered over the assistant window using absolute screen coordinates, including when the window is on a secondary monitor. The assistant must visibly mark retained exception images as reference-only and forbidden for manual use. If the user captures an exception item, explain that successful saving restores `captured` status before opening the overlay.

Aggregate acceptance must fingerprint all effective required captures and exception decisions. If a required file is replaced, added, deleted, or renamed, or a waiver/applicability decision changes, synchronization marks aggregate acceptance `stale`. Publishing then requires one renewed aggregate confirmation.

## Writing eligibility

Use the generated manifest's `manual_assets` list or each locale entry's `usable_in_manual` flag as the only screenshot input for writing. Do not infer eligibility from target-path existence. Omit waived and not-applicable images without leaving placeholders, and keep the surrounding instructions understandable without the image. Report a warning when the omitted image leaves critical instructions unclear. `blocked` and `needs-retake` required images block publishing. Use `scripts/screenshot_state.py apply-staging-eligibility <workspace> <task-id> <locale>` to remove ineligible image tags only from the task's fixed `staging/` directory, then use `check-usage` to validate staging Markdown. If a formal manual already references an image that later becomes ineligible, report a publish conflict and request explicit deletion or update confirmation; never remove the formal reference automatically.

Never fabricate application screenshots. Use localized live interfaces for each locale that requires them. Preserve originals and create annotated copies separately. Pause for authentication, licenses, customer data, ambiguous dialogs, destructive actions, or unexpected application state.
