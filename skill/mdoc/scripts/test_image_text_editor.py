"""Focused rendering regression checks for the mdoc image text editor."""
from __future__ import annotations

import importlib.util
import sys
import types
import copy
import tempfile
from pathlib import Path

from PIL import Image, ImageFont


SCRIPT = Path(__file__).with_name("image_text_editor.py")


def load_editor_module():
    """Load the editor without requiring the full mdoc CLI dependency set."""
    locking = types.ModuleType("mdoc_core.locking")
    locking.task_lock = lambda _task: None
    paths = types.ModuleType("mdoc_core.paths")
    paths.staged_target = lambda *args: None
    screenshots = types.ModuleType("mdoc_core.screenshots")
    screenshots.accept = lambda *args: None
    screenshots.png_info = lambda *args: None
    screenshots.synchronize = lambda *args: None
    state = types.ModuleType("mdoc_core.state")
    state.load_state = lambda *args: {}
    state.save_state = lambda *args: None
    sys.modules.update({
        "mdoc_core": types.ModuleType("mdoc_core"),
        "mdoc_core.locking": locking,
        "mdoc_core.paths": paths,
        "mdoc_core.screenshots": screenshots,
        "mdoc_core.state": state,
    })
    spec = importlib.util.spec_from_file_location("image_text_editor_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class EditorHarness:
    """Small non-Tk harness that exercises the editor's production methods."""

    def __init__(self, module, image):
        self.module = module
        self.base_image = image.convert("RGBA")
        self.fonts = {"Segoe UI": Path(r"C:\Windows\Fonts\segoeui.ttf")}
        self.layers = []
        self.selected_layer_id = None
        self.selected_part = None
        self.undo_stack = []
        self.redraw_count = 0
        self.asset_directory = Path(tempfile.mkdtemp())

    def push_undo(self):
        self.undo_stack.append([])

    def _sync_style_controls(self, *_args):
        pass

    def mark_dirty(self):
        pass

    def redraw(self):
        self.redraw_count += 1

    def selected_layer(self):
        return next(layer for layer in self.layers if layer["id"] == self.selected_layer_id)


def bind(module, harness):
    names = (
        "font_for", "text_metrics", "layer_geometry", "uses_default_background",
        "source_coverage", "source_rect", "sample_background_color", "composite",
        "add_layer", "group_rect", "snap", "move_layer_by", "canvas_motion",
        "expand_background_to_source", "resize_layer", "pan_release", "add_image_layer",
    )
    for name in names:
        setattr(harness, name, getattr(module.ImageTextEditor, name).__get__(harness, EditorHarness))


def test_known_replacement_masks_longer_source(module):
    base = Image.new("RGBA", (220, 70), "#2C5372")
    harness = EditorHarness(module, base)
    bind(module, harness)
    template = {
        "text": "New label",
        "sources": ["Old longer label"],
        "style": {"font": "Segoe UI", "font_size": 18.0, "text_color": "#000000", "bg_color": "#FFFFFF", "padding": 0.0, "align": "left", "line_spacing": 1.2},
    }
    harness.add_layer(template, 30, 24)
    layer = harness.layers[0]
    geometry = harness.layer_geometry(layer)
    source = harness.source_rect(layer)

    assert layer["source_text"] == "Old longer label"
    assert layer["bg_color"].upper() == "#2C5372"
    assert geometry["bg_x"] <= source[0] and geometry["bg_y"] <= source[1]
    assert geometry["bg_x"] + geometry["bg_w"] >= source[2]
    assert geometry["bg_y"] + geometry["bg_h"] >= source[3]

    replacement = module.ImageTextEditor.text_metrics(harness, layer, layer["text"])
    source_metrics = module.ImageTextEditor.text_metrics(harness, layer, layer["source_text"])
    assert geometry["bg_w"] >= max(replacement["text_w"], source_metrics["text_w"])


def test_new_default_layer_has_zero_text_to_background_gap(module):
    assert module.DEFAULT_STYLE["padding"] == 0.0
    assert module.COVERAGE_GUTTER == 0.0
    base = Image.new("RGBA", (220, 70), "#F5F5F5")
    harness = EditorHarness(module, base)
    bind(module, harness)
    template = {
        "text": "New label",
        "sources": [],
        "style": dict(module.DEFAULT_STYLE),
    }
    harness.add_layer(template, 30, 24)
    layer = harness.layers[0]
    geometry = harness.layer_geometry(layer)
    assert geometry["bg_x"] == geometry["text_x"]
    assert geometry["bg_y"] == geometry["text_y"]


def test_system_blank_cover_masks_with_a_resizable_no_text_layer(module):
    base = Image.new("RGBA", (120, 80), "#2C5372")
    harness = EditorHarness(module, base)
    bind(module, harness)
    template = module.system_blank_cover_template(dict(module.DEFAULT_STYLE))

    assert template["kind"] == "system"
    assert template["text"] == ""
    assert template["label"] == "空白遮盖"

    harness.add_layer(template, 20, 18)
    layer = harness.layers[0]
    geometry = harness.layer_geometry(layer)
    result = harness.composite()

    assert layer["system_type"] == module.SYSTEM_BLANK_COVER_KIND
    assert geometry["bg_w"] == module.BLANK_COVER_INITIAL_SIZE
    assert geometry["bg_h"] == module.BLANK_COVER_INITIAL_SIZE
    assert result.getpixel((24, 22))[:3] == (255, 255, 255)


def test_system_point_cloud_icon_is_a_movable_and_resizable_32_pixel_layer(module):
    base = Image.new("RGBA", (120, 80), "#F5F5F5")
    harness = EditorHarness(module, base)
    bind(module, harness)
    template = module.system_point_cloud_icon_template()

    assert template["kind"] == "system"
    assert template["label"] == "Topcon Point Cloud 图标"
    harness.add_layer(template, 20, 18)
    layer = harness.layers[0]
    geometry = harness.layer_geometry(layer)

    assert layer["system_type"] == module.SYSTEM_POINT_CLOUD_ICON_KIND
    assert (geometry["image_w"], geometry["image_h"]) == (32.0, 32.0)
    assert harness.composite().getpixel((36, 34))[:3] != (245, 245, 245)

    harness.move_layer_by(layer, "group", 5, -3)
    assert (layer["image_x"], layer["image_y"]) == (25, 15)

    before = copy.deepcopy(layer)
    harness.resize_layer(layer, before, "group", "se", 8, 4)
    assert (layer["image_w"], layer["image_h"]) == (40, 36)


def test_pasted_image_is_persisted_as_a_movable_resizable_layer(module):
    harness = EditorHarness(module, Image.new("RGBA", (120, 80), "#F5F5F5"))
    bind(module, harness)
    pasted = Image.new("RGBA", (18, 12), (220, 30, 40, 128))

    harness.add_image_layer(pasted, 20, 18)
    layer = harness.layers[0]
    geometry = harness.layer_geometry(layer)

    assert layer["system_type"] == module.PASTED_IMAGE_KIND
    assert (geometry["image_x"], geometry["image_y"]) == (20, 18)
    assert (geometry["image_w"], geometry["image_h"]) == (18, 12)
    assert (harness.asset_directory / layer["image_asset"]).is_file()
    assert harness.composite().getpixel((25, 22))[:3] != (245, 245, 245)

    harness.move_layer_by(layer, "group", 5, -3)
    assert (layer["image_x"], layer["image_y"]) == (25, 15)
    before = copy.deepcopy(layer)
    harness.resize_layer(layer, before, "group", "se", 8, 4)
    assert (layer["image_w"], layer["image_h"]) == (26, 16)


def test_user_image_template_copies_png_and_can_be_renamed_and_deleted(module):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "sample.png"
        Image.new("RGBA", (14, 9), (10, 20, 30, 180)).save(source)
        store = module.TemplateStore.__new__(module.TemplateStore)
        store.fonts = {"Segoe UI": Path(r"C:\Windows\Fonts\segoeui.ttf")}
        store.path = root / "image-text-editor.json"
        store.asset_directory = root / "image-text-editor.assets"
        store.value = {"schema_version": 1, "templates": []}

        template = store.add_image(source, "Sample")
        asset = store.image_path(template)

        assert template["kind"] == module.IMAGE_TEMPLATE_KIND
        assert asset.is_file()
        assert Image.open(asset).size == (14, 9)
        store.rename(template, "Renamed")
        assert store.value["templates"][0]["label"] == "Renamed"
        store.delete(template)
        assert store.value["templates"] == []
        assert not asset.exists()


def test_text_and_background_share_the_same_bbox_coordinates(module):
    base = Image.new("RGBA", (180, 60), "#F5F5F5")
    harness = EditorHarness(module, base)
    bind(module, harness)
    template = {
        "text": "New label",
        "sources": [],
        "style": {"font": "Segoe UI", "font_size": 16.0, "text_color": "#000000", "bg_color": "#FFFFFF", "padding": 2.0, "align": "left", "line_spacing": 1.2},
    }
    harness.add_layer(template, 20, 18)
    layer = harness.layers[0]
    geometry = harness.layer_geometry(layer)
    result = harness.composite()
    foreground = [
        (x, y)
        for y in range(round(geometry["text_y"]), round(geometry["text_y"] + geometry["text_h"]))
        for x in range(round(geometry["text_x"]), round(geometry["text_x"] + geometry["text_w"]))
        if result.getpixel((x, y))[:3] != (245, 245, 245)
    ]
    assert foreground, "replacement text was not drawn inside its expected rectangle"


def test_group_drag_translates_text_background_and_source_anchor(module):
    base = Image.new("RGBA", (240, 90), "#F5F5F5")
    harness = EditorHarness(module, base)
    bind(module, harness)
    template = {
        "text": "New label",
        "sources": [],
        "style": {"font": "Segoe UI", "font_size": 16.0, "text_color": "#000000", "bg_color": "#FFFFFF", "padding": 2.0, "align": "left", "line_spacing": 1.2},
    }
    harness.add_layer(template, 35, 40)
    layer = harness.layers[0]
    before = {key: layer[key] for key in ("text_x", "text_y", "bg_x", "bg_y", "source_x", "source_y")}
    harness.scale = 1.0
    harness.pan_state = None
    harness.guides = []
    harness.drag_state = {"kind": "move", "start": (100, 100), "before": copy.deepcopy(layer), "part": "group"}

    module.ImageTextEditor.canvas_motion(harness, types.SimpleNamespace(x=117, y=91, state=0x0008))

    for key, delta in (("text_x", 17), ("bg_x", 17), ("source_x", 17), ("text_y", -9), ("bg_y", -9), ("source_y", -9)):
        assert layer[key] == before[key] + delta, key


def test_continuous_group_drag_does_not_accumulate_total_mouse_distance(module):
    base = Image.new("RGBA", (500, 200), "#F5F5F5")
    harness = EditorHarness(module, base)
    bind(module, harness)
    template = {
        "text": "New label",
        "sources": [],
        "style": {"font": "Segoe UI", "font_size": 16.0, "text_color": "#000000", "bg_color": "#FFFFFF", "padding": 2.0, "align": "left", "line_spacing": 1.2},
    }
    harness.add_layer(template, 40, 25)
    layer = harness.layers[0]
    before = {key: layer[key] for key in ("text_x", "text_y", "bg_x", "bg_y", "source_x", "source_y")}
    harness.scale = 4.0
    harness.pan_state = None
    harness.guides = []
    harness.drag_state = {"kind": "move", "start": (100, 100), "before": copy.deepcopy(layer), "part": "group"}

    # Tk emits multiple motion events while the same button press remains active.
    module.ImageTextEditor.canvas_motion(harness, types.SimpleNamespace(x=120, y=100, state=0))
    module.ImageTextEditor.canvas_motion(harness, types.SimpleNamespace(x=124, y=100, state=0))

    # Final cursor offset is 24 screen pixels, or exactly 6 source-image pixels.
    for key in ("text_x", "bg_x", "source_x"):
        assert layer[key] == before[key] + 6, key
    for key in ("text_y", "bg_y", "source_y"):
        assert layer[key] == before[key], key


def test_high_zoom_drag_follows_screen_distance_without_snap_drift(module):
    base = Image.new("RGBA", (500, 200), "#F5F5F5")
    harness = EditorHarness(module, base)
    bind(module, harness)
    template = {
        "text": "New label",
        "sources": [],
        "style": {"font": "Segoe UI", "font_size": 16.0, "text_color": "#000000", "bg_color": "#FFFFFF", "padding": 2.0, "align": "left", "line_spacing": 1.2},
    }
    harness.add_layer(template, 100, 25)
    layer = harness.layers[0]
    before = {key: layer[key] for key in ("text_x", "text_y", "bg_x", "bg_y", "source_x", "source_y")}
    harness.scale = 4.0
    harness.pan_state = None
    harness.guides = []
    harness.drag_state = {"kind": "move", "start": (400, 100), "before": copy.deepcopy(layer), "part": "group"}

    # Move by 20 screen pixels at 400%, equivalent to exactly 5 image pixels.
    # The left edge is now 4 image px (16 screen px) from the canvas-centre snap line.
    module.ImageTextEditor.canvas_motion(harness, types.SimpleNamespace(x=420, y=100, state=0))

    for key in ("text_x", "bg_x", "source_x"):
        assert layer[key] == before[key] + 5, key
    for key in ("text_y", "bg_y", "source_y"):
        assert layer[key] == before[key], key


def test_middle_button_release_ends_background_pan_before_layer_drag(module):
    harness = EditorHarness(module, Image.new("RGBA", (120, 80), "#F5F5F5"))
    bind(module, harness)
    harness.pan_state = (25, 30, 10.0, 12.0)

    harness.pan_release()

    assert harness.pan_state is None


def test_switching_base_with_no_layers_does_not_prompt(module):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        reference = root / "reference.png"
        capture = root / "capture.png"
        Image.new("RGB", (40, 30), "white").save(reference)
        Image.new("RGB", (40, 30), "black").save(capture)
        harness = types.SimpleNamespace(
            entry={"reference": reference, "capture": capture, "reference_label": "zh 参考图片", "capture_label": "en 新截图"},
            base_var=types.SimpleNamespace(get=lambda: "en 新截图"),
            base_source="original", snapshot_path=root / "missing.base.png", base_path=reference, layers=[], selected_layer_id=None,
            _refresh_base_choice=lambda: None, push_undo=lambda: None, _load_image=lambda path: setattr(harness, "base_path", path),
            mark_dirty=lambda: None, fit=lambda: None,
        )
        original_prompt = module.messagebox.askyesnocancel
        original_png_info = module.png_info
        module.png_info = lambda path: {"width": 40, "height": 30} if Path(path).is_file() else None
        module.messagebox.askyesnocancel = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("empty editor prompted to preserve layers"))
        try:
            module.ImageTextEditor._switch_base(harness)
        finally:
            module.messagebox.askyesnocancel = original_prompt
            module.png_info = original_png_info

        assert harness.base_source == "capture"


