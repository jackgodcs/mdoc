from __future__ import annotations

import unittest
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from mdoc_check.preview import render_text
from mdoc_check.checkers import BRIDGE


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.NamedTemporaryFile(suffix='.mjs', dir=BRIDGE.parent, delete=False)
        temporary.close()
        cls.bridge = Path(temporary.name)
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'bridges' / 'markdown.mjs', cls.bridge)
        cls.override = patch('mdoc_check.preview.BRIDGE', cls.bridge)
        cls.override.start()

    @classmethod
    def tearDownClass(cls):
        cls.override.stop()
        cls.bridge.unlink()

    def test_image_div_does_not_swallow_following_markdown(self):
        source = '<div align=center>\n<img src="image.png">\n</div>\n- **Minimum**: value\n- <img src="icon.png">**Unselect**\n'
        result = render_text(source)
        self.assertIn('<strong>Minimum</strong>', result['html'])
        self.assertIn('<strong>Unselect</strong>', result['html'])
        self.assertIn('data-source-line="4"', result['html'])
        self.assertIn('data-source-line="5"', result['html'])

    def test_adjacent_image_attributes_are_accepted(self):
        result = render_text('Click <img src="icon.png"width="30" style="vertical-align:middle"/> **Next**.')
        self.assertIn('<img src="icon.png" width="30"', result['html'])
        self.assertIn('<strong>Next</strong>', result['html'])

    def test_code_and_general_html_are_not_reinterpreted(self):
        image = '<img src="icon.png"width="30">'
        for source in [f'`{image}`', f'```html\n{image}\n```', f'    {image}']:
            self.assertNotIn('<img ', render_text(source)['html'])
        result = render_text('<div>\n**Literal HTML content**\n</div>\n**Still HTML**')
        self.assertNotIn('<strong>', result['html'])

    def test_image_compatibility_preserves_filtering_and_valid_markup(self):
        result = render_text('<img src="javascript:alert(1)"width="30" onerror="alert(1)">')
        self.assertNotIn('javascript:', result['html'])
        self.assertNotIn('onerror', result['html'])
        source = '<div>\n<img src="image.png">\n</div>\n\n- **Normal**'
        self.assertIn('<strong>Normal</strong>', render_text(source)['html'])

    def test_consecutive_image_blocks_and_indented_list_keep_source_lines(self):
        source = 'Click <img src="icon.png"width="30"/> **Next**.\n\n<div align=center>\n<img src="one.png">\n</div>\n<div align=center>\n<img src="two.png">\n</div>\n - **Minimum**\n- <img src="icon.png">**Unselect**'
        result = render_text(source)
        self.assertNotIn('&lt;img', result['html'])
        self.assertIn('<strong>Minimum</strong>', result['html'])
        self.assertIn('<strong>Unselect</strong>', result['html'])
        self.assertIn('data-source-line="9"', result['html'])
        self.assertIn('data-source-line="10"', result['html'])

    def test_image_container_inside_code_remains_literal(self):
        source = '<div>\n<img src="image.png">\n</div>\n- **Literal**'
        for code in [f'```html\n{source}\n```', '\n'.join('    ' + line for line in source.splitlines())]:
            result = render_text(code)
            self.assertNotIn('<img ', result['html'])
            self.assertNotIn('<strong>', result['html'])
