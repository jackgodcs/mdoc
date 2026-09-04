---
name: mdoc
description: Plan, capture, write, validate, audit, and publish multilingual product manuals in a schema_version 1 mdoc workspace. Use for mdoc workspace setup, task continuation, screenshots, Quality Gate checks, and release-ready manual changes.
---

# mdoc Manual Authoring

Use mdoc for configuration-driven multilingual product manuals. Load product names, book roots, locales, evidence, task scope, and publishing rules from the workspace; keep the skill product-neutral.

## Normal Entry

Prefer the single CLI state machine:

```powershell
mdoc task continue --workspace <manual-repository-root> --task <task-id>
```

`task continue` is idempotent. It advances until the next required human gate, a Quality Gate finding, a publishing conflict, or `ready_for_review`.

## Authority Boundaries

- Treat each independently governed manual root as one workspace; sibling workspaces may share a higher-level VCS checkout.
- `.mdoc/workspace.yaml` is the only portable workspace authority; `.mdoc/workspace.local.yaml` is machine-local and may contain only local resources, applications, and runtimes.
- Task authority is `.mdoc/tasks/<task-id>/task.yaml` plus `.mdoc/tasks/<task-id>/task-state.json`; reports, manifests, build output, and publish plans are derived artifacts.
- Agents may write only the controlled task `staging/` area and requested local reports. Formal manual files are changed only by mdoc publishing transactions.
- Preserve every existing file in task `staging/` that is outside the current frozen manifest. Report these potentially useful files to the user, exclude them from the current task's checks and publication, and never delete them automatically.
- Do not migrate, repair, read as compatible, or recreate old mdoc protocol files. If a workspace is not schema_version 1, ask to create a fresh workspace.
- mdoc is neutral about Git/SVN. Never commit, push, tag, switch branches, or roll back a formal manual repository unless the user separately asks.

## Human Gates

There are three ordinary confirmation gates:

1. Definition confirmation after `task define` freezes the manifest.
2. Screenshot acceptance for tasks with declared screenshots.
3. Final manual acceptance after publishing reaches `ready_for_review`.

Deletion approvals, target conflicts, baseline drift, evidence uncertainty, review findings, and failed builds are exception pauses, not additional normal gates.

When a confirmed task declares screenshots, mdoc must create or refresh its task-specific one-click Windows screenshot launcher in the workspace root as soon as the task is ready for screenshot work. The launcher is part of task preparation and must not require a separate user command.

## Shared Screenshot Tasks

When a coordinator shares one manual workspace with multiple contributors, split declared screenshot targets into non-overlapping tasks. Contributors use `mdoc task contribute` or a coordinator-generated task launcher to edit captures and submit the capture manifest. They do not accept screenshots, continue tasks, publish, or finally accept tasks; those actions remain with the coordinator.

## Routing

- For workspace and task commands, read `references/workflow.md`.
- For config fields and local overrides, read `references/configuration-overview.md`.
- For screenshot capture and acceptance, read `references/screenshot-workflow.md`.
- For staging, publishing, deletion, and rollback boundaries, read `references/publishing-transactions.md`.
- For Quality Gate profiles, findings, builds, reviews, and PDF checks, read `references/quality-gate.md`.
- For PDF configuration, generation, checking, outputs, and cleanup, read `references/pdf.md`.
- For adapter security, read `references/adapters.md` before adding or changing generators/build adapters.

Keep user-facing Markdown concise and in the target manual language. Default CLI human output is Simplified Chinese; stable machine output is available only with `--json`.
