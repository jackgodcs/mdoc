# Workflow

mdoc runs one explicit state machine through the CLI. Normal work starts from a schema_version 1 workspace and advances with `mdoc task continue --workspace <manual-repository-root> --task <task-id>`.

## Workspace Flow

1. `mdoc workspace init` creates `.mdoc/workspace-draft.yaml`.
2. Edit the draft.
3. `mdoc workspace apply` validates the draft, writes `.mdoc/cache/workspace-candidate.json`, and reports the diff.
4. `mdoc workspace confirm` rechecks the draft hash, authority hash, and normalized hash, then writes `.mdoc/workspace.yaml` atomically.
5. Use `workspace revise/apply/confirm` for later portable changes.

Machine-local values use the matching `workspace local init/apply/confirm/revise` commands. Local configuration cannot change books, task scope, Quality Gate rules, or publishing authority.

## Task Flow

1. `mdoc task create` creates `.mdoc/tasks/<task-id>/task-draft.yaml` after validating task id, book id, and intent.
2. Edit the task draft.
3. `mdoc task define` validates the draft, freezes the expanded manifest, writes `task.yaml`, creates or resets `task-state.json`, and waits for definition confirmation.
4. `mdoc task confirm-definition` claims the scope, records the workspace and definition digests, captures baselines, imports generator output, and continues.
5. `mdoc task continue` stops at screenshots, authoring, findings, publishing exceptions, or final review.
6. `mdoc task confirm-final` moves a reviewed task to `accepted` and releases its scope claim.

The terminal states are `accepted` and `cancelled`. They cannot be reopened; use a new task for later work.

## Stable States

`draft`, `waiting_for_definition_confirmation`, `waiting_for_screenshots`, `waiting_for_screenshot_acceptance`, `waiting_for_authoring`, `verifying`, `waiting_for_resolution`, `publishing`, `ready_for_review`, `accepted`, and `cancelled` are the only task states.

Waiting for user input is normal, not a failure. Business failures use stable `MDOC-*` error codes in JSON output.

## Normal Pauses

The three ordinary human gates are definition confirmation, screenshot acceptance, and final manual acceptance. Exception pauses cover exact deletion approval, target conflicts, baseline drift, missing evidence, human review findings, build failures, and interrupted publish recovery.
