---
name: greenvalley-manual
description: Plan, capture, write, localize, validate, and publish multilingual GreenValley product manuals. Use when creating a complete manual module, adding a feature to an existing module, updating an existing feature, adding a locale, preparing screenshot plans, or resuming a configuration-driven manual-authoring workspace.
---

# GreenValley Manual Authoring

Use a configuration-driven workflow with explicit confirmation gates. Keep the skill product-neutral; load all product names, paths, locales, repository rules, and task state from the bound workspace.

## Start or Resume

1. Locate the project binding file or ask for the process workspace location.
2. Load configuration in this order: `workspace.yaml`, product profile, `workspace.local.yaml`, `task.yaml`, `task.local.yaml`. Later layers may override only their declared fields.
3. Run `scripts/validate_config.py` before acting. If configuration is absent, initialize it from templates through guided questions. If it exists, report only invalid, stale, or conflicting values.
4. Read `references/workflow.md` and the configuration guides relevant to the current phase.
5. Continue from `state.yaml`; do not repeat confirmed decisions in `decisions.yaml`.

## Task Routing

- `create_module`: design and create a complete module hierarchy.
- `add_feature`: add the smallest possible page and index/summary entries under an existing module.
- `update_feature`: update only declared pages and assets; preserve unrelated content and formatting.
- `add_locale`: create a missing locale according to the product profile.

For incremental tasks, never restructure neighboring content, normalize unrelated formatting, rename assets, or broaden scope without confirmation.

## Confirmation Gates

Use four normal gates:

1. Workspace/product configuration, only when new or invalid.
2. Manual structure and page manifest.
3. Screenshot capture plan.
4. Screenshot acceptance.

After screenshot acceptance, automatically stage, write, localize, validate, prepare a publish plan, publish incremental changes, and validate the formal repository. Pause only for deletion, overwrite conflicts, scope expansion, critical factual uncertainty, authentication, sensitive data, or destructive application actions.

Interaction modes are `guided`, `review`, and `automation`. Screenshot modes are `manual`, `assisted`, and `automated`. Neither automation mode authorizes deletion or silent overwrite.

## Evidence and Writing

Apply this evidence priority: confirmed decisions, live target application, approved screenshots, official product materials, target-version source analysis, existing target manual, older/adjacent manuals, AI inference. Do not publish critical facts based only on inference.

Select page templates from `assets/templates/pages/`: `module-index`, `category-index`, `operation`, `interface`, `workflow`, `concept`, or `faq`. Adapt to the repository style and omit empty sections.

Generate source-locale content first, rewrite locales that require localized UI screenshots, and apply copy strategies exactly as configured. Formal manuals must not contain local absolute paths, task-workspace links, unresolved placeholders, or source-analysis implementation details irrelevant to users.

## Screenshot Workflow

Read `references/screenshot-workflow.md`. Keep `screenshots.yaml` as the machine state source and generate one concise Markdown screenshot workbook for each locale that requires independent captures. Store workbooks and original captures under the bound manual repository's `.work/greenvalley-manual/<task-id>/` directory.

Use `scripts/screenshot_workbook.py sync <process-workspace> <task-id>` to scan target PNG paths, synchronize capture existence, refresh workbook progress and cards, preserve user-note blocks, and invalidate stale visual acceptance. Capture completion means only that the target PNG exists; never inspect image content during sync. Run screenshot content review only when explicitly requested. A user may instead explicitly accept all current screenshots after visually reviewing the workbooks; record that aggregate acceptance in `state.yaml` without changing individual `review.status` values. Never fabricate software screenshots.

## Staging, Publishing, and Safety

Generate into task staging first. Compute links for their final destinations. Validate staging, produce `publish-plan.yaml`, then apply minimal formal-repository changes and validate again.

Preserve all existing manual files and assets. Any deletion requires explicit confirmation of the exact objects, even when a config requests deletion. Existing same-name content, screenshots, or summary entries are conflicts, not implicit overwrite permission.

## Quality Gate

Run configuration, structure, Markdown/resource, and content-quality checks described in `references/validation-rules.md`. Classify findings as `error`, `warning`, `suggestion`, or `passed`. Automation may advance only to `ready_for_review`; only explicit user acceptance sets `accepted`.

## Resources

- `references/configuration-overview.md`: configuration relationships and user modes.
- `references/config/`: field-by-field configuration guides.
- `references/workflow.md`: complete phase and state workflow.
- `references/product-profile.md`: product adapter contract.
- `references/structure-design.md`: structure rules and page-template selection.
- `references/screenshot-workflow.md`: capture and review rules.
- `references/validation-rules.md`: quality gates and completion definition.
- `references/deletion-policy.md`: mandatory preservation and deletion rules.
- `assets/templates/`: editable neutral templates.
- `assets/examples/`: complete neutral examples that must pass schemas.
- `schemas/`: machine-readable configuration schemas.
- `scripts/validate_config.py`: schema and cross-file validation entry point.
- `scripts/screenshot_workbook.py`: screenshot workbook generation, file-existence synchronization, and aggregate visual-acceptance fingerprints.
