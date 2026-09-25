"""TagPup's routes find the rows the indexer wrote, whatever spelling they are handed.

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
import sys
import unittest
from unittest.mock import MagicMock, patch

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from face_rows import VECTORS_WITH_PATHS, add_face, add_vector  # noqa: E402
from handler_harness import Library  # noqa: E402

from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup.store import suggestions as saved_suggestions  # noqa: E402

WINDOWS = os.name == "nt"


def forward(path):
    """The spelling the browser tends to send: forward slashes."""
    return "/".join(re.split(r"[\\/]", path))


def key_of(path):
    """In-memory key, as the server computes it."""
    return os.path.normcase(os.path.abspath(path))


class HandlerCase(unittest.TestCase):
    def setUp(self):
        self.lib = Library(self, "native_rows")
        # Upper case in the folder name, so a lower-cased spelling cannot pass for it.
        self.folder = os.path.join(self.lib.root, "Parade Photos")
        os.makedirs(self.folder)
        self.db_path = self.lib.db_path

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
                "INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                " VALUES (?, ?, ?, ?, '[]', ?)",
                (stored, mtime, size, json.dumps(list(tags)),
                 json.dumps({"XMP:Subject": list(tags)})))
            add_vector(conn, stored, embedding)
            for name in faces:
                add_face(conn, stored, box="[]", name=name, prob=1.0)
            conn.commit()
        finally:
            conn.close()

    def count(self, sql, *params):
        conn = tagpup_db.connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    def call(self, method, path, body=None, query=None):
        """Ask one route as the page would, and return what it answered; an error
        reply fails the test."""
        status, reply = self.lib.post(path, body) if method == "POST" else self.lib.get(path, query)
        self.assertEqual(status, 200, reply)
        return reply


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

    def batch_read(file_paths, db_path=None, people=None):
        return [{"path": p, "mtime": 0.0, "size": 0, "tags": [], "people": [],
                 "captions": [], "raw_metadata": dict(raw_metadata or {})}
                for p in file_paths]
    extractor_cls.return_value.batch_read.side_effect = batch_read
    return extractor_cls


class TestDeletingAPhotoForgetsIt(HandlerCase):
    def test_the_row_its_faces_and_its_cached_embedding_all_go(self):
        photo = self.make_file("IMG_0001.jpg")
        # seed() keeps a vector for it, as the indexer does.
        self.seed(photo, faces=["Rowan Thackeray", None])
        self.assertEqual(self.count("SELECT COUNT(*) FROM embeddings"), 1)

        def recycle(path):
            os.remove(path)
            return True

        with patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=recycle):
            result = self.call("POST", "/api/photo/delete", {"path": forward(photo)})

        self.assertTrue(result["success"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM faces"), 0,
                         "the faces outlived their photo")
        self.assertEqual(self.count("SELECT COUNT(*) FROM embeddings"), 0,
                         "the vectors outlived their photo")


class TestSavingTagsAndACaption(HandlerCase):
    """Moved from TagTuner's copy of the route, which its page never called.

    The update there named a `title` column the photos table does not have, so it
    failed after the file had already been written.
    """

    def test_they_reach_the_photo_row(self):
        photo = self.make_file("IMG_0100.jpg")
        self.seed(photo, tags=())
        # TagPup records what the file holds after the write, so the stand-in reads
        # back what a real write would have left there.
        written = {"XMP:Subject": ["Places/Harbour"], "XMP:HierarchicalSubject": ["Places/Harbour"],
                   "XMP:Description": "Harbour at dusk"}
        with patch("tagpup.files.exiftool_session.ExifToolSession", fake_exiftool([written])), \
                patch("tagpup.files.metadata.sync_title_to_filename", side_effect=lambda p, *rest: p):
            result = self.call("POST", "/api/photo/save-metadata",
                               {"path": photo, "title": "Harbour at dusk",
                                "tags": ["Places/Harbour"]})

        conn = tagpup_db.connect(self.db_path)
        try:
            tags, captions = conn.execute("SELECT tags, captions FROM photos").fetchone()
        finally:
            conn.close()
        self.assertEqual(json.loads(tags), ["Places/Harbour"])
        self.assertEqual(json.loads(captions), ["Harbour at dusk"])
        self.assertTrue(result["success"])


class TestSavingACaptionThatRenamesThePhoto(HandlerCase):
    def test_the_row_moves_and_its_faces_come_with_it(self):
        photo = self.make_file("Parade - 1.jpg")
        self.seed(photo, faces=["Rowan Thackeray", "Ada Marchetti"])
        renamed_to = os.path.join(self.folder, "Parade - 1 - Finish line.jpg")

        def sync_title(photo_path, title, executable, rename_format):
            os.rename(photo_path, renamed_to)
            # metadata joins onto whatever spelling it was given.
            return forward(renamed_to)

        with patch("tagpup.files.exiftool_session.ExifToolSession",
                   fake_exiftool([{"XMP:Description": "Finish line"}])), \
                patch("tagpup.files.metadata.sync_title_to_filename", side_effect=sync_title):
            result = self.call("POST", "/api/photo/save-metadata",
                               {"path": forward(photo), "title": "Finish line", "tags": []})

        new_stored = os.path.abspath(renamed_to)
        self.assertEqual(result["new_path"], new_stored)
        conn = tagpup_db.connect(self.db_path)
        try:
            rows = [r[0] for r in conn.execute("SELECT path FROM photos")]
            names = sorted(r[0] for r in conn.execute(
                "SELECT name FROM faces WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (new_stored,)))
            embedding = conn.execute("SELECT e.vector FROM " + VECTORS_WITH_PATHS + " WHERE p.path = ?",
                                     (new_stored,)).fetchone()[0]
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

        def sync_title(photo_path, title, executable, rename_format):
            os.rename(photo_path, renamed_to)
            return renamed_to

        with patch("tagpup.files.exiftool_session.ExifToolSession", fake_exiftool([{}])), \
                patch("tagpup.files.metadata.sync_title_to_filename", side_effect=sync_title):
            result = self.call("POST", "/api/photo/save-metadata",
                               {"path": photo, "title": "Finish line", "tags": []})

        self.assertIn("index_warning", result)
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM faces WHERE photo_id = (SELECT id FROM photos WHERE path = ?)",
                                    os.path.abspath(renamed_to)), 1)


class TestTheFolderScanUsesWhatTheIndexKnows(HandlerCase):
    def test_unchanged_files_are_not_read_again(self):
        photos = [self.make_file(name) for name in ("IMG_0001.jpg", "IMG_0002.jpg")]
        for photo in photos:
            self.seed(photo)

        extractor = fake_extractor()
        with patch("tagpup.files.metadata.MetadataExtractor", extractor):
            listed = self.call("GET", "/api/folder/scan", query={"path": forward(self.folder), "force": "true"})

        extractor.return_value.batch_read.assert_not_called()
        self.assertEqual(sorted(p["path"] for p in listed),
                         sorted(os.path.abspath(p) for p in photos))
        self.assertTrue(all(p["tags"] == ["Cross Country"] for p in listed))


class TestTheFolderScanReadsARowWithNoStamp(HandlerCase):
    def test_a_photo_suggest_saw_but_the_index_never_read_is_read(self):
        # Suggest makes a row for such a photo with no mtime or size; the scan compared
        # them with the file's and failed with TypeError (docs/findings.md, #94).
        from tagpup.store import photos as store_photos
        photo = self.make_file("IMG_0001.jpg")
        conn = tagpup_db.connect(self.db_path)
        try:
            store_photos.ensure_row(conn, photo)
            conn.commit()
        finally:
            conn.close()
        extractor = fake_extractor()
        with patch("tagpup.files.metadata.MetadataExtractor", extractor):
            listed = self.call("GET", "/api/folder/scan", query={"path": forward(self.folder), "force": "true"})
        extractor.return_value.batch_read.assert_called_once()
        self.assertEqual([os.path.abspath(photo)], [p["path"] for p in listed])


class TestSmartRenameNumbersByDateTaken(HandlerCase):
    """Reported: a photo whose caption was just saved -- so its file is the newest --
    was numbered last although it was taken first. The folder cache lookup never
    matched, so every photo fell back to its file time."""

    def test_the_first_taken_is_numbered_first_and_paths_come_back_stored(self):
        taken = {"a.jpg": "10:00:00", "b.jpg": "10:05:00", "c.jpg": "10:10:00"}
        # a.jpg was taken first but written last.
        mtimes = {"a.jpg": 3000.0, "b.jpg": 1000.0, "c.jpg": 2000.0}
        files = {name: self.make_file(name, mtime=mtimes[name]) for name in taken}
        for path in files.values():
            self.seed(path)
        self.lib.folders().put(self.folder, {
            key_of(path): photo_actions.page_record(
                os.path.abspath(path),
                {"raw_metadata": {"EXIF:DateTimeOriginal": "2026:07:04 " + taken[name]}},
                mtimes[name], 10)
            for name, path in files.items()
        })

        preserved = [{"XMP-xmpMM:PreservedFileName": "original.jpg"}]
        with patch("tagpup.files.exiftool_session.ExifToolSession", fake_exiftool(preserved)), \
                patch("tagpup.files.metadata.MetadataExtractor", fake_extractor()):
            result = self.call("POST", "/api/folder/rename-photos", {
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
        # ExifTool answers the shift's read with the photo's date, in the spelling it
        # answers every path in.
        session = MagicMock()
        session.return_value.__enter__.return_value.get_tags.side_effect = lambda paths, tags=None: [
            {"SourceFile": forward(p), "EXIF:DateTimeOriginal": "2024:07:04 10:00:00"} for p in paths]
        # The route reads the cold folder and the service reads it back, both through
        # tagpup.files.
        with patch("tagpup.files.metadata.MetadataExtractor", extractor), \
                patch("tagpup.files.exiftool_session.ExifToolSession", session):
            result = self.call("POST", "/api/folder/time-shift", {
                "folder_path": forward(self.folder), "camera_model": "All Cameras",
                "shift_minutes": 30})

        self.assertEqual([p["path"] for p in result["updated_photos"]], [os.path.abspath(photo)])
        read = [p for call in extractor.return_value.batch_read.call_args_list for p in call.args[0]]
        self.assertTrue(read)
        self.assertTrue(all(p == os.path.abspath(photo) for p in read), read)
        self.assertIsNotNone(self.lib.folders().get(self.folder))


class TestSavedSuggestionsSurviveTheNewKeys(HandlerCase):
    def test_a_folder_asked_for_in_another_spelling_is_found(self):
        photo = os.path.abspath(os.path.join(self.folder, "IMG_0001.jpg"))
        conn = tagpup_db.connect(self.db_path)
        try:
            saved_suggestions.put(conn, photo, {"tags": [], "people": [], "title": None})
            conn.commit()
        finally:
            conn.close()

        runs = suggestion_jobs.runs_for(self.lib.library)
        # The spelling the browser sends, and on Windows the one the old code keyed a
        # folder by: lower case, forward slashes.
        spellings = [forward(self.folder)]
        if WINDOWS:
            spellings.append(forward(os.path.abspath(self.folder).lower()))
        for spelling in spellings:
            entry = runs.status(spelling)
            self.assertEqual(entry["status"], "completed", spelling)
            self.assertEqual(list(entry["suggestions"]), [photo], spelling)


if __name__ == "__main__":
    unittest.main()
