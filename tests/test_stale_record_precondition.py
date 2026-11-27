"""A save from the page never overwrites what the file holds and the page did not read (findings #533).

The details panel sends a photo's WHOLE tag list on a tag add, a Date Taken edit and carry forward, and the
server writes the list as given. A record built from the library's row (a library view) or from this
browser's half-hour cache of a scan (a folder view) can say less than the file: another program added a keyword
after the index read it, or the row was made for a photo nobody read (Suggest's shape: the path alone). So

* /api/library/photo builds the record as a folder scan builds one for a photo: the file's stamp against the
  row's, and the file read with ExifTool when they differ or the row has none;
* a write from the page names the stamp (mtime, size) of the record it was built from, and the server refuses
  with 409 "changed on disk" when the file's own stamp differs -- held and file-only writes alike -- and never
  writes. Each save is made as the page makes it, to POST /api/photos/write op `set` (tests/page_writes.py).

Real ExifTool and real Pillow on photos made here; rows as the indexer and Suggest make them
(tests/photo_rows.py); fictional names.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpus  # noqa: E402
import damaged_photos  # noqa: E402
import own_home  # noqa: E402
import page_writes  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.core import fields  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import field_values  # noqa: E402
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


def date_in(path):
    return (fields.field_values(in_file(path, "EXIF:DateTimeOriginal").get("EXIF:DateTimeOriginal")) or [""])[0]


def title_in(path):
    return (fields.field_values(in_file(path, "XMP:Title").get("XMP:Title")) or [""])[0]


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
        (row="read"), what Suggest makes of a photo never read (row="unread"), or none. The file is made once for its
        seed and fields, and kept (tests/corpus.py)."""
        wrote = {}
        if file_tags:
            wrote["Subject"] = list(file_tags)
        if caption:
            wrote["Description"] = caption
        path = corpus.noise_jpeg(os.path.join(folder or self.folder, name), seed, (160, 120), wrote, EXIFTOOL)
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
        """The panel's save from `record` (page_writes.set_from): its `title`, `tags`, `date_taken`, the `path` it names,
        and a `stamp` or `base` other than the record's, as named."""
        return self.post("/photos/write", page_writes.set_from(record, **changes))

    def assertSaved(self, reply):
        """The save succeeded, as the page reads the envelope; its photo's part of it (page_writes.saved)."""
        self.assertEqual((200, True), (reply.status_code, reply.get_json()["success"]), reply.data)
        return page_writes.saved(reply.get_json())


