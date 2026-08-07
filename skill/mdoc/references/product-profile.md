# Product Profile Contract

A product profile adapts the generic workflow to one product manual family. It defines manual layout, locale strategy, writing style, summary behavior, screenshot policy, and preservation rules. It must not contain machine-specific absolute paths.

Required areas:

- product identity
- manual layout and index filenames
- locale source and target strategies
- Markdown/image conventions
- structure and summary depth
- preservation and deletion policy
- evidence priority defaults

When missing, inspect existing manuals and generate a draft. Ask only about rules that cannot be inferred safely. When present, validate it against the repository before starting a task.

## Screenshot display sizing

When `writing.image_sizing.mode` is `scaled_steps`, compute the reference width from the PNG header only: `original_width * scale`. Clamp it to the first and last configured step, then choose the nearest configured step; choose the lower step on a tie. When same-named locale variants calculate to different steps, `locale_variant_strategy: minimum_step` uses the smallest calculated step for every variant so localization does not enlarge the smaller capture. The generated HTML `width` must be exactly one configured step and must include `style="max-width: 100%; height: auto;"`. Boundary clamping may enlarge an image whose scaled reference is below the minimum step. This process is metadata-only and is not screenshot content review.

Use `scripts/image_sizing.py apply <staging-root>` after staging assets and Markdown exist, and `scripts/image_sizing.py check <staging-root>` during validation. Pass the configured scale and steps when they differ from the defaults.

## Optional Quality Gate

The optional `validation` block defines product terminology, builder compatibility, image limits, locale punctuation, spelling resources, declarative product patterns, and default policy. Omission means advisory, opt-in validation and causes no existing workflow change. Keep build commands and machine paths in workspace configuration, not the product profile. Product validation may add namespaced declarative rules but cannot execute scripts or override built-in `MDOC-*` semantics.
