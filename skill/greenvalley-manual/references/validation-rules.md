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

Automation may advance only to ready_for_review. Capture completion requires every effective required target PNG to exist or have an explicit `not-applicable` or `waived` decision. Publishing additionally requires either independent screenshot review approval or non-stale aggregate user visual acceptance of the current screenshot fingerprint. Final completion still requires configured locales, no errors, reports, and explicit user acceptance.
