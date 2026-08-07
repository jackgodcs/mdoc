# Manual Quality Gate

## Entry points

Use task mode for an initialized authoring task and book mode for an existing manual:

```powershell
python scripts/manual_lint.py check --workspace <process-workspace> --task <task-id> --phase staging --profile full
python scripts/manual_lint.py check --book-root <book-root> --product-profile <profile.yaml> --profile full
python scripts/manual_lint.py fix --book-root <book-root> --product-profile <profile.yaml> --profile full --safe
```

`check` is read-only. `fix --safe` previews unless `--apply` is supplied. Reports contain paths relative to the book root.

## Profiles and policy

- `quick`: missing targets, path case, absolute paths, filename spaces, definite Markdown/HTML defects, and placeholders.
- `full`: all quick rules plus every FAQ-derived image, layout, list, paragraph, URL, terminology, spelling, punctuation, and table rule.
- `release`: full plus configured build adapters and visual-review state.

The default is advisory and opt-in. Only an explicitly inherited `required` policy can block publishing. Tasks may add required components or raise advisory to required; they may not disable or weaken an inherited requirement.

## Rule catalog

Repository and links: `MDOC-PATH-MISSING`, `MDOC-PATH-CASE`, `MDOC-PATH-ABSOLUTE`, `MDOC-FILENAME-SPACE`, `MDOC-LINK-LEVEL`, `MDOC-LINK-AMBIGUOUS`, `MDOC-ANCHOR-MISSING`, `MDOC-ANCHOR-DUPLICATE`.

Markdown and HTML: `MDOC-HTML-IMG-SYNTAX`, `MDOC-HTML-BLOCK-SYNTAX`, `MDOC-HTML-BLANK-LINE`, `MDOC-HEADING-SYNTAX`, `MDOC-HEADING-HIERARCHY`, `MDOC-FENCE-UNCLOSED`, `MDOC-EMPHASIS-SYNTAX`, `MDOC-PLACEHOLDER`, `MDOC-LIST-COMPAT`, `MDOC-PARAGRAPH-LONG`.

Images: `MDOC-IMAGE-SYNTAX`, `MDOC-IMAGE-WIDTH`, `MDOC-IMAGE-WIDTH-STEP`, `MDOC-IMAGE-DIMENSION`, `MDOC-IMAGE-READABLE`, `MDOC-IMAGE-LOCALE-WIDTH`, `MDOC-IMAGE-INLINE-WIDTH`.

URLs, language, and terminology: `MDOC-BARE-URL`, `MDOC-AUTOLINK-POLICY`, `MDOC-PRODUCT-NAME`, `MDOC-TERM-FORBIDDEN`, `MDOC-TERM-CASE`, `MDOC-TERM-INCONSISTENT`, `MDOC-SPELLING`, `MDOC-SPELLING-UNAVAILABLE`, `MDOC-LOCALE-PUNCT`, `MDOC-PUNCT-SPACING`, `MDOC-FULLWIDTH-MIXED`, `MDOC-QUOTE-STYLE`.

Tables: `MDOC-TABLE-SYNTAX`, `MDOC-TABLE-STYLE`, `MDOC-TABLE-HTML`, `MDOC-TABLE-VISUAL`.

## Configuration layers

Merge skill defaults, product profile, workspace/local configuration, then task/local configuration. Product rules are declarative. Supported custom pattern types are `forbidden_text`, `required_text`, `filename_pattern`, `path_pattern`, `terminology_variant`, and `punctuation`. Product rules must use a product namespace and cannot override `MDOC-*`. Configuration cannot execute arbitrary rule scripts. Build adapters are explicit command arrays in the workspace configuration.

## Baselines and suppressions

Fingerprints use rule ID, relative file, and a normalized problem object; line movement does not create a new issue. Store baselines in the process workspace, never the formal manual. Classify matches as `new`, `existing`, `touched_existing`, and `resolved`.

Use narrow inline suppression only for legitimate source text:

```markdown
<!-- mdoc-lint-disable-next-line MDOC-PRODUCT-NAME reason="software error text" -->
```

Block suppressions use matching `mdoc-lint-disable` and `mdoc-lint-enable`. A rule ID and reason are mandatory. Do not suppress missing targets, case mismatches, absolute paths, unclosed fences, or definite HTML syntax errors; put demonstrative invalid syntax in a code block instead. Reports include suppressed findings and new suppressions.

## Safe fixes

Safe fixes may correct resolvable path case, remove an isolated image-attribute quote, normalize definite heading syntax, replace configured forbidden product-name variants in prose, add configured HTML block spacing, and convert uniquely resolvable absolute resource paths. Never automatically choose image widths, rewrite list semantics, spelling, punctuation, UI source text, ambiguous links, image content, or complex tables. Preserve encoding, BOM, and line endings.

## Builds and visual review

Build adapters are optional unless the publish policy requires them. Record success, failure, timeout, missing configuration, logs, and an input/configuration fingerprint. A successful build is not visual acceptance.

`manual_pdf_check.py` is the generated-PDF problem finder. It scans every page, renders only problem pages for a local viewer, maps findings to Markdown where possible, and supports narrowly confirmed ignores. See `pdf-check.md`.

## Reports

JSON is authoritative; Markdown is the human summary. Each finding includes rule ID, severity, confidence, baseline status, locale, relative file, line, message, evidence, suggested fix, fix capability, suppression state, and fingerprint. `not_requested`, `not_configured`, and `stale` must never be presented as passed.
