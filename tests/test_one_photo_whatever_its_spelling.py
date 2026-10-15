"""One photo whatever its path's spelling (F10): the CLI, the routes and the faces find the row the indexer wrote.

The index holds native absolute paths (backslashes on Windows) as paths.stored writes them. The browser sends forward
slashes, a folder is typed in another case, a relative folder is walked: each of those once missed the row and said nothing
-- a deleted photo kept its row and faces, a caption rename inserted a second row and left the faces behind, the folder
scan re-read every file, the index embedded the same photos again under a second row, a face lost its photo's people.
Rows are written the way the indexer writes them (os.path.abspath, photo_rows), never through the code under test; ExifTool
and the models are stand-ins, because the question is where the rows end up.

Lifted: test_tagpup_server_finds_native_rows (#94, #276), test_cli_path_spelling and test_path_spelling_in_faces_and_records
(the index locks went to test_index_progress_end_to_end; the folder time shift to test_time_shift). The old CLI test
asserted that the model embedded nothing, which the CLI never does for a row that has its vector (it passed with the
spelling broken); this one asserts that no file is read. Mutation proof: paths.key() not folding case fails the CLI
test; paths.stored() keeping a forward-slash spelling fails the routes, the walk and the faces; ensure_row not finding
the row fails the faces, the CLI and the rename.
"""
import gc
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from PIL import Image

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import photo_rows  # noqa: E402
from face_rows import VECTORS_WITH_PATHS, add_face, add_vector, configured_model  # noqa: E402
from handler_harness import Library  # noqa: E402
from test_face_clustering_rules import FaceClusteringTestBase, identity_vector  # noqa: E402

from tagpup.core import fields, paths  # noqa: E402
from tagpup.core.dates import record_year  # noqa: E402
from tagpup.files.images import photos_under  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.ml.clip import output_dim  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.services import settings  # noqa: E402
from tagpup.services.photos import page_record  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import added_folders  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import suggestions as saved_suggestions  # noqa: E402
from tagpup_cli import cli  # noqa: E402


def forward(path):
    """The spelling the browser tends to send: forward slashes."""
    return "/".join(re.split(r"[\\/]", path))


def make_jpeg(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (16, 16), color="green").save(path, "JPEG")


def swap_case(text):
    return "".join(c.lower() if c.isupper() else c.upper() for c in text)


def expected_dim():
    """The length the library's model makes, as the CLI checks it: a library made in a test's home holds the defaults."""
    return output_dim(settings.DEFAULTS["model.name"]) or 512


