# Configuration Overview

## Load order

1. workspace.yaml
2. product profile referenced by workspace.yaml
3. workspace.local.yaml
4. task.yaml
5. task.local.yaml

Later layers override only compatible fields. Relative paths are resolved against the file that declares them unless the schema says otherwise.

## Files

- workspace.yaml: shared workspace identity and repository binding.
- workspace.local.yaml: machine-specific application and local paths.
- product-profile.yaml: product manual layout, locales, style, and policy.
- task.yaml: operation, module/feature scope, interaction and capture modes.
- task.local.yaml: task-specific machine paths and example data.
- sources.yaml: evidence registry and snapshot policy.
- structure.yaml: approved pages, hierarchy, templates, and summary placement.
- screenshots.yaml: capture tasks and acceptance state.
- terminology.csv: multilingual terminology.
- decisions.yaml: confirmed decisions.
- state.yaml: phase and check status.
- publish-plan.yaml: planned add/update/delete operations; delete entries are proposals only.

## User modes

Guided asks one decision at a time. Review validates prepared files and summarizes only meaningful questions. Automation continues while configurations are complete, but still stops for deletion, overwrite conflicts, sensitive actions, authentication, scope expansion, and critical uncertainty.

See references/config/ for field-level guidance. Templates live under assets/templates and neutral complete examples under assets/examples.

