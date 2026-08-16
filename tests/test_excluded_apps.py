"""Mind stays out of the apps listed in excluded_apps.

The engine matches these against the process image name. Matching the window
title instead would be far too loose: a document named "telegram notes.txt"
would switch Mind off in whatever editor was showing it.
"""

import ast
import unittest
from pathlib import Path

from mind.config_store import DEFAULT_CONFIG


def engine_default_excluded() -> tuple[str, ...]:
    source_path = Path(__file__).parents[1] / "SwiftSlate.pyw"
    module = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "DEFAULT_EXCLUDED_APPS":
                return tuple(ast.literal_eval(node.value))
    raise AssertionError("DEFAULT_EXCLUDED_APPS is not defined in SwiftSlate.pyw")


def engine_source() -> str:
    return (Path(__file__).parents[1] / "SwiftSlate.pyw").read_text(encoding="utf-8")


class ExcludedAppsTests(unittest.TestCase):
    def test_telegram_is_excluded_by_default_in_both_places(self):
        self.assertIn("telegram", engine_default_excluded())
        self.assertIn("telegram", DEFAULT_CONFIG["excluded_apps"])

    def test_exclusion_matches_the_process_name_not_the_window_title(self):
        source = engine_source()
        start = source.index("def _window_is_excluded")
        body = source[start : source.index("\ndef ", start + 1)]
        self.assertIn("QueryFullProcessImageNameW", body)
        self.assertNotIn(
            "GetWindowTextW",
            body,
            "matching the window title would disable Mind for any document whose "
            "name happens to contain an excluded app's name",
        )

    def test_keystrokes_are_dropped_rather_than_buffered_in_excluded_apps(self):
        # Holding keystrokes from an excluded app would keep typed text in memory
        # for no reason, and risk a trigger firing there later.
        source = engine_source()
        self.assertIn("if fg_excluded:", source)
        marker = source.index("if fg_excluded:")
        block = source[marker : marker + 200]
        self.assertIn("keystroke_buffer.clear()", block)
        self.assertIn("return", block)

    def test_the_cache_is_dropped_when_the_list_changes(self):
        # The decision is cached per foreground window, so an edited list has to
        # invalidate it or it would not apply until the user switched apps.
        source = engine_source()
        marker = source.index('log("WARNING: excluded_apps must be a list')
        block = source[marker : marker + 400]
        self.assertIn("last_fg_hwnd = 0", block)


if __name__ == "__main__":
    unittest.main()
