from __future__ import annotations

import json
import inspect
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

try:
    from .standalone_image_editor import StandaloneImageEditor, load_project, project_asset, project_paths
except ImportError:
    from standalone_image_editor import StandaloneImageEditor, load_project, project_asset, project_paths


class StandaloneImageEditorTests(unittest.TestCase):
    def test_managed_candidate_uses_the_input_as_image_and_project_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "candidate.png"
            Image.new("RGBA", (20, 10), "white").save(source)
            editor = StandaloneImageEditor.__new__(StandaloneImageEditor)
            editor.source_path = editor.image_output_path = editor.project_image_path = source
            editor.managed_candidate = True
            editor.base_image = Image.new("RGBA", (20, 10), "red")
            editor.layers = []; editor.composite = lambda: editor.base_image.copy(); editor.allow_overwrite_var = types.SimpleNamespace(get=lambda: True)
            editor.project_dirty = True; editor.image_dirty = editor.dirty = True; editor._update_title = lambda: None; editor._image_destination = lambda *_args: self.fail("managed save must not ask for an output path")
            module = sys.modules[StandaloneImageEditor.__module__]
            with patch.object(module.messagebox, "showinfo"):
                self.assertTrue(editor.save_image())
            with Image.open(source) as saved:
                self.assertEqual((255, 0, 0, 255), saved.convert("RGBA").getpixel((0, 0)))
            self.assertEqual(source, editor.project_image_path)
            build = inspect.getsource(StandaloneImageEditor._build)
            self.assertIn('state="disabled" if self.managed_candidate', build)
            self.assertIn("if not self.managed_candidate", build)

    def test_managed_jpeg_candidate_is_overwritten_as_jpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "candidate.jpg"
            Image.new("RGB", (20, 10), "white").save(source, format="JPEG")
            editor = StandaloneImageEditor.__new__(StandaloneImageEditor)
            editor.source_path = editor.image_output_path = editor.project_image_path = source; editor.managed_candidate = True
            editor.base_image = Image.new("RGBA", (20, 10), (255, 0, 0, 128)); editor.base_has_alpha = True
            editor.layers = []; editor.composite = lambda: editor.base_image.copy(); editor.allow_overwrite_var = types.SimpleNamespace(get=lambda: True)
            editor.project_dirty = True; editor.image_dirty = editor.dirty = True; editor._update_title = lambda: None; editor._image_destination = lambda *_args: self.fail("managed save must not ask for an output path")
            module = sys.modules[StandaloneImageEditor.__module__]
            with patch.object(module.messagebox, "showinfo"):
                self.assertTrue(editor.save_image())
            with Image.open(source) as saved:
                self.assertEqual("JPEG", saved.format); self.assertEqual("RGB", saved.mode)
            self.assertFalse(source.with_suffix(".png").exists())

    def test_normal_save_defaults_to_source_format_and_shares_project_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "source.jpg"; output = root / "source-edited.jpg"
            Image.new("RGB", (20, 10), "white").save(source)
            editor = StandaloneImageEditor.__new__(StandaloneImageEditor)
            editor.source_path = source; editor.image_output_path = editor.project_image_path = None; editor.managed_candidate = False
            editor.base_image = Image.new("RGBA", (20, 10), "red"); editor.base_has_alpha = True; editor.layers = []; editor.composite = lambda: editor.base_image.copy()
            editor.allow_overwrite_var = types.SimpleNamespace(get=lambda: False); editor.project_dirty = True; editor.image_dirty = editor.dirty = True; editor._update_title = lambda: None
            editor._image_destination = lambda title, initial: output
            module = sys.modules[StandaloneImageEditor.__module__]
            with patch.object(module.messagebox, "showinfo"):
                self.assertTrue(editor.save_image())
            with Image.open(output) as saved: self.assertEqual("JPEG", saved.format)
            self.assertEqual(output, editor.image_output_path); self.assertEqual(output, editor.project_image_path); self.assertTrue(source.is_file())

    def test_save_as_can_change_jpeg_output_to_png(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "source.jpg"; output = root / "converted.png"
            Image.new("RGB", (20, 10), "white").save(source)
            editor = StandaloneImageEditor.__new__(StandaloneImageEditor)
            editor.source_path = source; editor.image_output_path = root / "existing.jpg"; editor.project_image_path = root / "existing.jpg"; editor.managed_candidate = False
            editor.base_image = Image.new("RGBA", (20, 10), (255, 0, 0, 128)); editor.base_has_alpha = True; editor.layers = []; editor.composite = lambda: editor.base_image.copy()
            editor.allow_overwrite_var = types.SimpleNamespace(get=lambda: False); editor.project_dirty = True; editor.image_dirty = editor.dirty = True; editor._update_title = lambda: None
            editor._image_destination = lambda title, initial: output
            module = sys.modules[StandaloneImageEditor.__module__]
            with patch.object(module.messagebox, "showinfo"):
                self.assertTrue(editor.save_image(save_as=True))
            with Image.open(output) as saved: self.assertEqual("PNG", saved.format); self.assertEqual("RGBA", saved.mode)
            self.assertEqual(output, editor.image_output_path); self.assertEqual(output, editor.project_image_path)

    def test_supported_extensions_write_the_declared_encoding(self) -> None:
        expectations = {".png": ("PNG", "RGBA"), ".jpg": ("JPEG", "RGB"), ".jpeg": ("JPEG", "RGB"), ".bmp": ("BMP", "RGB"), ".gif": ("GIF", "P"), ".tif": ("TIFF", "RGBA"), ".tiff": ("TIFF", "RGBA"), ".webp": ("WEBP", "RGBA")}
        with tempfile.TemporaryDirectory() as temporary:
            editor = StandaloneImageEditor.__new__(StandaloneImageEditor)
            editor.base_image = Image.new("RGBA", (8, 6), (255, 0, 0, 128)); editor.composite = lambda: editor.base_image.copy()
            editor.managed_candidate = False
            module = sys.modules[StandaloneImageEditor.__module__]
            with patch.object(module.messagebox, "showerror"):
                for suffix, expected in expectations.items():
                    output = Path(temporary) / f"result{suffix}"
                    saved, flattened = editor._write_image(output)
                    self.assertTrue(saved, suffix); self.assertEqual(suffix in {".jpg", ".jpeg", ".bmp", ".gif"}, flattened, suffix)
                    with Image.open(output) as image:
                        self.assertEqual(expected[0], image.format, suffix); self.assertEqual(expected[1], image.mode, suffix)

    def test_ordinary_lossy_container_overwrite_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.webp"
            Image.new("RGBA", (8, 6), (255, 0, 0, 128)).save(source, format="WEBP", lossless=True)
            editor = StandaloneImageEditor.__new__(StandaloneImageEditor)
            editor.source_path = source; editor.image_output_path = editor.project_image_path = None; editor.managed_candidate = False
            editor.base_image = Image.new("RGBA", (8, 6), "red"); editor.composite = lambda: editor.base_image.copy()
            editor.allow_overwrite_var = types.SimpleNamespace(get=lambda: True); editor.project_dirty = True; editor.image_dirty = editor.dirty = True; editor._update_title = lambda: None
            module = sys.modules[StandaloneImageEditor.__module__]
            with patch.object(module.messagebox, "askokcancel", return_value=False) as confirm, patch.object(module.messagebox, "showinfo"):
                self.assertFalse(editor.save_image())
            confirm.assert_called_once()
            with Image.open(source) as saved: self.assertEqual("WEBP", saved.format)

    def test_managed_candidate_does_not_prompt_for_flattened_overwrite(self) -> None:
        editor = StandaloneImageEditor.__new__(StandaloneImageEditor)
        editor.managed_candidate = True; editor.source_path = Path("candidate.webp")
        module = sys.modules[StandaloneImageEditor.__module__]
        with patch.object(module.messagebox, "askokcancel") as confirm:
            self.assertTrue(editor._confirm_flattened_overwrite(editor.source_path))
        confirm.assert_not_called()

    def test_full_image_path_is_in_window_title_not_the_toolbar(self) -> None:
        editor = StandaloneImageEditor.__new__(StandaloneImageEditor)
        editor.source_path = Path(r"C:\Users\pc\Desktop\a-very-long-image-name-edited.png")
        editor.image_dirty = False
        editor.project_dirty = False
        titles = []
        editor.title = titles.append

        editor._update_title()

        self.assertIn(str(editor.source_path), titles[-1])
        self.assertNotIn("path_label", inspect.getsource(StandaloneImageEditor._build))

    def test_project_paths_are_strictly_derived_from_the_selected_image(self) -> None:
        image = Path("example.png")
        record, assets = project_paths(image)
        self.assertEqual(Path("example.mdoc-image-edit.json"), record)
        self.assertEqual(Path("example.mdoc-image-edit-assets"), assets)

    def test_project_assets_cannot_escape_the_sidecar_directory(self) -> None:
        root = Path("assets")
        self.assertEqual(root / "layer.png", project_asset(root, "layer.png"))
        self.assertIsNone(project_asset(root, "../layer.png"))
        self.assertIsNone(project_asset(root, "nested/layer.png"))

    def test_project_load_skips_missing_assets_and_keeps_valid_layers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "example.png"
            Image.new("RGB", (40, 30), "white").save(image)
            record, assets = project_paths(image)
            assets.mkdir()
            Image.new("RGB", (40, 30), "white").save(assets / "base.png")
            Image.new("RGBA", (4, 3), "red").save(assets / "kept.png")
            record.write_text(json.dumps({
                "schema_version": 1, "mode": "standalone", "image_size": [40, 30], "base_snapshot": "base.png",
                "layers": [
                    {"id": "text", "text": "Topcon", "text_x": 1, "text_y": 1, "bg_x": 1, "bg_y": 1, "bg_w": 20, "bg_h": 10},
                    {"id": "kept", "system_type": "pasted-image", "image_asset": "kept.png", "image_x": 1, "image_y": 1, "image_w": 4, "image_h": 3},
                    {"id": "missing", "system_type": "pasted-image", "image_asset": "missing.png", "image_x": 1, "image_y": 1, "image_w": 4, "image_h": 3},
                ],
            }), encoding="utf-8")

            loaded, base, layers, skipped = load_project(image)

            self.assertEqual("standalone", loaded["mode"])
            self.assertEqual(assets / "base.png", base)
            self.assertEqual(["text", "kept"], [layer["id"] for layer in layers])
            self.assertEqual(1, skipped)

    def test_dimension_change_opens_only_the_selected_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "example.png"
            Image.new("RGB", (41, 30), "white").save(image)
            record, assets = project_paths(image)
            assets.mkdir()
            Image.new("RGB", (40, 30), "white").save(assets / "base.png")
            record.write_text(json.dumps({"schema_version": 1, "mode": "standalone", "image_size": [40, 30], "base_snapshot": "base.png", "layers": [{"id": "old"}]}), encoding="utf-8")

            loaded, base, layers, skipped = load_project(image)

            self.assertIn("尺寸已变化", loaded["warning"])
            self.assertIsNone(base)
            self.assertEqual([], layers)
            self.assertEqual(0, skipped)

    def test_image_save_writes_flattened_image_without_project_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "result.png"
            editor = StandaloneImageEditor.__new__(StandaloneImageEditor)
            editor.base_image = Image.new("RGBA", (20, 10), "white")
            editor.layers = []
            editor.composite = lambda: editor.base_image.copy()
            editor.image_output_path = output
            editor.project_image_path = None
            editor.source_path = root / "source.jpg"
            editor.allow_overwrite_var = types.SimpleNamespace(get=lambda: False)
            editor.project_dirty = True
            editor.image_dirty = editor.dirty = True
            editor._update_title = lambda: None
            module = sys.modules[StandaloneImageEditor.__module__]
            original = module.messagebox.showinfo
            module.messagebox.showinfo = lambda *args, **kwargs: None
            try:
                self.assertTrue(editor.save_image())
            finally:
                module.messagebox.showinfo = original

            self.assertTrue(output.is_file())
            record, assets = project_paths(output)
            self.assertFalse(record.exists())
            self.assertFalse(assets.exists())
            self.assertFalse(editor.image_dirty)
            self.assertTrue(editor.project_dirty)

    def test_project_save_writes_png_json_assets_and_loads_again(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "project.png"
            session = root / "session"
            session.mkdir()
            Image.new("RGBA", (5, 4), "red").save(session / "layer.png")
            editor = StandaloneImageEditor.__new__(StandaloneImageEditor)
            editor.base_image = Image.new("RGBA", (20, 10), "white")
            editor.layers = [{
                "id": "image", "system_type": "pasted-image", "image_asset": "layer.png",
                "image_x": 1, "image_y": 2, "image_w": 5, "image_h": 4,
                "text_x": 1, "text_y": 2, "bg_x": 1, "bg_y": 2, "bg_w": 5, "bg_h": 4,
            }]
            editor.asset_directory = session
            editor.composite = lambda: editor.base_image.copy()
            editor.project_image_path = output
            editor.image_output_path = None
            editor.source_path = root / "source.png"
            editor.selected_layer_id = "image"
            editor._view_record = lambda: {"scale": 1.0, "center": [10, 5]}
            editor.image_dirty = editor.project_dirty = editor.dirty = True
            editor._update_title = lambda: None
            module = sys.modules[StandaloneImageEditor.__module__]
            original = module.messagebox.showinfo
            module.messagebox.showinfo = lambda *args, **kwargs: None
            try:
                self.assertTrue(editor.save_project())
            finally:
                module.messagebox.showinfo = original

            record, assets = project_paths(output)
            self.assertTrue(output.is_file())
            self.assertTrue(record.is_file())
            self.assertTrue((assets / "base.png").is_file())
            self.assertTrue((assets / "layer.png").is_file())
            value, base, layers, skipped = load_project(output)
            self.assertEqual("standalone", value["mode"])
            self.assertEqual(assets / "base.png", base)
            self.assertEqual(["image"], [layer["id"] for layer in layers])
            self.assertEqual(0, skipped)
            self.assertFalse(any(path.name == ".mdoc" for path in root.rglob("*")))

    def test_project_save_preserves_unrecognized_existing_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "project.png"
            _record, assets = project_paths(output)
            assets.mkdir()
            (assets / "useful.bin").write_bytes(b"preserve")
            editor = StandaloneImageEditor.__new__(StandaloneImageEditor)
            editor.base_image = Image.new("RGBA", (20, 10), "white")
            editor.layers = []
            editor.asset_directory = root / "session"
            editor.asset_directory.mkdir()
            editor.composite = lambda: editor.base_image.copy()
            editor.project_image_path = output
            editor.image_output_path = None
            editor.source_path = root / "source.png"
            editor.selected_layer_id = None
            editor._view_record = lambda: {"scale": 1.0, "center": [10, 5]}
            editor.image_dirty = editor.project_dirty = editor.dirty = True
            editor._update_title = lambda: None
            module = sys.modules[StandaloneImageEditor.__module__]
            original = module.messagebox.showinfo
            module.messagebox.showinfo = lambda *args, **kwargs: None
            try:
                self.assertTrue(editor.save_project())
            finally:
                module.messagebox.showinfo = original
            self.assertEqual(b"preserve", (assets / "useful.bin").read_bytes())


if __name__ == "__main__":
    unittest.main()
