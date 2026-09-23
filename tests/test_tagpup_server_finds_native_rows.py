"""TagPup's handlers find the rows the indexer wrote, whatever spelling they are handed.

The index holds native absolute paths -- backslashes on Windows -- because that is what
the indexer writes. The server looked them up with forward slashes, so on Windows every
one of these missed and said nothing: a deleted photo kept its row and faces, a caption
rename inserted a second row and left the faces behind, and the folder scan re-read
every file with ExifTool because it never found the metadata it already had.

The rows here are written the way the indexer writes them (os.path.abspath), never
through the code under test. ExifTool is replaced with stand-ins, because the question
is where the rows end up, not what ExifTool does.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import db as tagpup_db
import tagpup_server
from index import PhotoIndex
from tagpup_server import TagPupHTTPRequestHandler, set_active_db_path

WINDOWS = os.name == "nt"


def forward(path):
    """The spelling the browser tends to send: forward slashes."""
    return "/".join(re.split(r"[\\/]", path))


def key_of(path):
    """In-memory key, as the server computes it."""
    return os.path.normcase(os.path.abspath(path))


class HandlerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tagpup_native_rows_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # Upper case in the folder name, so a lower-cased spelling cannot pass for it.
        self.folder = os.path.join(self.tmp, "Parade Photos")
        os.makedirs(self.folder)
        self.db_path = os.path.join(self.tmp, "native_rows.db")
        index = PhotoIndex(db_path=self.db_path)
        index.load()
        index.close()
        set_active_db_path(self.db_path)

        def forget_state():
            set_active_db_path(self.db_path)
            TagPupHTTPRequestHandler.folder_cache.clear()
            TagPupHTTPRequestHandler.suggest_status.clear()
            set_active_db_path(None)
            tagpup_server.invalidate_people_cache()
        self.addCleanup(forget_state)

    def make_file(self, name, mtime=None):
        path = os.path.join(self.folder, name)
        with open(path, "wb") as f:
            f.write(b"not really a jpeg " + name.encode("utf-8"))
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def seed(self, path, tags=("Cross Country",), faces=(), embedding=b"an-embedding",
             stat_from_disk=True):
        stored = os.path.abspath(path)
        mtime = size = None
        if stat_from_disk and os.path.exists(stored):
            st = os.stat(stored)
            mtime, size = st.st_mtime, st.st_size
        conn = tagpup_db.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata, embedding)"
                " VALUES (?, ?, ?, ?, '[]', '[]', ?, ?)",
                (stored, mtime, size, json.dumps(list(tags)),
                 json.dumps({"XMP:Subject": list(tags)}), embedding))
            conn.executemany(
                "INSERT INTO faces (photo_path, box, name, prob) VALUES (?, '[]', ?, 1.0)",
                [(stored, name) for name in faces])
            conn.commit()
        finally:
            conn.close()

    def count(self, sql, *params):
        conn = tagpup_db.connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    def call(self, method, body=None, query=None):
        """Run one handler method as a request would, and return what it sent."""
        handler = TagPupHTTPRequestHandler.__new__(TagPupHTTPRequestHandler)
        handler.db_path = self.db_path
        sent = {}
        handler.read_json_body = lambda: body
        handler.send_json = lambda data: sent.setdefault("json", data)
        handler.send_json_error = lambda code, message: sent.setdefault("error", (code, message))
        handler.get_exiftool_path = lambda: "exiftool"
        set_active_db_path(self.db_path)
        if query is None:
            getattr(handler, method)()
        else:
            getattr(handler, method)(query)
        self.assertNotIn("error", sent, sent.get("error"))
        return sent["json"]


def fake_exiftool(get_tags_result):
    """ExifToolHelper stand-in: accepts every write, answers every read the same."""
    et = MagicMock()
    et.get_tags.return_value = get_tags_result
    helper = MagicMock()
    helper.return_value.__enter__.return_value = et
    helper.return_value.__exit__.return_value = False
    return helper


def fake_extractor(raw_metadata=None):
    """MetadataExtractor stand-in that reports each file it is asked about."""
    extractor_cls = MagicMock()

    def batch_read(file_paths, db_path=None):
        return [{"path": p, "mtime": 0.0, "size": 0, "tags": [], "people": [],
                 "captions": [], "raw_metadata": dict(raw_metadata or {})}
                for p in file_paths]
    extractor_cls.return_value.batch_read.side_effect = batch_read
    return extractor_cls


class TestDeletingAPhotoForgetsIt(HandlerCase):
    def test_the_row_its_faces_and_its_cached_embedding_all_go(self):
        photo = self.make_file("IMG_0001.jpg")
        stored = os.path.abspath(photo)
        self.seed(photo, faces=["Rowan Thackeray", None])
        conn = tagpup_db.connect(self.db_path)
        conn.execute("INSERT INTO embedding_cache (path, mtime, size, embedding) VALUES (?, 1, 1, ?)",
                     (stored, b"\x00" * 8))
        conn.commit()
        conn.close()

        def recycle(path):
            os.remove(path)
            return True

        with patch.object(tagpup_server, "send_to_recycle_bin", side_effect=recycle):
            result = self.call("handle_post_photo_delete", {"path": forward(photo)})

        self.assertTrue(result["success"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM faces"), 0,
                         "the faces outlived their photo")
        self.assertEqual(self.count("SELECT COUNT(*) FROM embedding_cache"), 0)


class TestSavingACaptionThatRenamesThePhoto(HandlerCase):
    def test_the_row_moves_and_its_faces_come_with_it(self):
        photo = self.make_file("Parade - 1.jpg")
        self.seed(photo, faces=["Rowan Thackeray", "Ada Marchetti"])
        renamed_to = os.path.join(self.folder, "Parade - 1 - Finish line.jpg")

        def sync_title(photo_path, title, executable):
            os.rename(photo_path, renamed_to)
            # metadata joins onto whatever spelling it was given.
            return forward(renamed_to)

        with patch("exiftool_session.ExifToolSession", fake_exiftool([{"XMP:Description": "Finish line"}])), \
                patch("metadata.sync_title_to_filename", side_effect=sync_title):
            result = self.call("handle_post_photo_save_metadata",
                               {"path": forward(photo), "title": "Finish line", "tags": []})

        new_stored = os.path.abspath(renamed_to)
        self.assertEqual(result["new_path"], new_stored)
        conn = tagpup_db.connect(self.db_path)
        try:
            rows = [r[0] for r in conn.execute("SELECT path FROM photos")]
            names = sorted(r[0] for r in conn.execute(
                "SELECT name FROM faces WHERE photo_path = ?", (new_stored,)))
            embedding = conn.execute("SELECT embedding FROM photos").fetchone()[0]
            captions = conn.execute("SELECT captions FROM photos").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(rows, [new_stored], "the rename left a second row behind")
        self.assertEqual(names, ["Ada Marchetti", "Rowan Thackeray"])
        self.assertEqual(embedding, b"an-embedding")
        self.assertEqual(json.loads(captions), ["Finish line"])

    def test_a_destination_the_index_already_has_is_not_doubled(self):
        photo = self.make_file("Parade - 1.jpg")
        self.seed(photo, faces=["Rowan Thackeray"])
        renamed_to = os.path.join(self.folder, "Parade - 1 - Finish line.jpg")
        self.seed(renamed_to, faces=["Ada Marchetti"], embedding=b"another-photo",
                  stat_from_disk=False)

        def sync_title(photo_path, title, executable):
            os.rename(photo_path, renamed_to)
            return renamed_to

        with patch("exiftool_session.ExifToolSession", fake_exiftool([{}])), \
                patch("metadata.sync_title_to_filename", side_effect=sync_title):
            result = self.call("handle_post_photo_save_metadata",
                               {"path": photo, "title": "Finish line", "tags": []})

        self.assertIn("index_warning", result)
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM faces WHERE photo_path = ?",
                                    os.path.abspath(renamed_to)), 1)


class TestTheFolderScanUsesWhatTheIndexKnows(HandlerCase):
    def test_unchanged_files_are_not_read_again(self):
        photos = [self.make_file(name) for name in ("IMG_0001.jpg", "IMG_0002.jpg")]
        for photo in photos:
            self.seed(photo)

        extractor = fake_extractor()
        with patch("metadata.MetadataExtractor", extractor):
            listed = self.call("handle_get_folder_scan",
                               query={"path": [forward(self.folder)], "force": ["true"]})

        extractor.return_value.batch_read.assert_not_called()
        self.assertEqual(sorted(p["path"] for p in listed),
                         sorted(os.path.abspath(p) for p in photos))
        self.assertTrue(all(p["tags"] == ["Cross Country"] for p in listed))


class TestSmartRenameNumbersByDateTaken(HandlerCase):
    """Reported: a photo whose caption was just saved -- so its file is the newest --
    was numbered last although it was taken first. The folder cache lookup never
    matched, so every photo fell back to its file time."""

    def test_the_first_taken_is_numbered_first_and_paths_come_back_stored(self):
        from metadata import build_photo_ui_record
        taken = {"a.jpg": "10:00:00", "b.jpg": "10:05:00", "c.jpg": "10:10:00"}
        # a.jpg was taken first but written last.
        mtimes = {"a.jpg": 3000.0, "b.jpg": 1000.0, "c.jpg": 2000.0}
        files = {name: self.make_file(name, mtime=mtimes[name]) for name in taken}
        for path in files.values():
            self.seed(path)
        set_active_db_path(self.db_path)
        TagPupHTTPRequestHandler.folder_cache[key_of(self.folder)] = {
            key_of(path): build_photo_ui_record(
                os.path.abspath(path),
                {"raw_metadata": {"EXIF:DateTimeOriginal": "2026:07:04 " + taken[name]}},
                mtimes[name], 10)
            for name, path in files.items()
        }

        preserved = [{"XMP-xmpMM:PreservedFileName": "original.jpg"}]
        with patch("exiftool_session.ExifToolSession", fake_exiftool(preserved)), \
                patch("metadata.MetadataExtractor", fake_extractor()):
            result = self.call("handle_post_folder_rename_photos", {
                "folder_path": forward(self.folder),
                "photo_paths": [forward(files[n]) for n in ("c.jpg", "b.jpg", "a.jpg")],
                "grouping": "Parade",
            })

        updated = result["updated_paths"]
        # By file name first, so the order is checked even where the spelling is wrong.
        numbered = {os.path.basename(old): os.path.basename(new) for old, new in updated.items()}
        self.assertEqual(numbered, {"a.jpg": "Parade - 1.jpg", "b.jpg": "Parade - 2.jpg",
                                    "c.jpg": "Parade - 3.jpg"})
        returned = list(updated) + list(updated.values()) + [p["path"] for p in result["updated_photos"]]
        for path in returned:
            self.assertEqual(path, os.path.abspath(path), "not the stored form: %r" % path)
        self.assertEqual(result["index_rows_moved"], 3)
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos WHERE path = ?",
                                    os.path.join(os.path.abspath(self.folder), "Parade - 1.jpg")), 1)


class TestTimeShiftKeepsTheRealPaths(HandlerCase):
    def test_a_cold_folder_is_walked_and_returned_in_its_own_case(self):
        photo = self.make_file("IMG_0001.jpg")
        extractor = fake_extractor({"EXIF:Model": "Test Camera"})
        with patch("metadata.MetadataExtractor", extractor), patch("exiftool_session.ExifToolSession", MagicMock()):
            result = self.call("handle_post_folder_time_shift", {
                "folder_path": forward(self.folder), "camera_model": "All Cameras",
                "shift_minutes": 30})

        self.assertEqual([p["path"] for p in result["updated_photos"]], [os.path.abspath(photo)])
        read = [p for call in extractor.return_value.batch_read.call_args_list for p in call.args[0]]
        self.assertTrue(read)
        self.assertTrue(all(p == os.path.abspath(photo) for p in read), read)
        set_active_db_path(self.db_path)
        self.assertIn(key_of(self.folder), TagPupHTTPRequestHandler.folder_cache)


class TestSavedSuggestionsSurviveTheNewKeys(HandlerCase):
    def test_a_folder_saved_under_the_old_key_is_found(self):
        photo = os.path.abspath(os.path.join(self.folder, "IMG_0001.jpg"))
        # How the old code keyed a folder: lower case, forward slashes.
        old_key = forward(os.path.abspath(self.folder).lower())
        cache_file = TagPupHTTPRequestHandler._suggestions_cache_path(self.db_path)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({old_key: {"status": "completed", "completed": 1, "total": 1,
                                 "suggestions": {forward(photo): {"tags": [], "people": []}}}}, f)

        TagPupHTTPRequestHandler.load_suggestions_cache(self.db_path)
        set_active_db_path(self.db_path)
        entry = TagPupHTTPRequestHandler.suggest_status.get(key_of(self.folder))
        self.assertIsNotNone(entry, "saved suggestions were loaded under a key nothing asks for")
        self.assertEqual(list(entry["suggestions"]), [photo])


if __name__ == "__main__":
    unittest.main()
