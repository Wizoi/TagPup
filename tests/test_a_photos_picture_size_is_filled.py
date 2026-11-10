"""The photos indexed before migration 39 get their picture size from their header, once (#2738, #2761, #2804; F3): the
one-off scripts/fill_picture_sizes.py over tagpup.services.picture_sizes, dry run first, --apply to write.

The rows are made as the index made them before 39 (store.photos.record_indexed with no size: NULL), the pictures by
Pillow in a home of the test's own. A turned photo (Orientation 6) records its STORED pixels, the frame of its face boxes,
as the index does (images.picture_size). A header that gives no size, a file changed since its row was read, a share
asleep: the row keeps NULL and is counted. Stopped part-way (Ctrl+C, or a second one at once), the chunks written stand
and a run again goes on; a second whole run writes nothing; a row an undo put back with no size is filled like any
other. The dry run writes nothing, and --apply is refused while the app runs. Fictional names.
"""
import hashlib
import io
import os
import sys
import threading
import unittest
from contextlib import closing
from unittest import mock

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import fill_picture_sizes  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.files import images, shares  # noqa: E402
from tagpup.services import picture_sizes  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db, journal, photos  # noqa: E402

ORIENTATION = 274
#: The photos each test starts from, by id: the size the index records (images.picture_size), or None for a file that
#: gives none. A JPEG's is its stored pixels; Pillow opens a turned TIFF turned, so its header's size is the turned one,
#: which is its face boxes' frame too (images.shown_shape; #2804).
PICTURES = (("turned.jpg", (300, 200)), ("turned.tif", (200, 300)), ("wide.jpg", (640, 480)),
            ("broken.jpg", None), ("empty.jpg", None))


def picture(path, size, turned=False):
    """A picture at `path` the index records as `size`: a turned TIFF is stored the other way round (PICTURES)."""
    tiff = path.endswith(".tif")
    image = Image.new("RGB", size[::-1] if tiff and turned else size, (90, 140, 200))
    if tiff:
        image.save(path, tiffinfo={ORIENTATION: 6} if turned else {})
        return
    exif = Image.Exif()
    if turned:
        exif[ORIENTATION] = 6
    image.save(path, exif=exif.tobytes())


class PhotosIndexedBefore39(unittest.TestCase):
    """A library of five photos the index read before migration 39, a home of its own."""

    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library("pictures.db")
        index = PhotoIndex(db_path=self.db_path)
        index.load()
        index.close()
        self.folder = os.path.join(self.home.root, "Pictures", "Coast")
        os.makedirs(self.folder)
        self.ids = {}
        for name, size in PICTURES:
            path = os.path.join(self.folder, name)
            if size:
                picture(path, size, turned=name.startswith("turned"))
            else:
                with open(path, "wb") as handle:
                    handle.write(b"" if name == "empty.jpg" else b"no picture here")
            self.ids[name] = self.old_row(path)
        shares.forget()
        self.addCleanup(shares.forget)

    def old_row(self, path):
        """The row the index made of `path` before migration 39: everything it read, and no picture size."""
        def record(conn):
            photos.record_indexed(conn, path, photo_rows.as_read(path, {}))
            return photos.ensure_row(conn, path)
        return db.write_with_connection(self.db_path, record)

    def sizes(self):
        with closing(db.connect(db.readonly_uri(self.db_path), uri=True)) as conn:
            rows = conn.execute("SELECT path, width, height FROM photos").fetchall()
        return {os.path.basename(path): None if width is None else (width, height) for path, width, height in rows}

    def expected(self, *names):
        return {name: size if name in names else None for name, size in PICTURES}


