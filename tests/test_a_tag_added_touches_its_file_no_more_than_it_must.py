"""A tag added or taken off from the photo panel touches its file no more than it must (Files, F1).

One save of a photo a folder view holds, no rename, looks at its file four times: the route's check that it is there,
the stamp the page named compared under the lock, the stat before the write and the stat after it. It was six: the
route looked at the file twice after the write for what the write had looked at already. It starts one ExifTool.
A tag added to a selection (Add to all selected) looks at each file five times: the route's look for a file gone
before the write, the write's four, none after it. It was seven, two after the write (#3069).

A tag taken off or added is the own writer's patch in place (field_values.PATCH_IN_PLACE, 4d; it was ExifTool's
rewrite of a tag added, three commands, before, #3070): ExifTool reads the file twice, before the write and once after
it, whole, for its row and as the patch's check of its fields at once, and writes it never. It was three: the check
read the fields once more before the read back whole read them again. Counts only, never times
(docs/DEVELOPMENT.md, "Performance work").

The folded read is the patch's check all the same: a patch whose fields do not read back as written is put back and
answered as the photo's error (the envelope's `errors`, `success` false); a patch that did not land is too; and the
reply, and the folder map's record, carry the file's stamp after the save, at its new name after a rename by its title.

Real ExifTool and real Pillow on photos made here; rows as the indexer makes them (tests/photo_rows.py); fictional names.
"""
import contextlib
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import page_writes  # noqa: E402
import photo_rows  # noqa: E402
from test_stale_record_precondition import Fresh, stamp_of, tags_in, write_into  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import field_values, in_place  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import file_changes  # noqa: E402
from tagpup.store import db, file_journal  # noqa: E402

TWO = ["Trips/Coast", "Activity/Sailing"]


def bytes_of(path):
    with open(path, "rb") as handle:
        return handle.read()


class Touches:
    """The looks at one file, counted while it is open: os.stat of it (os.path.exists goes through os.stat), ExifTool
    commands naming it (ExifToolSession.execute), those of them that write it (a new file, `-o`, field_values._command)
    and ExifTool processes started (ExifToolSession.run, not running)."""

    def __init__(self, path):
        self.key = paths.key(path)
        self.stats = self.commands = self.writes = self.starts = 0
        self._inside = threading.local()

    def _names_it(self, value):
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if not isinstance(value, (str, os.PathLike)) or getattr(self._inside, "on", False):
            return False
        self._inside.on = True
        try:
            return paths.key(os.fspath(value)) == self.key
        except (TypeError, ValueError):
            return False
        finally:
            self._inside.on = False

    def __enter__(self):
        real_stat, real_execute, real_run = os.stat, ExifToolSession.execute, ExifToolSession.run
        touches = self

        def stat(path, *args, **kwargs):
            if touches._names_it(path):
                touches.stats += 1
            return real_stat(path, *args, **kwargs)

        def execute(session, *params, **kwargs):
            if any(touches._names_it(param) for param in params):
                touches.commands += 1
                touches.writes += "-o" in params
            return real_execute(session, *params, **kwargs)

        def run(session):
            if not session.running:
                touches.starts += 1
            return real_run(session)

        self._patches = [mock.patch.object(os, "stat", stat), mock.patch.object(ExifToolSession, "execute", execute),
                         mock.patch.object(ExifToolSession, "run", run)]
        for each in self._patches:
            each.start()
        return self

    def __exit__(self, *_raised):
        for each in reversed(self._patches):
            each.stop()
        return False

    def counted(self):
        return {"stats": self.stats, "exiftool commands": self.commands, "exiftool writes": self.writes,
                "exiftool starts": self.starts}


