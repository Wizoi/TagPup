"""A library's tag tree is in the library, and nowhere else.

It lived in a JSON file whose name decided which library it belonged to: any name ending
in photo_taxonomy.json meant photo_index.db beside it, so the CLI's test mode, which
names test_photo_taxonomy.json, saved its tree into the real library (docs/findings.md,
#61). And the CLI and the servers named the main library's file differently (#13).
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

import tagpup_cli  # noqa: E402
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.store import schema  # noqa: E402


class TheCliTestModeKeepsToItsLibrary(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tagpup_tree_")
        self.addCleanup(shutil.rmtree, self.home, True)
        environ = mock.patch.dict(os.environ, {"TAGPUP_HOME": self.home})
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop("TAGPUP_DB_PATH", None)
        self.real = os.path.join(tagpup_config.data_dir(), "photo_index.db")
        os.makedirs(os.path.dirname(self.real), exist_ok=True)
        schema.ensure(self.real)

    def test_test_mode_works_on_the_test_library(self):
        self.assertEqual("test_photo_index.db",
                         os.path.basename(tagpup_cli.get_db_path(test_mode=True, cli_db="photo_index")))

    def test_a_command_says_which_library_it_works_on(self):
        # There is no library the CLI opens unasked (docs/findings.md, #100): one it
        # picked on its own was indexed, written and reset by mistake.
        import click
        with self.assertRaises(click.UsageError):
            tagpup_cli.get_db_path()
        with self.assertRaises(click.UsageError):
            tagpup_cli.get_db_path(test_mode=True)


if __name__ == "__main__":
    unittest.main()
