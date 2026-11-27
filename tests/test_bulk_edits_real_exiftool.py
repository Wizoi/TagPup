"""A bulk edit through the real ExifTool and real JPEGs: tags added and taken off (tests/test_bulk_edits.py stands ExifTool in for everything else).

The route is Flask's test client; the edit is written when the request returns. Photos are small JPEGs written with
Pillow and ExifTool, rows as the indexer records them. Fictional names only.

A bulk write's connections (ConnectionsOfABulkWrite, docs/plans/bulk-write-connections.md): its writes share one
connection and its reads another, on its thread, whatever one photo does -- fails, crashes the process, or a follower of
its row raises.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exiftool_fields  # noqa: E402
import corpus  # noqa: E402
import own_home  # noqa: E402
import web_client  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import batches, bulk_edit, file_changes  # noqa: E402
from tagpup.store import db  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
TAKEN = "2024:07:04 10:00:00"
ALL = {"source": {"kind": "all"}}


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class RealFiles(unittest.TestCase):
    PHOTOS = 7

    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        patcher = mock.patch("tagpup.runtime.exiftool", return_value=EXIFTOOL)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.vl.tree("Trips/Coast", "Old/Stuff")
        self.paths = []
        for n in range(self.PHOTOS):
            path = make_jpeg(os.path.join(self.vl.pictures, "Coast", "p%d.jpg" % n), size=(32, 32))
            with ExifToolSession(executable=EXIFTOOL) as et:
                et.set_tags([path], tags={"EXIF:DateTimeOriginal": TAKEN, "XMP:Subject": ["Old/Stuff"]},
                            params=["-overwrite_original"])
            self.vl.photo("Coast", "p%d.jpg" % n, taken=TAKEN, tags=["Old/Stuff"], at=path)
            self.paths.append(path)

    def run_job(self, op, params, selection=ALL):
        reply = self.client.post("/library/api/library/bulk-edit", json={"op": op, "selection": selection, "params": params})
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()

    def read(self, path, *fields):
        with ExifToolSession(executable=EXIFTOOL) as et:
            return et.get_tags([path], tags=list(fields))[0]

    def test_tags_are_added_and_removed_in_the_files_and_the_rows(self):
        done = self.run_job("tags", {"add": ["Trips/Coast"], "remove": ["Old/Stuff"]})
        self.assertEqual((self.PHOTOS, 0), (done["changed"], done["error_count"]))
        for path in self.paths:
            held = self.read(path, "XMP:Subject")["XMP:Subject"]
            self.assertEqual(["Trips/Coast"], held if isinstance(held, list) else [held])
        for (tags,) in self.vl.rows("SELECT tags FROM photos"):
            self.assertEqual('["Trips/Coast"]', tags)
        again = self.run_job("tags", {"add": ["Trips/Coast"], "remove": ["Old/Stuff"]})
        self.assertEqual((0, self.PHOTOS), (again["changed"], again["unchanged"]))

    def test_a_file_that_is_not_a_picture_is_an_error_entry_and_the_others_are_written(self):
        with open(self.paths[3], "wb") as handle:
            handle.write(b"not a picture at all")
        done = self.run_job("tags", {"add": ["Trips/Coast"]})
        self.assertEqual((self.PHOTOS - 1, 1), (done["changed"], done["error_count"]))


def is_open(conn):
    """Is `conn` still open? A closed connection refuses even to say whether it is in a transaction."""
    try:
        return isinstance(conn.in_transaction, bool)
    except sqlite3.ProgrammingError:
        return False


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class ConnectionsOfABulkWrite(unittest.TestCase):
    """The owner tags 60 photos at once (three chunks): each JPEG is patched in place, which saves the patch's record,
    reads it back and records the row. That opened four connections a photo, 43% of a long write's time; the write now
    holds one for its writes and one for its reads (db.connections_held), and leaves none open however it ends."""
    PHOTOS = 60

    def setUp(self):
        batches.reopen()        # a test that drained a server in this process leaves batches stopped
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.library = self.vl.library
        patcher = mock.patch("tagpup.runtime.exiftool", return_value=EXIFTOOL)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.vl.tree("Trips/Coast", "Trips/Lakes", "Old/Stuff")
        self.paths, self.ids = [], []
        os.makedirs(os.path.join(self.vl.pictures, "Coast"))
        for n in range(self.PHOTOS):
            # One JPEG written by ExifTool, copied (corpus.jpeg); its row as the indexer records a read of it.
            path = corpus.jpeg(os.path.join(self.vl.pictures, "Coast", "p%02d.jpg" % n),
                               {"EXIF:DateTimeOriginal": TAKEN, "XMP:Subject": ["Old/Stuff"]}, EXIFTOOL)
            self.ids.append(self.vl.photo("Coast", "p%02d.jpg" % n, taken=TAKEN, tags=["Old/Stuff"], at=path))
            self.paths.append(path)

    # ---- what the write opened ----------------------------------------------------------------------

    def counting(self):
        """While it is entered, every connection opened (sqlite3.connect, which db.connect calls), in `self.opened` as
        (read-only?, connection), of the library's database only."""
        self.opened = []
        real = sqlite3.connect
        name = os.path.basename(self.library.path)

        def connect(target, *args, **kwargs):
            conn = real(target, *args, **kwargs)
            if name in str(target):
                self.opened.append(("mode=ro" in str(target), conn))
            return conn
        return mock.patch.object(sqlite3, "connect", side_effect=connect)

    def writers(self):
        return [conn for read_only, conn in self.opened if not read_only]

    def left_open(self):
        return [conn for _read_only, conn in self.opened if is_open(conn)]

    def tagged(self):
        with ExifToolSession(executable=EXIFTOOL) as et:
            found = et.get_tags(self.paths, tags=["XMP:Subject"])
        held = [each.get("XMP:Subject", []) for each in found]
        return sum(1 for each in held if "Trips/Coast" in (each if isinstance(each, list) else [each]))

    def edit(self, add=(), remove=()):
        steps = []
        with self.counting(), mock.patch.object(file_changes, "_reached", side_effect=steps.append):
            reply = self.client.post("/library/api/library/bulk-edit", json={
                "op": "tags", "selection": ALL, "params": {"add": list(add), "remove": list(remove)}})
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json(), steps.count(file_changes.PATCH_SAVED)

    # ---- the scenarios --------------------------------------------------------------------------------

    def test_sixty_photos_patched_in_place_open_fewer_connections_than_photos_and_leave_none_open(self):
        done, patched = self.edit(add=["Trips/Coast"])
        self.assertEqual((self.PHOTOS, 0), (done["changed"], done["error_count"]))
        self.assertEqual(self.PHOTOS, patched, "every photo was patched in place: the path that opened four a photo")
        self.assertLess(len(self.opened), self.PHOTOS,
                        "%d connections for %d photos: a connection (or four) a photo again" % (len(self.opened), self.PHOTOS))
        self.assertEqual([], self.left_open(), "the write left a connection open")
        self.assertEqual(self.PHOTOS, self.tagged())

    def test_a_photo_that_cannot_be_written_costs_no_connection_more(self):
        # Two edits of one kind (a tag added): one taken off also asks the faces, on connections of its own.
        self.edit(add=["Trips/Coast"])
        clean = len(self.writers())
        os.chmod(self.paths[29], 0o444)      # the 30th photo marked read-only, as Explorer's Properties marks one
        self.addCleanup(os.chmod, self.paths[29], 0o666)
        done, patched = self.edit(add=["Trips/Lakes"])
        self.assertEqual((self.PHOTOS - 1, 1), (done["changed"], done["error_count"]))
        self.assertEqual(self.PHOTOS - 1, patched)
        self.assertEqual(clean, len(self.writers()),
                         "a photo's failed write dropped the write's connection: the next photo opened another")
        self.assertEqual([], self.left_open())

    def crashed_at(self, step):
        """The process stops at `step` of the 30th photo (bulk_edit.run, as the route runs it): nothing it opened is left
        open before anything settles it, and the next settle finishes the change."""
        edit = bulk_edit.prepare(self.library, "tags", {"add": ["Trips/Coast"]})
        with self.counting(), mock.patch.object(file_changes, "_reached", side_effect=crash_at(step, nth=30)):
            with self.assertRaises(Crash):
                bulk_edit.run(self.library, edit, self.ids, exiftool_fields.opened(self, EXIFTOOL))
        self.assertEqual([], self.left_open(), "a crash left this thread holding a connection to the library")
        self.assertEqual(0, batches.running_here())
        self.assertEqual(1, file_changes.settle(self.library, exiftool_fields.opened(self, EXIFTOOL)))
        self.assertEqual(self.PHOTOS, self.tagged())
        self.assertEqual([("applied",)], self.vl.rows("SELECT status FROM changes WHERE operation LIKE 'bulk tags%'"))

    def test_a_crash_once_the_30th_file_is_written_leaves_no_connection_and_is_settled(self):
        self.crashed_at("file written")

    def test_a_crash_once_the_30th_file_is_recorded_leaves_no_connection_and_is_settled(self):
        self.crashed_at("file recorded")

    def follower_raises(self, error):
        """The 30th photo's row is recorded with a follower that writes a row and then raises `error` (as a rotation's
        boxes or a face assignment could): returns (the connection of that write, the connection of the write after it)."""
        db.write_with_connection(self.library.path, lambda conn: conn.execute("CREATE TABLE marker (n INTEGER)"))
        real_followers, real_write = file_changes._followers, db.write_with_connection
        done, used, failed = [], [], {}

        def marker(conn):
            conn.execute("INSERT INTO marker VALUES (30)")
            failed["at"] = len(used)
            raise error

        def followers(row, state):
            found = real_followers(row, state)
            if state == "done":
                done.append(row.id)
                if len(done) == 30:
                    found.append(marker)
            return found

        def write(target, operation, label="database write"):
            def recorded(conn):
                used.append(conn)
                return operation(conn)
            return real_write(target, recorded, label=label)

        edit = bulk_edit.prepare(self.library, "tags", {"add": ["Trips/Coast"]})
        with self.counting(), mock.patch.object(file_changes, "_followers", side_effect=followers), \
                mock.patch.object(db, "write_with_connection", side_effect=write):
            with self.assertRaises(type(error)):
                bulk_edit.run(self.library, edit, self.ids, exiftool_fields.opened(self, EXIFTOOL))
        self.assertEqual([(0,)], self.vl.rows("SELECT COUNT(*) FROM marker"), "the failed write's row was committed")
        self.assertGreater(len(used), failed["at"], "no write followed the failed one")
        self.assertEqual([], self.left_open())
        return used[failed["at"] - 1], used[failed["at"]]

    def test_a_follower_that_raises_its_own_error_rolls_back_and_the_next_write_keeps_the_connection(self):
        failed, after = self.follower_raises(ValueError("a follower of the row failed"))
        self.assertIs(failed, after, "rolled back and in no transaction, the held connection serves the next write")

    def test_a_follower_that_breaks_the_database_drops_the_connection_and_the_next_write_opens_another(self):
        failed, after = self.follower_raises(sqlite3.DatabaseError("database disk image is malformed"))
        self.assertIsNot(failed, after, "a connection SQLite failed on is not lent again")
        self.assertFalse(is_open(failed), "and it is closed when it is dropped")


if __name__ == "__main__":
    unittest.main()
