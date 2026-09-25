"""`tagpup_cli.py suggest` writes its suggestions beside the library when not told
where (docs/findings.md, #102).

It wrote suggestions.json into whatever folder it was run from, and run from the
checkout left one at its root. The models and ExifTool are stood in for: this is about
where the file goes, not what is in it.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402


class SuggestWithoutAnOutput(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.photos = os.path.join(self.home.root, "photos")
        os.makedirs(self.photos)
        Image.new("RGB", (32, 32), color="blue").save(os.path.join(self.photos, "a.jpg"), "JPEG")
        self.working = os.path.join(self.home.root, "working")
        os.makedirs(self.working)

    def suggest(self, *options):
        runtime = mock.Mock()
        runtime.settings.side_effect = lambda library, *a: library_settings.of(Library(self.db_path))
        runtime.model_key.return_value = None
        runtime.embeddings.return_value.of.return_value = [0.1] * 512
        suggester = mock.Mock()
        suggester.suggest_for_photo.side_effect = lambda path, *a, **k: {"path": path, "suggested_tags": []}
        suggester.apply_folder_consensus.side_effect = lambda found: found
        here = os.getcwd()
        os.chdir(self.working)
        try:
            with mock.patch.object(tagpup_cli, "get_runtime", return_value=runtime), \
                    mock.patch.object(tagpup_cli, "TagSuggester", return_value=suggester), \
                    mock.patch.object(tagpup_cli, "MetadataExtractor"):
                result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "suggest", self.photos] + list(options))
        finally:
            os.chdir(here)
        self.assertEqual(0, result.exit_code, result.output)
        return result

    def test_the_file_goes_beside_the_library_not_into_the_working_folder(self):
        self.suggest()
        self.assertEqual([], os.listdir(self.working))
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(self.db_path), "harbour_suggestions.json")))

    def test_an_output_given_is_where_it_goes(self):
        self.suggest("--output", "mine.json")
        self.assertEqual(["mine.json"], os.listdir(self.working))


if __name__ == "__main__":
    unittest.main()
