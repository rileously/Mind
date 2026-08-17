"""Searching a PC from a chat bot has to stay inside its root and stay quick."""

import shutil
import tempfile
import unittest
from pathlib import Path

from mind.telegram_files import NOISY_DIRS, rank_hits, search_files


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        (self.root / "Documents" / "2026").mkdir(parents=True)
        (self.root / "Documents" / "report.pdf").write_text("x", encoding="utf-8")
        (self.root / "Documents" / "2026" / "site-report.pdf").write_text("x", encoding="utf-8")
        (self.root / "report-archive").mkdir()

        # Things a search should not surface.
        (self.root / ".aws").mkdir()
        (self.root / ".aws" / "report-credentials").write_text("SECRET", encoding="utf-8")
        (self.root / "project" / "node_modules" / "pkg").mkdir(parents=True)
        (self.root / "project" / "node_modules" / "pkg" / "report.js").write_text(
            "x", encoding="utf-8"
        )

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _names(self, query, **kwargs):
        hits, _ = search_files(self.root, query, **kwargs)
        return [h.relative for h in hits]

    def test_finds_matches_at_any_depth(self):
        found = self._names("report")
        self.assertTrue(any("report.pdf" in n for n in found))
        self.assertTrue(any("site-report.pdf" in n for n in found))

    def test_folders_match_too(self):
        self.assertTrue(any("report-archive" in n for n in self._names("report")))

    def test_credential_folders_are_never_searched(self):
        # The whole point of pruning hidden directories rather than filtering
        # their results: the walk never descends into them.
        self.assertFalse(any(".aws" in n for n in self._names("report")))

    def test_dependency_folders_are_skipped(self):
        self.assertFalse(any("node_modules" in n for n in self._names("report")))

    def test_dependency_folders_can_be_searched_on_request(self):
        found = self._names("report", skip_noisy=False)
        self.assertTrue(any("node_modules" in n for n in found))

    def test_hidden_entries_can_be_searched_on_request(self):
        self.assertTrue(any(".aws" in n for n in self._names("report", include_hidden=True)))

    def test_every_result_stays_under_the_root(self):
        hits, _ = search_files(self.root, "report")
        for hit in hits:
            self.assertTrue(hit.path.resolve().is_relative_to(self.root))

    def test_a_blank_query_matches_nothing(self):
        self.assertEqual(self._names(""), [])
        self.assertEqual(self._names("   "), [])

    def test_the_result_cap_is_honoured(self):
        crowded = self.root / "many"
        crowded.mkdir()
        for index in range(30):
            (crowded / f"report-{index}.txt").write_text("x", encoding="utf-8")
        hits, truncated = search_files(self.root, "report", limit=10)
        self.assertEqual(len(hits), 10)
        self.assertTrue(truncated)

    def test_search_is_case_insensitive(self):
        self.assertTrue(self._names("REPORT"))


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        deep = self.root / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "report.pdf").write_text("x", encoding="utf-8")
        (self.root / "quarterly-report-draft.pdf").write_text("x", encoding="utf-8")
        (self.root / "report.docx").write_text("x", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_exact_shallow_name_wins(self):
        hits, _ = search_files(self.root, "report")
        self.assertEqual(hits[0].relative, "report.docx")

    def test_a_name_starting_with_the_query_beats_one_merely_containing_it(self):
        hits, _ = search_files(self.root, "report")
        names = [h.relative for h in hits]
        self.assertLess(
            names.index("report.docx"), names.index("quarterly-report-draft.pdf")
        )

    def test_ranking_leaves_the_set_of_results_alone(self):
        hits, _ = search_files(self.root, "report")
        self.assertEqual(len(rank_hits(list(hits), "report")), len(hits))


class NoisyDirTests(unittest.TestCase):
    def test_the_skip_list_is_lowercase_so_matching_works(self):
        # Comparison is done on a lowercased name; an uppercase entry here would
        # silently never match.
        for name in NOISY_DIRS:
            self.assertEqual(name, name.lower(), name)


if __name__ == "__main__":
    unittest.main()
