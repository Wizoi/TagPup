"""Nothing is written into a photo whose picture does not decode, nor into one that may be
an incomplete copy (docs/findings.md, #407).

The indexer wrote an XMP DocumentID into every photo lacking one as it read the batch's
metadata, before any picture was decoded. For 13 photos whose files were damaged -- cut
short, or zero bytes -- the write succeeded, the decode then failed, no row was
recorded, and the file's modified time had changed: the folder watcher saw it, sync
queued the indexer for it again, and each run loaded the photo index and CLIP to fail on
it again. The owner saw the modified dates of damaged photos change and suspected TagPup
of the damage. An interrupted copy also left files of their full size that are zero
bytes after some point; some still decode, grey below a line: those are indexed, and
left as they are.

Real ExifTool and real Pillow, on damaged JPEGs made here (tests/damaged_photos); only
the CLIP model is stood in for, after the picture has been decoded.
"""
import hashlib
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
from tagpup.files import images  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.files.identity import read_document_id  # noqa: E402
from tagpup.files.metadata import IdentityWriter, MetadataExtractor  # noqa: E402
from tagpup.ml.clip import output_dim  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()

#: When every file was written, before the test: a write now would change it.
THEN = 1_700_000_000


def fingerprint(path):
    """What the file is: its bytes, and when it was last written."""
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest(), os.stat(path).st_mtime_ns


def identity_in_file(path):
    with ExifToolSession(executable=EXIFTOOL) as et:
        return read_document_id(et.get_tags([path], tags=["XMP-xmpMM:DocumentID"])[0])


def vector():
    return [0.1] * (output_dim(library_settings.DEFAULTS["model.name"]) or 512)


