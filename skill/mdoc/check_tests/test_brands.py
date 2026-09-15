from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mdoc_check.brands import inspect, load


class BrandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name); (self.root / ".mdoc").mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_missing_common_config_is_initialized_and_used(self) -> None:
        messages = []; config, paths = load(self.root, "en", messages.append)
        self.assertEqual([self.root / ".mdoc" / "check-brands.yaml"], paths)
        self.assertEqual(["已初始化品牌配置：.mdoc/check-brands.yaml"], messages)
        issue = inspect("使用Lidar360MLS处理。", "zh/Main/Page.md", config, {paths[0]: ".mdoc/check-brands.yaml"})[0]
        self.assertEqual("terminology.brand-name", issue["rule"]); self.assertEqual("LiDAR360MLS", issue["details"]["expected"])

    def test_language_layer_merges_variants_and_wins_source_order(self) -> None:
        common = self.root / ".mdoc" / "check-brands.yaml"; language = self.root / ".mdoc" / "check-brands.ja.yaml"
        common.write_text("schema_version: 1\nbrands:\n  - canonical: NVIDIA\n    variants: [Nvidia]\n", encoding="utf-8")
        language.write_text("schema_version: 1\nbrands:\n  - canonical: NVIDIA\n    variants: [Nvidia, nVidia]\n  - canonical: GeoCue\n    variants: []\n", encoding="utf-8")
        config, paths = load(self.root, "ja"); issue = inspect("Nvidia", "ja/Page.md", config, {path: path.relative_to(self.root).as_posix() for path in paths})[0]
        self.assertEqual(".mdoc/check-brands.ja.yaml", issue["details"]["configuration"]); self.assertIn("GeoCue", config["cspell_words"])

    def test_visible_scope_boundaries_and_longest_overlap(self) -> None:
        path = self.root / ".mdoc" / "check-brands.yaml"
        config = {"brands": [{"canonical": "LiDAR360MLS", "variants": {"Lidar360MLS": [path], "Lidar360": [path]}}]}
        source = "Lidar360MLS [Lidar360MLS](Lidar360MLS.md) `Lidar360MLS` <span title='Lidar360MLS'>Lidar360MLS</span> MyLidar360MLSPlugin"
        issues = inspect(source, "en/Page.md", config, {path: ".mdoc/check-brands.yaml"})
        self.assertEqual(3, len(issues)); self.assertTrue(all(item["details"]["actual"] == "Lidar360MLS" for item in issues))

    def test_html_image_alt_is_visible_but_other_attributes_are_not(self) -> None:
        path = self.root / ".mdoc" / "check-brands.yaml"
        config = {"brands": [{"canonical": "LiDAR360MLS", "variants": {"Lidar360MLS": [path]}}]}
        source = "<img src='Lidar360MLS.png' title='Lidar360MLS' alt=\"Lidar360MLS GridStatistics\"/>"
        issues = inspect(source, "en/Page.md", config, {path: ".mdoc/check-brands.yaml"})
        self.assertEqual(1, len(issues)); self.assertEqual("Lidar360MLS", issues[0]["details"]["actual"]); self.assertEqual(source.index("Lidar360MLS GridStatistics") + 1, issues[0]["column"])

    def test_conflicting_and_unknown_configuration_is_rejected(self) -> None:
        path = self.root / ".mdoc" / "check-brands.yaml"
        cases = [
            "schema_version: 1\nunknown: true\nbrands: []\n",
            "schema_version: 1\nbrands:\n  - canonical: A\n    variants: [bad]\n  - canonical: B\n    variants: [BAD]\n",
            "schema_version: 1\nbrands:\n  - canonical: A\n    variants: []\n  - canonical: A\n    variants: []\n",
        ]
        for value in cases:
            with self.subTest(value=value):
                path.write_text(value, encoding="utf-8")
                with self.assertRaises(ValueError): load(self.root, "en")

    def test_explicit_empty_brand_list_has_no_hidden_fallback(self) -> None:
        path = self.root / ".mdoc" / "check-brands.yaml"; path.write_text("schema_version: 1\nbrands: []\n", encoding="utf-8")
        config, _paths = load(self.root, "en")
        self.assertEqual([], inspect("Lidar360MLS Nvidia", "en/Page.md", config, {}))


if __name__ == "__main__":
    unittest.main()
