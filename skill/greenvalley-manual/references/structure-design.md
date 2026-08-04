# Structure Design

## Task modes

- create_module: may create a new module root, categories, pages, and summary hierarchy.
- add_feature: add only the new page and required parent index/summary entries.
- update_feature: update declared pages/assets without unrelated reformatting.
- add_locale: reproduce an existing source structure using the configured locale strategy.

## Page templates

Use module-index, category-index, operation, interface, workflow, concept, and faq. Reserve api-reference for later extension. Assign a template to each page in structure.yaml.

## Granularity

Give independent entries, independent operations, or separately illustrated functions their own pages. Merge short, tightly related editing operations. Each category should have an index when that matches the repository convention.

## Incremental protection

Do not restructure neighbors, reorder existing summary entries, rename assets, normalize unrelated formatting, or fix unrelated inconsistencies. Report such opportunities separately.

