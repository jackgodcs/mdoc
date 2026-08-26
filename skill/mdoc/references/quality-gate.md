# Quality Gate

Quality Gate is the single checking engine for mdoc tasks and existing books. Task verification does not run a second validator; it calls Quality Gate in task, published-task, or book context.

## Profiles

- `standard`: deterministic checks for UTF-8 Markdown, placeholders, local absolute paths, internal links, summary reachability, locale rules, and declared exact custom rules.
- `full`: `standard` plus configured human reviews such as factual accuracy, language quality, visual accuracy, and PDF visual quality.
- `release`: `full` plus at least one real build adapter run. If no adapter is configured, release cannot pass.

Tasks must pass at least `standard` before publishing. Existing-book checks are report-first; use `--enforce` when a nonzero exit code is required for automation.

## Findings

Findings keep severity and confidence separate:

- Severity: `error` or `warning`.
- Confidence: `exact`, `probable`, or `review`.

Only executed human review may produce `human_accepted`. A review finding that has not been performed remains `waiting_for_review` or `stale`; it must not be shown as passed.

## Safe Fixes

Safe fixes are deterministic and limited to task staging. Current built-in safe fixes may add a missing final newline to staged Markdown. Safe fixes must not edit formal manual files.

## Build And PDF Checks

Build adapters receive a materialized candidate book in an isolated directory and must emit a declared artifact. A release profile runs a real build before publishing and again in published-task context after the transaction.

When a build adapter declares `artifact_kind: pdf`, mdoc runs PDF Check against the emitted PDF and includes its counts in the build record. Effective PDF errors block the Quality Gate.

## Command Examples

```powershell
mdoc quality check --workspace <manual-repository-root> --task <task-id> --json
mdoc quality check --workspace <manual-repository-root> --book <book-id> --json
mdoc quality check --workspace <manual-repository-root> --book <book-id> --enforce --json
```
