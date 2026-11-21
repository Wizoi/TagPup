"""A page's write of photo files through the one entry point, POST /api/photos/write (plan one-write-path, cuts 2a and 2b; I15).

Somebody tags photos, saves one photo's title and tags, or moves a folder's Date Taken by some minutes (Time Shift), and
the page sends it to one route: `op` `tags` (each photo's own tags to add and take off), `set` (one photo's title, tags
and Date Taken from the record the page read) or `shift` (each photo's Date Taken moved by `params.minutes`), each photo
named by its path and by its row's id when it has one. The reply is one envelope whatever the op. What the owner lives
through: an update stopping the server's batches while a write is asked (nothing begins, nothing is written); two writes
at once (both land, one after the other); a share that does not answer (that photo is its error at once, the others are
written, nothing waits on it), or stops answering part-way through a shift (the change ends there, `written` naming the
photos that landed), or does not give a held folder's listing when its Time Shift answers (the folder's photos are
answered as the write read them back, never scanned again); a page whose record is stale (its base, or its id now naming another file, is refused 409 and
nothing is written); a chunk of the page's tag queue naming a photo renamed since it was read (refused 409, the photo
read again by its id, the chunk sent where it is now and written: the pages' rule, web/common/photo-write.js readMoved,
against the real route rather than the pages' fakes); a photo of a folder the library does not hold, which has no row and is named by its path alone. A
library view's bulk edit goes through the same service and still lets a waiting save in between its chunks: that one is
tests/test_batches.py's (ASaveBetweenTwoChunks), on the fake ExifTool, as it needs three chunks of photos.

Real ExifTool on JPEGs made here, rows as the indexer records them (photo_rows.add_read), the route through Flask's
test client in a home of the test's own (tests/handler_harness.py). The replies of a folder's Time Shift that the page's
tests answer from (tests/frontend/fixtures/shift_replies.json, harness.mjs shiftedAs) are written here.
"""
import contextlib
import json
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import page_fixtures  # noqa: E402
import photo_rows  # noqa: E402
from tests.handler_harness import Library  # noqa: E402
from tests.test_bulk_writes_start_from_the_file import EXIFTOOL, EXISTING, make_photo, tags_in  # noqa: E402
from tests.test_file_changes_one_at_a_time import at_once  # noqa: E402
from tests.test_taxonomy_lifecycle import requires_exiftool  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import field_values, shares  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import batches, file_changes, photo_writes  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.store import db, journal  # noqa: E402

ROUTE = "/api/photos/write"
TAKEN = "2020:06:01 10:00:00"
SHIFT_REPLIES = page_fixtures.path_of("shift_replies.json")


def date(photos, taken=TAKEN):
    """Give each photo a camera's Date Taken, as a camera writes it."""
    with ExifToolSession(executable=EXIFTOOL) as et:
        et.set_tags(list(photos), tags={"EXIF:DateTimeOriginal": taken}, params=["-overwrite_original"])


def taken_in(path):
    """The photo's Date Taken as its file holds it."""
    with ExifToolSession(executable=EXIFTOOL) as et:
        return et.get_tags([path], tags=["EXIF:DateTimeOriginal"])[0].get("EXIF:DateTimeOriginal")


