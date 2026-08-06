# Validation Rules

## Four layers

1. Configuration: schemas, overrides, IDs, allowed roots, state consistency.
2. Structure: declared pages and category indexes, locale structure, scope boundaries, no deletions.
3. Markdown/resources: summary links, image links, internal links, readable images, no unresolved placeholders or local absolute paths.
4. Content: localized UI terms, cross-locale scope, evidence support, required limitations, no internal implementation detail unless user-relevant.

## Severity

- error: blocks publishing or review readiness.
- warning: requires visibility but may not block.
- suggestion: optional improvement.
- passed: check succeeded.

Automation may advance only to ready_for_review. Capture completion requires every effective required locale to have an eligible target PNG in `captured`/compatible `approved` state or an explicit `not-applicable`/`waived` decision. Ordinary synchronization validates existence only, not image content, but explicit `blocked`, `needs-retake`, `not-applicable`, and `waived` states override file existence. Retained exception PNGs are reference-only. Staging Markdown must not reference an ineligible screenshot filename; required `blocked` and `needs-retake` items block publishing. Existing formal-manual references that become ineligible are reported as conflicts and are not removed without explicit confirmation. Publishing may proceed after independent screenshot review approval or non-stale aggregate user visual acceptance; aggregate acceptance represents the user's own visual verification even when individual reviews remain pending. Final completion still requires configured locales, no errors, reports, and explicit user acceptance.
