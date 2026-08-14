from __future__ import annotations

import json
import os
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from .paths import SOURCE_DIR, data_dir, legacy_data_dir
from .secrets import protect_text, unprotect_text


CONFIG_SCHEMA_VERSION = 2
BUNDLED_COMMANDS_REVISION = 1
LEGACY_DEFAULT_PALETTE_ACTIONS = [
    "fix", "improve", "formal", "casual", "shorten", "reply", "dhivehi",
]
DEFAULT_PALETTE_ACTIONS = [
    "fix",
    "improve",
    "summarize",
    "action-items",
    "formal",
    "shorten",
    "reply",
    "dhivehi",
    "local-clean-spacing",
    "local-writing-stats",
]
_BUNDLED_ADDITIONS = {
    1: {"summarize", "action-items", "english", "bullets"},
}


DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": CONFIG_SCHEMA_VERSION,
    "bundled_commands_revision": BUNDLED_COMMANDS_REVISION,
    "onboarding_complete": False,
    "provider": "gemini",
    "provider_profile": "gemini",
    "model": "gemini-3.5-flash-lite",
    "endpoint": "",
    "temperature": 0.5,
    "prefix": "?",
    "key_delay": 200,
    "spinner": "animated",
    "autocorrect_after_space": False,
    "autocorrect_strength": "balanced",
    "theme": "system",
    "accent_color": "teal",
    "start_with_windows": False,
    "start_engine_on_launch": False,
    "mind_palette_enabled": False,
    "mind_palette_auto_show_on_selection": False,
    "word_definitions_enabled": True,
    "mind_palette_shortcut": "Ctrl+Alt+M",
    "mind_palette_actions": DEFAULT_PALETTE_ACTIONS,
    "mind_palette_columns": 2,
    "mind_palette_show_preview": True,
    "mind_palette_width": 390,
    "mind_palette_image_ocr_enabled": True,
    "api_keys": [],
    "api_keys_protected": "",
}


PROVIDER_DEFAULTS = {
    "gemini": {"provider": "gemini", "model": "gemini-3.5-flash-lite", "endpoint": ""},
    "groq": {"provider": "groq", "model": "openai/gpt-oss-120b", "endpoint": ""},
    "ollama": {"provider": "custom", "model": "llama3.2", "endpoint": "http://localhost:11434/v1"},
    "lmstudio": {"provider": "custom", "model": "local-model", "endpoint": "http://localhost:1234/v1"},
    "custom": {"provider": "custom", "model": "", "endpoint": ""},
}