@requires_exiftool
class AWriteThroughTheOneEntryPoint(unittest.TestCase):
    def setUp(self):
        self.lib = Library(self)
        self.held = os.path.join(self.lib.photos, "Regatta Day")
        self.photos = [make_photo(os.path.join(self.held, "p%d.jpg" % n)) for n in range(3)]
        self.ids = self.add_rows(self.photos)

    def add_rows(self, photos):
        conn = db.connect(self.lib.db_path)
        try:
            ids = [photo_rows.add_read(conn, photo, {"XMP:Subject": list(EXISTING)}) for photo in photos]
            conn.commit()
        finally:
            conn.close()
        return ids

    def changes(self):
        """The changes of photo files in the journal (the library's first settings stamp left out)."""
        return [change for change in journal.history(self.lib.db_path, limit=20) if change["files"]]

    def tags_op(self, adds, group=None):
        """{path: (id, [tags to add])} as the page sends it."""
        return {"op": "tags", "group": group,
                "changes": {path: {"id": photo_id, "add": add} for path, (photo_id, add) in adds.items()}}

    def test_tags_by_id_are_written_as_one_journaled_change_and_answered_in_one_envelope(self):
        status, reply = self.lib.post(ROUTE, self.tags_op({path: (photo_id, ["Places/Quay"])
                                                          for path, photo_id in zip(self.photos[:2], self.ids)}))
        self.assertEqual(200, status, reply)
        self.assertTrue(reply["success"])
        self.assertEqual((2, 2, 2, 0, 0), (reply["attempted"], reply["changed"], reply["with_rows"], reply["file_only"],
                                           reply["error_count"]))
        self.assertEqual([], reply["errors"])
        self.assertIsNone(reply["stopped"])
        self.assertIsNotNone(reply["change"])
        self.assertEqual(1, len(self.changes()))
        for path in self.photos[:2]:
            written = reply["written"][path]
            self.assertIn("Places/Quay", written["tags"])
            self.assertEqual(int(os.stat(path).st_size), written["size"])
            self.assertIn("Places/Quay", tags_in(path))
        self.assertNotIn("Places/Quay", tags_in(self.photos[2]))

    def test_a_set_from_a_stale_base_is_refused_409_and_one_from_the_file_is_written(self):
        photo, photo_id = self.photos[0], self.ids[0]
        stamp = os.stat(photo)
        record = {"id": photo_id, "title": "Quay at dusk", "tags": EXISTING + ["Places/Quay"],
                  "stamp": {"mtime": stamp.st_mtime, "size": stamp.st_size}}
        status, reply = self.lib.post(ROUTE, {"op": "set", "changes": {photo: {**record,
                                                                            "base": {"tags": ["Beach"], "title": ""}}}})
        self.assertEqual(409, status, reply)
        self.assertTrue(reply["changed_on_disk"])
        self.assertNotIn("Places/Quay", tags_in(photo))
        self.assertEqual([], self.changes())

        status, reply = self.lib.post(ROUTE, {"op": "set", "changes": {photo: {**record,
                                                                            "base": {"tags": EXISTING, "title": ""}}}})
        self.assertEqual(200, status, reply)
        written = reply["written"][photo]
        self.assertEqual(photo, written["new_path"])
        self.assertEqual({"tags": EXISTING + ["Places/Quay"], "title": "Quay at dusk"},
                         {"tags": sorted(written["base"]["tags"]), "title": written["base"]["title"]})
        self.assertEqual(int(os.stat(photo).st_size), written["size"])
        self.assertIn("Places/Quay", tags_in(photo))

    def test_an_id_that_names_another_file_now_is_refused_409_and_nothing_is_written(self):
        # The page's record says photo 1 is at the second path (a rename it did not see), or names a row since deleted.
        for wrong_id in (self.ids[0], max(self.ids) + 100):
            with self.subTest(wrong_id=wrong_id):
                status, reply = self.lib.post(ROUTE, self.tags_op({self.photos[1]: (wrong_id, ["Places/Quay"]),
                                                                   self.photos[2]: (self.ids[2], ["Places/Quay"])}))
                self.assertEqual(409, status, reply)
                self.assertTrue(reply["changed_on_disk"])
                self.assertIn("p1.jpg", reply["error"])
                self.assertEqual(0, reply["changed"])
        for path in self.photos:
            self.assertNotIn("Places/Quay", tags_in(path))
        self.assertEqual([], self.changes())

    def test_a_chunk_naming_a_photo_renamed_since_is_refused_409_read_again_by_its_id_and_written_where_it_is(self):
        # The page read p0 at its old name; a Smart Rename in another tab renamed it since (a change of its own).
        old, photo_id = self.photos[0], self.ids[0]
        new = os.path.join(self.held, "p0 Quay at dusk.jpg")
        file_changes.rename(self.lib.library, "smart rename", {old: new}, {}, EXIFTOOL)
        before = len(self.changes())
        chunk = lambda named: self.tags_op({named: (photo_id, ["Places/Quay"]),   # noqa: E731
                                            self.photos[1]: (self.ids[1], ["Places/Quay"])}, group="tap-1")
        status, reply = self.lib.post(ROUTE, chunk(old))
        # What the pages read of it (readMoved): a 409 marked changed_on_disk, nothing written, the envelope beside it.
        self.assertEqual(409, status, reply)
        self.assertEqual((False, True, {}, 0), (reply["success"], reply["changed_on_disk"], reply["written"], reply["changed"]))
        self.assertIn("p0.jpg", reply["error"])
        self.assertEqual(before, len(self.changes()))
        self.assertNotIn("Places/Quay", tags_in(self.photos[1]))

        status, read = self.lib.get("/api/library/photo", {"id": photo_id})
        self.assertEqual(200, status, read)
        self.assertTrue(paths.same(new, read["photo"]["path"]), read["photo"]["path"])
        status, reply = self.lib.post(ROUTE, chunk(read["photo"]["path"]))
        self.assertEqual(200, status, reply)
        self.assertTrue(reply["success"])
        self.assertEqual(2, reply["changed"])
        for path in (new, self.photos[1]):
            self.assertIn("Places/Quay", tags_in(path))
        self.assertEqual(before + 1, len(self.changes()))

        # A photo the library no longer has reads 404: the page settles it skipped, gone as a file gone in Explorer is.
        status, _gone = self.lib.get("/api/library/photo", {"id": max(self.ids) + 100})
        self.assertEqual(404, status)

    def test_a_set_of_a_photo_renamed_since_is_409_by_its_id_and_of_a_missing_file_by_path_only_is_400(self):
        # #3295: the save of the record a page read before a rename answers as a chunk's does (409, read again), not 400.
        old, photo_id = self.photos[0], self.ids[0]
        new = os.path.join(self.held, "p0 Quay at dusk.jpg")
        file_changes.rename(self.lib.library, "smart rename", {old: new}, {}, EXIFTOOL)
        before = len(self.changes())

        def save(path, named_id):
            return self.lib.post(ROUTE, {"op": "set", "changes": {path: {
                "id": named_id, "title": "Quay", "tags": EXISTING, "stamp": {"mtime": 1.0, "size": 1},
                "base": {"tags": EXISTING, "title": ""}}}})
        status, reply = save(old, photo_id)
        self.assertEqual(409, status, reply)
        self.assertEqual((False, True, {}), (reply["success"], reply["changed_on_disk"], reply["written"]))
        self.assertEqual(before, len(self.changes()))
        status, reply = save(os.path.join(self.held, "gone.jpg"), None)
        self.assertEqual(400, status, reply)
        self.assertEqual(before, len(self.changes()))

    def test_the_hold_of_the_lock_is_the_callers_alone(self):
        # One place decides which hold of the lock a write runs under: its caller (the route's batch_exclusively through
        # all its files; a library view's bulk edit, none, so a save is let in between its chunks). The service takes
        # none of its own: an id it checks is checked under the caller's hold, here a stale one refused, nothing written.
        def no_lock():
            raise AssertionError("photo_writes.write took the lock of changes of photo files itself")
        with mock.patch.object(file_changes, "exclusively", no_lock):
            result = photo_writes.write(self.lib.library, photo_writes.TAGS,
                                        {self.photos[1]: (self.ids[0], {"add": ["Places/Quay"]})}, EXIFTOOL, None)
        self.assertTrue(result.refused)
        self.assertEqual(0, result.changed)
        self.assertNotIn("Places/Quay", tags_in(self.photos[1]))

    def test_a_photo_with_no_row_is_named_by_its_path_and_written_to_its_file_only(self):
        loose = make_photo(os.path.join(self.lib.root, "Just Looking", "q.jpg"))
        status, reply = self.lib.post(ROUTE, self.tags_op({loose: (None, ["Places/Quay"])}))
        self.assertEqual(200, status, reply)
        self.assertEqual((1, 0), (reply["file_only"], reply["with_rows"]))
        self.assertIsNone(reply["change"])
        self.assertIn("Places/Quay", tags_in(loose))
        self.assertEqual([], self.lib.rows("SELECT id FROM photos WHERE path = ?", (paths.stored(loose),)))
        self.assertEqual([], self.changes())

    def test_two_writes_at_once_both_land_one_after_the_other(self):
        client = self.lib.app.test_client

        def post(body):
            def call():
                reply = client().post(ROUTE, json=body)
                return reply.status_code, reply.get_json()
            return call
        first, second = at_once(
            post(self.tags_op({path: (photo_id, ["Places/Quay"]) for path, photo_id in zip(self.photos, self.ids)})),
            post(self.tags_op({path: (photo_id, ["Places/Harbour"])
                               for path, photo_id in zip(self.photos[1:], self.ids[1:])})))
        self.assertEqual(200, first[0], first)
        self.assertEqual(200, second[0], second)
        self.assertNotIn("Places/Harbour", tags_in(self.photos[0]))
        for path in self.photos[1:]:
            self.assertTrue({"Places/Quay", "Places/Harbour"} <= set(tags_in(path)))
        self.assertEqual(2, len(self.changes()))

    def test_a_write_asked_while_an_update_stops_the_batches_begins_nothing(self):
        # More than one chunk is a batch: it waits for its turn, and an update's stop refuses it before it begins.
        many = [os.path.join(self.held, "r%02d.jpg" % n) for n in range(batches.CHUNK + 1)]
        batches.stop_all()
        self.addCleanup(batches.reopen)
        status, reply = self.lib.post(ROUTE, self.tags_op({path: (None, ["Places/Quay"]) for path in many}))
        self.assertEqual(400, status, reply)
        self.assertIn("cancelled before it began", reply["error"])
        self.assertEqual([], self.changes())

    def test_a_photo_on_a_share_that_does_not_answer_is_its_error_and_the_others_are_written_at_once(self):
        asleep_folder = os.path.join(self.lib.photos, "Coast")
        asleep = make_photo(os.path.join(asleep_folder, "IMG_0001.jpg"))
        (asleep_id,) = self.add_rows([asleep])
        woken, under = threading.Event(), os.path.normcase(asleep_folder)
        self.addCleanup(woken.set)
        shares.forget()
        self.addCleanup(shares.forget)
        real_stat, real_share_of = os.stat, shares.share_of

        def on_it(path):
            return os.path.normcase(str(path)).startswith(under)

        def share_of(folder):
            # The share is its own, as a real one is (\\server\share or a mapped drive): the test's folders are all
            # on one local drive, and a share is taken as away by its key, so without this the local photo is too.
            return "//nas/coast" if on_it(folder) else real_share_of(folder)

        def stat(path, *args, **kwargs):
            if on_it(path):
                woken.wait(1.5)
                raise FileNotFoundError(2, "not found")
            return real_stat(path, *args, **kwargs)
        with mock.patch.object(os, "stat", stat), mock.patch.object(shares, "on_a_network_drive", on_it), \
                mock.patch.object(shares, "SHARE_WAIT", 0.05), mock.patch.object(shares, "share_of", share_of):
            began = time.monotonic()
            status, reply = self.lib.post(ROUTE, self.tags_op({asleep: (asleep_id, ["Places/Quay"]),
                                                               self.photos[0]: (self.ids[0], ["Places/Quay"])}))
            took = time.monotonic() - began
            set_status, set_reply = self.lib.post(ROUTE, {"op": "set", "changes": {asleep: {
                "id": asleep_id, "title": "", "tags": EXISTING + ["Places/Quay"],
                "base": {"tags": EXISTING, "title": ""}}}})
        self.assertEqual(200, status, reply)
        self.assertFalse(reply["success"])
        self.assertEqual(1, reply["error_count"])
        (error,) = reply["errors"]
        self.assertEqual((asleep, asleep_id), (error["path"], error["id"]))
        self.assertIn("could not be reached", error["why"])
        self.assertIn("Places/Quay", tags_in(self.photos[0]))
        self.assertLess(took, 60, "a share asleep is answered at once, never ExifTool's five-minute deadline")
        self.assertEqual(503, set_status, set_reply)
        self.assertNotIn("Places/Quay", tags_in(asleep))

    def shift_op(self, named, minutes=30):
        """{path: id} as the page sends a shift of them."""
        return {"op": "shift", "params": {"minutes": minutes},
                "changes": {path: {"id": photo_id} for path, photo_id in named.items()}}

    def test_a_shift_is_journaled_as_a_time_shift_and_answered_with_each_photos_stamp(self):
        date(self.photos)
        status, reply = self.lib.post(ROUTE, self.shift_op(dict(zip(self.photos[:2], self.ids))))
        self.assertEqual(200, status, reply)
        self.assertTrue(reply["success"])
        self.assertEqual((2, 2, 2, 0, 0), (reply["attempted"], reply["changed"], reply["with_rows"], reply["file_only"],
                                           reply["error_count"]))
        self.assertEqual(["time shift"], [change["operation"] for change in self.changes()])
        self.assertEqual({path: {"mtime": os.stat(path).st_mtime, "size": int(os.stat(path).st_size),
                                 "taken": "2020:06:01 10:30:00"} for path in self.photos[:2]}, reply["written"])
        self.assertEqual(["2020:06:01 10:30:00"] * 2 + [TAKEN], [taken_in(path) for path in self.photos])

    def test_a_shift_sent_in_chunks_of_one_group_is_one_line_of_history_each_photo_told_its_new_date(self):
        # The page sends a folder's Time Shift as its tag changes go, a chunk a request under the one token of the owner's
        # click (#3284): History shows one line for it, and each reply names every photo's Date Taken as its file holds it
        # now, which the page shows without spelling a date of its own. A token that is not one is refused, nothing written.
        date(self.photos)
        status, refused = self.lib.post(ROUTE, {**self.shift_op({self.photos[0]: self.ids[0]}), "group": "a/b"})
        self.assertEqual((400, {}), (status, refused["written"]), refused)
        self.assertEqual([], self.changes())
        replies = []
        for named in ({self.photos[0]: self.ids[0], self.photos[1]: self.ids[1]}, {self.photos[2]: self.ids[2]}):
            status, reply = self.lib.post(ROUTE, {**self.shift_op(named), "group": "shift-1"})
            self.assertEqual(200, status, reply)
            replies.append(reply)
        self.assertEqual([{"2020:06:01 10:30:00"}] * 2, [{each["taken"] for each in reply["written"].values()}
                                                         for reply in replies])
        self.assertEqual(["2020:06:01 10:30:00"] * 3, [taken_in(path) for path in self.photos])
        status, history = self.lib.get("/api/history", {"limit": 5})
        self.assertEqual(200, status, history)
        (line,) = [each for each in history["changes"] if each["files"]]   # the library's first settings stamp left out
        self.assertEqual(("time shift", "shift-1", 2, 3), (line["operation"], line["group"], line["writes"],
                                                           line["summary"]["photos"]))

    def test_a_shift_with_an_id_that_names_another_file_now_is_refused_409_and_nothing_is_written(self):
        date(self.photos)
        status, reply = self.lib.post(ROUTE, self.shift_op({self.photos[1]: self.ids[0], self.photos[2]: self.ids[2]}))
        self.assertEqual(409, status, reply)
        self.assertTrue(reply["changed_on_disk"])
        self.assertIn("p1.jpg", reply["error"])
        self.assertEqual((0, {}), (reply["changed"], reply["written"]))
        self.assertEqual([TAKEN] * 3, [taken_in(path) for path in self.photos])
        self.assertEqual([], self.changes())

    @contextlib.contextmanager
    def a_share_that_stops_answering_at(self, stop):
        """The held folder on a share of its own that stops answering at the photo `stop`: the write's own read of each
        file just before it writes it does not answer for that one."""
        shares.forget()
        self.addCleanup(shares.forget)
        real_read_one, real_share_of, under = field_values.read_one, shares.share_of, os.path.normcase(self.held)

        def share_of(folder):
            # Its own share, as a real one is: the test's folders are all on one local drive, taken as away by its key.
            return "//nas/regatta" if os.path.normcase(str(folder)).startswith(under) else real_share_of(folder)

        def read_one(et, path, wanted):
            if paths.key(path) == paths.key(stop):
                raise field_values.NotAnswered("ExifTool did not answer for it in 1 s")
            return real_read_one(et, path, wanted)
        with mock.patch.object(field_values, "read_one", read_one), mock.patch.object(shares, "share_of", share_of):
            yield

    def test_a_share_that_stops_answering_part_way_through_a_shift_ends_it_with_what_landed(self):
        date(self.photos)
        with self.a_share_that_stops_answering_at(self.photos[1]):
            status, reply = self.lib.post(ROUTE, self.shift_op(dict(zip(self.photos, self.ids))))
        self.assertEqual(400, status, reply)
        self.assertIn("ExifTool stopped answering after 1 of 3 photos", reply["error"])
        self.assertEqual((1, [self.photos[1]]), (reply["changed"], [error["path"] for error in reply["errors"]]))
        self.assertEqual([self.photos[0]], list(reply["written"]), "what landed, not what was asked")
        self.assertEqual(["2020:06:01 10:30:00", TAKEN, TAKEN], [taken_in(path) for path in self.photos])
        self.assertEqual([("time shift", "applied")], [(change["operation"], change["status"]) for change in self.changes()])

    def test_the_shift_replies_the_page_reads_are_written_for_its_test(self):
        # A folder's Time Shift as the page sends it (web/tagpup/rename.js applyTimeShift): its photos by path, no id (a
        # folder's records carry none), in one request. `stopped`: the share stops answering at the second of three, the
        # first written; `shifted`: two photos written. Each photo named PHOTO_<n> in the order sent, its stamp 1 and 1.
        date(self.photos)
        found = {}
        for name, sent, stop in (("stopped", self.photos, self.photos[1]), ("shifted", self.photos[1:], None)):
            body = {"op": "shift", "params": {"minutes": 30}, "changes": {path: {"id": None} for path in sent}}
            with self.a_share_that_stops_answering_at(stop) if stop else contextlib.nullcontext():
                status, reply = self.lib.post(ROUTE, body)
            for own in reply["written"].values():
                own.update(mtime=1, size=1)
            text = json.dumps(reply)
            for n, path in enumerate(sent):
                text = text.replace(json.dumps(path)[1:-1], "PHOTO_%d" % n)
            found[name] = {"status": status, "reply": json.loads(text)}
        self.assertEqual((400, ["PHOTO_0"]), (found["stopped"]["status"], list(found["stopped"]["reply"]["written"])))
        self.assertEqual((200, ["PHOTO_0", "PHOTO_1"]), (found["shifted"]["status"],
                                                         sorted(found["shifted"]["reply"]["written"])))
        self.assertNotIn(json.dumps(self.lib.photos)[1:-1], json.dumps(found), "a path of the test's home left in")
        text = json.dumps(found, sort_keys=True, indent=1) + "\n"
        page_fixtures.hold_to(self, SHIFT_REPLIES, text, "tests.test_a_write_through_the_one_entry_point", "the Time Shift")

    def test_a_held_folders_time_shift_answers_every_photo_shifted_when_its_listing_does_not_answer(self):
        # The owner opens a held folder and moves its Date Taken; the share is slow by the time the shift answers. The
        # reply is the folder as the write read it back: every photo, shifted, and no scan of a listing that does not
        # answer (which would empty the page's folder under "Time shift applied") nor a 500 for a journaled write.
        date(self.photos)
        status, opened = self.lib.get("/api/folder/scan", {"path": self.held})
        self.assertEqual((200, 3), (status, len(opened)), opened)
        with mock.patch.object(shares, "listing", return_value=shares.UNANSWERED), \
                mock.patch.object(photo_actions, "scan_folder", wraps=photo_actions.scan_folder) as scan:
            status, reply = self.lib.post("/api/folder/time-shift", {
                "folder_path": self.held, "camera_model": "All Cameras", "shift_minutes": 30})
        self.assertEqual(200, status, reply)
        self.assertEqual((3, 3), (reply["updated_count"], reply["requested_count"]))
        self.assertEqual(sorted(paths.key(path) for path in self.photos),
                         sorted(paths.key(record["path"]) for record in reply["updated_photos"]))
        self.assertEqual(["2020:06:01 10:30:00"] * 3, [taken_in(path) for path in self.photos])
        self.assertEqual({os.stat(path).st_mtime for path in self.photos},
                         {record["mtime"] for record in reply["updated_photos"]}, "each record as read back after the write")
        self.assertEqual(0, scan.call_count, "a shift's answer is never a scan of the folder")
        self.assertEqual(["time shift"], [change["operation"] for change in self.changes()])

    def test_a_body_that_means_nothing_is_refused_400(self):
        photo, photo_id = self.photos[0], self.ids[0]
        for body in ({"op": "shift", "changes": {photo: {"id": photo_id}}},
                     {"op": "shift", "changes": {photo: {"id": photo_id}}, "params": {"minutes": 1.5}},
                     {"op": "shift", "changes": {photo: {"id": photo_id, "add": []}}, "params": {"minutes": 30}},
                     {"op": "unshift", "changes": {photo: {"id": photo_id}}, "params": {"minutes": 30}},
                     {"op": "tags", "changes": {photo: {"id": "one", "add": ["Places/Quay"]}}},
                     {"op": "tags", "changes": {photo: {"id": photo_id, "title": "x"}}},
                     {"op": "set", "changes": {photo: {"id": photo_id, "title": "", "tags": []},
                                               self.photos[1]: {"id": self.ids[1], "title": "", "tags": []}}},
                     {"op": "set", "changes": {photo: {"id": photo_id, "title": "", "tags": [], "base": "x"}}},
                     {"op": "set", "changes": {photo: {"id": photo_id, "title": "", "tags": []}}}):
            with self.subTest(body=body):
                status, reply = self.lib.post(ROUTE, body)
                self.assertEqual(400, status, reply)
        self.assertEqual([], self.changes())

    def test_each_op_reads_its_photos_by_one_parser_and_refuses_them_in_one_sentence(self):
        # A page's changes are read the same way whatever the op (_entries): a write naming no photo, or an id that is
        # not a photo's, is refused by the same sentence, nothing written.
        photo = self.photos[0]
        own = {"tags": {"add": ["Places/Quay"]}, "shift": {},
               "set": {"title": "", "tags": [], "base": {"tags": [], "title": ""}}}
        for op in ("tags", "set", "shift"):
            for changes, sentence in (({}, "The write names no photo."),
                                      ({photo: {"id": "one", **own[op]}}, "id must be a photo id or null.")):
                with self.subTest(op=op, changes=changes):
                    status, reply = self.lib.post(ROUTE, {"op": op, "changes": changes, "params": {"minutes": 30}})
                    self.assertEqual(400, status, reply)
                    self.assertTrue(reply["error"].startswith(sentence), reply["error"])
        self.assertEqual([], self.changes())


if __name__ == "__main__":
    unittest.main()
