"""The page's cached scan of a folder is read again once the library's photos changed, whoever changed them.

The server keeps what the page was last shown of a folder (tagpup.web.tagpup_routes.FolderCache) and was told
of its own writes only. A CLI `sync --apply` or `undo`, or a snapshot restore in another process rewrote the
photos' rows and the open page's next scan still described them as they had been. The cache is kept with the
library's photos counter as it was before the scan, which the triggers move for a write from any process
(tagpup.store.generations), and handed back only while it has not moved.

A folder's clips are listed beside its photos (video, merge B2): a clip the index has not recorded is a card with no
picture until it is; once recorded, the next open gives it its id and its poster by id (/api/photo-thumb), the one way a
clip's picture is served. A clip and a photo of one base name are two records. Suggest is handed a folder's photos and
its clips (DECISIONS 2026-11-13); a time shift is the page's choice of photos, never a clip
(tests/frontend/shifting-a-folders-date-taken.test.mjs; one named anyway: tests/test_a_clip_is_not_turned.py). Smart Rename answers the folder as a scan gives it, its clips with it, scanned again when anything moved the
library since the folder was opened (#2564: a tag saved on one of its photos, the index recording the clip).

A folder's scan as the page reads it -- each record with its `order`, the server's key of its place (tagpup.core.dates
taken_sort_key), photos dated and one not -- is written here for the page tests (tests/frontend/fixtures/scan_replies.json,
harness.mjs orderOf and scanned; AFolderScanFromThePage), which spelled that key by hand (#3326).
"""
import json
import os
import sys
import unittest
from unittest import mock

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_exiftool  # noqa: E402
import page_fixtures  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.store import db  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402


