# screenshots.yaml

Records screenshot requirements and acceptance state. Use stable screenshot IDs and English filenames. Each task declares pages, locale policy, entry steps, preconditions, expected state, sensitivity rules, capture state, locale state objects, file mapping, history, and review state. The GUI manifest is generated from this file and is never authoritative.

Do not mark approved when files are missing. Do not use approved status to authorize overwriting an unrelated existing file.

Locale capture status is authoritative for manual-use eligibility. A PNG under `not-applicable`, `waived`, `blocked`, or `needs-retake` may remain at the target path for preview, but it is reference-only and must be absent from `capture.files` and generated `manual_assets`. Only existing `captured` or compatible `approved` locale assets may be used in staging or formal manuals.