class TheViewReadsTheFile(Fresh):
    def test_a_keyword_another_program_added_is_in_the_record_and_a_save_keeps_it(self):
        path, photo_id = self.make("a.jpg", 1, ["Trips/Coast"])
        write_into(path, Subject=["Trips/Coast", "Trips/Lakes"])      # another program, after the index read it
        record = self.record(photo_id)
        self.assertEqual(["Trips/Coast", "Trips/Lakes"], sorted(record["tags"]))
        reply = self.save(record, tags=record["tags"] + ["Activity/Sailing"])
        self.assertSaved(reply)
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
        self.assertSaved(reply)
        self.assertEqual(["Activity/Sailing", "Trips/Coast", "Trips/Lakes"], sorted(tags_in(path)))
        self.assertEqual(("Harbour at dawn", ""), (title_in(path), caption_in(path)),
                         "the caption the file held, shown as its title, is kept: moved into the title")

    def test_a_caption_another_program_wrote_is_kept_by_a_tag_save(self):
        path, photo_id = self.make("a.jpg", 4, ["Trips/Coast"])
        write_into(path, Description="Written elsewhere")
        record = self.record(photo_id)
        self.assertEqual("Written elsewhere", record["title"])
        reply = self.save(record, tags=record["tags"] + ["Trips/Lakes"])
        self.assertSaved(reply)
        self.assertEqual(("Written elsewhere", ""), (title_in(path), caption_in(path)))

    def test_a_date_taken_edit_from_the_view_keeps_the_files_tags(self):
        path, photo_id = self.make("a.jpg", 5, ["Trips/Coast"], row="unread")
        record = self.record(photo_id)
        reply = self.save(record, date_taken="2021-05-06T07:08:09")
        self.assertSaved(reply)
        self.assertEqual(["Trips/Coast"], tags_in(path))

    def test_carry_forward_adds_to_what_the_file_holds(self):
        path, photo_id = self.make("a.jpg", 6, ["Trips/Coast"], row="unread")
        record = self.record(photo_id)
        carried = ["Activity/Sailing"]                      # the previous photo's tags, merged by the page
        reply = self.save(record, tags=sorted(set(record["tags"]) | set(carried)))
        self.assertSaved(reply)
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

    def test_the_reply_gives_the_new_stamp_and_the_next_save_with_it_passes(self):
        path, photo_id = self.make("a.jpg", 11, ["Trips/Coast"])
        record = self.record(photo_id)
        first = self.assertSaved(self.save(record, tags=["Trips/Coast", "Activity/Sailing"]))
        stamp = {"mtime": first["mtime"], "size": first["size"]}
        self.assertEqual(stamp_of(path)["size"], stamp["size"])
        again = self.save(record, tags=["Trips/Coast", "Activity/Sailing", "Trips/Lakes"], stamp=stamp,
                          base=first["base"])
        self.assertSaved(again)
        stale = self.save(record, tags=["Trips/Coast"])                # the old stamp
        self.assertEqual(409, stale.status_code)

    def test_a_rename_by_the_caption_reports_the_stamp_of_the_file_under_its_new_name(self):
        path, photo_id = self.make("a.jpg", 12, ["Trips/Coast"])
        record = self.record(photo_id)
        saved = self.assertSaved(self.save(record, title="Harbour"))
        self.assertEqual(stamp_of(saved["new_path"])["size"], saved["size"])

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
        record = {"path": path, "id": None, "title": "", "tags": ["Trips/Coast"], **stamp_of(path)}   # no row: Just look
        write_into(path, Subject=["Trips/Coast", "Trips/Lakes"])
        reply = self.save(record, tags=["Activity/Sailing"])
        self.assertEqual(409, reply.status_code)
        self.assertEqual(["Trips/Coast", "Trips/Lakes"], sorted(tags_in(path)))
        fresh = self.save({**record, "tags": ["Trips/Coast", "Trips/Lakes"], **stamp_of(path)}, tags=["Activity/Sailing"])
        self.assertSaved(fresh)

    def test_a_bulk_write_reports_each_written_photos_new_stamp(self):
        path, _photo_id = self.make("a.jpg", 15, ["Trips/Coast"])
        reply = self.post("/photos/write", page_writes.tags({path: {"add": ["Activity/Sailing"]}}))
        self.assertSaved(reply)
        stamps = reply.get_json()["written"]
        self.assertEqual({stamp_of(path)["size"]}, {each["size"] for each in stamps.values()})

    def test_a_rotate_reports_the_size_as_well_as_the_time(self):
        path, _photo_id = self.make("a.jpg", 16, [])
        reply = self.post("/photo/rotate", {"path": path, "direction": "left"})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(stamp_of(path)["size"], reply.get_json()["size"])


