# Standalone Image Editor

Launch `Open-mdoc-Image-Editor.cmd` from the installed mdoc skill, double-click it to choose an image, or drag one supported image onto the CMD. The tool accepts PNG, JPEG, BMP, GIF first frames, TIFF, and WebP as inputs and exports PNG.

The standalone editor reuses the screenshot editor's canvas, text and image layers, templates, zoom, pan, resize, color sampling, undo, redo, and existing shortcuts. It never loads an mdoc workspace or task and must not synchronize screenshots, accept captures, publish files, or change task state.

**Save Image** and **Save Image As** write a flattened image in the input format by default, with PNG available from Save As. They do not create project data. **Save Project** and **Save Project As** write the same flattened image plus `<name>.mdoc-image-edit.json` and `<name>.mdoc-image-edit-assets/`, including the unflattened PNG base snapshot and image-layer assets. Managed candidate mode always overwrites the supplied candidate using the format declared by its extension.

Opening an image automatically loads only its exact same-name sidecar project. The JSON cannot be selected directly. A project is loaded only when its recorded dimensions match the selected image. Invalid fields and image layers with missing assets are skipped; the remaining valid layers load normally. No project error or missing asset causes automatic deletion or repair.

Personal strings and image templates are shared with the screenshot editor through the current user's mdoc template store. A workspace `image-text-editor.json` may be imported explicitly for the current standalone session; it does not bind the editor to that workspace or modify the selected configuration.
