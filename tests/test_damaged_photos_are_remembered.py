"""A photo found damaged is remembered, by its path and the stamp its file had, and not
read again until the file changes (docs/findings.md, #407; tagpup.store.damaged_files).

The indexer failed on the same damaged files every run: each run loaded the photo index
and CLIP, read them, failed, and recorded nothing -- 12 runs in 8 minutes one day. Found
once, a photo is recorded and logged at WARNING once; the next index passes it over
without reading it; replaced by a good copy it is indexed at once and forgotten.

Real ExifTool and real Pillow, on damaged JPEGs made here (tests/damaged_photos); only
the CLIP model is stood in for, after the picture has been decoded.
"""
import logging
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import own_home  # noqa: E402
from click.testing import CliRunner  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.ml.clip import output_dim  # noqa: E402
from tagpup.services import damaged_photos as damaged  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
THEN = 1_700_000_000


def vector():
    return [0.1] * (output_dim(library_settings.DEFAULTS["model.name"]) or 512)


class Case(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="damaged_kept_")
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.library = Library(self.db_path)
        self.folder = os.path.join(self.home.root, "Harbour")
        self.cut = damaged_photos.truncated(os.path.join(self.folder, "cut short.jpg"))
        self.half = damaged_photos.second_half_zeros(os.path.join(self.folder, "copy stopped.jpg"))
        self.whole = os.path.join(self.folder, "whole.jpg")
        damaged_photos.whole_jpeg(self.whole)
        for path in (self.cut, self.half, self.whole):
            os.utime(path, (THEN, THEN))

    def index(self):
        with mock.patch("tagpup.ml.clip.ClipModel._init_model"), \
                mock.patch("tagpup.ml.clip.ClipModel.embed_image", autospec=True,
                           side_effect=self.embedded) as embed:
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", self.folder, "--skip-faces"])
        self.assertEqual(0, result.exit_code, result.output)
        return result, [call.args[1] for call in embed.call_args_list]

    def embedded(self, model, path, seen=None):
        """The real embed_image, with the model stood in for after the decode."""
        from tagpup.files import images
        picture = images.opened(path, upright=True)
        if seen is not None:
            seen(picture)
        return vector()

    def rows(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return {os.path.basename(path) for (path,) in conn.execute("SELECT path FROM photos")}
        finally:
            conn.close()

    def listed(self):
        return {os.path.basename(each["path"]): each["kind"] for each in damaged.listed(self.library)}


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class TheIndexerRemembers(Case):
    def test_what_it_found_is_listed_with_why(self):
        self.index()
        self.assertEqual({"cut short.jpg": "truncated", "copy stopped.jpg": damaged.INCOMPLETE}, self.listed())
        shown = {each["name"]: each for each in damaged.listed(self.library)}
        self.assertEqual(os.path.getsize(self.cut), shown["cut short.jpg"]["size"])
        self.assertFalse(shown["cut short.jpg"]["indexed"])
        self.assertTrue(shown["copy stopped.jpg"]["indexed"])
        self.assertIn("incomplete copy", shown["copy stopped.jpg"]["reason"])
        self.assertEqual({"unreadable": 1, "incomplete": 1}, damaged.counts(self.library))

    def test_the_next_index_does_not_read_it_again(self):
        self.index()
        os.utime(self.whole, (THEN + 60, THEN + 60))   # something else to index
        result, embedded = self.index()
        self.assertNotIn(self.cut, embedded, "a photo found damaged, unchanged since, was read again")
        self.assertIn("Passed over 1 photo(s) found damaged before", result.output)

    def test_replaced_with_a_good_copy_it_is_indexed_and_forgotten(self):
        self.index()
        damaged_photos.whole_jpeg(self.cut, seed=11)
        result, embedded = self.index()
        self.assertIn(self.cut, embedded)
        self.assertIn("cut short.jpg", self.rows())
        self.assertEqual({"copy stopped.jpg": damaged.INCOMPLETE}, self.listed())
        self.assertEqual({"copy stopped.jpg"}, {os.path.basename(each.path) for each in damaged.records(self.library)})

    def test_it_is_logged_at_warning_once(self):
        with self.assertLogs("tagpup.services.damaged_photos", logging.WARNING) as said:
            self.index()
        self.assertEqual(2, len(said.records))
        stamp = (os.stat(self.cut).st_mtime, os.stat(self.cut).st_size)
        with mock.patch.object(logging.getLogger("tagpup.services.damaged_photos"), "warning") as warned:
            again = damaged.remember(self.library, [(self.cut, stamp, "truncated", "found again", 0)])
        self.assertEqual([], again)
        warned.assert_not_called()


class TheRecords(Case):
    """What the records say, made as the indexer makes them (damaged_photos.remember)."""

    def remember(self, path, kind="truncated"):
        stat = os.stat(path)
        return damaged.remember(self.library, [(path, (stat.st_mtime, stat.st_size), kind, "found", 0)])

    def test_a_file_changed_since_leaves_the_list_at_once(self):
        self.remember(self.cut)
        os.utime(self.cut, (THEN + 60, THEN + 60))
        self.assertEqual({}, self.listed())
        self.assertEqual(1, len(damaged.records(self.library)), "kept until a sync or an index forgets it")

    def test_found_again_with_a_new_stamp_it_is_new(self):
        self.assertEqual([self.cut], self.remember(self.cut))
        self.assertEqual([], self.remember(self.cut))
        os.utime(self.cut, (THEN + 60, THEN + 60))
        self.assertEqual([self.cut], self.remember(self.cut))

    def test_prune_forgets_a_file_changed_or_deleted_and_keeps_one_whose_folder_is_not_there(self):
        away = os.path.join(self.home.root, "Unplugged", "far.jpg")
        damaged_photos.truncated(away)
        for path in (self.cut, self.half, away):
            self.remember(path)
        os.utime(self.cut, (THEN + 60, THEN + 60))
        os.remove(self.half)
        os.remove(away)
        os.rmdir(os.path.dirname(away))    # a drive unplugged: the folder is not there
        self.assertEqual(2, damaged.prune(self.library))
        self.assertEqual([away], [each.path for each in damaged.records(self.library)])

    def test_removing_a_folder_from_the_library_forgets_its_records(self):
        from tagpup.store import photos as store_photos
        self.remember(self.cut)
        db.write_with_connection(self.db_path, lambda conn: store_photos.remove_under(conn, self.folder))
        self.assertEqual([], damaged.records(self.library))


if __name__ == "__main__":
    unittest.main()
