from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import sync_source


class SyncSourceTests(unittest.TestCase):
    def test_defaults_to_api26_and_syncs_only_static_ets_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "openharmony"
            argv = ["sync_source.py", "--source", str(source), "--jobs", "3"]

            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    sync_source.shutil, "which", return_value="/tools/repo"
                ),
                mock.patch.object(sync_source.subprocess, "run") as run,
            ):
                self.assertEqual(0, sync_source.main())

            init_command = run.call_args_list[0].args[0]
            self.assertEqual(
                [
                    "/tools/repo",
                    "init",
                    "-u",
                    sync_source.DEFAULT_MANIFEST_URL,
                    "-b",
                    "OpenHarmony-7.0-Release",
                    "--no-repo-verify",
                ],
                init_command,
            )

            sync_command = run.call_args_list[1].args[0]
            self.assertEqual(
                [
                    "/tools/repo",
                    "sync",
                    "-c",
                    "-j3",
                    "--fail-fast",
                    "--no-tags",
                ],
                sync_command[:6],
            )
            self.assertEqual(
                list(sync_source.ETS_STATIC_SOURCE_PROJECTS), sync_command[6:]
            )

    def test_static_project_set_excludes_full_product_sources(self) -> None:
        projects = set(sync_source.ETS_STATIC_SOURCE_PROJECTS)

        self.assertEqual(72, len(projects))
        self.assertIn("arkcompiler/ets_frontend", projects)
        self.assertIn("developtools/ace_ets2bundle", projects)
        self.assertIn("interface/sdk-js", projects)
        self.assertNotIn("applications/standard/launcher", projects)
        self.assertNotIn("device/board/hisilicon", projects)
        self.assertNotIn("kernel/linux/linux-5.10", projects)


if __name__ == "__main__":
    unittest.main()
