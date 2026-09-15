from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from ruamel.yaml import YAML

from mdoc_check.feedback import SearchIndex, image_references, locale_files, load_markdown, save_markdown
from mdoc_check.feedback_images import ImageCandidates
from mdoc_check.feedback_translation import load_providers, load_terms, save_providers, structure_differences, translate
from mdoc_check.feedback_translation_draft import blocks, commit, prepare, validate


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.workspace = Path(self.temp.name) / "manual"
        for locale in ("zh", "en", "ja"):
            root = self.workspace / "Guide" / locale; (root / "Main" / "media").mkdir(parents=True); (root / "Summary.md").write_text("# Summary\n", encoding="utf-8"); (root / "Main" / "Page.md").write_text("# Page\n\nExact phrase.\n\n![shot](media/Shot.png)\n", encoding="utf-8"); Image.new("RGB", (10, 8), "red" if locale == "zh" else "blue").save(root / "Main" / "media" / "Shot.png")
        (self.workspace / ".mdoc").mkdir(); config = {"schema_version": 1, "workspace": {"id": "feedback-test"}, "books": {"guide": {"root": "Guide", "source_locale": "zh", "locales": {locale: {"root": locale, "language": locale} for locale in ("zh", "en", "ja")}, "content_root": "Main", "assets_root": "images", "navigation": {"summary": "Summary.md"}}}}
        stream = __import__("io").StringIO(); YAML().dump(config, stream); (self.workspace / ".mdoc" / "workspace.yaml").write_text(stream.getvalue(), encoding="utf-8")

    def tearDown(self): self.temp.cleanup()

    def test_index_refresh_removes_deleted_and_finds_multiline_exact_text(self):
        index = SearchIndex(self.workspace); page = self.workspace / "Guide" / "zh" / "Main" / "Page.md"; page.write_text("# Title\n\nFirst line\nSecond line\n", encoding="utf-8")
        self.assertEqual(1, index.search("guide", "zh", "First line\nSecond line")["match_count"])
        page.unlink(); self.assertEqual(0, index.search("guide", "zh", "First line")["match_count"])

    def test_locale_mapping_and_conflict(self):
        self.assertTrue(all(item["exists"] for item in locale_files(self.workspace, "guide", "main/page.MD")))
        source = load_markdown(self.workspace, "guide", "en", "Main/Page.md"); path = Path(source["physical_path"]); path.write_text("external", encoding="utf-8")
        self.assertTrue(save_markdown(self.workspace, "guide", "en", "Main/Page.md", "edited", source["state"])["conflict"])
        self.assertTrue(save_markdown(self.workspace, "guide", "en", "Main/Page.md", "edited", source["state"], True)["saved"])

    def test_image_candidate_copy_replace_and_undo(self):
        manager = ImageCandidates(self.workspace)
        try:
            reference = image_references(self.workspace, "guide", "en", "Main/Page.md")[0]["reference"]; target = self.workspace / "Guide" / "en" / "Main" / "media" / "Shot.png"; before = target.read_bytes()
            candidate = manager.copy_locale("00000000-0000-4000-8000-000000000001", "guide", "en", "Main/Page.md", reference, "zh"); self.assertTrue(manager.save(candidate["key"])["saved"]); self.assertNotEqual(before, target.read_bytes())
            self.assertTrue(manager.revert("guide", "en", "Main/Page.md", reference)["reverted"]); self.assertEqual(before, target.read_bytes())
        finally: manager.close()

    def test_image_candidate_status_discard_and_replacement_cleanup(self):
        manager = ImageCandidates(self.workspace)
        try:
            reference = image_references(self.workspace, "guide", "en", "Main/Page.md")[0]["reference"]
            first = manager.create("00000000-0000-4000-8000-000000000001", "guide", "en", "Main/Page.md", reference); self.assertTrue(manager.has_pending())
            self.assertEqual(first["key"], manager.list("guide", "en", "Main/Page.md")[0]["candidate_key"])
            first_path = Path(first["candidate"]); first_path.with_name(first_path.stem + "-edited.png").write_bytes(first_path.read_bytes()); first_path.with_name(first_path.stem + ".mdoc-image-edit.json").write_text("{}", encoding="utf-8"); first_path.with_name(first_path.stem + ".mdoc-image-edit-assets").mkdir()
            second = manager.copy_locale("00000000-0000-4000-8000-000000000002", "guide", "en", "Main/Page.md", reference, "zh")
            self.assertFalse(first_path.exists()); self.assertFalse(first_path.with_name(first_path.stem + "-edited.png").exists()); self.assertFalse(first_path.with_name(first_path.stem + ".mdoc-image-edit.json").exists()); self.assertFalse(first_path.with_name(first_path.stem + ".mdoc-image-edit-assets").exists()); self.assertTrue(Path(second["candidate"]).exists())
            second_parent = Path(second["candidate"]).parent; self.assertTrue(manager.discard(second["key"])["discarded"]); self.assertFalse(manager.has_pending()); self.assertFalse(second_parent.exists())
        finally: manager.close()

    def test_image_list_deduplicates_resolved_paths_and_copy_locale_ignores_reference_case(self):
        page = self.workspace / "Guide" / "en" / "Main" / "Page.md"; page.write_text("# Page\n\n![one](media/Shot.png)\n![two](./media/Shot.png)\n", encoding="utf-8")
        manager = ImageCandidates(self.workspace)
        try:
            items = manager.list("guide", "en", "Main/Page.md"); self.assertEqual(1, len(items)); self.assertEqual([3, 4], items[0]["lines"])
            copied = manager.copy_locale("00000000-0000-4000-8000-000000000003", "guide", "en", "Main/Page.md", "MEDIA/SHOT.PNG", "zh")
            self.assertTrue(Path(copied["candidate"]).is_file())
        finally: manager.close()

    def test_image_editor_opens_candidate_in_managed_mode(self):
        manager = ImageCandidates(self.workspace)
        try:
            reference = image_references(self.workspace, "guide", "en", "Main/Page.md")[0]["reference"]
            candidate = manager.create("00000000-0000-4000-8000-000000000005", "guide", "en", "Main/Page.md", reference)
            with patch("mdoc_check.feedback_images.Path.home", return_value=self.workspace), patch("mdoc_check.feedback_images.subprocess.Popen") as opened:
                launcher = self.workspace / ".codex" / "skills" / "mdoc" / "Open-mdoc-Image-Editor.cmd"; launcher.parent.mkdir(parents=True); launcher.write_text("@echo off\n", encoding="utf-8")
                self.assertEqual("opened", manager.open_editor(candidate["key"])["status"]); self.assertEqual("--managed-candidate", opened.call_args.args[0][-1])
        finally: manager.close()

    def test_image_format_extension_mismatch_is_a_non_blocking_warning(self):
        target = self.workspace / "Guide" / "en" / "Main" / "media" / "Shot.png"
        Image.new("RGB", (10, 8), "blue").save(target, format="JPEG")
        manager = ImageCandidates(self.workspace)
        try:
            item = manager.list("guide", "en", "Main/Page.md")[0]
            self.assertEqual("jpeg", item["info"]["format"]); self.assertIn("扩展名为 PNG", item["info"]["warning"])
            candidate = manager.create("00000000-0000-4000-8000-000000000006", "guide", "en", "Main/Page.md", item["reference"]); self.assertIn("扩展名为 PNG", candidate["info"]["warning"]); self.assertTrue(manager.save(candidate["key"])["saved"])
        finally: manager.close()

    def test_provider_secret_is_not_returned_and_structure_changes_block(self):
        provider = {"id": "gv", "name": "GV", "api_type": "openai-completions", "base_url": "http://example.test/v1", "api_key": "secret", "models": [{"id": "model", "name": "Model", "context_tokens": 128000, "max_output_tokens": 4096}], "default_model": "model"}
        save_providers(self.workspace, {"providers": [provider]}); public = load_providers(self.workspace); self.assertNotIn("api_key", public["providers"][0]); self.assertTrue(public["providers"][0]["key_configured"])
        source = "Use " + chr(96) + "code" + chr(96) + " and [link](a.md)."; self.assertTrue(structure_differences(source, "Use code and link."))
        response = json.dumps({"translations": [{"locale": "en", "text": source}]})
        with patch("mdoc_check.feedback_translation.request", return_value=response): value = translate(self.workspace, "gv", "zh", ["en"], source)
        self.assertEqual("ready", value["results"][0]["status"])
        with patch("mdoc_check.feedback_translation.request", return_value=json.dumps([{"locale": "en", "text": source}])): value = translate(self.workspace, "gv", "zh", ["en"], source)
        self.assertEqual("ready", value["results"][0]["status"])
        with patch("mdoc_check.feedback_translation.request", return_value=json.dumps(["invalid"])):
            with self.assertRaisesRegex(ValueError, "翻译服务响应格式无效"): translate(self.workspace, "gv", "zh", ["en"], source)
        self.assertFalse(load_terms(self.workspace)["exists"])

    def test_provider_revision_can_continue_after_a_successful_save(self):
        provider = {"id": "gv", "name": "GV", "api_type": "openai-completions", "base_url": "http://example.test/v1", "api_key": "secret", "models": [{"id": "model", "name": "Model", "context_tokens": 128000, "max_output_tokens": 4096}], "default_model": "model"}
        first = save_providers(self.workspace, {"providers": [provider]})
        provider["name"] = "GV Updated"
        second = save_providers(self.workspace, {"providers": [provider]}, first["revision"])
        self.assertTrue(second["saved"]); self.assertNotEqual(first["revision"], second["revision"])

    def test_translation_draft_uses_configured_locale_order_and_block_context(self):
        value = prepare(self.workspace, "guide", "zh", "Main/Page.md", 8, 20)
        self.assertEqual(["en", "ja"], [item["locale"] for item in value["targets"]])
        self.assertTrue(value["source"]["selection"]["complete_blocks"] is False)
        self.assertEqual("Exact phrase", value["source"]["selection"]["text"])
        self.assertEqual("Exact phrase.", value["source"]["scope"]["text"])
        self.assertEqual(8, value["source"]["scope"]["from"])
        self.assertEqual(21, value["source"]["scope"]["to"])
        self.assertTrue(all(item["context"]["target"] for item in value["targets"]))
        self.assertIn("<p>Exact phrase.</p>", value["source"]["context"]["target"][0]["html"])
        self.assertIn("<img src=\"media/Shot.png\"", value["source"]["context"]["after"]["html"])
        self.assertEqual(3, len(blocks((self.workspace / "Guide" / "zh" / "Main" / "Page.md").read_text(encoding="utf-8"))))

    def test_translation_commit_requires_all_languages_and_writes_transaction(self):
        draft = prepare(self.workspace, "guide", "zh", "Main/Page.md", 8, 21)
        with self.assertRaisesRegex(ValueError, "尚未完成处理"):
            commit(self.workspace, "guide", draft["source"], [{**draft["targets"][0], "status": "ignored"}, {**draft["targets"][1], "status": "pending"}])
        target = draft["targets"][0]; mapped = target["context"]["target"]; content = target["content"][:mapped[0]["from"]] + "Translated." + target["content"][mapped[-1]["to"]:]
        value = commit(self.workspace, "guide", draft["source"], [{**target, "status": "confirmed", "content": content}, {**draft["targets"][1], "status": "ignored"}])
        self.assertTrue(value["saved"]); self.assertIn("Translated.", (self.workspace / "Guide" / "en" / "Main" / "Page.md").read_text(encoding="utf-8"))
        self.assertFalse(validate("[a](x.md)", "a")["valid"])
        self.assertEqual([], list((self.workspace / ".mdoc" / "runtime").glob("feedback-translation-*")))

    def test_translation_draft_preserves_four_locale_order_and_missing_target_can_be_ignored(self):
        root = self.workspace / "Guide" / "fr"; (root / "Main").mkdir(parents=True); (root / "Summary.md").write_text("# Sommaire\n", encoding="utf-8")
        config_path = self.workspace / ".mdoc" / "workspace.yaml"; config = YAML().load(config_path.read_text(encoding="utf-8")); config["books"]["guide"]["locales"]["fr"] = {"root": "fr", "language": "French"}
        stream = __import__("io").StringIO(); YAML().dump(config, stream); config_path.write_text(stream.getvalue(), encoding="utf-8")
        draft = prepare(self.workspace, "guide", "zh", "Main/Page.md", 8, 21)
        self.assertEqual(["en", "ja", "fr"], [item["locale"] for item in draft["targets"]]); self.assertFalse(draft["targets"][-1]["exists"])
        value = commit(self.workspace, "guide", draft["source"], [{**item, "status": "ignored"} for item in draft["targets"]])
        self.assertTrue(value["saved"]); self.assertEqual([], value["files"])

    def test_translation_conflict_prevents_all_automatic_writes(self):
        draft = prepare(self.workspace, "guide", "zh", "Main/Page.md", 8, 21); en_before = (self.workspace / "Guide" / "en" / "Main" / "Page.md").read_text(encoding="utf-8")
        targets = []
        for target in draft["targets"]:
            mapped = target["context"]["target"]; content = target["content"][:mapped[0]["from"]] + target["locale"].upper() + target["content"][mapped[-1]["to"]:]
            targets.append({**target, "status": "confirmed", "content": content})
        (self.workspace / "Guide" / "ja" / "Main" / "Page.md").write_text("# external\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "目标文件已变化"): commit(self.workspace, "guide", draft["source"], targets)
        self.assertEqual(en_before, (self.workspace / "Guide" / "en" / "Main" / "Page.md").read_text(encoding="utf-8"))
        self.assertFalse((self.workspace / ".mdoc" / "runtime").exists())

    def test_translation_source_and_manual_conflicts_are_rejected(self):
        draft = prepare(self.workspace, "guide", "zh", "Main/Page.md", 8, 21)
        source_path = self.workspace / "Guide" / "zh" / "Main" / "Page.md"; source_path.write_text("# changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "源语言文件已变化"): commit(self.workspace, "guide", draft["source"], [{**item, "status": "ignored"} for item in draft["targets"]])
        source_path.write_text("# Page\n\nExact phrase.\n\n![shot](media/Shot.png)\n", encoding="utf-8"); draft = prepare(self.workspace, "guide", "zh", "Main/Page.md", 8, 21)
        (self.workspace / "Guide" / "en" / "Main" / "Page.md").write_text("# manually changed again\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "人工处理文件在确认后发生变化"): commit(self.workspace, "guide", draft["source"], [{**draft["targets"][0], "status": "manual"}, {**draft["targets"][1], "status": "ignored"}])


if __name__ == "__main__": unittest.main()
