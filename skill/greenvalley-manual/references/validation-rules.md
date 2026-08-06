# Validation Rules

## Four layers

1. Configuration: schemas, overrides, IDs, allowed roots, and state consistency.
2. Structure: declared pages, category indexes, locale structure, scope boundaries, and preservation.
3. Markdown/resources: summary links, image links, internal links, readable images, placeholders, and local paths.
4. Content: localized UI terms, cross-locale scope, evidence, limitations, terminology, spelling, and locale style.

## Optional Quality Gate

Quality Gate is available for task staging, a formal repository, or a standalone existing book. It does not run or block publishing unless configuration or the user requests it. Missing configuration defaults to `mode: advisory`, `auto_run: false`, and `default_profile: full`.

Profiles are `quick`, `full`, and `release`. `quick` runs deterministic repository checks. `full` covers every FAQ-derived static or review rule. `release` adds configured HTML/PDF builds and visual review. See `manual-lint.md`.

Only `mode: required` combined with `publish_policy.required_before_publish: true` creates a publishing prerequisite. A task may tighten an inherited policy but may not weaken one. Advisory errors remain visible but do not revoke explicit publishing authority.

## Findings

Severity and confidence are independent:

- severity: `error`, `warning`, `suggestion`, or `passed`
- confidence: `exact`, `probable`, or `review`

Baseline status is `new`, `existing`, `touched_existing`, or `resolved`. Baselines change blocking behavior only; they never turn a finding into `passed`.

## Existing screenshot rules

Automation may advance only to `ready_for_review`; only explicit user acceptance sets `accepted`. Screenshot capture requires an eligible target PNG or an explicit `not-applicable`/`waived` decision. Explicit exception states override file existence. Staging must not reference an ineligible screenshot. Existing formal references that become ineligible are conflicts and are not removed without confirmation.

When scaled-step image sizing is configured, validate PNG dimensions from the header. Every local image tag must use the deterministic width and responsive style configured by `writing.image_sizing`.

When a shared FAQ directory is configured, validation must reject FAQ-template pages stored below feature module roots, missing entries in the shared FAQ index, FAQ links placed under the feature module branch, or newly added feature FAQ links that are not the last child of the dedicated FAQ branch in `Summary.md`.
