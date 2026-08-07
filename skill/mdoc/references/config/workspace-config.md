# workspace.yaml

Defines the reusable workspace identity, product profile reference, repository binding, active manual version, task root, and default modes. It contains shareable paths relative to the workspace, not machine-specific executable or source-package paths.

Required: schema_version, workspace.id, product.profile, repository.root, manual.active_version, tasks.root.

Machine-only values belong in workspace.local.yaml. On first use the skill generates this file through guided questions. On later runs it validates existing values and asks only about problems.

