# Screenshot Workflow

Screenshots are declared in `task.yaml` and captured under the task directory. The CLI owns synchronization and acceptance; the GUI assistant is only a thin helper over the same task state.

## Capture Paths

Declared captures live at:

```text
.mdoc/tasks/<task-id>/captures/<locale>/<filename>.png
```

A user may place a valid PNG at the declared path in any session. `mdoc task continue` and the screenshot assistant both discover the file, read its PNG header and digest, and update `task-state.json` through the shared core logic.

## Item Status

The stable item states are `pending`, `captured`, `needs_retake`, `waived`, `not_applicable`, and `accepted`.

`needs_retake`, `waived`, and `not_applicable` are explicit human states and override file existence. `accepted` is reserved for explicit per-image review. Normal aggregate screenshot acceptance does not mark every item accepted.

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

The assistant previews declared captures, captures a screen region, and calls CLI-equivalent actions for status changes and aggregate acceptance. It must not write formal manual files directly.