class Decoding(unittest.TestCase):
    """images.opened tells a damaged picture from a file it could not read."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="damaged_")
        self.folder = os.path.join(self.home.root, "Harbour")

    def test_each_kind_of_damage_is_named(self):
        for kind, path in damaged_photos.unreadable(self.folder).items():
            with self.assertRaises(images.Unreadable, msg=kind) as raised:
                images.opened(path, upright=True)
            self.assertEqual(kind, raised.exception.kind)
            self.assertNotIn(self.folder, raised.exception.detail, "the reason carries the path")

    def test_a_picture_that_stops_in_zeros_is_zero_filled(self):
        data = b"JFIF" * 100 + bytes(images.ZERO_TAIL)
        found = images.damage(data, OSError("image file is truncated (3 bytes not processed)"))
        self.assertEqual(("zero-filled", images.ZERO_TAIL), (found.kind, found.zero_tail))

    def test_a_copy_zero_after_half_way_decodes_and_says_so(self):
        path = damaged_photos.second_half_zeros(os.path.join(self.folder, "copy stopped.jpg"))
        zeros = images.opened(path, upright=True).info[images.ZERO_TAIL_INFO]
        self.assertGreaterEqual(zeros, images.ZERO_TAIL)
        self.assertEqual(zeros, images.zero_tail_of(path), "the file's end alone says the same")

    def test_a_whole_photo_decodes_and_ends_in_no_zeros(self):
        path = os.path.join(self.folder, "whole.jpg")
        damaged_photos.whole_jpeg(path)
        picture = images.opened(path, upright=False)
        self.assertEqual((160, 120), picture.size)
        self.assertEqual(0, picture.info[images.ZERO_TAIL_INFO])

    def test_a_few_zeros_after_the_picture_are_padding(self):
        # 18 photos under photo_index's folders end in zero bytes, 16 at most.
        path = os.path.join(self.folder, "padded.jpg")
        body = damaged_photos.whole_jpeg(path)
        with open(path, "wb") as handle:
            handle.write(body + bytes(16))
        self.assertEqual(16, images.opened(path, upright=False).info[images.ZERO_TAIL_INFO])
        self.assertEqual(0, images.zero_tail_of(path))

    def test_a_missing_file_is_not_called_damaged(self):
        # A network share gone for a moment raises what the system raised: no reason to
        # remember a photo as damaged.
        with self.assertRaises(OSError) as raised:
            images.opened(os.path.join(self.folder, "gone.jpg"), upright=True)
        self.assertNotIsInstance(raised.exception, images.Unreadable)


class FakeExifTool:
    def __init__(self):
        self.writes = []

    def set_tags(self, paths, tags=None, params=None):
        self.writes.append(list(paths))


class TheIdentityWriter(unittest.TestCase):
    def writer(self, et):
        writer = IdentityWriter("exiftool")
        writer._session = lambda: et
        return writer

    def test_it_writes_only_into_a_photo_that_decoded(self):
        et = FakeExifTool()
        record = {"path": "D:/Harbour/jetty.jpg", "raw_metadata": {}, "document_id": None}
        self.assertIsNone(self.writer(et).give(record, decoded=False))
        self.assertEqual([], et.writes)
        self.assertTrue(self.writer(et).give(record, decoded=True))
        self.assertEqual([["D:/Harbour/jetty.jpg"]], et.writes)

    def test_nor_into_one_exiftool_could_not_read(self):
        et = FakeExifTool()
        record = MetadataExtractor._empty("D:/Harbour/jetty.jpg", RuntimeError("Entire file is binary zeros"))
        self.assertIsNone(self.writer(et).give(record, decoded=True))
        self.assertEqual([], et.writes)

    def test_an_error_in_the_read_is_carried_on_the_record(self):
        record = MetadataExtractor()._structure("D:/Harbour/jetty.jpg",
                                                {"SourceFile": "D:/Harbour/jetty.jpg",
                                                 "ExifTool:Error": "File format error"}, None)
        self.assertEqual("File format error", record["read_error"])

    def test_reading_writes_nothing(self):
        # Reading a photo never writes to it: only IdentityWriter does, and only the
        # indexer makes one.
        et = FakeExifTool()
        et.get_tags = lambda batch, tags=None: [{"SourceFile": path} for path in batch]
        session = mock.MagicMock()
        session.return_value.__enter__.return_value = et
        with mock.patch("tagpup.files.metadata.ExifToolSession", session):
            MetadataExtractor("exiftool").batch_read(["D:/Harbour/jetty.jpg"])
        self.assertEqual([], et.writes)


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class TheIndexer(unittest.TestCase):
    """`tagpup_cli.py index` on a folder holding damaged photos beside a whole one.

    A file of nothing but zeros has a folder of its own: ExifTool refuses to read it,
    which fails the whole batch's read, and the batch read again file by file was never
    given identities -- so beside it, the damaged photos were spared by chance."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="damaged_index_")
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.folder = os.path.join(self.home.root, "Harbour")
        self.cut = damaged_photos.truncated(os.path.join(self.folder, "cut short.jpg"))
        self.half = damaged_photos.second_half_zeros(os.path.join(self.folder, "copy stopped.jpg"))
        self.whole = os.path.join(self.folder, "whole.jpg")
        damaged_photos.whole_jpeg(self.whole)
        self.zeros = damaged_photos.all_zeros(os.path.join(self.home.root, "Jetty", "all zeros.jpg"))
        for path in (self.cut, self.half, self.whole, self.zeros):
            os.utime(path, (THEN, THEN))
        self.before = {path: fingerprint(path) for path in (self.cut, self.half, self.zeros)}

    def index(self, folder=None):
        with mock.patch("tagpup.ml.clip.ClipModel._init_model"), \
                mock.patch("tagpup.ml.clip.ClipModel.embed_picture", return_value=vector()) as embed:
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", folder or self.folder,
                                                         "--skip-faces"])
        self.assertEqual(0, result.exit_code, result.output)
        return result, embed

    def rows(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return {os.path.basename(path): document_id
                    for path, document_id in conn.execute("SELECT path, document_id FROM photos")}
        finally:
            conn.close()

    def test_a_photo_that_does_not_decode_is_not_written_to_nor_indexed(self):
        result, embed = self.index()
        self.assertEqual(self.before[self.cut], fingerprint(self.cut), "the truncated photo was written to")
        self.assertNotIn("cut short.jpg", self.rows())
        self.assertIn("1 photo(s) could not be read", result.output)

    def test_a_possibly_incomplete_copy_is_indexed_and_not_written_to(self):
        result, embed = self.index()
        self.assertEqual(self.before[self.half], fingerprint(self.half), "the incomplete copy was written to")
        self.assertIn("copy stopped.jpg", self.rows(), "a photo that decodes was not indexed")
        self.assertIsNone(self.rows()["copy stopped.jpg"])
        self.assertIn("1 photo(s) may be incomplete copies", result.output)
        self.assertEqual(2, embed.call_count)

    def test_the_whole_photo_beside_them_is_given_its_identity(self):
        self.index()
        minted = identity_in_file(self.whole)
        self.assertTrue(minted and minted.startswith("xmp.did:"))
        self.assertEqual(minted, self.rows()["whole.jpg"])

    def test_a_file_of_zeros_is_not_written_to(self):
        result, embed = self.index(os.path.dirname(self.zeros))
        self.assertEqual(self.before[self.zeros], fingerprint(self.zeros))
        self.assertEqual(0, embed.call_count)
        self.assertEqual({}, self.rows())
        self.assertIn("1 photo(s) could not be read", result.output)


if __name__ == "__main__":
    unittest.main()
