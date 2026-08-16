"""The engine and the desktop app each declare the built-in trigger list.

SwiftSlate.pyw runs as a standalone script and never imports the mind package, so
the two declarations cannot share a constant. This test fails if they drift apart,
which would make the Diagnostics trigger count contradict the command library
total again.
"""

import ast
from pathlib import Path
import unittest


def engine_system_commands() -> tuple[str, ...]:
    source_path = Path(__file__).parents[1] / "SwiftSlate.pyw"
    module = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "SYSTEM_COMMANDS":
                return tuple(ast.literal_eval(node.value))
    raise AssertionError("SYSTEM_COMMANDS is not defined in SwiftSlate.pyw")


class SystemCommandTests(unittest.TestCase):
    def test_engine_and_app_declare_the_same_builtin_triggers(self):
        from mind.main_window import SYSTEM_TRIGGERS

        self.assertEqual(engine_system_commands(), SYSTEM_TRIGGERS)

    def test_builtin_triggers_are_not_shipped_in_commands_json(self):
        import json

        commands_path = Path(__file__).parents[1] / "commands.json"
        commands = json.loads(commands_path.read_text(encoding="utf-8-sig"))
        shipped = {command["trigger"] for command in commands if "trigger" in command}
        overlap = shipped.intersection(engine_system_commands())
        self.assertEqual(
            overlap,
            set(),
            "commands.json must not redefine a built-in trigger; the engine keeps the "
            "file entry and the built-in silently stops working",
        )


if __name__ == "__main__":
    unittest.main()