class ATagAdded(Fresh):
    def held(self, name, seed, preserved=None):
        """A photo holding two tags in every keyword field (and, with `preserved`, the name Smart Rename kept of it), its
        row as the indexer leaves it, and the record of it the folder view holds (a folder map's entry)."""
        path = os.path.join(self.folder, name)
        damaged_photos.whole_jpeg(path, seed=seed)
        values = {"XMP:Subject": list(TWO), "IPTC:Keywords": list(TWO), "EXIF:XPKeywords": ";".join(TWO)}
        if preserved:
            values["XMP-xmpMM:PreservedFileName"] = preserved
        write_into(path, **values)
        conn = db.connect(self.library.path)
        try:
            photo_id = photo_rows.add_read(conn, path, values)
            conn.commit()
        finally:
            conn.close()
        scan = self.api("/folder/scan", query_string={"path": self.folder})
        self.assertEqual(200, scan.status_code, scan.data)
        record = [each for each in scan.get_json() if paths.same(each["path"], path)][0]
        return path, photo_id, record

    def row_tags(self, photo_id):
        conn = db.connect(self.library.path)
        try:
            return conn.execute("SELECT tags FROM photos WHERE id = ?", (photo_id,)).fetchone()[0]
        finally:
            conn.close()

    def cached(self, path):
        """The folder view's record of `path`, as the folder map serves it again."""
        scan = self.api("/folder/scan", query_string={"path": self.folder}).get_json()
        return [each for each in scan if paths.same(each["path"], path)][0]

    def counted_save(self, path, record, tags):
        """The touches of `path` by one page save of `record` with `tags`, and whether it was a patch in place."""
        with Touches(path) as touches, mock.patch.object(file_journal, "save_patch",
                                                         side_effect=file_journal.save_patch) as patched:
            reply = self.save(record, tags=tags)
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(sorted(tags), sorted(tags_in(path)))
        return touches.counted(), bool(patched.call_count)

    def test_one_tag_taken_off_looks_at_its_file_four_times_and_asks_exiftool_twice(self):
        path, _photo_id, record = self.held("a.jpg", 51)
        counted, patched = self.counted_save(path, record, ["Trips/Coast"])
        self.assertTrue(patched, "a tag taken off is a patch in place")
        self.assertEqual({"stats": 4, "exiftool commands": 2, "exiftool writes": 0, "exiftool starts": 1}, counted)

    def test_one_tag_added_is_a_patch_that_looks_at_its_file_four_times_and_asks_exiftool_twice(self):
        path, _photo_id, record = self.held("f.jpg", 56)
        counted, patched = self.counted_save(path, record, TWO + ["Trips/Lakes"])
        self.assertTrue(patched, "a tag added is the own writer's patch in place")
        self.assertEqual({"stats": 4, "exiftool commands": 2, "exiftool writes": 0, "exiftool starts": 1}, counted)

    def test_fields_that_do_not_read_back_as_written_put_the_file_back(self):
        path, photo_id, record = self.held("b.jpg", 52)
        original, tags_before = bytes_of(path), self.row_tags(photo_id)
        real_read = field_values.read

        def read(et, photo_paths, wanted, *args, **kwargs):
            found = real_read(et, photo_paths, wanted, *args, **kwargs)
            held = found.get(paths.key(path))
            written = bytes_of(path) != original
            if written and isinstance(held, dict) and "XMP:Subject" in held:
                held["XMP:Subject"] = list(TWO)                 # what it held before: the patch did not take
            return found

        with mock.patch.object(field_values, "read", read):
            reply = self.save(record, tags=["Trips/Coast"])
        body = reply.get_json()
        self.assertEqual((200, False), (reply.status_code, body["success"]), reply.data)
        self.assertIn("do not read back as written", page_writes.why(body))
        self.assertEqual(original, bytes_of(path), "its bytes were put back")
        self.assertEqual(tags_before, self.row_tags(photo_id), "its row is as it was")

    def test_a_read_back_whole_that_fails_checks_the_patch_by_its_fields_alone(self):
        # The read back whole fails (ExifTool reports an error of the file, a read that did not answer): the patch is
        # checked by a read of its fields, as before the two reads were one, and the row records the fields written.
        path, _photo_id, record = self.held("g.jpg", 57)
        with mock.patch.object(file_changes, "_read_whole", return_value=None) as whole:
            counted, patched = self.counted_save(path, record, ["Trips/Coast"])
        self.assertTrue(patched)
        self.assertEqual(1, whole.call_count)
        self.assertEqual({"stats": 4, "exiftool commands": 3, "exiftool writes": 0, "exiftool starts": 1}, counted,
                         "the read before the write, the check's own read, and the write's read back of the fields "
                         "it kept (_read_back), as when the read back whole failed before (it is mocked here)")
        self.assertEqual(["Trips/Coast"], self.cached(path)["tags"])

    def test_a_patch_that_did_not_land_is_an_error_and_changes_nothing(self):
        path, photo_id, record = self.held("c.jpg", 53)
        original, tags_before = bytes_of(path), self.row_tags(photo_id)
        with mock.patch.object(in_place, "_write", lambda _handle, _changes: None):
            reply = self.save(record, tags=["Trips/Coast"])
        body = reply.get_json()
        self.assertEqual((200, False), (reply.status_code, body["success"]), reply.data)
        self.assertIn("did not leave the file as it wrote it", page_writes.why(body))
        self.assertEqual(original, bytes_of(path))
        self.assertEqual(tags_before, self.row_tags(photo_id))

    def test_the_reply_and_the_folder_map_carry_the_stamp_after_the_save(self):
        path, _photo_id, record = self.held("d.jpg", 54)
        reply = self.save(record, tags=["Trips/Coast"])
        self.assertEqual(200, reply.status_code, reply.data)
        body, now = page_writes.saved(reply.get_json()), stamp_of(path)
        self.assertEqual(now["size"], body["size"])
        self.assertAlmostEqual(now["mtime"], body["mtime"], places=6)
        cached = self.cached(path)
        self.assertEqual((body["mtime"], body["size"]), (cached["mtime"], cached["size"]))
        again = self.save(cached, tags=cached["tags"] + ["Trips/Lakes"])
        self.assertEqual(200, again.status_code, again.data)

    def test_a_rename_by_its_title_carries_the_stamp_of_the_file_under_its_new_name(self):
        path, _photo_id, record = self.held("Regatta - 1 - Start.jpg", 55, preserved="IMG_0001.jpg")
        reply = self.save(record, title="Harbour")
        self.assertEqual(200, reply.status_code, reply.data)
        body = page_writes.saved(reply.get_json())
        new_path = body["new_path"]
        self.assertFalse(paths.same(path, new_path), "renamed after its title")
        now = stamp_of(new_path)
        self.assertEqual(now["size"], body["size"])
        self.assertAlmostEqual(now["mtime"], body["mtime"], places=6)
        cached = self.cached(new_path)
        self.assertEqual((body["mtime"], body["size"]), (cached["mtime"], cached["size"]))


    def test_a_tag_added_to_two_photos_at_once_looks_at_each_file_five_times(self):
        """Add to all selected: the route looks at each file once before the write (tagging.leave_out_missing: is it
        gone?), the write four times as a save's does, and nothing after it: the folder map's record and the reply take
        the stamp the write recorded the row with (file_changes.write_fields' details["stamps"]). It was seven: the route
        looked twice more after the write, for the folder maps and for the reply (#3069). The reply's stamp is the
        file's now, and the panel's next save from the record is not refused."""
        photos = [self.held("h.jpg", 58)[0], self.held("i.jpg", 59)[0]]
        with contextlib.ExitStack() as stack:
            touches = [stack.enter_context(Touches(path)) for path in photos]
            reply = self.client.post("/library/api/photos/write", json={"op": "tags", "changes": {photo: {"add": ["Trips/Lakes"]}
                                                                                        for photo in photos}})
        self.assertEqual((200, True), (reply.status_code, reply.get_json()["success"]), reply.data)
        self.assertEqual([5, 5], [each.counted()["stats"] for each in touches])
        stamps = reply.get_json()["written"]
        for path in photos:
            (sent,) = [stamp for named, stamp in stamps.items() if paths.same(named, path)]
            now = stamp_of(path)
            self.assertEqual(now["size"], sent["size"])
            self.assertAlmostEqual(now["mtime"], sent["mtime"], places=6)
            cached = self.cached(path)
            self.assertEqual((sent["mtime"], sent["size"]), (cached["mtime"], cached["size"]))
            self.assertEqual(sorted(TWO + ["Trips/Lakes"]), sorted(cached["tags"]))
        again = self.save(self.cached(photos[0]), tags=["Trips/Coast"])
        self.assertEqual(200, again.status_code, again.data)

if __name__ == "__main__":
    unittest.main()
