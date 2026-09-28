"""`tagpup_cli.py index <folder>` adds the folder it is given, however it is typed.

Indexing a folder is adding it (tagpup.services.libraries.record_added), which takes only
a full path: `index .`, run in the folder, added nothing, so the vectors kept for its
photos, before their rows were recorded, had no row to go to. The folder is handed on as
the index stores it (tagpup.core.paths.stored).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402


class IndexAddsItsFolder(unittest.TestCase):
    def test_a_folder_typed_as_dot(self):
        home = own_home.for_test(self)
        db_path = home.library("harbour.db")
        library_actions.create(db_path)
        folder = os.path.join(home.root, "Regatta")
        os.makedirs(folder)
        Image.new("RGB", (8, 8)).save(os.path.join(folder, "start.jpg"), "JPEG")
        runtime = mock.Mock()
        runtime.settings.side_effect = lambda library, *a: library_settings.of(Library(db_path))
        runtime.model_key.return_value = None
        here = os.getcwd()
        os.chdir(folder)
        try:
            # Stopped where the files would be read: what is under test is the adding.
            with mock.patch.object(tagpup_cli, "get_runtime", return_value=runtime), \
                    mock.patch.object(tagpup_cli, "MetadataExtractor", side_effect=SystemExit(0)):
                result = CliRunner().invoke(tagpup_cli.cli, ["--db", db_path, "index", "."])
        finally:
            os.chdir(here)
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIsNone(library_actions.not_in(Library(db_path), folder), "the folder was not added")


if __name__ == "__main__":
    unittest.main()