class AScanKeptWhileTheLibraryMoves(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.db_path = self.home.library("library.db")
        self.folder = os.path.join(self.home.root, "Photos", "Regatta")
        os.makedirs(self.folder)
        self.photo = os.path.join(self.folder, "one.jpg")
        Image.new("RGB", (8, 8)).save(self.photo, "JPEG")
        self.record(["Boats/Dinghy"])

    def record(self, subjects):
        """Another process-like writer: its own connection, the row as the indexer records what it read."""
        def write(conn):
            photo_rows.hold(conn, self.folder)
            photo_rows.add_read(conn, self.photo, {"XMP:Subject": subjects})
        db.write_with_connection(self.db_path, write)

    def scan(self):
        reply = self.client.get("/library/api/folder/scan", query_string={"path": self.folder})
        self.assertEqual(200, reply.status_code, reply.data)
        return [record["tags"] for record in reply.get_json()]

    def test_a_scan_is_kept_while_nothing_moved(self):
        first = self.scan()
        self.assertEqual([["Boats/Dinghy"]], first)
        kept = self.cache().get(self.folder)
        self.assertIsNotNone(kept)
        self.assertEqual(kept, self.cache().get(self.folder))

    def test_a_write_from_another_connection_makes_the_next_scan_read_again(self):
        self.assertEqual([["Boats/Dinghy"]], self.scan())
        self.record(["Boats/Keelboat"])
        self.assertIsNone(self.cache().get(self.folder), "the scan was handed back after the photos changed")
        self.assertEqual([["Boats/Keelboat"]], self.scan())

    def cache(self):
        from tagpup.core.library import Library
        return tagpup_routes.folders.of(Library(self.db_path))


class AFolderOfPhotosAndAClip(unittest.TestCase):
    """Two photos and a clip, the clip sharing one photo's base name; the folder opened before the index records the
    clip and after. Reverting services.photos.scan_folder to its photos-only listing fails every test here."""

    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.db_path = self.home.library("library.db")
        files = fake_exiftool.Files()       # the reader of the files the index does not describe
        patcher = mock.patch("tagpup.files.exiftool_session.ExifToolSession", files.session)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.folder = os.path.join(self.home.root, "Photos", "Lighthouse")
        os.makedirs(self.folder)
        self.photos = [os.path.join(self.folder, name) for name in ("dawn.jpg", "dusk.jpg")]
        for photo in self.photos:
            Image.new("RGB", (8, 8)).save(photo, "JPEG")
        self.clip = os.path.join(self.folder, "dusk.mp4")
        with open(self.clip, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)

        def hold(conn):
            photo_rows.hold(conn, self.folder)
            for photo in self.photos:
                photo_rows.add_read(conn, photo, {"XMP:Subject": ["Places/Lighthouse"]})
        db.write_with_connection(self.db_path, hold)

    def scan(self):
        reply = self.client.get("/library/api/folder/scan", query_string={"path": self.folder})
        self.assertEqual(200, reply.status_code, reply.data)
        return {record["filename"]: record for record in reply.get_json()}

    def record_the_clip(self):
        return db.write_with_connection(self.db_path, lambda conn: photo_rows.add_read(conn, self.clip, {}))

    def test_a_clip_is_listed_beside_the_photos_and_has_no_picture_until_the_index_records_it(self):
        found = self.scan()
        self.assertEqual(["dawn.jpg", "dusk.jpg", "dusk.mp4"], sorted(found))
        self.assertEqual("video", found["dusk.mp4"]["media"])
        self.assertEqual(["photo", "photo"], [found[name]["media"] for name in ("dawn.jpg", "dusk.jpg")])
        self.assertNotIn("thumb", found["dusk.mp4"], "a clip with no row has no picture to be asked for")
        self.assertNotIn("id", found["dusk.mp4"])
        for name in ("dawn.jpg", "dusk.jpg"):
            self.assertNotIn("thumb", found[name], "a folder's photo is drawn from its file, as before")

    def test_once_the_index_records_the_clip_the_next_open_gives_its_poster_by_id(self):
        self.assertNotIn("thumb", self.scan()["dusk.mp4"])
        clip_id = self.record_the_clip()
        clip = self.scan()["dusk.mp4"]
        self.assertEqual(clip_id, clip["id"])
        self.assertTrue(clip["thumb"].startswith("/api/photo-thumb?id=%d&v=" % clip_id), clip["thumb"])
        self.assertEqual("video", clip["media"])

    def test_suggest_is_handed_the_folders_clips(self):
        self.record_the_clip()
        started = {}

        class Runs:
            def start(self, folder, work):
                started["photos"] = sorted(os.path.basename(meta["path"]) for meta in work.photos().values())
                return "preparing"
        with mock.patch.object(tagpup_routes.suggestion_jobs, "runs_for", lambda library: Runs()):
            reply = self.client.post("/library/api/folder/suggest-start", json={"folder_path": self.folder})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(["dawn.jpg", "dusk.jpg", "dusk.mp4"], started["photos"], "Suggest was not handed the clip")

    def test_smart_rename_after_the_library_moved_answers_the_folder_with_its_clip(self):
        # #2564: the folder is opened, then the library moves -- the index records the clip, as it does a while after
        # the open -- and the folder's photos are renamed. The answer, which the page shows as the folder, and the next
        # open both list the clip: a rename never touches a clip, and it does not take it off the page either.
        self.assertNotIn("thumb", self.scan()["dusk.mp4"])
        clip_id = self.record_the_clip()
        reply = self.client.post("/library/api/folder/rename-photos", json={
            "folder_path": self.folder, "grouping": "Lighthouse", "photo_paths": self.photos})
        self.assertEqual(200, reply.status_code, reply.data)
        answered = {os.path.basename(record["path"]): record for record in reply.get_json()["updated_photos"]}
        self.assertIn("dusk.mp4", answered, "Smart Rename's answer dropped the folder's clip")
        self.assertEqual(("video", clip_id), (answered["dusk.mp4"]["media"], answered["dusk.mp4"]["id"]))
        self.assertTrue(answered["dusk.mp4"]["thumb"].startswith("/api/photo-thumb?id=%d&v=" % clip_id))
        self.assertEqual(2, sum(record["media"] == "photo" for record in answered.values()))
        self.assertTrue(os.path.exists(self.clip), "the clip keeps its name")
        reopened = self.scan()
        self.assertIn("dusk.mp4", reopened, "the next open after the rename lost the clip")
        self.assertEqual(clip_id, reopened["dusk.mp4"]["id"])


#: The replies of a folder's scan the page tests read (tests/frontend/harness.mjs orderOf, scanned).
SCAN_REPLIES = page_fixtures.path_of("scan_replies.json")


class AFolderScanFromThePage(unittest.TestCase):
    """A folder opened as the page opens it (GET /api/folder/scan), its records as the route sends them (_sorted), written
    for the page tests: what each record's `order` is, never spelled by a test."""

    def test_the_scan_reply_the_page_reads_is_written_for_its_test(self):
        # `folder`: two photos dated, IMG_0001.jpg after IMG_0002.jpg by Date Taken, and IMG_0003.jpg holding none, its
        # file time a whole second (1600000000), after both. Rows as the indexer records a read of each, so the scan
        # takes each from its row. Each size 1; the folder named FOLDER, in both of its spellings.
        app, home = web_client.app_for(self, "tagpup")
        library = home.library("library.db")
        folder = os.path.join(home.root, "Photos", "Regatta")
        os.makedirs(folder)
        made = (("IMG_0001.jpg", "2020:06:01 10:20:00"), ("IMG_0002.jpg", "2020:06:01 10:00:00"), ("IMG_0003.jpg", None))
        for n, (name, _taken) in enumerate(made):
            path = os.path.join(folder, name)
            Image.new("RGB", (8, 8), (40 * n, 90, 160)).save(path, "JPEG")
            os.utime(path, (1600000000, 1600000000))

        def hold(conn):
            photo_rows.hold(conn, folder)
            for name, taken in made:
                photo_rows.add_read(conn, os.path.join(folder, name), {"EXIF:DateTimeOriginal": taken} if taken else {})
        db.write_with_connection(library, hold)
        reply = app.test_client().get("/library/api/folder/scan", query_string={"path": folder})
        body = reply.get_json()
        self.assertEqual((200, ["IMG_0002.jpg", "IMG_0001.jpg", "IMG_0003.jpg"]),
                         (reply.status_code, [record["filename"] for record in body]), body)
        self.assertEqual([None, None, 1600000000], [record["mtime"] if record["taken"] is None else None for record in body])
        for record in body:
            record.update(size=1)
        text = json.dumps({"folder": {"status": reply.status_code, "reply": body}}, sort_keys=True, indent=1) + "\n"
        for spelled in (folder, folder.replace(os.sep, "/")):
            text = text.replace(json.dumps(spelled)[1:-1], "FOLDER")
        self.assertNotIn(json.dumps(home.root)[1:-1], text, "a path of the test's home left in")
        page_fixtures.hold_to(self, SCAN_REPLIES, text, "tests.test_folder_scan_follows_the_library", "a folder's scan")


class ALooseFolderWhenTheTreeChanges(unittest.TestCase):
    """#1451: a folder the library does not hold is read from its files, told the tree's people. A tree edit that
    rewrites no photo (a branch made a people branch) moves the taxonomy counter only; the next open reads the files
    again and shows the person. Reverting services.photos.scan_stamp to the photos counter alone fails it."""

    def test_the_next_open_shows_the_people_the_tree_now_names(self):
        app, home = web_client.app_for(self, "tagpup")
        client = app.test_client()
        files = fake_exiftool.Files()       # field_values.opened looks ExifToolSession up when it starts
        patcher = mock.patch("tagpup.files.exiftool_session.ExifToolSession", files.session)
        patcher.start()
        self.addCleanup(patcher.stop)
        folder = os.path.join(home.root, "Loose")
        os.makedirs(folder)
        photo = os.path.join(folder, "one.jpg")
        Image.new("RGB", (8, 8)).save(photo, "JPEG")
        files.keep_tags(photo, ["Cousins/Rowan Thackeray"])
        made = client.post("/library/api/taxonomy/create", json={"name": "Cousins"}).get_json()

        def people():
            reply = client.get("/library/api/folder/scan", query_string={"path": folder})
            self.assertEqual(200, reply.status_code, reply.data)
            return [record["people"] for record in reply.get_json()]
        self.assertEqual([[]], people())
        update = client.post("/library/api/taxonomy/update", json={"id": made["id"], "has_face": 1})
        self.assertEqual(200, update.status_code, update.data)
        self.assertEqual([["Rowan Thackeray"]], people(), "the open folder still showed the old people")


if __name__ == "__main__":
    unittest.main()
