"""Pruning stranded one-file runtimes must never touch a folder still in use."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

from mind import runtime_cleanup
from mind.runtime_cleanup import prune_runtime_dirs, stale_runtime_dirs


OLD = 10_000


class RuntimeCleanupTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def _make(self, name: str, age_seconds: int = OLD, contents: str = "runtime") -> Path:
        target = self.root / name
        target.mkdir()
        payload = target / "python312.dll"
        payload.write_text(contents, encoding="utf-8")
        stamp = time.time() - age_seconds
        import os

        os.utime(target, (stamp, stamp))
        return target

    def test_old_runtime_folders_are_removed(self):
        self._make("_MEI111111")
        self._make("_MEI222222")
        removed, freed = prune_runtime_dirs(root=self.root)
        self.assertEqual(removed, 2)
        self.assertGreater(freed, 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_recent_folders_are_left_alone(self):
        # A sibling Mind process may still be unpacking into a fresh folder.
        self._make("_MEI333333", age_seconds=5)
        removed, _freed = prune_runtime_dirs(root=self.root)
        self.assertEqual(removed, 0)
        self.assertTrue((self.root / "_MEI333333").is_dir())

    def test_unrelated_folders_are_never_touched(self):
        keep = self.root / "important-data"
        keep.mkdir()
        (keep / "notes.txt").write_text("keep me", encoding="utf-8")
        removed, _freed = prune_runtime_dirs(root=self.root)
        self.assertEqual(removed, 0)
        self.assertTrue((keep / "notes.txt").is_file())

    def test_folder_with_an_open_file_is_skipped(self):
        target = self._make("_MEI444444")
        handle = (target / "python312.dll").open("rb")
        try:
            removed, _freed = prune_runtime_dirs(root=self.root)
        finally:
            handle.close()
        self.assertEqual(removed, 0, "a runtime with an open file must survive")
        self.assertTrue(target.is_dir())
        self.assertTrue((target / "python312.dll").is_file())

    def test_current_runtime_is_excluded(self):
        target = self._make("_MEI555555")
        original = runtime_cleanup.current_runtime_dir
        runtime_cleanup.current_runtime_dir = lambda: target.resolve()
        try:
            self.assertEqual(stale_runtime_dirs(root=self.root), [])
        finally:
            runtime_cleanup.current_runtime_dir = original

    def test_missing_root_is_not_an_error(self):
        removed, freed = prune_runtime_dirs(root=self.root / "does-not-exist")
        self.assertEqual((removed, freed), (0, 0))

    @unittest.skipUnless(sys.platform == "win32", "renaming semantics are Windows-specific")
    def test_open_file_blocks_rename_claim(self):
        target = self._make("_MEI666666")
        handle = (target / "python312.dll").open("rb")
        try:
            with self.assertRaises(OSError):
                target.rename(target.with_name(target.name + ".pruning"))
        finally:
            handle.close()


if __name__ == "__main__":
    unittest.main()
