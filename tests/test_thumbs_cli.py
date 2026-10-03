"""The CLI's `thumbs warm` (tagpup_cli.py; docs/SPEC_TAGPUP_CLI.md): a dry run unless --apply, counts and bytes,
resumable, writing only the cache folder. Photos are rows as the indexer records them, real JPEGs on disk."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from click.testing import CliRunner  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.store import schema  # noqa: E402
from tagpup_cli import cli  # noqa: E402


class Warm(unittest.TestCase):
    def setUp(self):
        self.vl = ViewLibrary(self)
        self.library = self.vl.library
        for name in ("a.jpg", "b.jpg", "c.jpg"):
            self.vl.photo("Regatta", name, real=True, size=(400, 300))
        self.vl.photo("Regatta", "gone.jpg")

    def run_warm(self, *args, code=0):
        result = CliRunner().invoke(cli, ["--db", self.vl.path, "thumbs", "warm"] + list(args))
        self.assertEqual(code, result.exit_code, result.output)
        return " ".join(result.output.split())

    def test_a_dry_run_says_what_it_would_make_and_writes_nothing(self):
        said = self.run_warm()
        self.assertIn("0 thumbnail(s)", said)
        self.assertIn("4 photo(s) looked at: 0 already have a thumbnail, 3 need one, 1 have no file", said)
        self.assertIn("--apply would make 3, about", said)
        self.assertIn("Nothing was written", said)
        self.assertFalse(os.path.exists(self.library.thumbs))

    def test_apply_makes_them_and_says_how_many_and_how_big(self):
        said = self.run_warm("--apply")
        self.assertIn("Made 3,", said)
        self.assertEqual(3, sum(len(files) for _root, _dirs, files in os.walk(self.library.thumbs)))

    def test_it_can_be_run_again_and_goes_on_from_what_is_there(self):
        self.run_warm("--apply", "--limit", "2")
        said = self.run_warm("--apply", "--limit", "2")
        self.assertIn("Made 1,", said)
        self.assertIn("2 already have a thumbnail", said)
        said = self.run_warm()
        self.assertIn("3 already have a thumbnail, 0 need one", said)

    def test_a_folder_limits_it(self):
        self.vl.photo("Harbour", "d.jpg", real=True)
        said = self.run_warm("--apply", "--folder", os.path.join(self.vl.pictures, "Harbour"))
        self.assertIn("Made 1,", said)

    def test_a_library_that_is_behind_is_brought_up_to_date_only_by_apply(self):
        from test_migrations import at_version
        old = self.vl.home.library("older.db")
        at_version(old, schema.LATEST - 1)
        result = CliRunner().invoke(cli, ["--db", old, "thumbs", "warm"])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertNotIn("Brought the library up to date", result.output)
        result = CliRunner().invoke(cli, ["--db", old, "thumbs", "warm", "--apply"])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("Brought the library up to date", result.output)

    def test_a_cache_that_cannot_be_written_stops_it_and_is_exit_1(self):
        from tagpup.files import thumbs
        with mock.patch.object(thumbs, "write", side_effect=OSError(28, "No space left on device")):
            said = self.run_warm("--apply", code=1)
        self.assertIn("cannot be written", said)
        self.assertIn("Stopped", said)

    def test_an_unplaced_root_is_a_sentence_and_nothing_is_made(self):
        import contextlib

        @contextlib.contextmanager
        def unplaced(_library):
            raise roots_service.Unplaced("This machine does not place the root pictures: add it to machine_roots.json.")
            yield

        with mock.patch.object(roots_service, "pinned", unplaced):
            said = self.run_warm("--apply", code=1)
        self.assertIn("Not run: This machine does not place the root pictures", said)
        self.assertFalse(os.path.exists(self.library.thumbs))


if __name__ == "__main__":
    unittest.main()
