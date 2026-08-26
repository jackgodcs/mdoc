# Adapters

Generators and build adapters are declared in `.mdoc/workspace.yaml` and run by mdoc in controlled directories. A task cannot provide custom executable validation scripts.

## Command Shape

Adapter `command` must be an argument array. The first item must be `runtime:<id>` and the runtime executable must be declared in `.mdoc/workspace.local.yaml`.

The second item is a repository-relative script path. mdoc copies that script into an isolated tools directory and runs the copied script, not an arbitrary shell string.

## Generators

Generators run during `task define`, produce files in an isolated output directory, and must match the declared output root, locale, kind, and filename pattern. The expanded outputs are frozen into the task manifest and later imported into staging byte-for-byte.

## Builds

Build adapters run against a materialized candidate book and receive `MDOC_ARTIFACT_DIR`. They must write the declared artifact inside that directory. The build record stores command output, adapter digest, script digest, candidate digest, artifact path, artifact digest, and duration.

Use `artifact_kind: pdf` when the artifact is a PDF that must also go through PDF Check in release/full review workflows.

## Isolation

Adapters receive a small environment containing temporary directories and mdoc-provided variables. Do not rely on user shell aliases, current interactive directories, or global Python packages.
