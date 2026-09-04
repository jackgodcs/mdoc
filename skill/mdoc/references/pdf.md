# PDF

PDF uses one built-in pipeline: workspace `book.json` and `Summary.md`, HonKit 6.2.2 ebook HTML, generated-copy image optimization, Calibre 9.14.0 pagination, repaired bookmarks, qpdf 12.4.1 lossless optimization, and structural checks. Workspace adapters cannot replace this pipeline.

## Commands

```powershell
mdoc pdf init --workspace <workspace>
mdoc pdf doctor --workspace <workspace>
mdoc pdf build --workspace <workspace> --book <book> --locale <locale> --scope book --yes
mdoc pdf check --workspace <workspace> --pdf <file> --book <book> --locale <locale>
mdoc pdf clean --workspace <workspace>
```

`page` and `section` scopes require `--target` containing a unique `Summary.md` target. They retain full-book chapter numbers. Batch builds use `--all-locales` or `--all-books`; configured concurrency defaults to three and may be reduced by the memory guard. `--jobs` overrides the configured value and `--force-jobs` bypasses the guard.

## Configuration

`pdf init` creates `.mdoc/workspace-draft.yaml`; it never edits workspace authority directly. Apply and confirm the draft through the normal workspace flow. Existing schema-version 1 workspaces remain valid without `pdf`, but PDF commands require it. Per-book `pdf` values override `pdf.defaults` recursively. Every selected locale must contain a valid `book.json` with `title` and `language`.

The default image profile is 180 DPI, maximum width 1128 pixels, minimum source size 20480 bytes, JPEG quality 70, 4:4:4 subsampling, white transparency flattening, and no upscaling. Only generated copies are changed. qpdf recompresses Flate streams at level 9 and generates object streams; it does not optimize images.

## Results

Default PDFs are written below `.mdoc/artifacts/pdf/<book>/<locale>/`. Existing output requires `--yes`; `--no-overwrite` skips it. Successful large intermediates are removed unless `--keep-work`; failed work is retained unless `--discard-work`. Cleanup removes expired work and old reports, never final PDFs.

Permanent checks cover parseability, nonempty pages, visible TOC destinations, bookmark destinations matching the TOC, embedded fonts with Unicode mappings, and replacement characters. Missing local resources are findings and do not fail a completed PDF unless `--strict-resources` is used. PDF generation is not currently required for normal task publication.
