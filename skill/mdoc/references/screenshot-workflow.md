# Screenshot Workflow

Screenshots are declared in `task.yaml` and captured under the task directory. The CLI owns synchronization and acceptance; the GUI assistant is only a thin helper over the same task state.

## Capture Paths

Declared captures live at:

```text
.mdoc/tasks/<task-id>/captures/<locale>/<filename>.(png|jpg|jpeg)
```

A user may place a valid PNG or JPEG at the declared path in any session. `mdoc task continue` and the screenshot assistant both discover the file, validate its dimensions and digest, and update `task-state.json` through the shared core logic.

## Item Status

The stable item states are `pending`, `captured`, `blocked`, `needs_retake`, `waived`, `not_applicable`, and `accepted`.

`blocked`, `needs_retake`, `waived`, and `not_applicable` are explicit human states and override file existence. Use a concise reason for an exception status. A later successful capture, imported image, original-image copy, or text-editor save restores the item to `captured` and clears the exception reason. `accepted` is reserved for explicit per-image review. Normal aggregate screenshot acceptance does not mark every item accepted.

## Aggregate Acceptance

When all required screenshots are captured or explicitly waived/not applicable, run:

```powershell
mdoc task screenshots accept --workspace <manual-repository-root> --task <task-id>
```

The command copies accepted capture files into task `staging/` at their declared manual destinations, stores an aggregate manifest digest, and then continues the task. If any capture file changes later, the aggregate acceptance becomes stale and authoring/Quality Gate results depending on the screenshot set are invalidated.

## Locale Copy

If the task locale plan declares a screenshot `copy_from` strategy, the CLI copies the source locale PNG byte-for-byte into the target locale capture path. This copy is state-machine owned; agents should not hand-copy screenshots into formal manuals.

## Assistant

Use:

```powershell
mdoc task screenshots open --workspace <manual-repository-root> --task <task-id>
```

The assistant previews declared captures, captures a screen region, and calls CLI-equivalent actions for status changes and aggregate acceptance. It must not write formal manual files directly. Its generic capture workflow is available to every mdoc task: frozen-screen selection, editable corner and edge handles, drag-to-move, precision magnifier, window hover selection, current-monitor or all-monitor scope, `Ctrl+Shift+Z`, automatic selection of the next item after saving, and a draggable original/new-image comparison divider. The display settings are local to each user and do not change the shared workspace.

After aggregate screenshot acceptance, a coordinator may still open the image text editor and save a correction. mdoc refreshes the controlled capture and staging copy while preserving the aggregate screenshot acceptance; the user remains responsible for the visual correctness of that correction. Contributor edits never accept screenshots and instead make their submission stale until they submit again.

When OCR candidates include a false positive and the contributor confirms that the original image needs no replacement, use **原图作为新截图** for the selected item. It copies the original reference image byte-for-byte into that item's controlled capture path, validates the image, and clears prior text-editor layers for that item. The item is then shown as captured and can be submitted normally; the formal manual image remains untouched until coordinator acceptance and publishing.

## Shared-Workspace Collaboration

For a shared manual workspace, the coordinator creates and confirms each task. Divide screenshot work by non-overlapping manual asset paths; a practical boundary is the first directory under the book's image root. Access control comes from the file share itself; mdoc does not identify or log in contributors. Only the coordinator performs aggregate acceptance, task continuation, publishing, and final acceptance.

Generate a task-specific launcher in the shared workspace root:

```powershell
mdoc task create-contributor-launcher --workspace <manual-repository-root> --task <task-id>
```

The generated `.cmd` file uses its own location to find the shared workspace and opens contributor mode. Contributor mode can edit declared captures, set screenshot item statuses, and submit a capture manifest:

For a Windows UNC share, use the generated launcher instead of starting an mdoc `.cmd` from the UNC working directory. The launcher temporarily maps the share to a command-shell drive, then passes that mapped path to the assistant. If running the command manually, pass the complete UNC path, for example `\\server\share\manual`.

```powershell
mdoc task contribute --workspace <manual-repository-root> --task <task-id>
mdoc task screenshots submit --workspace <manual-repository-root> --task <task-id>
```

Submission records only the current capture-manifest digest under the task control directory. It does not copy files to `staging/`, alter formal manual files, run the publish state machine, or replace coordinator acceptance. Any later capture change marks that submission stale.

### Shared Text Templates

The image text editor has no product-specific replacements in the mdoc installation. To give every contributor the same reusable labels and source-label masking rules, the coordinator may place this optional file in the shared workspace:

```text
.mdoc/image-text-editor.json
```

```json
{
  "schema_version": 1,
  "templates": [
    {
      "id": "replacement-example",
      "kind": "default",
      "text": "New label",
      "sources": ["Old label"],
      "style": {
        "font": "Segoe UI",
        "font_size": 9,
        "text_color": "#000000",
        "bg_color": "#FFFFFF",
        "padding": 0,
        "align": "left",
        "line_spacing": 1.2
      }
    }
  ]
}
```

`sources` is optional. When present, the editor expands the initial background mask to cover the longest source label. The file is project data in the shared workspace and must not be committed to the mdoc repository or distributed with the mdoc package. Without it, contributors can still create personal templates in their local mdoc configuration.
