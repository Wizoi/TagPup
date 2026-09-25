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
from tagpup.store import schema, taxonomy  # noqa: E402


class TheCliTestModeKeepsToItsLibrary(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tagpup_tree_")
        self.addCleanup(shutil.rmtree, self.home, True)
        environ = mock.patch.dict(os.environ, {"TAGPUP_HOME": self.home})
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop("TAGPUP_DB_PATH", None)
        self.config = tagpup_config.load()
        self.real = os.path.join(tagpup_config.data_dir(self.config), "photo_index.db")
        os.makedirs(os.path.dirname(self.real), exist_ok=True)
        schema.ensure(self.real)

    def test_test_mode_works_on_the_test_library(self):
        self.assertEqual("test_photo_index.db",
                         os.path.basename(tagpup_cli.get_db_path(self.config, test_mode=True, cli_db="photo_index")))

    def test_a_command_says_which_library_it_works_on(self):
        # There is no library the CLI opens unasked (docs/findings.md, #100): one it
        # picked on its own was indexed, written and reset by mistake.
        import click
        with self.assertRaises(click.UsageError):
            tagpup_cli.get_db_path(self.config)
        with self.assertRaises(click.UsageError):
            tagpup_cli.get_db_path(self.config, test_mode=True)

    def test_a_tree_saved_in_test_mode_stays_out_of_the_real_library(self):
        test_library = tagpup_cli.get_db_path(self.config, test_mode=True, cli_db="photo_index")
        schema.ensure(test_library)
        tree = taxonomy.TagTaxonomy(test_library)
        tree.load()
        tree.add_tag("Trips/Harbour Walk")
        tree.save()
        self.assertIsNotNone(taxonomy.find(test_library, "Trips/Harbour Walk"))
        self.assertIsNone(taxonomy.find(self.real, "Trips/Harbour Walk"))

    def test_no_file_is_written_beside_either(self):
        tree = taxonomy.TagTaxonomy(self.real)
        tree.load()
        tree.add_tag("Trips/Harbour Walk")
        tree.save()
        folder = os.path.dirname(self.real)
        self.assertEqual([], [name for name in os.listdir(folder) if name.endswith(".json")])


if __name__ == "__main__":
    unittest.main()
