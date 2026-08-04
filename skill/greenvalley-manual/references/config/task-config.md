# task.yaml

Defines one manual-authoring task.

Required: schema_version, task.id, task.operation, interaction.mode, capture.mode, target, and scope.

operation must be create_module, add_feature, update_feature, or add_locale. interaction.mode is guided, review, or automation. capture.mode is manual, assisted, or automated.

Use repository-relative module paths. Keep external absolute paths in task.local.yaml and refer to them by IDs in sources.yaml.