class ConfigStore:
    def __init__(self, root: Path | None = None, source: Path | None = None):
        self.root = Path(root) if root else data_dir()
        self.source = Path(source) if source else SOURCE_DIR
        self.config_path = self.root / "config.json"
        self.commands_path = self.root / "commands.json"
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def legacy_available(self) -> bool:
        legacy = legacy_data_dir()
        return (legacy / "config.json").exists()

    def load(self) -> dict[str, Any]:
        result = deepcopy(DEFAULT_CONFIG)
        if not self.config_path.exists():
            return result
        try:
            with self.config_path.open("r", encoding="utf-8-sig") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                result.update(loaded)
                try:
                    schema_version = int(loaded.get("schema_version", 1))
                except (TypeError, ValueError):
                    schema_version = 1
                if (
                    schema_version < 2
                    and loaded.get("mind_palette_actions") == LEGACY_DEFAULT_PALETTE_ACTIONS
                ):
                    result["mind_palette_actions"] = list(DEFAULT_PALETTE_ACTIONS)
                result["schema_version"] = CONFIG_SCHEMA_VERSION
        except (OSError, ValueError, json.JSONDecodeError):
            backup = self.config_path.with_suffix(".json.invalid")
            try:
                shutil.copy2(self.config_path, backup)
            except OSError:
                pass
        result["api_keys"] = []
        return result

    def save(self, config: dict[str, Any]) -> None:
        output = deepcopy(DEFAULT_CONFIG)
        output.update(config)
        output["api_keys"] = []
        output["schema_version"] = CONFIG_SCHEMA_VERSION
        self._atomic_json_write(self.config_path, output)

    def get_keys(self, config: dict[str, Any] | None = None) -> list[str]:
        current = config or self.load()
        protected = current.get("api_keys_protected", "")
        if not isinstance(protected, str) or not protected:
            return []
        try:
            decoded = json.loads(unprotect_text(protected))
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            return []
        if not isinstance(decoded, list):
            return []
        return [item.strip() for item in decoded if isinstance(item, str) and item.strip()]

    def set_keys(self, config: dict[str, Any], keys: list[str]) -> dict[str, Any]:
        clean = [key.strip() for key in keys if isinstance(key, str) and key.strip()]
        updated = deepcopy(config)
        updated["api_keys"] = []
        updated["api_keys_protected"] = protect_text(json.dumps(clean)) if clean else ""
        return updated

    def ensure_commands(self) -> None:
        if self.commands_path.exists():
            self._upgrade_bundled_commands()
            return
        source_commands = self.source / "commands.json"
        if source_commands.exists():
            shutil.copy2(source_commands, self.commands_path)
        else:
            self._atomic_json_write(self.commands_path, [])

    def _upgrade_bundled_commands(self) -> None:
        if not self.config_path.exists():
            return
        try:
            with self.config_path.open("r", encoding="utf-8-sig") as handle:
                stored_config = json.load(handle)
            revision = int(stored_config.get("bundled_commands_revision", 0))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return
        if revision >= BUNDLED_COMMANDS_REVISION:
            return

        source_commands = self.source / "commands.json"
        try:
            with self.commands_path.open("r", encoding="utf-8-sig") as handle:
                current = json.load(handle)
            with source_commands.open("r", encoding="utf-8-sig") as handle:
                bundled = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            return
        if not isinstance(current, list) or not isinstance(bundled, list):
            return

        additions = set().union(*(
            triggers for item_revision, triggers in _BUNDLED_ADDITIONS.items()
            if revision < item_revision <= BUNDLED_COMMANDS_REVISION
        ))
        existing = {
            str(command.get("trigger", "")) for command in current if isinstance(command, dict)
        }
        new_items = [
            command for command in bundled
            if isinstance(command, dict)
            and str(command.get("trigger", "")) in additions
            and str(command.get("trigger", "")) not in existing
        ]
        if new_items:
            self.save_commands([*current, *new_items])

        config = self.load()
        config["bundled_commands_revision"] = BUNDLED_COMMANDS_REVISION
        self.save(config)

    def load_commands(self) -> list[dict[str, Any]]:
        self.ensure_commands()
        try:
            with self.commands_path.open("r", encoding="utf-8-sig") as handle:
                items = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def save_commands(self, commands: list[dict[str, Any]]) -> None:
        clean: list[dict[str, Any]] = []
        seen: set[str] = set()
        for command in commands[:100]:
            trigger = str(command.get("trigger", "")).strip()
            kind = str(command.get("type", "ai"))
            if not trigger or len(trigger) > 50 or trigger in seen:
                continue
            if kind not in {"ai", "replacer-text", "replacer-shell"}:
                continue
            seen.add(trigger)
            item = {
                "trigger": trigger,
                "type": kind,
                "enabled": bool(command.get("enabled", True)),
            }
            if kind == "ai":
                item["prompt"] = str(command.get("prompt", "")).strip()
            else:
                item["value"] = str(command.get("value", ""))
            clean.append(item)
        self._atomic_json_write(self.commands_path, clean)

    def restore_default_commands(self) -> None:
        source_commands = self.source / "commands.json"
        if not source_commands.exists():
            raise FileNotFoundError("Bundled commands.json is missing.")
        shutil.copy2(source_commands, self.commands_path)

    def import_legacy(self) -> dict[str, Any]:
        legacy = legacy_data_dir()
        legacy_config_path = legacy / "config.json"
        if not legacy_config_path.exists():
            raise FileNotFoundError("No SwiftSlate configuration was found.")
        with legacy_config_path.open("r", encoding="utf-8-sig") as handle:
            old = json.load(handle)
        if not isinstance(old, dict):
            raise ValueError("The existing configuration is not valid.")

        config = deepcopy(DEFAULT_CONFIG)
        for key in ("provider", "model", "endpoint", "temperature", "prefix", "key_delay", "spinner"):
            if key in old:
                config[key] = old[key]
        provider = str(config.get("provider", "gemini"))
        config["provider_profile"] = provider if provider in {"gemini", "groq"} else "custom"
        legacy_keys = old.get("api_keys", [])
        if isinstance(legacy_keys, list):
            config = self.set_keys(config, legacy_keys)
        config["onboarding_complete"] = True
        self.save(config)

        legacy_commands = legacy / "commands.json"
        if legacy_commands.exists():
            shutil.copy2(legacy_commands, self.commands_path)
        else:
            self.ensure_commands()
        return config

    @staticmethod
    def provider_values(profile: str) -> dict[str, str]:
        return deepcopy(PROVIDER_DEFAULTS.get(profile, PROVIDER_DEFAULTS["custom"]))

    @staticmethod
    def _atomic_json_write(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