class APictureSizeFill(PhotosIndexedBefore39):
    def fill(self, **kwargs):
        return picture_sizes.fill(self.db_path, **kwargs)

    def test_each_photo_gets_its_headers_stored_size_and_one_that_gives_none_keeps_null(self):
        filled = self.fill()
        self.assertEqual((3, 5), (filled.wrote, filled.read))
        self.assertEqual({"sized": 3, "unreadable": 2, "away": 0, "gone": 0, "stale": 0}, dict(filled.counts))
        self.assertEqual(self.expected("turned.jpg", "turned.tif", "wide.jpg"), self.sizes())
        turned = os.path.join(self.folder, "turned.jpg")
        self.assertEqual(images.picture_size(turned), self.sizes()["turned.jpg"], "not what the index records")

    def test_a_second_run_writes_nothing_and_reads_only_what_gave_no_size(self):
        self.fill()
        again = self.fill()
        self.assertEqual((0, 2, 2), (again.wrote, again.read, again.counts["unreadable"]))

    def test_stopped_part_way_the_chunks_written_stand_and_a_run_again_finishes(self):
        heard = []
        with mock.patch.object(picture_sizes, "CHUNK", 1):
            stopped = self.fill(stop=lambda: len(heard) >= 2, heard=heard.append)
        self.assertEqual((True, 2, 2), (stopped.stopped, stopped.wrote, stopped.read))
        self.assertEqual(self.expected("turned.jpg", "turned.tif"), self.sizes())
        rest = self.fill()
        self.assertEqual((1, 3), (rest.wrote, rest.read))
        self.assertEqual(self.expected("turned.jpg", "turned.tif", "wide.jpg"), self.sizes())

    def test_a_second_ctrl_c_stops_at_once_and_keeps_the_chunks_written(self):
        looked, real = [], picture_sizes.look

        def look(*args):
            looked.append(args)
            if len(looked) == 3:
                raise KeyboardInterrupt
            return real(*args)
        with mock.patch.object(picture_sizes, "CHUNK", 1), mock.patch.object(picture_sizes, "look", look):
            with self.assertRaises(KeyboardInterrupt):
                self.fill()
        self.assertEqual(self.expected("turned.jpg", "turned.tif"), self.sizes())
        self.assertEqual(1, self.fill().wrote)

    def test_a_file_changed_since_its_row_was_read_is_stale_and_keeps_null(self):
        with open(os.path.join(self.folder, "wide.jpg"), "ab") as handle:
            handle.write(b"written by another program")
        filled = self.fill()
        self.assertEqual((2, 1), (filled.wrote, filled.counts["stale"]))
        self.assertEqual(self.expected("turned.jpg", "turned.tif"), self.sizes())

    def test_a_file_changed_between_its_look_and_its_read_is_stale(self):
        real = shares.stamp_of

        def stamp_then_change(path):
            stamp = real(path)
            if path.endswith("wide.jpg"):
                with open(path, "ab") as handle:
                    handle.write(b"x")
            return stamp
        with mock.patch.object(shares, "stamp_of", stamp_then_change):
            filled = self.fill()
        self.assertEqual((2, 1), (filled.wrote, filled.counts["stale"]))

    def test_a_share_asleep_is_counted_away_and_a_run_later_fills_its_photos(self):
        share = os.path.join(self.home.root, "Share")
        os.makedirs(share)
        for name in ("pier.jpg", "dunes.jpg"):
            path = os.path.join(share, name)
            picture(path, (120, 80))
            self.old_row(path)
        asleep, under, real_stat = threading.Event(), os.path.normcase(share), os.stat
        self.addCleanup(asleep.set)

        def stat(path, *args, **kwargs):
            if os.path.normcase(str(path)).startswith(under):
                asleep.wait(2)
                raise FileNotFoundError(2, "not found")
            return real_stat(path, *args, **kwargs)
        with mock.patch.object(os, "stat", stat), \
                mock.patch.object(shares, "on_a_network_drive", lambda path: os.path.normcase(str(path)).startswith(under)), \
                mock.patch.object(shares, "SHARE_WAIT", 0.05), mock.patch.object(shares, "AWAY_SECONDS", 0.2):
            filled = self.fill()
        self.assertEqual((3, 2, 1), (filled.wrote, filled.counts["away"], len(filled.away)))
        self.assertEqual((None, None), (self.sizes()["pier.jpg"], self.sizes()["dunes.jpg"]))
        asleep.set()
        shares.forget()
        self.assertEqual(2, self.fill().wrote)
        self.assertEqual(((120, 80), (120, 80)), (self.sizes()["pier.jpg"], self.sizes()["dunes.jpg"]))

    def test_a_photo_whose_file_is_gone_is_counted_gone_and_keeps_null(self):
        os.remove(os.path.join(self.folder, "wide.jpg"))
        filled = self.fill()
        self.assertEqual((2, 1), (filled.wrote, filled.counts["gone"]))
        self.assertIsNone(self.sizes()["wide.jpg"])

    def test_limit_looks_at_that_many_photos(self):
        filled = self.fill(limit=2)
        self.assertEqual((2, 2), (filled.read, filled.wrote))
        self.assertEqual(self.expected("turned.jpg", "turned.tif"), self.sizes())

    def test_a_row_an_undo_put_back_with_no_size_is_filled(self):
        """A photo deleted before the fill ran, its delete undone after: the undo puts its row back as it was, NULL."""
        deletion = journal.apply(self.db_path, "delete", [journal.delete("photos", (self.ids["wide.jpg"],), {})])
        self.assertEqual(2, self.fill().wrote)
        journal.undo(self.db_path, deletion.change_id)
        self.assertIsNone(self.sizes()["wide.jpg"])
        self.assertEqual(1, self.fill().wrote)
        self.assertEqual((640, 480), self.sizes()["wide.jpg"])