class TheFileIsComparedWithWhatThePageRead(Fresh):
    """The stamp is the cheap first check; under the lock, after the read of the file, its tags (a set) and caption
    are compared with `base`, what the page read (findings #551, #552)."""

    def hold_stamp(self, path):
        """Put the file's modified time back to what it was, as a copy that keeps its times does."""
        return os.stat(path).st_mtime_ns

    def test_a_same_length_tag_change_with_the_time_restored_is_refused_and_the_file_is_unchanged(self):
        path, photo_id = self.make("a.jpg", 20, ["Trips/Coast"])
        record = self.record(photo_id)
        stat = os.stat(path)
        write_into(path, Subject=["Trips/Cliff"])                       # "Trips/Coast" -> a name of the same length
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        if os.stat(path).st_size != stat.st_size:
            self.skipTest("the rewrite did not keep the size on this ExifTool: the stamp catches it")
        before = open(path, "rb").read()
        reply = self.save(record, tags=["Trips/Coast", "Activity/Sailing"])
        self.assertEqual(409, reply.status_code, reply.data)
        self.assertTrue(reply.get_json()["changed_on_disk"])
        self.assertEqual(before, open(path, "rb").read())

    def test_a_copy_that_keeps_its_times_with_other_keywords_is_refused(self):
        path, photo_id = self.make("a.jpg", 21, ["Trips/Coast"])
        record = self.record(photo_id)
        other = path + ".other"
        damaged_photos.whole_jpeg(other, seed=21)
        write_into(other, Subject=["Trips/Lakes"])
        # the copy has the original's size and time: pad it to the same length and put the time back
        stat = os.stat(path)
        with open(other, "rb") as handle:
            body = handle.read()
        original = os.path.getsize(path)
        if len(body) > original:
            self.skipTest("cannot pad a copy to the original's length")
        with open(path, "wb") as handle:
            handle.write(body + b"\0" * (original - len(body)))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        reply = self.save(record, tags=["Trips/Coast", "Activity/Sailing"])
        self.assertEqual(409, reply.status_code, reply.data)
        self.assertEqual(["Trips/Lakes"], tags_in(path))

    def test_a_photo_that_could_not_be_read_when_opened_is_flagged_and_a_save_from_it_is_refused(self):
        path, photo_id = self.make("a.jpg", 22, [], row="unread")
        with open(path, "wb") as handle:                                # empty: ExifTool cannot read it (File is empty)
            handle.write(b"")
        record = self.record(photo_id)
        self.assertTrue(record.get("unreadable"), record)
        good = path + ".good"
        damaged_photos.whole_jpeg(good, seed=22)
        write_into(good, Subject=["Trips/Coast", "Trips/Lakes"])
        os.replace(good, path)                                          # replaced by a good file between open and save
        # The page refuses the save itself, in its own words (web/common/photo-write.js UNREADABLE_SAVE,
        # tests/frontend/stale-record.test.mjs); a request naming no base read is a shape the server does not take (#3298).
        reply = self.save(record, title="", tags=["Activity/Sailing"], base=None)
        self.assertEqual((400, False), (reply.status_code, reply.get_json()["success"]), reply.data)
        self.assertIn("base must be", page_writes.why(reply.get_json()))
        # the same save with the stamp of the good file still cannot be made from a base that was not read
        again = self.save(record, title="", tags=["Activity/Sailing"], stamp=stamp_of(path), base=None)
        self.assertEqual((400, False), (again.status_code, again.get_json()["success"]), again.data)
        self.assertEqual(["Trips/Coast", "Trips/Lakes"], sorted(tags_in(path)), "the keywords are kept")

    def test_a_foreign_change_to_a_field_the_save_does_not_touch_is_not_refused(self):
        path, photo_id = self.make("a.jpg", 23, ["Trips/Coast"])
        record = self.record(photo_id)
        write_into(path, Rating=4)                                      # another program rated it; tags and caption are as read
        reply = self.save(record, tags=["Trips/Coast", "Activity/Sailing"], stamp=stamp_of(path))
        self.assertSaved(reply)
        self.assertEqual(["Activity/Sailing", "Trips/Coast"], sorted(tags_in(path)))

    def test_a_tag_save_reads_no_date_so_a_clips_headers_are_walked_only_for_a_save_of_its_date(self):
        """#3425's review: the date fields join the save's read only when its base names a date. A clip's QuickTime
        header dates are read by walking its boxes (field_values), and a walk that fails would fail a tag save."""
        path, photo_id = self.make("a.jpg", 29, ["Trips/Coast"])
        record = self.record(photo_id)
        asked = []

        def read_one(et, photo_path, names):
            asked.append(set(names))
            return real_read_one(et, photo_path, names)
        real_read_one = field_values.read_one
        with mock.patch.object(field_values, "read_one", side_effect=read_one):
            self.assertSaved(self.save(record, tags=["Trips/Coast", "Activity/Sailing"], stamp=stamp_of(path)))
            self.assertSaved(self.save(self.record(photo_id), date_taken="2021-05-06T07:08:09", stamp=stamp_of(path)))
        headers = set(fields.CLIP_HEADER_DATES)
        self.assertEqual([False, True], [bool(names & headers) for names in asked[:1] + asked[-1:]], asked)

    def test_a_date_another_program_changed_is_refused_and_one_it_left_alone_is_not(self):
        """#3425: a save of a Date Taken names the date the page read, as a save of tags names its tags."""
        path, photo_id = self.make("a.jpg", 30, ["Trips/Coast"])
        write_into(path, DateTimeOriginal="2020:01:02 03:04:05")
        record = self.record(photo_id)
        self.assertEqual("2020:01:02 03:04:05", record["taken"])
        write_into(path, Rating=4)                                      # the bake: a field that is not a date
        self.assertSaved(self.save(record, date_taken="2021-05-06T07:08:09", stamp=stamp_of(path)))
        self.assertEqual("2021:05:06 07:08:09", date_in(path))
        write_into(path, DateTimeOriginal="2022:02:02 02:02:02")       # another tab, after the page read it
        reply = self.save(record, date_taken="2023-03-03T03:03:03", stamp=stamp_of(path),
                          base=page_writes.base_of(record) | {"date_taken": "2021:05:06 07:08:09"})
        self.assertEqual(409, reply.status_code, reply.data)
        self.assertEqual("2022:02:02 02:02:02", date_in(path), "the date it holds is not written over")

    def test_a_photo_the_page_read_without_a_date_is_refused_when_another_program_dated_it(self):
        path, photo_id = self.make("a.jpg", 31, ["Trips/Coast"])
        record = self.record(photo_id)
        self.assertFalse(record["taken"])
        write_into(path, DateTimeOriginal="2020:01:02 03:04:05")
        reply = self.save(record, date_taken="2021-05-06T07:08:09", stamp=stamp_of(path))
        self.assertEqual(409, reply.status_code, reply.data)
        self.assertEqual("2020:01:02 03:04:05", date_in(path))

    def test_a_foreign_tag_is_refused_with_the_stamp_restored_to_what_the_page_has(self):
        path, photo_id = self.make("a.jpg", 24, ["Trips/Coast"])
        record = self.record(photo_id)
        write_into(path, Subject=["Trips/Coast", "Trips/Lakes"])
        reply = self.save(record, tags=["Trips/Coast"], stamp=stamp_of(path))      # even a stamp that matches
        self.assertEqual(409, reply.status_code, reply.data)
        self.assertEqual(["Trips/Coast", "Trips/Lakes"], sorted(tags_in(path)))

    def test_a_foreign_caption_is_refused_too(self):
        path, photo_id = self.make("a.jpg", 25, ["Trips/Coast"], caption="As read")
        record = self.record(photo_id)
        write_into(path, Description="Changed elsewhere")
        reply = self.save(record, stamp=stamp_of(path))
        self.assertEqual(409, reply.status_code, reply.data)
        self.assertEqual("Changed elsewhere", caption_in(path))

    def test_the_reply_gives_the_base_the_next_save_names(self):
        path, photo_id = self.make("a.jpg", 26, ["Trips/Coast"], caption="One")
        record = self.record(photo_id)
        first = self.assertSaved(self.save(record, tags=["Trips/Coast", "Activity/Sailing"], title="Two",
                                           stamp=stamp_of(path)))
        base = first["base"]
        self.assertEqual(["Activity/Sailing", "Trips/Coast"], sorted(base["tags"]))
        self.assertEqual("Two", base["title"])
        stamp = {"mtime": first["mtime"], "size": first["size"]}
        again = self.save(record, path=first["new_path"], title="Two", tags=base["tags"] + ["Trips/Lakes"], stamp=stamp,
                          base=base)
        self.assertSaved(again)

    def test_the_folder_views_stale_cache_is_refused_by_the_base_alone(self):
        path, _id = self.make("a.jpg", 27, ["Trips/Coast"])
        scan = self.api("/folder/scan", query_string={"path": self.folder}).get_json()
        record = [each for each in scan if each["path"] == path][0]
        write_into(path, Subject=["Trips/Coast", "Trips/Lakes"])
        reply = self.save(record, tags=["Trips/Coast"], stamp=stamp_of(path))
        self.assertEqual(409, reply.status_code, reply.data)

    def test_a_malformed_base_is_a_400(self):
        path, photo_id = self.make("a.jpg", 29, ["Trips/Coast"])
        record = self.record(photo_id)
        for base in ("x", {"tags": "Trips/Coast"}, {"tags": [1]}, {"tags": [], "title": 3}):
            reply = self.save(record, tags=[], base=base)
            self.assertEqual(400, reply.status_code, base)

    def test_bulk_tags_never_sends_a_whole_list_and_is_unchanged(self):
        path, _id = self.make("a.jpg", 30, ["Trips/Coast"])
        write_into(path, Subject=["Trips/Coast", "Trips/Lakes"])
        reply = self.post("/photos/write", page_writes.tags({path: {"add": ["Activity/Sailing"]}}))
        self.assertSaved(reply)
        self.assertEqual(["Activity/Sailing", "Trips/Coast", "Trips/Lakes"], sorted(tags_in(path)))

    def test_the_extra_request_is_small(self):
        base = {"tags": ["Trips/Coast/Harbour", "People/Wren Halloway", "Activity/Sailing"], "title": "Harbour at dawn"}
        import json
        self.assertLess(len(json.dumps({"base": base})), 200, "a photo's base is about a hundred bytes")


