"""Just look lets the owner edit the photo FILES of a folder the library does not hold.

The owner opened a new library, chose a folder for it, and was offered Just look; "that
should still let me change captions on photos, do smart renames, and add/update tags if it
does not go into an index" (2026-10-02), and then rotate, delete and the date changes:
none of them needs the database. What stayed refused (test_writes_only_in_folders_held) is
what a write made of a row -- and a row makes its folder the library's (2026-09-28).

So for a photo in a folder the library does not hold, a caption or a tag (one photo or
many), a Smart Rename, a turn, a Date Taken, a time shift and a delete write the FILE and
nothing of the library's: no row, no journal change, nothing derived, no thumbnail, no
damaged-photo record, nothing for sync or the watcher. Every test here dumps every table of the
library before and after (tests/library_dump.py) and says it is the same. Which photos
these are is the library's own answer, per photo, at the time of the write -- never a flag
the page sends. In a folder the library holds part of, in one request, the held photos
write as always and the others to their files only, and the reply says how many each.

Real ExifTool and real Pillow, on photos made here; the Recycle Bin is stood in for.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import library_dump  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.core import fields, paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import field_values, images  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.files.metadata import MetadataExtractor  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as settings_service  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()

DATE = "2019:06:15 10:30:00"


def in_file(path, *names):
    """What the photo's file holds of `names`, as ExifTool answers it."""
    with ExifToolSession(executable=EXIFTOOL) as et:
        return et.get_tags([path], tags=list(names))[0]


def tags_in(path):
    return fields.field_values(in_file(path, "XMP:Subject").get("XMP:Subject"))


def caption_in(path):
    return (fields.field_values(in_file(path, "XMP:Description").get("XMP:Description")) or [""])[0]


def write_into(path, **tags):
    with ExifToolSession(executable=EXIFTOOL) as et:
        et.set_tags([path], tags=tags, params=["-overwrite_original"])


