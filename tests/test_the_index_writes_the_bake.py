"""The index writes the bake on its own (docs/PLAN_KEEP_IN_STEP.md, cut 2; scenarios 2, 11, 13 and 14): the owner indexes a
folder -- by hand, or the queue does after a sync -- and runs no bake command.

What the owner lives through, each with the CLI's index over a real ExifTool and the stand-in models (stand_in_models):
- a new folder is indexed: each file then holds a bake that matches its row, and the next index of the folder rewrites
  no file that holds it already;
- History never lists a bake, and a keyword save before one is undone past it;
- the process is killed at each step of a file's write: the next start settles it, the fingerprint row follows, and the
  finished record is gone;
- the index's own decode of a photo is its fingerprint: the bake decodes no photo again;
- a face deleted in TagPup is gone from the file at the folder's next index, and does not come back.
"""
import json
import os
import sys
import unittest
from unittest import mock

from click.testing import CliRunner
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baked_files  # noqa: E402
import own_home  # noqa: E402
from stand_in_models import Hold, NewFaces, RefusableFaces  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import images  # noqa: E402
from tagpup.ml import clip  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import bake_after_index, baking, duplicate_faces, file_changes, tagging  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, file_journal  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
SIZE = (640, 480)
DETECTOR = face_service.detector_of(NewFaces(), None)
FACE = [300, 200, 400, 330]


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class TheIndexWritesTheBake(unittest.TestCase):

    def setUp(self):
        self.home = own_home.for_test(self, prefix="index_writes_the_bake_")
        self.db_path = self.home.library("harbour.db")
        self.assertEqual(1, library_actions.create(self.db_path).changed)
        self.library = Library(self.db_path)
        self.folder = paths.stored(os.path.join(self.home.root, "Harbour"))
        os.makedirs(self.folder)
        vector = baked_files.unit(1152, 99).tolist()
        self.detector = RefusableFaces({"a.jpg": [(FACE, 0.99, 1)]}, vector=baked_files.face_vector)
        for patcher in (mock.patch.object(clip.ClipModel, "_init_model"),
                        mock.patch.object(clip.ClipModel, "picture_input", return_value="input"),
                        mock.patch.object(clip.ClipModel, "embed_input", return_value=vector),
                        mock.patch.object(Runtime, "gpu_turn", lambda runtime, what, *args, **kwargs: Hold()),
                        mock.patch.object(runtimes, "on_the_card", return_value=True),
                        mock.patch.object(Runtime, "faces", lambda runtime, library, settings=None: self.detector)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def picture(self, name, colour):
        path = os.path.join(self.folder, name)
        Image.new("RGB", SIZE, colour).save(path, "JPEG")
        return path

    def index(self):
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", self.folder])
        if isinstance(result.exception, BaseException) and not isinstance(result.exception, SystemExit):
            raise result.exception
        self.assertEqual(0, result.exit_code, result.output)
        return result

    def query(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def boxes(self, path):
        return [json.loads(box) for path_of, box in self.query(
            "SELECT p.path, f.box FROM faces f JOIN photos p ON p.id = f.photo_id") if paths.key(path_of) == paths.key(path)]

    def fingerprint_row(self, path):
        return [(key, bytes(hash)) for path_of, key, hash in self.query(
            "SELECT p.path, f.key, f.hash FROM photo_fingerprints f JOIN photos p ON p.id = f.photo_id")
            if paths.key(path_of) == paths.key(path)]

    def assertBaked(self, path, faces):
        """The file holds the bake its row makes (planned with its decode: Current), its fingerprint its row's."""
        self.assertIsInstance(baked_files.plan(self.db_path, path, DETECTOR), baking.Current, path)
        held = baked_files.held(path)
        self.assertEqual(faces, len(held.faces), path)
        self.assertEqual([(held.fingerprint_key, held.fingerprint)], self.fingerprint_row(path))

    def assertNoBakeListed(self):
        listed = journal_service.history(self.library, limit=1000)["changes"]
        self.assertEqual([], [entry for entry in listed if entry["operation"] == baking.INDEX_OPERATION])
        self.assertEqual([], self.query("SELECT id FROM changes WHERE operation = ?", (baking.INDEX_OPERATION,)),
                         "a finished bake leaves no record")
        self.assertEqual([], file_journal.unfinished(self.db_path))

    def test_a_new_folder_is_indexed_and_baked_and_its_next_index_rewrites_no_file_that_holds_it(self):
        a, b = self.picture("a.jpg", (200, 40, 40)), self.picture("b.jpg", (40, 200, 40))
        first = self.index()
        self.assertIn("Bake: 2 file(s) written, 0 already held it.", " ".join(first.output.split()))
        self.assertBaked(a, 1)
        self.assertBaked(b, 0)
        self.assertNoBakeListed()
        stamps = {path: os.stat(path).st_mtime_ns for path in (a, b)}

        c = self.picture("c.jpg", (40, 40, 200))
        second = self.index()
        self.assertIn("Bake: 1 file(s) written, 2 already held it.", " ".join(second.output.split()))
        self.assertEqual(stamps, {path: os.stat(path).st_mtime_ns for path in (a, b)}, "a file holding its bake is not written")
        self.assertBaked(c, 0)
        self.assertNoBakeListed()

    def test_history_lists_no_bake_and_a_save_before_one_is_undone_past_it(self):
        a = self.picture("a.jpg", (200, 40, 40))
        self.index()
        saved = tagging.save_photo(self.library, a, "Ferry at the quay", ["Harbour/Ferry"], None, EXIFTOOL, "")
        self.assertTrue(saved.changed, saved.refused)
        (face_id,) = [face_id for (face_id,) in self.query("SELECT id FROM faces")]
        self.assertEqual(1, duplicate_faces.remove(self.library, [face_id]))
        self.picture("b.jpg", (40, 200, 40))
        self.index()   # the folder's bake writes a.jpg again, after the save
        self.assertBaked(a, 0)
        self.assertNoBakeListed()
        self.assertIsNone(journal_service.history(self.library, change_id=saved.details["change"])["changes"][0]["why_not"])
        undone = journal_service.undo(self.library, saved.details["change"], apply=True, exiftool_path=EXIFTOOL)
        self.assertFalse(undone.refused, undone.refused)
        self.assertBaked(a, 0)

    def test_a_crash_at_each_step_of_a_files_write_is_settled_and_leaves_no_record(self):
        for n, step in enumerate(file_changes.STEPS):
            with self.subTest(step=step):
                path = self.picture("crash %d.jpg" % n, (20 * n, 90, 160))
                with mock.patch.object(file_changes, "_reached", side_effect=crash_at(step)):
                    with self.assertRaises(Crash):
                        self.index()
                self.assertEqual([baking.INDEX_OPERATION], [each.operation for each in file_journal.unfinished(self.db_path)])
                self.assertEqual([], [entry for entry in journal_service.history(self.library)["changes"]
                                      if entry["operation"] == baking.INDEX_OPERATION], "in flight, it is listed nowhere")
                file_changes.settle(self.library, EXIFTOOL)
                self.assertNoBakeListed()
                bake_after_index.bake_folders(self.library, EXIFTOOL, DETECTOR, [self.folder], {})
                self.assertBaked(path, 0)
                self.assertNoBakeListed()

    def test_the_index_hands_its_decode_over_and_the_bake_decodes_nothing(self):
        paths_made = [self.picture(name, colour) for name, colour in (("a.jpg", (200, 40, 40)), ("b.jpg", (40, 200, 40)))]
        bake_folders = bake_after_index.bake_folders

        def without_a_decode(*args, **kwargs):
            with mock.patch.object(images, "fingerprint", side_effect=AssertionError("decoded again")):
                return bake_folders(*args, **kwargs)
        with mock.patch.object(bake_after_index, "bake_folders", without_a_decode):
            result = self.index()
        self.assertIn("Bake: 2 file(s) written", " ".join(result.output.split()))
        for path, faces in zip(paths_made, (1, 0)):
            self.assertBaked(path, faces)

    def test_a_face_deleted_in_tagpup_is_gone_from_the_file_at_the_folders_next_index_and_does_not_return(self):
        a = self.picture("a.jpg", (200, 40, 40))
        self.index()
        self.assertBaked(a, 1)
        (face_id,) = [face_id for (face_id,) in self.query("SELECT id FROM faces")]
        self.assertEqual(1, duplicate_faces.remove(self.library, [face_id]))
        self.picture("b.jpg", (40, 200, 40))
        self.index()
        self.assertBaked(a, 0)
        self.picture("c.jpg", (40, 40, 200))
        self.index()
        self.assertEqual([], self.boxes(a), "the file's old bake gave the face back")
        self.assertBaked(a, 0)


if __name__ == "__main__":
    unittest.main()