class CliDatabaseCase(unittest.TestCase):
    """A library the CLI works on (TAGPUP_DB_PATH) in a folder of its own; shared with test_a_row_describes_its_file_one_way."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(gc.collect)   # an orphaned connection holds the file
        self.db_path = os.path.join(self.tmp.name, "spelling.db")
        os.environ["TAGPUP_DB_PATH"] = self.db_path
        self.addCleanup(os.environ.pop, "TAGPUP_DB_PATH", None)

    def seed(self, rows):
        """rows: [(path as the index should hold it, file on disk)]"""
        index = PhotoIndex(self.db_path, configured_model())
        index.load()
        metas = []
        for stored_as, on_disk in rows:
            stat = os.stat(on_disk)
            metas.append({"path": stored_as, "mtime": stat.st_mtime, "size": stat.st_size,
                          "tags": [], "people": [], "captions": [], "raw_metadata": {}})
        index.build_or_update([[0.1] * expected_dim()] * len(metas), metas, dim=expected_dim())
        index.close()

    def photo_rows(self):
        conn = tagpup_db.connect(self.db_path)
        try:
            return [r[0] for r in conn.execute("SELECT path FROM photos")]
        finally:
            conn.close()

    def run_cli(self, args, input=None):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=self.tmp.name):
            return runner.invoke(cli, ["--test"] + args, input=input)


class TheCli(CliDatabaseCase):

    def test_a_forward_slash_folder_and_a_relative_one_walk_to_stored_paths(self):
        library = os.path.join(self.tmp.name, "Summer Trip")
        make_jpeg(os.path.join(library, "beach.jpg"))
        make_jpeg(os.path.join(library, "Day Two", "dunes.jpg"))
        found = photos_under(library.replace(os.sep, "/"))
        self.assertEqual(2, len(found))
        for path in found:
            self.assertEqual(path, paths.stored(path), "scan produced a path the index would not store: %r" % path)
        try:
            relative = os.path.relpath(library)
        except ValueError:
            return   # the temp directory is on another drive than the cwd
        self.assertTrue(all(os.path.isabs(path) for path in photos_under(relative)))

    @unittest.skipUnless(paths.CASE_INSENSITIVE, "two case spellings are two files here")
    @patch("tagpup.ml.clip.ClipModel._init_model")
    @patch("tagpup_cli.MetadataExtractor.batch_read")
    def test_a_folder_indexed_under_another_case_is_not_read_again(self, batch_read, _init_model):
        library = os.path.join(self.tmp.name, "Garden Party")
        photo = os.path.join(library, "lanterns.jpg")
        make_jpeg(photo)
        # The row as it was written when the folder was typed another way.
        self.seed([(os.path.join(os.path.dirname(library), swap_case("Garden Party"), "lanterns.jpg"), photo)])
        batch_read.side_effect = lambda files, *a, **k: [
            {"path": f, "tags": [], "people": [], "captions": [], "raw_metadata": {},
             "mtime": os.stat(f).st_mtime, "size": os.stat(f).st_size} for f in files]
        result = self.run_cli(["index", "--skip-faces", library])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("up to date", result.output)
        rows = self.photo_rows()
        self.assertEqual(1, len(rows), "one photo, %d rows: %r" % (len(rows), rows))
        batch_read.assert_not_called()


@unittest.skipIf(os.sep == "/", "one separator, so one spelling")
class TheFaces(FaceClusteringTestBase):
    def test_a_face_recorded_under_another_spelling_lands_on_its_photos_own_row_and_still_anchors(self):
        typed = self.add_photo("picnic.jpg", people=["Mira Castellane"])    # forward slashes
        conn = tagpup_db.connect(self.db_path)
        try:
            # The row as the indexer writes it (paths.stored); the face as typed.
            conn.execute("UPDATE photos SET path = ? WHERE path = ?", (paths.stored(typed), typed))
            face = store_faces.insert(conn, typed, [0, 0, 100, 100], identity_vector(3).tobytes(), prob=0.99)
            conn.commit()
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0],
                             "the face made a second row for its photo")
        finally:
            conn.close()
        self.resolve()
        self.assertEqual("Mira Castellane", self.name_of(face))

    def test_the_browsers_record_carries_the_stored_spelling_and_its_year_is_read_under_either_separator(self):
        typed = os.path.join(os.path.abspath(os.sep), "Library", "Harbour", "boats.jpg")
        record = page_record(typed.replace(os.sep, "/"), {})
        self.assertEqual((paths.stored(typed), "boats.jpg"), (record["path"], record["filename"]))
        for sep in {"/", os.sep}:
            self.assertEqual(2011, record_year({"path": sep.join(["", "Library", "Trips 2011", "Coast", "boats.jpg"])}))
            self.assertEqual(2013, record_year({"path": sep.join(["", "Library", "Trips 2011", "boats 2013.jpg"])}),
                             "the filename year wins over the folder")


def fake_exiftool(answer):
    """ExifToolSession stand-in: answers every read with `answer`, over which it keeps each write to a file, as the
    file would (a journaled write that left the file as it was is no change, #276)."""
    kept, et, helper = {}, MagicMock(), MagicMock()

    def get_tags(paths_, tags=None, params=None):
        written = kept.get(paths_[0], {}) if len(paths_) == 1 else {}
        return [dict(found, **written) for found in answer]

    def set_tags(paths_, tags=None, params=None):
        for path in paths_:   # answered under the group ExifTool reads it back in: XMP-xmpMM:x as XMP:x
            held = kept.setdefault(path, {})
            held.update({fields.read_key(name): value for name, value in (tags or {}).items()})
            held.update({fields.read_key(param[1:-1]): [] for param in params or []
                         if param.startswith("-") and param.endswith("=")})
    et.get_tags.side_effect, et.set_tags.side_effect = get_tags, set_tags
    helper.return_value.__enter__.return_value = et
    helper.return_value.__exit__.return_value = False
    return helper


def fake_extractor():
    """MetadataExtractor stand-in that reports each file it is asked about."""
    cls = MagicMock()
    cls.return_value.batch_read.side_effect = lambda file_paths, db_path=None, people=None: [
        {"path": p, "mtime": 0.0, "size": 0, "tags": [], "people": [], "captions": [], "raw_metadata": {}}
        for p in file_paths]
    return cls


class HandlerCase(unittest.TestCase):
    """The page asks in forward slashes; the rows hold the stored spelling. Shared with
    test_rename_moves_suggestions_and_embeddings."""

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

    def seed(self, path, tags=("Cross Country",), faces=(), embedding=b"an-embedding"):
        """The row the indexer wrote for `path`: its stamp from the file when there is one, its tags, a vector, and
        `faces`' names."""
        stored = os.path.abspath(path)
        conn = tagpup_db.connect(self.db_path)
        try:
            photo_rows.add_read(conn, stored, {"XMP:Subject": list(tags)})
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
        """Ask one route as the page would; an error reply fails the test."""
        status, reply = self.lib.post(path, body) if method == "POST" else self.lib.get(path, query)
        self.assertEqual(200, status, reply)
        return reply

class TheRoutes(HandlerCase):
    def test_deleting_a_photo_forgets_its_row_faces_and_cached_embedding(self):
        photo = self.make_file("IMG_0001.jpg")
        self.seed(photo, faces=["Rowan Thackeray", None])

        def recycle(path):
            os.remove(path)
            return True
        with patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=recycle):
            self.assertTrue(self.call("POST", "/api/photo/delete", {"path": forward(photo)})["success"])
        self.assertEqual([0, 0, 0], [self.count("SELECT COUNT(*) FROM " + table)
                                     for table in ("photos", "faces", "embeddings")])

    def test_a_caption_that_renames_the_photo_moves_its_row_with_its_faces_and_tags_and_never_onto_another_row(self):
        photo = self.make_file("Parade - 1.jpg")
        self.seed(photo, tags=(), faces=["Rowan Thackeray", "Ada Marchetti"])
        renamed_to = os.path.join(self.folder, "Parade - 1 - Finish line.jpg")
        # What a real write leaves in the file, which the row then records.
        written = {"XMP:Subject": ["Places/Harbour"], "XMP:Description": "Finish line"}
        with patch("tagpup.files.exiftool_session.ExifToolSession", fake_exiftool([written])), \
                patch("tagpup.files.metadata.title_filename", side_effect=lambda *a: forward(renamed_to)):
            result = self.call("POST", "/api/photo/save-metadata",
                               {"path": forward(photo), "title": "Finish line", "tags": ["Places/Harbour"]})
        new_stored = os.path.abspath(renamed_to)
        self.assertEqual(new_stored, result["new_path"])
        conn = tagpup_db.connect(self.db_path)
        try:
            rows = [r[0] for r in conn.execute("SELECT path FROM photos")]
            names = sorted(r[0] for r in conn.execute(
                "SELECT name FROM faces WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (new_stored,)))
            vector = conn.execute("SELECT e.vector FROM " + VECTORS_WITH_PATHS + " WHERE p.path = ?",
                                  (new_stored,)).fetchone()[0]
            tags, captions = conn.execute("SELECT tags, captions FROM photos").fetchone()
        finally:
            conn.close()
        self.assertEqual([new_stored], rows, "the rename left a second row behind")
        self.assertEqual(["Ada Marchetti", "Rowan Thackeray"], names)
        self.assertEqual((b"an-embedding", ["Places/Harbour"], ["Finish line"]), (vector, json.loads(tags), json.loads(captions)))

        # A destination the index already has is not doubled: both stay as they were, and the answer says so.
        other = self.make_file("Parade - 2.jpg")
        taken = os.path.join(self.folder, "Parade - 2 - Done.jpg")
        self.seed(other, faces=["Rowan Thackeray"])
        self.seed(taken, faces=["Ada Marchetti"], embedding=b"another-photo")
        with patch("tagpup.files.exiftool_session.ExifToolSession", fake_exiftool([{}])), \
                patch("tagpup.files.metadata.title_filename", side_effect=lambda *a: taken):
            result = self.call("POST", "/api/photo/save-metadata", {"path": other, "title": "Done", "tags": []})
        self.assertIn("index_warning", result)
        self.assertEqual(3, self.count("SELECT COUNT(*) FROM photos"))
        self.assertEqual(1, self.count("SELECT COUNT(*) FROM faces WHERE photo_id = (SELECT id FROM photos WHERE path = ?)",
                                       os.path.abspath(taken)))

    def test_the_folder_scan_reads_only_the_photo_the_index_never_read(self):
        known = [self.make_file(name) for name in ("IMG_0001.jpg", "IMG_0002.jpg")]
        for photo in known:
            self.seed(photo)
        # Suggest makes a row for such a photo with no mtime or size; the scan compared them with the file's and failed
        # with a TypeError (#94).
        unread = self.make_file("IMG_0003.jpg")
        conn = tagpup_db.connect(self.db_path)
        try:
            store_photos.ensure_row(conn, unread, admit=True)   # in a folder added
            conn.commit()
        finally:
            conn.close()
        extractor = fake_extractor()
        with patch("tagpup.files.metadata.MetadataExtractor", extractor):
            listed = self.call("GET", "/api/folder/scan", query={"path": forward(self.folder), "force": "true"})
        extractor.return_value.batch_read.assert_called_once()
        self.assertEqual([os.path.abspath(unread)], list(extractor.return_value.batch_read.call_args.args[0]))
        self.assertEqual(sorted(os.path.abspath(p) for p in known + [unread]), sorted(p["path"] for p in listed))

    def test_smart_rename_numbers_by_date_taken_and_answers_in_the_stored_form(self):
        # Reported: a photo whose caption was just saved -- so its file is the newest -- was numbered last although it was
        # taken first: the folder cache lookup never matched, so every photo fell back to its file time.
        taken = {"a.jpg": "10:00:00", "b.jpg": "10:05:00", "c.jpg": "10:10:00"}
        mtimes = {"a.jpg": 3000.0, "b.jpg": 1000.0, "c.jpg": 2000.0}      # a.jpg was taken first but written last
        files = {name: self.make_file(name, mtime=mtimes[name]) for name in taken}
        for path in files.values():
            self.seed(path)
        self.lib.folders().put(self.folder, {
            os.path.normcase(os.path.abspath(path)): photo_actions.page_record(
                os.path.abspath(path), {"raw_metadata": {"EXIF:DateTimeOriginal": "2026:07:04 " + taken[name]}},
                mtimes[name], 10)
            for name, path in files.items()})
        # As ExifTool answers it: under XMP, not the group it is written in.
        with patch("tagpup.files.exiftool_session.ExifToolSession", fake_exiftool([{"XMP:PreservedFileName": "original.jpg"}])), \
                patch("tagpup.files.metadata.MetadataExtractor", fake_extractor()):
            result = self.call("POST", "/api/folder/rename-photos", {
                "folder_path": forward(self.folder), "photo_paths": [forward(files[n]) for n in ("c.jpg", "b.jpg", "a.jpg")],
                "grouping": "Parade"})
        updated = result["updated_paths"]
        self.assertEqual({"a.jpg": "Parade - 1.jpg", "b.jpg": "Parade - 2.jpg", "c.jpg": "Parade - 3.jpg"},
                         {os.path.basename(old): os.path.basename(new) for old, new in updated.items()})
        for path in list(updated) + list(updated.values()) + [p["path"] for p in result["updated_photos"]]:
            self.assertEqual(os.path.abspath(path), path, "not the stored form: %r" % path)
        self.assertEqual(3, result["index_rows_moved"])
        self.assertEqual(1, self.count("SELECT COUNT(*) FROM photos WHERE path = ?",
                                       os.path.join(os.path.abspath(self.folder), "Parade - 1.jpg")))

    def test_saved_suggestions_are_found_by_a_folder_in_another_spelling(self):
        photo = os.path.abspath(os.path.join(self.folder, "IMG_0001.jpg"))
        conn = tagpup_db.connect(self.db_path)
        try:
            added_folders.record(conn, os.path.dirname(photo), subfolders=False)   # Suggest ran there: the folder was added
            saved_suggestions.put(conn, photo, {"tags": [], "people": [], "title": None})
            conn.commit()
        finally:
            conn.close()
        runs = suggestion_jobs.runs_for(self.lib.library)
        # The spelling the browser sends, and on Windows the one the old code keyed a folder by: lower case, forward slashes.
        spellings = [forward(self.folder)] + ([forward(os.path.abspath(self.folder).lower())] if os.name == "nt" else [])
        for spelling in spellings:
            entry = runs.status(spelling)
            self.assertEqual(("completed", [photo]), (entry["status"], list(entry["suggestions"])), spelling)


if __name__ == "__main__":
    unittest.main()
