"""Focused rendering regression checks for the mdoc image text editor."""
from __future__ import annotations

import importlib.util
import sys
import types
import copy
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
        "expand_background_to_source",
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
    test_text_and_background_share_the_same_bbox_coordinates(editor)
    test_group_drag_translates_text_background_and_source_anchor(editor)
    test_continuous_group_drag_does_not_accumulate_total_mouse_distance(editor)
    test_high_zoom_drag_follows_screen_distance_without_snap_drift(editor)
    test_snap_threshold_is_stable_in_screen_pixels(editor)
    print("image_text_editor regression checks passed")