def stamp(path):
    stat = os.stat(path)
    return (stat.st_mtime_ns, stat.st_size)


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class Base(unittest.TestCase):
    """harbour: Pictures/Regatta holds two photos (rows, made as the indexer makes them); its
    subfolder Scans holds one the library does not hold; Share/Lighthouse, another folder,
    holds three. The library is `library`."""

    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.library = Library(self.home.library("library.db"))
        self.addCleanup(tagpup_routes.folders.forget, self.library)
        self.regatta = os.path.join(self.home.root, "Pictures", "Regatta")
        self.scans = os.path.join(self.regatta, "Scans")
        self.lighthouse = os.path.join(self.home.root, "Share", "Lighthouse")
        self.held = [damaged_photos.whole_jpeg(os.path.join(self.regatta, name), seed=n) and
                     os.path.join(self.regatta, name)
                     for n, name in enumerate(["regatta_01.jpg", "regatta_02.jpg"], start=1)]
        self.scan = os.path.join(self.scans, "scan_01.jpg")
        damaged_photos.whole_jpeg(self.scan, seed=5)
        self.loose = [os.path.join(self.lighthouse, "IMG_000%d.jpg" % n) for n in (1, 2, 3)]
        for n, path in enumerate(self.loose, start=10):
            damaged_photos.whole_jpeg(path, seed=n)
        conn = db.connect(self.library.path)
        try:
            for path in self.held:
                photo_rows.add_read(conn, path, {"XMP:Subject": ["Trips/Regatta"]})
            conn.commit()
        finally:
            conn.close()
        self.before = library_dump.dump(self.library.path)

    def post(self, route, body):
        return self.client.post("/library/api" + route, json=body)

    def assert_library_unchanged(self):
        after = library_dump.dump(self.library.path)
        self.assertEqual(sorted(self.before), sorted(after), "a table appeared or went")
        for table in self.before:
            self.assertEqual(self.before[table], after[table], "the library's %s changed" % table)

    def rows(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def not_held(self, *photo_paths):
        return library_actions.not_held(self.library, list(photo_paths))


class TheLibraryIsDumpedWhole(Base):
    def test_the_dump_sees_the_tables_a_write_could_change(self):
        for table in ("photos", "changes", "change_files", "change_rows", "generations", "settings", "job_runs",
                      "damaged_files", "embeddings", "faces", "added_folders", "tag_taxonomy", "photo_tags",
                      "photo_people", "suggestions", "sync_runs", "roots"):
            self.assertIn(table, self.before, "the dump does not look at %s" % table)
        self.assertEqual(2, len(self.before["photos"]))


class AnEditWritesTheFileOnly(Base):
    def test_a_caption_and_tags_on_one_photo(self):
        photo = self.loose[0]
        reply = self.post("/photo/save-metadata", {"path": photo, "title": "Lamp room",
                                                  "tags": ["Trips/Lighthouse", "People/Rowan Thackeray"]})
        self.assertEqual(200, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertEqual((1, 0), (body["file_only"], body["with_rows"]))
        self.assertEqual(["Trips/Lighthouse", "People/Rowan Thackeray"], tags_in(photo))
        self.assertEqual("Lamp room", caption_in(photo))
        self.assert_library_unchanged()
        self.assertEqual([self.lighthouse], self.not_held(photo), "the write made the folder the library's")

    def test_a_person_not_in_the_tree_is_written_as_given_and_the_tree_is_not_grown(self):
        photo = self.loose[0]
        reply = self.post("/photo/save-metadata", {"path": photo, "title": "", "tags": ["People/Fictional Newcomer"]})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(["People/Fictional Newcomer"], tags_in(photo))
        self.assert_library_unchanged()

    def test_a_forged_flag_changes_nothing_either_way(self):
        # The server decides by the library's folders: not by anything the page says.
        reply = self.post("/photo/save-metadata", {"path": self.loose[0], "title": "x", "tags": ["A/B"],
                                                  "just_looking": False, "file_only": False, "held": True})
        self.assertEqual((200, 1, 0), (reply.status_code, reply.get_json()["file_only"], reply.get_json()["with_rows"]))
        self.assert_library_unchanged()
        reply = self.post("/photo/save-metadata", {"path": self.held[0], "title": "x", "tags": ["A/B"],
                                                  "just_looking": True, "file_only": True, "held": False})
        self.assertEqual((200, 0, 1), (reply.status_code, reply.get_json()["file_only"], reply.get_json()["with_rows"]))
        self.assertEqual(["A/B"], __import__("json").loads(self.rows("SELECT tags FROM photos WHERE path = ?",
                                                                    (self.held[0],))[0][0]))

    def test_tags_added_and_taken_off_many(self):
        write_into(self.loose[0], Subject=["Old/Tag", "Keep/Me"])
        self.before = library_dump.dump(self.library.path)
        reply = self.post("/photos/bulk-tags", {"paths": self.loose[:2], "add_tags": ["Trips/Lighthouse"],
                                               "remove_tags": ["Old/Tag"]})
        self.assertEqual(200, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertEqual((2, 0), (body["file_only"], body["with_rows"]))
        self.assertEqual(["Keep/Me", "Trips/Lighthouse"], tags_in(self.loose[0]))
        self.assertEqual(["Trips/Lighthouse"], tags_in(self.loose[1]))
        self.assertEqual(sorted(self.loose[:2]), sorted(body["written"]))
        self.assert_library_unchanged()

    def test_a_tag_that_may_not_be_set_is_refused_and_nothing_is_written(self):
        before = stamp(self.loose[0])
        reply = self.post("/photos/bulk-tags", {"paths": self.loose[:1], "add_tags": ["Places|Harbour"],
                                               "remove_tags": []})
        self.assertEqual(400, reply.status_code, reply.data)
        self.assertEqual(before, stamp(self.loose[0]))
        self.assert_library_unchanged()

    def test_a_rotate_turns_the_file_and_leaves_every_thumbnail_alone(self):
        thumbs = os.path.join(self.home.root, "thumbs")
        os.makedirs(thumbs)
        with open(os.path.join(thumbs, "someone elses.jpg"), "wb") as handle:
            handle.write(b"a thumbnail")
        cache = sorted(os.listdir(self.library.thumbs)) if os.path.isdir(self.library.thumbs) else []
        reply = self.post("/photo/rotate", {"path": self.loose[0], "direction": "left"})
        self.assertEqual(200, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertEqual((1, 0), (body["file_only"], body["with_rows"]))
        self.assertEqual(8, int(in_file(self.loose[0], "EXIF:Orientation")["EXIF:Orientation"]))
        self.assertEqual(os.stat(self.loose[0]).st_mtime, body["mtime"])
        self.assertEqual(cache, sorted(os.listdir(self.library.thumbs)) if os.path.isdir(self.library.thumbs) else [])
        self.assertEqual(["someone elses.jpg"], os.listdir(thumbs))
        self.assert_library_unchanged()

    def test_a_delete_goes_to_the_recycle_bin_and_says_nothing_else_changed(self):
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: os.remove(p) or True):
            reply = self.post("/photo/delete", {"path": self.loose[0]})
        self.assertEqual(200, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertEqual((1, 0), (body["file_only"], body["with_rows"]))
        self.assertIn("Recycle Bin", body["message"])
        self.assertIn("Nothing in library changed", body["message"])
        self.assertFalse(os.path.exists(self.loose[0]))
        self.assert_library_unchanged()

    def test_a_failed_delete_deletes_nothing_and_says_so(self):
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", return_value=False):
            reply = self.post("/photo/delete", {"path": self.loose[0]})
        self.assertEqual(500, reply.status_code, reply.data)
        self.assertTrue(os.path.exists(self.loose[0]))
        self.assert_library_unchanged()

    def test_a_date_taken_on_one_photo(self):
        reply = self.post("/photo/save-metadata", {"path": self.loose[0], "title": "", "tags": [],
                                                  "date_taken": "2018-03-04T09:08:07"})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual("2018:03:04 09:08:07", in_file(self.loose[0], "EXIF:DateTimeOriginal")["EXIF:DateTimeOriginal"])
        self.assert_library_unchanged()

    def test_a_time_shift_over_photos_with_and_without_the_date(self):
        write_into(self.loose[0], DateTimeOriginal=DATE)
        write_into(self.loose[1], DateTimeOriginal="2020:01:01 00:00:00")
        self.before = library_dump.dump(self.library.path)
        untouched = stamp(self.loose[2])
        reply = self.post("/folder/time-shift", {"folder_path": self.lighthouse, "camera_model": fields.ALL_CAMERAS,
                                                "shift_minutes": 90})
        self.assertEqual(200, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertEqual((2, 3, 2, 0), (body["updated_count"], body["requested_count"], body["file_only"],
                                       body["with_rows"]))
        self.assertEqual("2019:06:15 12:00:00", in_file(self.loose[0], "EXIF:DateTimeOriginal")["EXIF:DateTimeOriginal"])
        self.assertEqual("2020:01:01 01:30:00", in_file(self.loose[1], "EXIF:DateTimeOriginal")["EXIF:DateTimeOriginal"])
        self.assertEqual(untouched, stamp(self.loose[2]), "a photo with no date was written")
        self.assert_library_unchanged()

    def test_a_scan_of_the_folder_changes_nothing_either(self):
        reply = self.client.get("/library/api/folder/scan", query_string={"path": self.lighthouse})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assert_library_unchanged()


class ASmartRename(Base):
    def rename(self, photo_paths, grouping="Lighthouse"):
        return self.post("/folder/rename-photos", {"folder_path": self.lighthouse, "photo_paths": photo_paths,
                                                  "grouping": grouping})

    def names(self, folder=None):
        return sorted(os.listdir(folder or self.lighthouse))

    def test_renames_the_files_and_writes_the_name_each_had(self):
        for n, path in enumerate(self.loose, start=1):
            write_into(path, Description="Caption %d" % n)
        self.before = library_dump.dump(self.library.path)
        reply = self.rename(self.loose)
        self.assertEqual(200, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertEqual((3, 0, 0), (body["file_only"], body["with_rows"], body["index_rows_moved"]))
        now = self.names()
        self.assertEqual(3, len(now))
        self.assertTrue(all(name.startswith("Lighthouse - ") for name in now), now)
        kept = sorted(str(in_file(os.path.join(self.lighthouse, name), "XMP-xmpMM:PreservedFileName").get(
            "XMP-xmpMM:PreservedFileName") or in_file(os.path.join(self.lighthouse, name), "XMP:PreservedFileName").get(
            "XMP:PreservedFileName")) for name in now)
        self.assertEqual(["IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg"], kept)
        self.assert_library_unchanged()

    def test_a_file_in_the_way_is_moved_aside_as_always(self):
        in_the_way = damaged_photos.whole_jpeg(os.path.join(self.lighthouse, "Lighthouse - 1.jpg"), seed=99) and \
            os.path.join(self.lighthouse, "Lighthouse - 1.jpg")
        self.before = library_dump.dump(self.library.path)
        reply = self.rename(self.loose)
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertIn("Lighthouse - 1_conflict_1.jpg", self.names())
        self.assertTrue(os.path.exists(in_the_way))
        self.assert_library_unchanged()

    def test_one_interrupted_part_way_puts_every_name_back(self):
        real = os.rename
        calls = []

        def failing(source, target, *args, **kwargs):
            calls.append(source)
            if len(calls) == 4:
                raise OSError(5, "the share went away")
            return real(source, target, *args, **kwargs)

        original = self.names()
        with mock.patch("os.rename", failing):
            reply = self.rename(self.loose)
        self.assertEqual(500, reply.status_code, reply.data)
        self.assertEqual(original, self.names(), "a name was left changed, or a temporary one left behind")
        self.assert_library_unchanged()

    def test_a_photo_that_does_not_decode_refuses_the_whole_rename(self):
        damaged = damaged_photos.all_zeros(os.path.join(self.lighthouse, "IMG_0004.jpg"), size=images.ZERO_TAIL + 100)
        original = self.names()
        self.before = library_dump.dump(self.library.path)
        reply = self.rename(self.loose + [damaged])
        self.assertEqual(409, reply.status_code, reply.data)
        self.assertIn("IMG_0004.jpg", reply.get_json()["error"])
        self.assertEqual(original, self.names())
        self.assert_library_unchanged()

    def test_the_caption_then_renames_a_photo_that_was_renamed_before(self):
        write_into(self.loose[0], Description="First")
        self.assertEqual(200, self.rename(self.loose[:1]).status_code)
        renamed = [os.path.join(self.lighthouse, name) for name in self.names() if name.startswith("Lighthouse")][0]
        self.before = library_dump.dump(self.library.path)
        reply = self.post("/photo/save-metadata", {"path": renamed, "title": "Second", "tags": []})
        self.assertEqual(200, reply.status_code, reply.data)
        new_path = reply.get_json()["new_path"]
        self.assertNotEqual(paths.key(renamed), paths.key(new_path))
        self.assertTrue(os.path.exists(new_path))
        self.assertFalse(os.path.exists(renamed))
        self.assertEqual("Second", caption_in(new_path))
        self.assert_library_unchanged()


class ADamagedPhotoIsNeverWritten(Base):
    def setUp(self):
        super().setUp()
        self.cut = damaged_photos.all_zeros(os.path.join(self.lighthouse, "cut short.jpg"), size=images.ZERO_TAIL + 100)
        self.zeros = damaged_photos.second_half_zeros(os.path.join(self.lighthouse, "incomplete.jpg"))
        self.before = library_dump.dump(self.library.path)

    def test_one_photo_is_refused_for_each_write_and_nothing_is_recorded(self):
        for photo in (self.cut, self.zeros):
            before = stamp(photo)
            for route, body in (("/photo/save-metadata", {"path": photo, "title": "x", "tags": ["A/B"]}),
                                ("/photo/rotate", {"path": photo, "direction": "left"})):
                reply = self.post(route, body)
                self.assertEqual(409, reply.status_code, (route, reply.data))
                self.assertIn("nothing is written", reply.get_json()["error"])
            self.assertEqual(before, stamp(photo))
        self.assert_library_unchanged()

    def test_a_bulk_write_skips_it_and_writes_the_rest(self):
        reply = self.post("/photos/bulk-tags", {"paths": [self.loose[0], self.cut, self.zeros],
                                               "add_tags": ["Trips/Lighthouse"], "remove_tags": []})
        self.assertEqual(200, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertEqual((2, 1), (body["skipped_damaged"], body["file_only"]))
        self.assertEqual(sorted([self.cut, self.zeros]), sorted(each["path"] for each in body["skipped"]))
        self.assertEqual(["Trips/Lighthouse"], tags_in(self.loose[0]))
        self.assertEqual(images.ZERO_TAIL + 100, os.path.getsize(self.cut), "a zero-filled file was written into")
        self.assert_library_unchanged()

    def test_a_time_shift_is_refused_whole(self):
        write_into(self.loose[0], DateTimeOriginal=DATE)
        self.before = library_dump.dump(self.library.path)
        reply = self.post("/folder/time-shift", {"folder_path": self.lighthouse, "camera_model": fields.ALL_CAMERAS,
                                                "shift_minutes": 5})
        self.assertEqual(409, reply.status_code, reply.data)
        self.assertEqual(DATE, in_file(self.loose[0], "EXIF:DateTimeOriginal")["EXIF:DateTimeOriginal"])
        self.assert_library_unchanged()

    def test_a_damaged_photo_may_still_be_deleted(self):
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: os.remove(p) or True):
            reply = self.post("/photo/delete", {"path": self.cut})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertFalse(os.path.exists(self.cut))
        self.assert_library_unchanged()


class AWriteThatFailsForOneFileOfMany(Base):
    def test_reports_exactly_what_was_written(self):
        real = field_values.write
        calls = []

        def second_fails(et, photo_path, values):
            calls.append(photo_path)
            if len(calls) == 2:
                raise RuntimeError("the share went away")
            return real(et, photo_path, values)

        with mock.patch("tagpup.files.field_values.write", second_fails):
            reply = self.post("/photos/bulk-tags", {"paths": self.loose, "add_tags": ["Trips/Lighthouse"],
                                                   "remove_tags": []})
        self.assertEqual(500, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertEqual([paths.stored(self.loose[0])], list(body["written"]), "an attempt was reported as a write")
        self.assertEqual(["Trips/Lighthouse"], tags_in(self.loose[0]))
        self.assertEqual([[], []], [tags_in(self.loose[1]), tags_in(self.loose[2])])
        self.assert_library_unchanged()

    def test_an_interrupted_time_shift_reports_what_changed(self):
        from tagpup.services import photos as photo_actions
        for n, path in enumerate(self.loose):
            write_into(path, DateTimeOriginal="2019:06:1%d 10:00:00" % n)
        self.before = library_dump.dump(self.library.path)
        real = field_values.write
        calls = []

        def second_fails(et, photo_path, values):
            calls.append(photo_path)
            if len(calls) == 2:
                raise RuntimeError("the share went away")
            return real(et, photo_path, values)

        with mock.patch("tagpup.files.field_values.write", second_fails):
            result = photo_actions.shift_date_taken(self.library, self.loose, 60, EXIFTOOL)
        self.assertEqual((3, 2, 1), (result.attempted, result.changed, len(result.errors)))
        self.assertIn("IMG_0002.jpg", result.errors[0][0])
        self.assertEqual("2019:06:10 11:00:00", in_file(self.loose[0], "EXIF:DateTimeOriginal")["EXIF:DateTimeOriginal"])
        self.assertEqual("2019:06:11 10:00:00", in_file(self.loose[1], "EXIF:DateTimeOriginal")["EXIF:DateTimeOriginal"])
        self.assertEqual("2019:06:12 11:00:00", in_file(self.loose[2], "EXIF:DateTimeOriginal")["EXIF:DateTimeOriginal"])
        self.assert_library_unchanged()

    def test_a_file_changed_outside_since_it_was_read_is_not_overwritten(self):
        from tagpup.services import tagging
        real_read = field_values.read
        seen = []

        def changes_after_the_plan(et, photo_paths, wanted, *args, **kwargs):
            found = real_read(et, photo_paths, wanted, *args, **kwargs)
            if not seen:
                seen.append(True)
                write_into(self.loose[0], Subject=["Someone/Elses"])
            return found

        with mock.patch("tagpup.files.field_values.read", changes_after_the_plan):
            result = tagging.change_tags(self.library, self.loose[:1], ["Trips/Lighthouse"], [], EXIFTOOL)
        self.assertFalse(result.ok)
        self.assertEqual(0, result.changed)
        self.assertEqual(["Someone/Elses"], tags_in(self.loose[0]), "an edit made elsewhere was overwritten")
        self.assert_library_unchanged()


class AFolderTheLibraryHoldsPartOf(Base):
    """Regatta is held; its subfolder Scans is not: 'holds 2 of 3'."""

    def test_one_request_writes_each_photo_the_way_its_folder_asks(self):
        reply = self.post("/photos/bulk-tags", {"paths": [self.held[0], self.scan], "add_tags": ["Trips/Regatta 2"],
                                               "remove_tags": []})
        self.assertEqual(200, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertEqual((1, 1), (body["file_only"], body["with_rows"]))
        self.assertEqual(["Trips/Regatta 2"], tags_in(self.held[0]))
        self.assertEqual(["Trips/Regatta 2"], tags_in(self.scan))
        row = self.rows("SELECT tags FROM photos WHERE path = ?", (self.held[0],))
        self.assertIn("Trips/Regatta 2", row[0][0], "the held photo's row was not told")
        self.assertEqual([], self.rows("SELECT id FROM photos WHERE path = ?", (self.scan,)), "the other got a row")
        self.assertEqual([self.scans], self.not_held(self.scan))
        self.assertEqual(2, self.rows("SELECT COUNT(*) FROM photos")[0][0])

    def test_a_time_shift_over_the_folder_and_its_subfolder(self):
        for path in self.held + [self.scan]:
            write_into(path, DateTimeOriginal=DATE)
        self.before = library_dump.dump(self.library.path)
        reply = self.post("/folder/time-shift", {"folder_path": self.regatta, "camera_model": fields.ALL_CAMERAS,
                                                "shift_minutes": 60})
        self.assertEqual(200, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertEqual((3, 1, 2), (body["updated_count"], body["file_only"], body["with_rows"]))
        for path in self.held + [self.scan]:
            self.assertEqual("2019:06:15 11:30:00", in_file(path, "EXIF:DateTimeOriginal")["EXIF:DateTimeOriginal"])
        self.assertEqual(2, self.rows("SELECT COUNT(*) FROM photos")[0][0])
        self.assertEqual([self.scans], self.not_held(self.scan))
        self.assertEqual(1, self.rows("SELECT COUNT(*) FROM changes WHERE operation = 'time shift'")[0][0],
                         "the held photos' write is one change; the other's is none")

    def test_a_smart_rename_of_both_moves_the_held_rows_and_only_the_files_of_the_other(self):
        reply = self.post("/folder/rename-photos", {"folder_path": self.regatta, "grouping": "Regatta",
                                                   "photo_paths": self.held + [self.scan]})
        self.assertEqual(200, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertEqual((1, 2, 2), (body["file_only"], body["with_rows"], body["index_rows_moved"]))
        self.assertEqual(["Regatta - 3.jpg"], os.listdir(self.scans))
        self.assertEqual([], [row for row in self.rows("SELECT path FROM photos") if "Scans" in row[0]])
        names = sorted(os.path.basename(row[0]) for row in self.rows("SELECT path FROM photos"))
        self.assertEqual(["Regatta - 1.jpg", "Regatta - 2.jpg"], names)

    def test_the_other_part_failing_leaves_the_first_renamed_and_says_so(self):
        from tagpup.files import names as file_names
        real = file_names.rename_all
        calls = []

        def second_part_fails(renames, aside=None):
            calls.append(renames)
            if len(calls) == 2:
                raise file_names.RenameFailed(OSError("the share went away"), [])
            return real(renames, aside=aside)

        with mock.patch.object(file_names, "rename_all", second_part_fails):
            reply = self.post("/folder/rename-photos", {"folder_path": self.regatta, "grouping": "Regatta",
                                                       "photo_paths": self.held + [self.scan]})
        self.assertEqual(500, reply.status_code, reply.data)
        self.assertIn("were renamed, and stay so", reply.get_json()["error"])
        self.assertEqual(["scan_01.jpg"], os.listdir(self.scans), "the part that failed was put back")
        self.assertEqual(["Regatta - 1.jpg", "Regatta - 2.jpg"],
                         sorted(name for name in os.listdir(self.regatta) if name.endswith(".jpg")))
        self.assertEqual(["Regatta - 1.jpg", "Regatta - 2.jpg"],
                         sorted(os.path.basename(row[0]) for row in self.rows("SELECT path FROM photos")),
                         "the held rows follow the files that were renamed")

    def test_a_rotate_of_a_held_photo_still_has_its_rows(self):
        reply = self.post("/photo/rotate", {"path": self.held[0], "direction": "right"})
        self.assertEqual((200, 0, 1), (reply.status_code, reply.get_json()["file_only"], reply.get_json()["with_rows"]))


class TheFolderIsAddedWhileAFileOnlyWriteIsInFlight(Base):
    def test_the_photo_gets_its_row_from_the_indexer_and_never_from_the_write(self):
        real = field_values.write
        added = []

        def adds_the_folder_first(et, photo_path, values):
            if not added:
                added.append(library_actions.record_added(self.library, [self.lighthouse]))
            return real(et, photo_path, values)

        with mock.patch("tagpup.files.field_values.write", adds_the_folder_first):
            reply = self.post("/photo/save-metadata", {"path": self.loose[0], "title": "Lamp room",
                                                      "tags": ["Trips/Lighthouse"]})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual([1], added)
        # The write decided at its start: the file only, and no half state.
        self.assertEqual((1, 0), (reply.get_json()["file_only"], reply.get_json()["with_rows"]))
        self.assertEqual([], self.rows("SELECT id FROM photos WHERE path = ?", (self.loose[0],)))
        self.assertEqual(["Trips/Lighthouse"], tags_in(self.loose[0]))
        self.assertEqual([], self.not_held(self.loose[0]), "the folder was added")
        # The indexer, now, reads the file with what the owner wrote into it.
        conn = db.connect(self.library.path)
        try:
            record = MetadataExtractor(exiftool_path=EXIFTOOL).batch_read([self.loose[0]])[0]
            store_photos.record_indexed(conn, self.loose[0], record)
            conn.commit()
        finally:
            conn.close()
        tags, captions = self.rows("SELECT tags, captions FROM photos WHERE path = ?", (self.loose[0],))[0]
        self.assertIn("Trips/Lighthouse", tags)
        self.assertIn("Lamp room", captions)
        # And the next write is a held one.
        reply = self.post("/photo/save-metadata", {"path": self.loose[0], "title": "Lamp room 2",
                                                  "tags": ["Trips/Lighthouse"]})
        self.assertEqual((1, 0), (reply.get_json()["with_rows"], reply.get_json()["file_only"]))


class AFolderEditedWhileJustLookingIsIndexedAsEdited(Base):
    def test_the_rows_hold_the_tags_captions_and_names(self):
        write_into(self.loose[0], Description="First")
        self.assertEqual(200, self.post("/photos/bulk-tags", {"paths": self.loose, "add_tags": ["Trips/Lighthouse"],
                                                             "remove_tags": []}).status_code)
        self.assertEqual(200, self.post("/photo/save-metadata", {
            "path": self.loose[1], "title": "Stairs", "tags": ["Trips/Lighthouse", "People/Rowan Thackeray"]
        }).status_code)
        reply = self.post("/folder/rename-photos", {"folder_path": self.lighthouse, "grouping": "Lighthouse",
                                                   "photo_paths": self.loose})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assert_library_unchanged()
        # Added, and indexed as the indexer indexes: each file read, each row recorded.
        self.assertEqual(1, library_actions.record_added(self.library, [self.lighthouse]))
        found = sorted(os.path.join(self.lighthouse, name) for name in os.listdir(self.lighthouse))
        conn = db.connect(self.library.path)
        try:
            for record in MetadataExtractor(exiftool_path=EXIFTOOL).batch_read(found):
                store_photos.record_indexed(conn, record["path"], record)
            conn.commit()
        finally:
            conn.close()
        rows = {os.path.basename(path): (tags, captions) for path, tags, captions in
                self.rows("SELECT path, tags, captions FROM photos") if "Lighthouse" in path}
        self.assertEqual(3, len(rows))
        self.assertTrue(all(name.startswith("Lighthouse - ") for name in rows), rows)
        self.assertTrue(all("Trips/Lighthouse" in tags for tags, _captions in rows.values()))
        self.assertEqual(1, sum("Stairs" in captions for _tags, captions in rows.values()))
        self.assertEqual(1, sum("People/Rowan Thackeray" in tags for tags, _captions in rows.values()))


class TwoTabs(Base):
    def test_two_edits_from_two_pages_both_land(self):
        one = self.post("/photos/bulk-tags", {"paths": self.loose, "add_tags": ["Trips/Lighthouse"], "remove_tags": []})
        other = self.post("/photos/bulk-tags", {"paths": self.loose, "add_tags": ["Trips/Harbour"], "remove_tags": []})
        self.assertEqual((200, 200), (one.status_code, other.status_code))
        self.assertEqual(["Trips/Lighthouse", "Trips/Harbour"], tags_in(self.loose[0]))
        self.assert_library_unchanged()


class WhatStaysRefused(Base):
    """The CLI's write of suggestions needs the library's database: still refused in a folder it does
    not hold. (Suggest and Apply All are not: tests/test_analyse_only_suggest.py.)"""

    def test_the_cli_write_of_suggestions(self):
        from tagpup.services import tagging
        result = tagging.write_suggestions(self.library, [(self.loose[0], ["Trips/Lighthouse"], "")], EXIFTOOL)
        self.assertTrue(result.refused)
        self.assertEqual([], tags_in(self.loose[0]))
        self.assert_library_unchanged()


class TheLibraryCannotBeReadJustNow(Base):
    def test_the_write_fails_and_the_file_is_not_written(self):
        import sqlite3
        before = stamp(self.loose[0])
        with mock.patch("tagpup.store.folders.holds", side_effect=sqlite3.OperationalError("database is locked")):
            reply = self.post("/photo/save-metadata", {"path": self.loose[0], "title": "x", "tags": ["A/B"]})
            bulk = self.post("/photos/bulk-tags", {"paths": self.loose, "add_tags": ["A/B"], "remove_tags": []})
        # Not a 409 sending the owner to add a folder; not a write decided without the answer.
        self.assertEqual((500, 500), (reply.status_code, bulk.status_code), (reply.data, bulk.data))
        self.assertEqual(before, stamp(self.loose[0]))
        self.assert_library_unchanged()


class Roots(Base):
    """A folder not held can lie under one of the library's roots or outside every root: no
    row, so no conversion; paths are native, and sync's account of the folder does not move."""

    def setUp(self):
        super().setUp()
        self.pictures = os.path.join(self.home.root, "Pictures")
        self.new_folder = os.path.join(self.pictures, "2025 Coast")
        self.under = [self.new_folder + os.sep + "coast_01.jpg"]
        damaged_photos.whole_jpeg(self.under[0], seed=21)
        settings_service.of(self.library)
        self.assertTrue(settings_service.change(self.library, {settings_service.ROOTS: self.pictures}).ok)
        self.before = library_dump.dump(self.library.path)

    def test_under_a_root_and_outside_every_root(self):
        listed = sync.review(self.library, [self.pictures])
        self.assertIn(self.new_folder, [folder["path"] for folder in listed["folders"]])
        for photo in (self.under[0], self.loose[0]):
            reply = self.post("/photo/save-metadata", {"path": photo, "title": "x", "tags": ["Trips/Coast"]})
            self.assertEqual((200, 1), (reply.status_code, reply.get_json()["file_only"]), reply.data)
            self.assertEqual(["Trips/Coast"], tags_in(photo))
        self.assertEqual(listed, sync.review(self.library, [self.pictures]), "the edit changed what sync lists")
        self.assert_library_unchanged()


class AnotherLibraryHoldingTheFolder(Base):
    def test_its_database_is_not_touched_by_an_edit_through_this_one(self):
        other = self.home.library("quayside.db")
        library_actions.create(other)
        conn = db.connect(other)
        try:
            for path in self.loose:
                photo_rows.add_read(conn, path, {"XMP:Subject": ["Trips/Lighthouse"]})
            conn.commit()
        finally:
            conn.close()
        before = library_dump.dump(other)
        reply = self.post("/photos/bulk-tags", {"paths": self.loose, "add_tags": ["Trips/Harbour"], "remove_tags": []})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(before, library_dump.dump(other))
        self.assert_library_unchanged()


class TheModuleOpensNoDatabase(unittest.TestCase):
    def test_file_only_imports_nothing_of_the_store_and_opens_nothing(self):
        import re
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tagpup", "services",
                               "file_only.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertEqual([], re.findall(r"^\s*(?:from|import)\s+tagpup\.store\b.*$", source, re.M))
        self.assertEqual([], re.findall(r"\bdb\.|sqlite3|\.connect\(", source))


if __name__ == "__main__":
    unittest.main()