class TheScript(PhotosIndexedBefore39):
    """scripts/fill_picture_sizes.py: what the owner runs, and reads."""

    def run_script(self, *args):
        said = io.StringIO()
        code = fill_picture_sizes.run(self.db_path, "--apply" in args, out=lambda line: said.write(line + "\n"),
                                      **({"limit": int(args[-1])} if "--limit" in args else {}))
        return code, said.getvalue()

    def library_bytes(self):
        digest = hashlib.sha256()
        for suffix in ("", "-wal", "-journal"):   # a read-only open may leave an empty -wal: no write
            if os.path.exists(self.db_path + suffix):
                with open(self.db_path + suffix, "rb") as handle:
                    digest.update(handle.read())
        return digest.hexdigest()

    def test_a_dry_run_says_the_counts_and_the_time_and_writes_nothing(self):
        clip = os.path.join(self.folder, "harbour.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"a clip")
        self.old_row(clip)
        before = self.library_bytes()
        code, said = self.run_script()
        self.assertEqual(0, code)
        self.assertIn("From the index: to fill 5, already filled 0, clips 1.", said)
        self.assertIn("sized 3, unreadable 2, away 0, gone 0, stale 0", said)
        self.assertIn("--apply on 5 photo(s) takes about", said)
        self.assertEqual(before, self.library_bytes())
        self.assertEqual(self.expected(), {name: size for name, size in self.sizes().items() if name != "harbour.mp4"})

    def test_apply_is_refused_while_the_app_runs(self):
        with mock.patch.object(fill_picture_sizes.launcher, "running", return_value=[{"pid": 4242}]):
            code, said = self.run_script("--apply")
        self.assertEqual(1, code)
        self.assertIn("Not run: TagPup is running (1 app server(s))", said)
        self.assertEqual(self.expected(), self.sizes())
        self.assertFalse(os.path.exists(os.path.join(self.home.data, "backups")), "a snapshot was taken")

    def test_apply_takes_a_snapshot_once_a_day_and_says_what_it_wrote(self):
        code, said = self.run_script("--apply", "--limit", "1")
        self.assertEqual(0, code)
        self.assertIn("Snapshot: taken (daily", said)
        self.assertIn("Wrote 1 row(s): their picture size recorded. Read 1 photo(s)", said)
        code, said = self.run_script("--apply")
        self.assertIn("Snapshot: today's stands", said)
        self.assertIn("Wrote 2 row(s)", said)
        self.assertEqual(self.expected("turned.jpg", "turned.tif", "wide.jpg"), self.sizes())


if __name__ == "__main__":
    unittest.main()
