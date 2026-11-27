"""A write that records part of a file never stamps a row nobody read from its file.

Suggest makes a row for a photo the index never read (store.photos.ensure_row): the
path, and nothing else -- mtime and size empty, so the folder scan and the refresh read
the file. A tag write on such a row recorded the keyword fields as the row's whole raw
metadata and stamped it with the file's mtime and size: the row claimed to match its
file, and was trusted from then on, with no Date Taken (docs/findings.md, "Photos of a
folder showed without their Date Taken"). 107 rows of one folder held only the three
keyword fields.

Each writer here runs on a real photo carrying a Date Taken, through the service the
page calls, with real ExifTool. After it, the row is either still unread (mtime empty),
or holds what its file holds, Date Taken and all. A row read from its file is still
stamped, so the scan need not read it again. A row read from its file that the file has moved on from since (a date set in
another program) is stamped by none of the partial writers, and by the two that read the file back whole it is stamped with
all the file holds (#249).

Lifted: test_partial_writes_stamp_only_a_current_row (#247 #249). Mutation: store/photos.py `follow_fields`, the
`if stat is not None and _describes_before(row_mtime, row_size, before):` that adds mtime and size -> `if stat is not None:`;
every writer of the moved-on arm fails (apply all, add to selected, rename a tag, rotate, shift date), and the unread arm with it.
The old file's tolerance test of store.photos.describes is test_a_row_describes_its_file_one_way's.

A read that fails never empties a row (AReadThatFailsKeepsTheRow): an index run while a photo is open in another program
that shares nothing (an editor; the server patching it in place) leaves that photo unread, its row as it was, and reads it
at the next index; a photo whose file ExifTool refuses is stamped, so it is not read again every run, and keeps the
keywords, captions and metadata its row held, its people and keyword rows built from them (#3436).
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image  # noqa: E402

import own_home  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import exiftool_session, lock_owners, metadata  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
import page_writes  # noqa: E402
from tagpup.services import tagging  # noqa: E402
from tagpup.store import db, schema  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from face_rows import configured_model, people_of  # noqa: E402
import photo_rows  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
requires_exiftool = unittest.skipUnless(EXIFTOOL, "ExifTool not installed")

TAKEN = "2019:06:15 10:30:00"
ELSEWHERE = "2011:05:06 07:08:09"


def make_photo(folder, name, keywords=()):
    """A small JPEG carrying a Date Taken, a camera and `keywords`, written by ExifTool."""
    path = os.path.join(folder, name)
    Image.new("RGB", (8, 6), (90, 120, 150)).save(path)
    fields = {"EXIF:DateTimeOriginal": TAKEN, "EXIF:Make": "Quayside Optics"}
    if keywords:
        fields.update({"XMP:Subject": list(keywords), "IPTC:Keywords": list(keywords)})
    with exiftool_session.ExifToolSession(executable=EXIFTOOL) as et:
        et.set_tags([path], tags=fields, params=["-overwrite_original"])
    return path


class Photos(unittest.TestCase):
    """A library in a home of its own, a folder of real photos beside it."""

    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library("harbour.db")
        schema.ensure(self.db_path)
        self.library = Library(self.db_path)
        self.folder = os.path.join(self.home.root, "Harbour Day")
        os.makedirs(self.folder)

    def suggested(self, path):
        """The row Suggest makes for a photo the index never read, in a folder added."""
        conn = db.connect(self.db_path)
        try:
            store_photos.ensure_row(conn, path, admit=True)
            conn.commit()
        finally:
            conn.close()

    def indexed(self, path):
        stat = os.stat(path)
        raw = {"SourceFile": path, "EXIF:DateTimeOriginal": TAKEN, "DateTimeOriginal": TAKEN,
               "EXIF:Make": "Quayside Optics", "Make": "Quayside Optics"}
        conn = db.connect(self.db_path)
        try:
            conn.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                         " VALUES (?, ?, ?, '[]', '[]', ?)", (path, stat.st_mtime, stat.st_size, json.dumps(raw)))
            store_photos.date_photos(conn)
            conn.commit()
        finally:
            conn.close()

    def row(self, path):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            mtime, size, raw, taken = conn.execute(
                "SELECT mtime, size, raw_metadata, taken FROM photos WHERE path = ?", (path,)).fetchone()
        finally:
            conn.close()
        return mtime, size, json.loads(raw or "{}"), taken


@requires_exiftool
class PartialWritesOnAnUnreadRow(Photos):

    def assertUnreadOrWhole(self, path):
        mtime, size, raw, taken = self.row(path)
        if mtime is None:
            self.assertIsNone(size)
            return
        # Stamped: then it must hold what the file holds, its Date Taken first.
        stat = os.stat(path)
        self.assertEqual((mtime, size), (stat.st_mtime, stat.st_size))
        self.assertEqual(raw.get("EXIF:DateTimeOriginal"), TAKEN,
                         "the row claims to match its file but holds only %s" % sorted(raw))
        self.assertIsNotNone(taken)

    def assertUnread(self, path):
        mtime, size, raw, _taken = self.row(path)
        self.assertEqual((mtime, size), (None, None),
                         "a partial write stamped a row never read from its file (it holds %s)" % sorted(raw))

    def test_apply_all(self):
        path = make_photo(self.folder, "a.jpg")
        self.suggested(path)
        result = tagging.add_tags(self.library, {path: ["Activity/Sailing"]}, EXIFTOOL)
        self.assertEqual((result.ok, result.changed), (True, 1))
        self.assertUnread(path)

    def test_add_to_all_selected(self):
        path = make_photo(self.folder, "b.jpg")
        self.suggested(path)
        result = tagging.change_tags(self.library, [path], ["Activity/Sailing"], [], EXIFTOOL)
        self.assertEqual((result.ok, result.changed), (True, 1))
        self.assertUnread(path)

    def test_renaming_a_tag(self):
        path = make_photo(self.folder, "c.jpg", keywords=["Activity/Sailing"])
        self.suggested(path)
        result = tagging.replace_tag(self.library, [path], "Activity/Sailing", "Activity/Rowing", EXIFTOOL)
        self.assertEqual((result.ok, result.changed), (True, 1))
        self.assertUnread(path)

    def test_a_photo_not_carrying_the_renamed_tag(self):
        """Its row is told what the file holds, and nothing was written to the file."""
        path = make_photo(self.folder, "d.jpg", keywords=["Activity/Rowing"])
        self.suggested(path)
        tagging.replace_tag(self.library, [path], "Activity/Sailing", "Activity/Kayaking", EXIFTOOL)
        self.assertUnread(path)

    def test_rotating(self):
        path = make_photo(self.folder, "g.jpg")
        self.suggested(path)
        result = photo_actions.rotate(self.library, path, "left", EXIFTOOL)
        self.assertTrue(result.ok)
        self.assertUnread(path)

    def test_saving_one_photo(self):
        """The save reads the file back whole, so it may record it all."""
        path = make_photo(self.folder, "h.jpg")
        self.suggested(path)
        result = tagging.save_photo(self.library, path, "", ["Activity/Sailing"], None, EXIFTOOL, "",
                                    base=page_writes.base(self.library, path))
        self.assertTrue(result.ok)
        self.assertUnreadOrWhole(path)

    def test_shifting_date_taken(self):
        """The shift reads the files back whole too."""
        path = make_photo(self.folder, "i.jpg")
        self.suggested(path)
        result = photo_actions.shift_date_taken(self.library, [path], 0, EXIFTOOL)
        self.assertTrue(result.ok)
        self.assertUnreadOrWhole(path)


@requires_exiftool
class PartialWritesOnARowReadFromItsFile(Photos):
    """A row read from its file is stamped by the write, as before, or every photo
    tagged in bulk is read again on every scan."""

    def test_apply_all_stamps_a_row_that_was_read(self):
        path = make_photo(self.folder, "j.jpg")
        self.indexed(path)
        tagging.add_tags(self.library, {path: ["Activity/Sailing"]}, EXIFTOOL)
        mtime, size, raw, taken = self.row(path)
        stat = os.stat(path)
        self.assertEqual((mtime, size), (stat.st_mtime, stat.st_size))
        self.assertEqual((raw["EXIF:DateTimeOriginal"], raw["XMP:Subject"]), (TAKEN, "Activity/Sailing"))
        self.assertIsNotNone(taken)

    def test_rotating_stamps_a_row_that_was_read(self):
        path = make_photo(self.folder, "k.jpg")
        self.indexed(path)
        self.assertTrue(photo_actions.rotate(self.library, path, "left", EXIFTOOL).ok)
        stat = os.stat(path)
        self.assertEqual(self.row(path)[:2], (stat.st_mtime, stat.st_size))


@requires_exiftool
class PartialWritesOnARowTheFileHasMovedOnFrom(Photos):
    """A row read from its file, and the file then changed by another program, is not told it matches: the scan reads it."""

    def moved_on(self, name):
        path = make_photo(self.folder, name, keywords=["Activity/Sailing"])
        self.indexed(path)
        read_at = self.row(path)[:2]
        with exiftool_session.ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([path], tags={"EXIF:DateTimeOriginal": ELSEWHERE}, params=["-overwrite_original"])
        # The other program's save is a moment after the row was made (the scan's tolerance is 0.1 s).
        stat = os.stat(path)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5 * 10 ** 9))
        return path, read_at

    def test_a_partial_write_leaves_it_for_the_scan(self):
        writers = {
            "apply all": lambda path: tagging.add_tags(self.library, {path: ["Activity/Rowing"]}, EXIFTOOL),
            "add to selected": lambda path: tagging.change_tags(self.library, [path], ["Activity/Rowing"], [], EXIFTOOL),
            "rename a tag": lambda path: tagging.replace_tag(
                self.library, [path], "Activity/Sailing", "Activity/Kayaking", EXIFTOOL),
            "rotate": lambda path: photo_actions.rotate(self.library, path, "left", EXIFTOOL),
            "shift date": lambda path: photo_actions.shift_date_taken(self.library, [path], 60, EXIFTOOL),
        }
        for label, write in writers.items():
            with self.subTest(label):
                path, read_at = self.moved_on(label.replace(" ", "_") + ".jpg")
                self.assertEqual(write(path).changed, 1)
                self.assertEqual(self.row(path)[:2], read_at, "a partial write stamped a row that did not describe its file")

    def test_saving_one_photo_reads_the_file_back_whole_and_records_all_of_it(self):
        path, _read_at = self.moved_on("save.jpg")
        self.assertTrue(tagging.save_photo(self.library, path, "", ["Activity/Rowing"], None, EXIFTOOL, "",
                                           base=page_writes.base(self.library, path)).ok)
        mtime, size, raw, _taken = self.row(path)
        stat = os.stat(path)
        self.assertEqual((mtime, size), (stat.st_mtime, stat.st_size))
        self.assertEqual(raw["EXIF:DateTimeOriginal"], ELSEWHERE)


@requires_exiftool
class AReadThatFailsKeepsTheRow(Photos):
    """The index runs while one photo is open in a program that shares nothing and another has become a file ExifTool
    refuses. Neither row loses its keywords, title or metadata."""

    KEYWORDS = ["People/Wren Halloway", "Trips/Lake"]
    TITLE = "Rowing at dawn"

    def index(self, photo_paths):
        """An index of `photo_paths` as tagpup_cli's: the whole read with the bakes, a record with no stamp left unread
        (the CLI's `left_unread`), the rest recorded by the index's one writer (PhotoIndex.build_or_update), no vector.
        {path: why it was left unread, None for one recorded}."""
        records = photo_rows.read_whole(EXIFTOOL, photo_paths, people=store_taxonomy.people_vocabulary(self.db_path),
                                        bakes=True)
        recorded = [record for record in records if record["mtime"] is not None]
        index = PhotoIndex(self.db_path, configured_model())
        index.load()
        try:
            index.build_or_update([None] * len(recorded), recorded)
        finally:
            index.close()
        return {record["path"]: record.get("unread") for record in records}

    def kept(self, path):
        """(tags, captions, the row's Date Taken field, its people, its keyword rows, its stamp) of the row at `path`."""
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            photo_id, tags, captions, raw, mtime, size = conn.execute(
                "SELECT id, tags, captions, raw_metadata, mtime, size FROM photos WHERE path = ?", (path,)).fetchone()
            keyword_rows = conn.execute("SELECT COUNT(*) FROM photo_tags WHERE photo_id = ?", (photo_id,)).fetchone()[0]
            return (sorted(json.loads(tags)), json.loads(captions), json.loads(raw).get("EXIF:DateTimeOriginal"),
                    people_of(conn, path), keyword_rows, (mtime, size))
        finally:
            conn.close()

    def photo(self, name):
        path = make_photo(self.folder, name, self.KEYWORDS)
        with exiftool_session.ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([path], tags={"XMP:Title": self.TITLE}, params=["-overwrite_original"])
        return path

    def test_a_held_file_and_a_refused_file_each_keep_their_rows_fields(self):
        held, refused = self.photo("held.jpg"), self.photo("refused.jpg")
        conn = db.connect(self.db_path)
        try:
            photo_rows.added(conn, held)   # the index adds the folder it is given first
            conn.commit()
        finally:
            conn.close()
        self.index([held, refused])
        whole = (sorted(self.KEYWORDS), [self.TITLE], TAKEN, ["Wren Halloway"], 2)
        self.assertEqual(whole, self.kept(held)[:5], "fixture: the first index records the photo whole")
        first_stamp = self.kept(held)[5]

        # Another program adds a keyword to one photo and keeps it open, sharing nothing; the other is overwritten with
        # bytes that are no picture.
        with exiftool_session.ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([held], tags={"XMP:Subject": self.KEYWORDS + ["Trips/Harbour"]}, params=["-overwrite_original"])
        with open(refused, "wb") as handle:
            handle.write(b"Not a picture: a page of notes saved under a photo's name.\n")
        with lock_owners.opened(held, lock_owners.NO_SHARING):
            unread = self.index([held, refused])
        self.assertEqual(whole + (first_stamp,), self.kept(held), "a held photo's row is left as it was, unstamped")
        stat = os.stat(refused)
        self.assertEqual(whole + ((stat.st_mtime, stat.st_size),), self.kept(refused),
                         "a refused photo is stamped and keeps its row's fields")
        self.assertEqual({held: metadata.HELD, refused: None}, unread)

        # Closed, the held photo is read at the next index, its new keyword and all.
        self.index([held])
        stat = os.stat(held)
        self.assertEqual((sorted(self.KEYWORDS + ["Trips/Harbour"]), [self.TITLE], TAKEN, ["Wren Halloway"], 3,
                          (stat.st_mtime, stat.st_size)), self.kept(held))


if __name__ == "__main__":
    unittest.main()
