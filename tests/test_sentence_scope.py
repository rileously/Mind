"""A sentence-scoped command must leave the rest of the paragraph untouched."""

import ast
import re
import unittest
from pathlib import Path


def load_splitter():
    source_path = Path(__file__).parents[1] / "SwiftSlate.pyw"
    module = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    body = [
        node for node in module.body
        if (isinstance(node, ast.FunctionDef) and node.name == "split_last_sentence")
        or (isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_LAST_SENTENCE" for target in node.targets
        ))
    ]
    namespace = {"re": re}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(source_path), "exec"), namespace)
    return namespace["split_last_sentence"]


class SentenceScopeTests(unittest.TestCase):
    def setUp(self):
        self.split = load_splitter()

    def test_last_sentence_only(self):
        head, tail = self.split("All good here. This one are broke.")
        self.assertEqual(head, "All good here. ")
        self.assertEqual(tail, "This one are broke.")

    def test_single_sentence_is_sent_whole(self):
        self.assertEqual(self.split("this one are broke"), ("", "this one are broke"))

    def test_line_break_counts_as_a_break(self):
        head, tail = self.split("Done!\nNext line here")
        self.assertEqual((head, tail), ("Done!\n", "Next line here"))

    def test_closing_quote_stays_with_the_head(self):
        head, tail = self.split('He said "no." Then he left')
        self.assertEqual((head, tail), ('He said "no." ', "Then he left"))

    def test_rejoining_reproduces_the_input(self):
        text = "One. Two. Three four"
        head, tail = self.split(text)
        self.assertEqual(head + tail, text)


if __name__ == "__main__":
    unittest.main()
