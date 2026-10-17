"""One answer to "does the row describe the file" (tagpup.store.photos.describes / same_mtime): the
index command's skip of a photo it has, the embeddings cache, the thumbnail's version stamp and the
refresh's re-read go by it, as sync does, and not by an `==` or a tolerance of their own. A photo whose modified time differs
by less than the tolerance -- a copy on a drive that rounds times, a share that keeps whole
milliseconds -- is current for all three; one that differs by more is not.

A file another program changes while TagPup reads it leaves a row older than its file, which the next scan reads again:
the stamp is taken before the read, by the indexer's read (MetadataExtractor), a save's whole read back
(file_changes._carry) and the settling of a file a crash left written (file_changes._record_held, #1462). Taken after, it
stamped content read before the change as the newer file.
"""
import os
import sys
import types
import unittest
from unittest import mock
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from face_rows import configured_model  # noqa: E402
from test_a_file_holds_what_exiftool_kept import ReadFilesCase  # noqa: E402
from test_file_journal import EXIFTOOL, Crash, crash_at  # noqa: E402
from test_one_photo_whatever_its_spelling import CliDatabaseCase, expected_dim, make_jpeg  # noqa: E402

from tagpup.files import field_values, metadata  # noqa: E402
from tagpup.services import file_changes, refresh_rows, settings, tagging, thumbnails  # noqa: E402
from tagpup.services.search import PhotoEmbeddings, PhotoIndex  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402


class SubSecondRounding(CliDatabaseCase):
    def setUp(self):
        super().setUp()
        self.photo = os.path.join(self.tmp.name, "Garden Party", "lanterns.jpg")
        make_jpeg(self.photo)
        self.seed([(self.photo, self.photo)])
        self.shift = lambda seconds: os.utime(self.photo, (os.stat(self.photo).st_mtime + seconds,) * 2)

    @patch("tagpup.ml.clip.ClipModel._init_model")
    @patch("tagpup.ml.clip.ClipModel.picture_input")
    @patch("tagpup.ml.clip.ClipModel.embed_input")
    @patch("tagpup_cli.MetadataExtractor.batch_read")
    def index(self, batch_read, embed_input, _picture_input, _init_model):
        embed_input.return_value = [0.1] * expected_dim()
        batch_read.side_effect = lambda files, *a, **k: [
            {"path": f, "tags": [], "people": [], "captions": [], "raw_metadata": {},
             "mtime": os.stat(f).st_mtime, "size": os.stat(f).st_size} for f in files]
        result = self.run_cli(["index", "--skip-faces", os.path.dirname(self.photo)])
        self.assertEqual(result.exit_code, 0, result.output)
        return embed_input.call_count

    def test_the_index_command_skips_a_photo_whose_time_differs_by_rounding(self):
        self.shift(0.04)
        self.assertEqual(0, self.index())

    def test_the_index_command_reads_a_photo_changed_since(self):
        self.shift(5)
        self.assertEqual(1, self.index())

    def test_the_embeddings_cache_holds_for_a_photo_whose_time_differs_by_rounding(self):
        self.shift(0.04)
        index = PhotoIndex(self.db_path, configured_model())
        index.open()
        clip = types.SimpleNamespace(settings=settings.LibrarySettings(dict(settings.DEFAULTS)).embedder)
        try:
            self.assertIsNotNone(PhotoEmbeddings(clip, index).cached(self.photo))
            self.shift(5)
            self.assertIsNone(PhotoEmbeddings(clip, index).cached(self.photo))
        finally:
            index.close()


def touched_by_another_program(path):
    """What another program's save does to the file's stamp: a newer modified time."""
    os.utime(path, (os.stat(path).st_mtime + 30,) * 2)


def describes_its_file(stamp, path):
    now = os.stat(path)
    return store_photos.describes(*stamp, (now.st_mtime, now.st_size))


class AFileChangedWhileItIsRead(ReadFilesCase):
    def test_the_indexers_read_stamps_the_file_as_it_was_before_the_read(self):
        a = self.make("a.jpg", ("Beach",), row=False)

        class Session:
            def __init__(self, executable=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_tags(self, files, tags=None):
                for path in files:
                    touched_by_another_program(path)
                return [{"SourceFile": path, "XMP:Subject": ["Beach"]} for path in files]
        with mock.patch.object(metadata, "ExifToolSession", Session):
            (record,) = metadata.MetadataExtractor().batch_read([a])
        self.assertFalse(describes_its_file((record["mtime"], record["size"]), a),
                         "a change made during the read was stamped as read")

    def test_a_saves_row_is_stamped_as_the_file_was_before_its_read_back(self):
        a = self.make_read("a.jpg")
        real = file_changes._read_whole

        def read_whole(et, row, read):
            touched_by_another_program(row.path)
            return real(et, row, read)
        with mock.patch.object(file_changes, "_read_whole", read_whole):
            saved = tagging.save_photo(self.library, a, "Quay at dusk", ["Beach"], None, EXIFTOOL, "")
        self.assertTrue(saved.ok, saved.message())
        stamp = self.rows("SELECT mtime, size FROM photos WHERE path = ?", (a,))[0]
        self.assertFalse(describes_its_file(stamp, a), "a change made during the read back was stamped as read")

    def test_a_file_a_crash_left_written_is_stamped_as_it_was_before_the_settles_read(self):
        a = self.make_read("a.jpg")
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written")):
            with self.assertRaises(Crash):
                self.add([a])
        real = field_values.read_one

        def read_one(et, path, wanted):
            held = real(et, path, wanted)
            touched_by_another_program(path)
            return held
        with mock.patch.object(field_values, "read_one", read_one):
            self.assertEqual(1, self.settle())
        stamp = self.rows("SELECT mtime, size FROM photos WHERE path = ?", (a,))[0]
        self.assertFalse(describes_its_file(stamp, a), "a change made during the settle's read was stamped as read")


class TheRefresh(unittest.TestCase):
    def test_a_row_read_again_whose_time_differs_by_rounding_has_not_changed_its_stamp(self):
        """#1368: refresh_rows compared the stamp with `!=`, so a re-read photo on a share that keeps whole milliseconds
        was a change of mtime/size, written again, where sync and the index call it current."""
        record = {"tags": [], "people": [], "captions": [], "raw_metadata": {}, "mtime": 1700000000.04, "size": 2048}
        row = ("[]", "[]", "[]", "{}", 1700000000.0, 2048, None)
        self.assertEqual([], refresh_rows.differences(None, "lanterns.jpg", record, row)[0])
        moved_on = dict(record, mtime=1700000001.0)
        self.assertEqual(["mtime/size"], refresh_rows.differences(None, "lanterns.jpg", moved_on, row)[0])


class TheThumbnailsVersion(unittest.TestCase):
    def test_a_version_within_the_tolerance_is_the_files(self):
        thumb = thumbnails.Thumb(b"", thumbnails.OK, "etag", 1700000000.25)
        self.assertTrue(thumb.is_version(1700000000.25 + store_photos.MTIME_TOLERANCE / 2))
        self.assertFalse(thumb.is_version(1700000000.25 + 1))

    def test_a_file_that_is_not_there_has_no_version(self):
        self.assertFalse(thumbnails.Thumb(b"", thumbnails.OK, None, None).is_version(0.0))


if __name__ == "__main__":
    unittest.main()