class ACaptionHeldAsATitleOnly(Fresh):
    """A photo whose caption another program put in its title fields alone (XMP:Title, IPTC:ObjectName; no
    Description): the page shows that title as its caption and names it in `base`. The save read the description
    fields only, found no caption, and refused every save from the page as changed on disk -- the owner, naming face
    after face of a group photo in a library view, was told the photo changed on disk when nothing had written it."""

    def make_titled(self, name, seed, file_tags):
        path = os.path.join(self.folder, name)
        damaged_photos.whole_jpeg(path, seed=seed)
        write_into(path, Subject=list(file_tags), **{"XMP:Title": "Pier at noon", "IPTC:ObjectName": "Pier at noon"})
        conn = db.connect(self.library.path)
        try:
            photo_id = photo_rows.add_read(conn, path, {"XMP:Subject": list(file_tags), "XMP:Title": "Pier at noon",
                                                        "IPTC:ObjectName": "Pier at noon"})
            conn.commit()
        finally:
            conn.close()
        return path, photo_id

    def test_naming_face_after_face_puts_each_person_on_the_photo(self):
        path, photo_id = self.make_titled("a.jpg", 41, ["People/Orla Penhallow"])
        record = self.record(photo_id)
        self.assertEqual("Pier at noon", record["title"])
        # What the page sends for each face named (web/common/photo-write.js postPhotoMetadata) and takes from each reply (takeStamp).
        stamp = {"mtime": record["mtime"], "size": record["size"]}
        base = {"tags": record["tags"], "title": record["title"]}
        where, tags = record["path"], list(record["tags"])
        for person in ("People/Tamsin Okonkwo", "People/Ewan Marchetti", "People/Bryony Sallis"):
            tags = tags + [person]
            body = self.assertSaved(self.save(record, path=where, tags=tags, stamp=stamp, base=base))
            where, base = body["new_path"], body["base"]
            stamp = {"mtime": body["mtime"], "size": body["size"]}
        self.assertEqual(sorted(tags), sorted(tags_in(where)))

    def test_a_title_another_program_changed_after_the_page_read_it_is_still_refused(self):
        path, photo_id = self.make_titled("a.jpg", 42, ["Trips/Coast"])
        record = self.record(photo_id)
        write_into(path, **{"XMP:Title": "Pier at dusk", "IPTC:ObjectName": "Pier at dusk"})
        reply = self.save(record, tags=["Trips/Coast", "Trips/Lakes"], stamp=stamp_of(path))
        self.assertEqual(409, reply.status_code, reply.data)
        self.assertEqual(["Trips/Coast"], tags_in(path))


