# PDF

PDF uses one built-in pipeline: workspace `book.json` and `Summary.md`, HonKit 6.2.2 ebook HTML, generated-copy image optimization, Calibre 9.14.0 pagination, repaired bookmarks, qpdf 12.4.1 lossless optimization, and structural checks. Workspace adapters cannot replace this pipeline.

## Commands

```powershell
mdoc pdf init --workspace <workspace>
mdoc pdf doctor --workspace <workspace>
mdoc pdf build --workspace <workspace> --book <book> --locale <locale> --scope book --yes
mdoc pdf build --workspace <workspace> --book <book> --locale <locale> --scope page --target Main/Topic.md --yes
mdoc pdf build --file <file.md>
mdoc pdf check --workspace <workspace> --pdf <file> --book <book> --locale <locale>
mdoc pdf clean --workspace <workspace>
```

`page` and `section` scopes require `--target` containing a `Summary.md` target. They retain full-book chapter numbers but normalize bookmark levels relative to the selected root. Scoped builds materialize only the selected Markdown files, their referenced local resources, configured styles, and resources recursively referenced by those styles; full-book builds retain the complete locale tree. When a target appears more than once, the first match is used and a notice is reported; pass `--summary-line <line>` to select an exact entry. `pdf check` accepts the same `--scope`, `--target`, and `--summary-line` arguments. Batch builds use `--all-locales` or `--all-books`; configured concurrency defaults to three and may be reduced by the memory guard, which reserves 2 GiB and budgets 4 GiB per build. `--jobs` overrides the configured value and `--force-jobs` bypasses the guard.

`--file` is a separate standalone mode. It accepts any readable `.md` or `.markdown` path without loading a workspace, book, locale, Summary, or `book.json`. Relative resources resolve from the Markdown file, including `../`; absolute Windows paths, UNC paths, and `file://` resources are also supported. Links to other Markdown files become plain text and are reported without pulling those files into the PDF. Missing resources are findings unless `--strict-resources` is used.

Standalone output defaults to `%TEMP%\mdoc\<name>.pdf` and overwrites atomically. Use `--output`, `--no-overwrite`, `--language`, `--font-family`, or `--pdf-config <yaml>` as needed. The optional YAML accepts `paper_size`, `margins_pt`, `image_optimization`, `optimization`, and `bookmarks`; unknown fields are ignored with notices and invalid known values fail. Title and language are inferred from at most the first five effective content lines. Standalone builds skip workspace business and structural checks but require a nonempty Calibre result and add one root bookmark.

## Configuration

`pdf init` creates `.mdoc/workspace-draft.yaml`; it never edits workspace authority directly. Apply and confirm the draft through the normal workspace flow. Existing schema-version 1 workspaces remain valid without `pdf`, but PDF commands require it. Per-book `pdf` values override `pdf.defaults` recursively. Every selected locale must contain a valid `book.json` with `title` and `language`.

The default image profile is 180 DPI, maximum width 1048 pixels, minimum source size 20480 bytes, JPEG quality 75, 4:4:4 subsampling, white transparency flattening, and no upscaling. Only generated copies are changed. Generated PDF image objects request smooth viewer interpolation. qpdf recompresses Flate streams at level 9 and generates object streams; it does not optimize images.

The default A4 margins are 67 points on the left and right and 36 points on the top and bottom.

Each Markdown page keeps the HonKit and Calibre document boundary. Within a page, images and figures stay indivisible, tables may split between rows, and code blocks may split between lines. Table rows remain intact and table headers repeat where the PDF renderer supports it.

## Results

Default workspace PDFs are written below `.mdoc/artifacts/pdf/<book>/<locale>/`. Existing workspace output requires `--yes`; `--no-overwrite` skips it. Successful large intermediates are removed unless `--keep-work`; failed work is retained unless `--discard-work`. Cleanup removes expired workspace work, old reports, and default standalone PDFs under `%TEMP%\mdoc`; it never removes workspace final PDFs or explicitly selected standalone output paths.

Workspace build reports include durations for preparation, HonKit, image optimization, Calibre, outline repair, qpdf, structural checks, output replacement, cleanup, and the complete build.

Permanent checks cover parseability, nonempty pages, visible TOC destinations, bookmark destinations matching the TOC, embedded fonts with Unicode mappings, and replacement characters. Missing local resources are findings and do not fail a completed PDF unless `--strict-resources` is used. PDF generation is not currently required for normal task publication.
