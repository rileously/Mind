import json
import os
import tempfile
import unittest
from pathlib import Path

from mind.config_store import (
    BUNDLED_COMMANDS_REVISION,
    CONFIG_SCHEMA_VERSION,
    DEFAULT_CONFIG,
    DEFAULT_PALETTE_ACTIONS,
    LEGACY_DEFAULT_PALETTE_ACTIONS,
    ConfigStore,
)


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"
        self.source = Path(self.temp.name) / "source"
        self.source.mkdir()
        (self.source / "commands.json").write_text(
            json.dumps([
                {"trigger": "fix", "type": "ai", "prompt": "Fix it."},
                {"trigger": "summarize", "type": "ai", "prompt": "Summarize it."},
                {"trigger": "action-items", "type": "ai", "prompt": "Extract tasks."},
                {"trigger": "english", "type": "ai", "prompt": "Translate it."},
                {"trigger": "bullets", "type": "ai", "prompt": "Make bullets."},
            ]),
            encoding="utf-8",
        )
        self.store = ConfigStore(self.root, self.source)

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults_do_not_complete_onboarding(self):
        config = self.store.load()
        self.assertFalse(config["onboarding_complete"])
        self.assertFalse(config["mind_palette_enabled"])
        self.assertFalse(config["mind_palette_auto_show_on_selection"])
        self.assertTrue(config["word_definitions_enabled"])
        self.assertEqual(config["schema_version"], CONFIG_SCHEMA_VERSION)
        self.assertEqual(config["bundled_commands_revision"], BUNDLED_COMMANDS_REVISION)
        self.assertEqual(config["mind_palette_actions"], DEFAULT_PALETTE_ACTIONS)
        self.assertEqual(config["mind_palette_shortcut"], "Ctrl+Alt+M")
        self.assertEqual(config["mind_palette_columns"], 2)
        self.assertTrue(config["mind_palette_show_preview"])
        self.assertEqual(config["mind_palette_width"], 390)
        self.assertTrue(config["mind_palette_image_ocr_enabled"])
        self.assertIn("dhivehi", config["mind_palette_actions"])
        self.assertEqual(config["accent_color"], "teal")
        self.assertEqual(config["provider"], "gemini")

    def test_schema_upgrade_adds_new_tools_without_replacing_user_commands(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.store.config_path.write_text(
            json.dumps({
                "schema_version": 1,
                "mind_palette_actions": LEGACY_DEFAULT_PALETTE_ACTIONS,
            }),
            encoding="utf-8",
        )
        self.store.commands_path.write_text(
            json.dumps([
                {"trigger": "fix", "type": "ai", "prompt": "My custom fix."},
                {"trigger": "mine", "type": "ai", "prompt": "Keep me."},
            ]),
            encoding="utf-8",
        )

        upgraded = self.store.load()
        commands = self.store.load_commands()
        by_trigger = {command["trigger"]: command for command in commands}

        self.assertEqual(upgraded["mind_palette_actions"], DEFAULT_PALETTE_ACTIONS)
        self.assertEqual(by_trigger["fix"]["prompt"], "My custom fix.")
        self.assertEqual(by_trigger["mine"]["prompt"], "Keep me.")
        self.assertTrue({"summarize", "action-items", "english", "bullets"} <= set(by_trigger))
        stored = json.loads(self.store.config_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["bundled_commands_revision"], BUNDLED_COMMANDS_REVISION)

    def test_bundled_commands_include_dhivehi_translation(self):
        commands_path = Path(__file__).resolve().parents[1] / "commands.json"
        commands = json.loads(commands_path.read_text(encoding="utf-8"))
        triggers = {item.get("trigger") for item in commands}
        self.assertTrue({"summarize", "action-items", "english", "bullets"} <= triggers)
        command = next(item for item in commands if item.get("trigger") == "dhivehi")
        self.assertEqual(command["type"], "ai")
        self.assertIn("Unicode Thaana letters", command["prompt"])

    def test_save_never_writes_plaintext_keys(self):
        config = dict(DEFAULT_CONFIG)
        config["api_keys"] = ["must-not-be-written"]
        self.store.save(config)
        raw = self.store.config_path.read_text(encoding="utf-8")
        self.assertNotIn("must-not-be-written", raw)
        self.assertEqual(json.loads(raw)["api_keys"], [])

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is required")
    def test_protected_keys_round_trip(self):
        config = self.store.set_keys(dict(DEFAULT_CONFIG), ["first-secret", "second-secret"])
        self.store.save(config)
        raw = self.store.config_path.read_text(encoding="utf-8")
        self.assertNotIn("first-secret", raw)
        self.assertEqual(self.store.get_keys(), ["first-secret", "second-secret"])

    def test_commands_are_seeded_and_disabled_commands_preserved(self):
        self.store.ensure_commands()
        commands = self.store.load_commands()
        self.assertEqual(commands[0]["trigger"], "fix")
        commands[0]["enabled"] = False
        self.store.save_commands(commands)
        self.assertFalse(self.store.load_commands()[0]["enabled"])

    def test_duplicate_commands_are_dropped(self):
        self.store.save_commands(
            [
                {"trigger": "fix", "type": "ai", "prompt": "One"},
                {"trigger": "fix", "type": "ai", "prompt": "Two"},
            ]
        )
        self.assertEqual(len(self.store.load_commands()), 1)


if __name__ == "__main__":
    unittest.main()