class TheServersCacheCarriesTheFilesStamp(Fresh):
    def test_after_a_bulk_write_the_cached_scan_has_the_new_stamp_and_a_save_from_it_passes(self):
        a, _ida = self.make("a.jpg", 40, ["Trips/Coast"])
        b, _idb = self.make("b.jpg", 41, ["Activity/Sailing"])
        scan = self.api("/folder/scan", query_string={"path": self.folder}).get_json()       # the folder is cached
        reply = self.post("/photos/write", page_writes.tags({a: {"add": ["Trips/Lakes"]}}))
        self.assertSaved(reply)
        again = self.api("/folder/scan", query_string={"path": self.folder}).get_json()      # served from the cache
        record = [each for each in again if each["path"] == a][0]
        self.assertEqual(stamp_of(a)["size"], record["size"])
        self.assertAlmostEqual(stamp_of(a)["mtime"], record["mtime"], places=3)
        saved = self.save(record, tags=record["tags"] + ["Activity/Sailing"])
        self.assertSaved(saved)
        self.assertEqual(2, len(scan))
        self.assertTrue(os.path.exists(b))


class TheIdIsReadStrictly(Fresh):
    def test_only_digits_are_an_id(self):
        for wanted in ("1_0", "+5", " 7", "1e2", "0x10", "-1", ""):
            reply = self.api("/library/photo", query_string={"id": wanted})
            self.assertEqual(400, reply.status_code, wanted)


if __name__ == "__main__":
    unittest.main()