def test_snap_threshold_is_stable_in_screen_pixels(module):
    base = Image.new("RGBA", (500, 200), "#F5F5F5")
    harness = EditorHarness(module, base)
    bind(module, harness)
    harness.layers = [{"id": "other", "text": "x", "font": "Segoe UI", "font_size": 10, "text_x": 300, "text_y": 20, "bg_x": 300, "bg_y": 20, "bg_w": 20, "bg_h": 20, "padding": 0, "line_spacing": 1.2}]
    moving = {"id": "moving"}

    harness.scale = 1.0
    snapped_x, _ = module.ImageTextEditor.snap(harness, moving, 296.5, 0, 10, 10, 0)
    assert snapped_x == 300

    harness.scale = 4.0
    unsnapped_x, _ = module.ImageTextEditor.snap(harness, moving, 296.5, 0, 10, 10, 0)
    assert unsnapped_x == 296.5


if __name__ == "__main__":
    editor = load_editor_module()
    test_known_replacement_masks_longer_source(editor)
    test_new_default_layer_has_zero_text_to_background_gap(editor)
    test_system_blank_cover_masks_with_a_resizable_no_text_layer(editor)
    test_system_point_cloud_icon_is_a_movable_and_resizable_32_pixel_layer(editor)
    test_pasted_image_is_persisted_as_a_movable_resizable_layer(editor)
    test_user_image_template_copies_png_and_can_be_renamed_and_deleted(editor)
    test_text_and_background_share_the_same_bbox_coordinates(editor)
    test_group_drag_translates_text_background_and_source_anchor(editor)
    test_continuous_group_drag_does_not_accumulate_total_mouse_distance(editor)
    test_high_zoom_drag_follows_screen_distance_without_snap_drift(editor)
    test_middle_button_release_ends_background_pan_before_layer_drag(editor)
    test_switching_base_with_no_layers_does_not_prompt(editor)
    test_snap_threshold_is_stable_in_screen_pixels(editor)
    print("image_text_editor regression checks passed")
