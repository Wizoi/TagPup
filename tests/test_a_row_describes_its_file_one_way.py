"""One answer to "does the row describe the file" (tagpup.store.photos.describes / same_mtime): the
index command's skip of a photo it has, the embeddings cache and the thumbnail's version stamp go by
it, as sync does, and not by an `==` or a tolerance of their own. A photo whose modified time differs
by less than the tolerance -- a copy on a drive that rounds times, a share that keeps whole
milliseconds -- is current for all three; one that differs by more is not.
"""
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from face_rows import configured_model  # noqa: E402
from test_one_photo_whatever_its_spelling import CliDatabaseCase, expected_dim, make_jpeg  # noqa: E402

from tagpup.services import settings, thumbnails  # noqa: E402
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
    @patch("tagpup.ml.clip.ClipModel.embed_image")
    @patch("tagpup_cli.MetadataExtractor.batch_read")
    def index(self, batch_read, embed_image, _init_model):
        embed_image.return_value = [0.1] * expected_dim()
        batch_read.side_effect = lambda files, *a, **k: [
            {"path": f, "tags": [], "people": [], "captions": [], "raw_metadata": {},
             "mtime": os.stat(f).st_mtime, "size": os.stat(f).st_size} for f in files]
        result = self.run_cli(["index", "--skip-faces", os.path.dirname(self.photo)])
        self.assertEqual(result.exit_code, 0, result.output)
        return embed_image.call_count

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
        clip = types.SimpleNamespace(settings=settings.LibrarySettings(dict(settings.DEFAULTS)).embedder,
                                     embed_image=None)
        try:
            self.assertIsNotNone(PhotoEmbeddings(clip, index).cached(self.photo))
            self.shift(5)
            self.assertIsNone(PhotoEmbeddings(clip, index).cached(self.photo))
        finally:
            index.close()


class TheThumbnailsVersion(unittest.TestCase):
    def test_a_version_within_the_tolerance_is_the_files(self):
        thumb = thumbnails.Thumb(b"", thumbnails.OK, "etag", 1700000000.25)
        self.assertTrue(thumb.is_version(1700000000.25 + store_photos.MTIME_TOLERANCE / 2))
        self.assertFalse(thumb.is_version(1700000000.25 + 1))

    def test_a_file_that_is_not_there_has_no_version(self):
        self.assertFalse(thumbnails.Thumb(b"", thumbnails.OK, None, None).is_version(0.0))


if __name__ == "__main__":
    unittest.main()
