from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mind import updater
from mind.updater import UpdateError, is_newer_version, parse_release, version_tuple


class UpdaterTests(unittest.TestCase):
    def test_version_parser_accepts_release_tags(self):
        self.assertEqual(version_tuple("v1.2.3"), (1, 2, 3))
        self.assertEqual(version_tuple("1.4"), (1, 4, 0))

    def test_version_comparison_is_numeric(self):
        self.assertTrue(is_newer_version("0.10.0", "0.9.9"))
        self.assertFalse(is_newer_version("0.3.0", "0.3.0"))

    def test_release_selects_mind_executable(self):
        release = parse_release(
            {
                "tag_name": "v0.4.0",
                "name": "Mind 0.4.0",
                "html_url": "https://github.com/rileously/Mind/releases/tag/v0.4.0",
                "assets": [
                    {
                        "name": "notes.txt",
                        "browser_download_url": "https://github.com/example/notes.txt",
                    },
                    {
                        "name": "Mind.exe",
                        "browser_download_url": "https://github.com/rileously/Mind/releases/download/v0.4.0/Mind.exe",
                    },
                    {
                        "name": "Mind.exe.sha256",
                        "browser_download_url": "https://github.com/rileously/Mind/releases/download/v0.4.0/Mind.exe.sha256",
                    },
                ],
            },
            "0.3.0",
        )
        self.assertIsNotNone(release)
        self.assertEqual(release.asset_name, "Mind.exe")
        self.assertTrue(release.checksum_url.endswith("Mind.exe.sha256"))

    def test_current_release_is_not_an_update(self):
        release = parse_release({"tag_name": "v0.3.0", "assets": []}, "0.3.0")
        self.assertIsNone(release)

    def test_untrusted_asset_is_rejected(self):
        with self.assertRaises(UpdateError):
            parse_release(
                {
                    "tag_name": "v9.0.0",
                    "assets": [
                        {"name": "Mind.exe", "browser_download_url": "https://example.com/Mind.exe"}
                    ],
                },
                "0.3.0",
            )

    def test_installer_waits_for_onefile_parent_and_child(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            updates_path = temporary_path / "Updates"
            updates_path.mkdir()
            source_path = updates_path / "Mind-0.4.0.exe"
            source_path.write_bytes(b"MZ")
            target_path = temporary_path / "Mind.exe"
            target_path.write_bytes(b"MZ")

            with (
                patch.object(updater.sys, "frozen", True, create=True),
                patch.object(updater.sys, "executable", str(target_path)),
                patch.object(updater, "updates_dir", return_value=updates_path),
                patch.object(updater.os, "getpid", return_value=1234),
                patch.object(updater.os, "getppid", return_value=5678),
                patch.object(updater.shutil, "copy2"),
                patch.object(updater.subprocess, "Popen") as popen,
            ):
                updater.launch_update_installer(source_path)

            command = popen.call_args.args[0]
            self.assertEqual(command[command.index("-MindProcessId") + 1], "1234")
            self.assertEqual(command[command.index("-MindParentProcessId") + 1], "5678")
            self.assertEqual(popen.call_args.kwargs["creationflags"], 0x08000000)


if __name__ == "__main__":
    unittest.main()
