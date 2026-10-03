"""A save from the page never overwrites what the file holds and the page did not read (findings #533).

The details panel sends a photo's WHOLE tag list on a tag add, a Date Taken edit and carry forward, and the
server writes the list as given. A record built from the library's row (a library view) or from this
browser's half-hour cache of a scan (a folder view) can say less than the file: another program added a keyword
after the index read it, or the row was made for a photo nobody read (Suggest's shape: the path alone). So

* /api/library/photo builds the record as a folder scan builds one for a photo: the file's stamp against the
  row's, and the file read with ExifTool when they differ or the row has none;
* a write from the page names the stamp (mtime, size) of the record it was built from, and the server refuses
  with 409 "changed on disk" when the file's own stamp differs -- held and file-only writes alike -- and never
  writes. A call without a stamp (the CLI, the MCP) behaves as it always did.

Real ExifTool and real Pillow on photos made here; rows as the indexer and Suggest make them
(tests/photo_rows.py); fictional names.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.core import fields  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()


def in_file(path, *names):
    with ExifToolSession(executable=EXIFTOOL) as et:
        return et.get_tags([path], tags=list(names))[0]


def tags_in(path):
    return fields.field_values(in_file(path, "XMP:Subject").get("XMP:Subject"))


def caption_in(path):
    return (fields.field_values(in_file(path, "XMP:Description").get("XMP:Description")) or [""])[0]


def write_into(path, **tags):
    with ExifToolSession(executable=EXIFTOOL) as et:
        et.set_tags([path], tags=tags, params=["-overwrite_original"])


def stamp_of(path):
    stat = os.stat(path)
    return {"mtime": stat.st_mtime, "size": stat.st_size}


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class Fresh(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.library = Library(self.home.library("library.db"))
        self.addCleanup(tagpup_routes.folders.forget, self.library)
        self.folder = os.path.join(self.home.root, "Pictures", "Regatta")
        self.elsewhere = os.path.join(self.home.root, "Share", "Lighthouse")

    def api(self, route, **kwargs):
        return self.client.get("/library/api" + route, **kwargs)

    def post(self, route, body):
        return self.client.post("/library/api" + route, json=body)

    def make(self, name, seed, file_tags=(), caption=None, row="read", folder=None):
        """A photo whose FILE holds `file_tags` and `caption`, and whose row is what the indexer made of a read of it
        (row="read"), what Suggest makes of a photo never read (row="unread"), or none."""
        path = os.path.join(folder or self.folder, name)
        damaged_photos.whole_jpeg(path, seed=seed)
        wrote = {}
        if file_tags:
            wrote["Subject"] = list(file_tags)
        if caption:
            wrote["Description"] = caption
        if wrote:
            write_into(path, **wrote)
        conn = db.connect(self.library.path)
        try:
            if row == "read":
                found = {"XMP:Subject": list(file_tags)}
                if caption:
                    found["XMP:Description"] = caption
                photo_id = photo_rows.add_read(conn, path, found)
            elif row == "unread":
                photo_id = photo_rows.add_unread(conn, path)
            else:
                photo_id = None
            conn.commit()
        finally:
            conn.close()
        return path, photo_id

    def record(self, photo_id):
        reply = self.api("/library/photo", query_string={"id": photo_id})
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()["photo"]

    def save(self, record, **changes):
        body = {"path": record["path"], "title": record.get("title", ""), "tags": record["tags"],
                "stamp": {"mtime": record["mtime"], "size": record["size"]}}
        body.update(changes)
        return self.post("/photo/save-metadata", body)


class TheViewReadsTheFile(Fresh):
    def test_a_keyword_another_program_added_is_in_the_record_and_a_save_keeps_it(self):
        path, photo_id = self.make("a.jpg", 1, ["Trips/Coast"])
        write_into(path, Subject=["Trips/Coast", "Trips/Lakes"])      # another program, after the index read it
        record = self.record(photo_id)
        self.assertEqual(["Trips/Coast", "Trips/Lakes"], sorted(record["tags"]))
        reply = self.save(record, tags=record["tags"] + ["Activity/Sailing"])
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(["Activity/Sailing", "Trips/Coast", "Trips/Lakes"], sorted(tags_in(path)))

    def test_a_record_that_the_file_still_matches_is_the_rows_and_carries_the_files_stamp(self):
        path, photo_id = self.make("a.jpg", 2, ["Trips/Coast"])
        record = self.record(photo_id)
        self.assertEqual(["Trips/Coast"], record["tags"])
        self.assertEqual(stamp_of(path)["size"], record["size"])
        self.assertAlmostEqual(stamp_of(path)["mtime"], record["mtime"], places=3)
        self.assertFalse(record.get("missing"))

    def test_a_never_read_row_shows_what_the_file_holds_and_a_save_keeps_it(self):
        path, photo_id = self.make("a.jpg", 3, ["Trips/Coast", "Activity/Sailing"], caption="Harbour at dawn", row="unread")
        record = self.record(photo_id)
        self.assertEqual(["Activity/Sailing", "Trips/Coast"], sorted(record["tags"]))
        self.assertEqual("Harbour at dawn", record["title"])
        reply = self.save(record, tags=record["tags"] + ["Trips/Lakes"])
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(["Activity/Sailing", "Trips/Coast", "Trips/Lakes"], sorted(tags_in(path)))
        self.assertEqual("Harbour at dawn", caption_in(path), "the caption the file held is kept")

    def test_a_caption_another_program_wrote_is_kept_by_a_tag_save(self):
        path, photo_id = self.make("a.jpg", 4, ["Trips/Coast"])
        write_into(path, Description="Written elsewhere")
        record = self.record(photo_id)
        self.assertEqual("Written elsewhere", record["title"])
        reply = self.save(record, tags=record["tags"] + ["Trips/Lakes"])
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual("Written elsewhere", caption_in(path))

    def test_a_date_taken_edit_from_the_view_keeps_the_files_tags(self):
        path, photo_id = self.make("a.jpg", 5, ["Trips/Coast"], row="unread")
        record = self.record(photo_id)
        reply = self.save(record, date_taken="2021-05-06T07:08:09")
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(["Trips/Coast"], tags_in(path))

    def test_carry_forward_adds_to_what_the_file_holds(self):
        path, photo_id = self.make("a.jpg", 6, ["Trips/Coast"], row="unread")
        record = self.record(photo_id)
        carried = ["Activity/Sailing"]                      # the previous photo's tags, merged by the page
        reply = self.save(record, tags=sorted(set(record["tags"]) | set(carried)))
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(["Activity/Sailing", "Trips/Coast"], sorted(tags_in(path)))

    def test_a_file_gone_is_the_row_with_a_flag(self):
        path, photo_id = self.make("a.jpg", 7, ["Trips/Coast"])
        os.remove(path)
        record = self.record(photo_id)
        self.assertTrue(record["missing"])
        self.assertEqual(["Trips/Coast"], record["tags"])


class AWriteNamesTheStampItWasBuiltFrom(Fresh):
    def test_a_save_from_a_record_the_file_has_left_behind_is_refused_and_writes_nothing(self):
        path, photo_id = self.make("a.jpg", 8, ["Trips/Coast"])
        record = self.record(photo_id)
        write_into(path, Subject=["Trips/Coast", "Trips/Lakes"])      # after the panel opened
        before = open(path, "rb").read()
        reply = self.save(record, tags=["Trips/Coast", "Activity/Sailing"])
        self.assertEqual(409, reply.status_code)
        body = reply.get_json()
        self.assertTrue(body["changed_on_disk"])
        self.assertIn("This photo changed on disk since you opened it", body["error"])
        self.assertEqual(before, open(path, "rb").read(), "the file is unchanged")

    def test_a_file_replaced_between_open_and_save_is_refused(self):
        path, photo_id = self.make("a.jpg", 9, ["Trips/Coast"])
        record = self.record(photo_id)
        damaged_photos.whole_jpeg(path, seed=99)                       # another file of the same name
        reply = self.save(record)
        self.assertEqual(409, reply.status_code)
        self.assertEqual([], tags_in(path))

    def test_without_a_stamp_a_save_behaves_as_it_always_did(self):
        path, photo_id = self.make("a.jpg", 10, ["Trips/Coast"])
        write_into(path, Subject=["Trips/Coast", "Trips/Lakes"])
        reply = self.post("/photo/save-metadata", {"path": path, "title": "", "tags": ["Activity/Sailing"]})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(["Activity/Sailing"], tags_in(path))

    def test_the_reply_gives_the_new_stamp_and_the_next_save_with_it_passes(self):
        path, photo_id = self.make("a.jpg", 11, ["Trips/Coast"])
        record = self.record(photo_id)
        first = self.save(record, tags=["Trips/Coast", "Activity/Sailing"])
        self.assertEqual(200, first.status_code, first.data)
        stamp = {"mtime": first.get_json()["mtime"], "size": first.get_json()["size"]}
        self.assertEqual(stamp_of(path)["size"], stamp["size"])
        again = self.save(record, tags=["Trips/Coast", "Activity/Sailing", "Trips/Lakes"], stamp=stamp)
        self.assertEqual(200, again.status_code, again.data)
        stale = self.save(record, tags=["Trips/Coast"])                # the old stamp
        self.assertEqual(409, stale.status_code)

    def test_a_rename_by_the_caption_reports_the_stamp_of_the_file_under_its_new_name(self):
        path, photo_id = self.make("a.jpg", 12, ["Trips/Coast"])
        record = self.record(photo_id)
        reply = self.save(record, title="Harbour")
        self.assertEqual(200, reply.status_code, reply.data)
        new_path = reply.get_json()["new_path"]
        self.assertEqual(stamp_of(new_path)["size"], reply.get_json()["size"])

    def test_the_folder_views_cached_record_is_protected_the_same_way(self):
        path, _photo_id = self.make("a.jpg", 13, ["Trips/Coast"])
        scan = self.api("/folder/scan", query_string={"path": self.folder}).get_json()
        record = [each for each in scan if each["path"] == path][0]
        write_into(path, Subject=["Trips/Coast", "Trips/Lakes"])      # edited elsewhere after the page cached the scan
        reply = self.save(record, tags=["Trips/Coast", "Activity/Sailing"])
        self.assertEqual(409, reply.status_code)
        self.assertEqual(["Trips/Coast", "Trips/Lakes"], sorted(tags_in(path)))

    def test_a_photo_of_a_folder_the_library_does_not_hold_is_protected_too(self):
        path, _none = self.make("b.jpg", 14, ["Trips/Coast"], row=None, folder=self.elsewhere)
        stamp = stamp_of(path)
        write_into(path, Subject=["Trips/Coast", "Trips/Lakes"])
        reply = self.post("/photo/save-metadata", {"path": path, "title": "", "tags": ["Activity/Sailing"], "stamp": stamp})
        self.assertEqual(409, reply.status_code)
        self.assertEqual(["Trips/Coast", "Trips/Lakes"], sorted(tags_in(path)))
        fresh = self.post("/photo/save-metadata", {"path": path, "title": "", "tags": ["Activity/Sailing"],
                                                  "stamp": stamp_of(path)})
        self.assertEqual(200, fresh.status_code, fresh.data)

    def test_a_bulk_write_reports_each_written_photos_new_stamp(self):
        path, _photo_id = self.make("a.jpg", 15, ["Trips/Coast"])
        reply = self.post("/photos/bulk-tags", {"paths": [path], "add_tags": ["Activity/Sailing"], "remove_tags": []})
        self.assertEqual(200, reply.status_code, reply.data)
        stamps = reply.get_json()["stamps"]
        self.assertEqual({stamp_of(path)["size"]}, {each["size"] for each in stamps.values()})

    def test_a_rotate_reports_the_size_as_well_as_the_time(self):
        path, _photo_id = self.make("a.jpg", 16, [])
        reply = self.post("/photo/rotate", {"path": path, "direction": "left"})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(stamp_of(path)["size"], reply.get_json()["size"])


class TheIdIsReadStrictly(Fresh):
    def test_only_digits_are_an_id(self):
        for wanted in ("1_0", "+5", " 7", "1e2", "0x10", "-1", ""):
            reply = self.api("/library/photo", query_string={"id": wanted})
            self.assertEqual(400, reply.status_code, wanted)


if __name__ == "__main__":
    unittest.main()
