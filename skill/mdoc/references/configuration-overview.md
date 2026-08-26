# Configuration Overview

mdoc v1 uses one control directory under the manual repository root: `.mdoc/`.

## Portable Authority

`.mdoc/workspace.yaml` is the only portable workspace configuration. It contains `schema_version: 1`, workspace identity, product identity, explicit books, locales, writing settings, screenshot settings, Quality Gate policy, publishing policy, generators, build adapters, and retention settings.

All portable paths are repository-relative. Book roots, locale roots, content roots, asset roots, navigation paths, generator inputs, adapter scripts, and adapter artifacts must stay inside the manual repository or the controlled adapter sandbox chosen for that field.

## Local Authority

`.mdoc/workspace.local.yaml` is machine-local. It may provide local resources, applications, and runtimes. It cannot change registered books, task scope, Quality Gate rules, publishing authority, or generator/build definitions.

Local resource changes affect only commands that actually consume those resources. Local editor and UI preferences must not invalidate completed task output.

## Draft Governance

Portable and local configuration changes use the same pattern:

```powershell
mdoc workspace revise --workspace <manual-repository-root>
mdoc workspace apply --workspace <manual-repository-root>
mdoc workspace confirm --workspace <manual-repository-root>
```

The local variant inserts `local` after `workspace`. `apply` writes a candidate JSON file under `.mdoc/cache/`; `confirm` rechecks the draft hash, authority hash, and normalized candidate hash before replacing the authority file.

## Task Files

Task authority is `.mdoc/tasks/<task-id>/task.yaml` and `.mdoc/tasks/<task-id>/task-state.json`. The task draft is editable before `task define`; the defined task freezes a manifest so `task continue` can verify scope, baselines, staging, screenshots, Quality Gate input, and publishing transactions deterministically.
