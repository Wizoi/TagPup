"""The index writes the bake on its own (docs/PLAN_KEEP_IN_STEP.md, cut 2; scenarios 2, 11, 13 and 14): the owner indexes a
folder -- by hand, or the queue does after a sync -- and runs no bake command.

What the owner lives through, each with the CLI's index over a real ExifTool and the stand-in models (stand_in_models):
- a new folder is indexed: each file then holds a bake that matches its row, and the next index of the folder rewrites
  no file that holds it already;
- History never lists a bake, and a keyword save before one is undone past it;
- the process is killed at each step of a file's write: the next start settles it, the fingerprint row follows, and the
  finished record is gone;
- the index's own decode of a photo is its fingerprint: the bake decodes no photo again;
- a share stops answering part-way through the bake: what it says names no change in History to undo;
- a face deleted from the library (one of a photo's two) is left in the file: a bake under the library's keys of the
  picture is never rewritten (owner, DECISIONS 2026-10-27), the file's mtime does not move, and the face does not come
  back in the library (the index takes faces from a bake only for a photo with no vector or never examined);
- a PNG is baked as a JPEG is;
- the owner turns a photo in the app between the bake's plan of its folder and its file's write (#1925): the bake
  describes the picture as planned, which the photo's decode now tells apart, so the read side rejects it.

Lifted: the deleted `bake-photos` command's tests of a PNG and of a turn between the plan and the write
(test_a_bake_is_written_folder_by_folder), now of the index's bake.
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
from tagpup.files import bake, field_values, images  # noqa: E402
from tagpup.ml import clip  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import bake_after_index, baking, batches, duplicate_faces, file_changes, tagging  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photos as photo_service  # noqa: E402
from tagpup.store import db, file_journal  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
SIZE = (640, 480)
DETECTOR = face_service.detector_of(NewFaces(), None)
FACE = [300, 200, 400, 330]
SECOND = [60, 60, 160, 180]


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

    def two_faces_in_a(self):
        """a.jpg holds two faces, FACE and SECOND, the second smaller."""
        self.detector.found["a.jpg"] = [(FACE, 0.99, 1), (SECOND, 0.98, 2)]

    def delete_the_second_face(self, path):
        """Delete SECOND of the photo at `path` as the owner does, by its id; its photo keeps FACE."""
        (face_id,) = [face_id for face_id, path_of, box in self.query(
            "SELECT f.id, p.path, f.box FROM faces f JOIN photos p ON p.id = f.photo_id")
            if paths.key(path_of) == paths.key(path) and json.loads(box) == SECOND]
        self.assertEqual(1, duplicate_faces.remove(self.library, [face_id]))

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
        with mock.patch.object(bake_after_index, "bake_folders"):
            self.index()   # indexed, not baked yet
        saved = tagging.save_photo(self.library, a, "Ferry at the quay", ["Harbour/Ferry"], None, EXIFTOOL, "")
        self.assertTrue(saved.changed, saved.refused)
        self.picture("b.jpg", (40, 200, 40))
        self.index()   # the folder's bake writes a.jpg, after the save
        self.assertBaked(a, 1)
        self.assertNoBakeListed()
        self.assertIsNone(journal_service.history(self.library, change_id=saved.details["change"])["changes"][0]["why_not"])
        undone = journal_service.undo(self.library, saved.details["change"], apply=True, exiftool_path=EXIFTOOL)
        self.assertFalse(undone.refused, undone.refused)
        self.assertBaked(a, 1)

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

    def test_a_ctrl_c_in_the_bake_leaves_the_runs_new_faces_matched(self):
        self.picture("a.jpg", (200, 40, 40))
        matched = []
        real = tagpup_cli._match_new_faces

        def match(db_path, after, upto):
            matched.append((after, upto))
            return real(db_path, after, upto)
        with mock.patch.object(tagpup_cli, "_match_new_faces", side_effect=match), \
                mock.patch.object(bake_after_index, "bake_folders", side_effect=KeyboardInterrupt):
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", self.folder])
        self.assertNotEqual(0, result.exit_code, "the Ctrl+C stopped the run")
        ((face_id,),) = self.query("SELECT id FROM faces")
        self.assertEqual(1, len(matched), "the run's new faces were not matched")
        after, upto = matched[0]
        self.assertTrue(after < face_id <= upto, matched)

    def test_a_png_is_indexed_and_baked(self):
        path = os.path.join(self.folder, "harbour.png")
        Image.new("RGB", SIZE, (40, 120, 200)).save(path, "PNG")
        self.detector.found["harbour.png"] = [(FACE, 0.99, 1)]
        self.index()
        self.assertBaked(path, 1)
        self.assertNoBakeListed()

    def test_a_photo_turned_between_the_bakes_plan_and_its_write_is_baked_as_planned_and_told_apart(self):
        a = self.picture("a.jpg", (200, 40, 40))
        before = images.fingerprint(images.opened(a, upright=True))
        real = file_changes.write_fields

        def turn_first(library, operation, *args, **kwargs):
            if operation == baking.INDEX_OPERATION:
                # The owner clicks Rotate in the app while the bake has planned the folder: the turn's own write first.
                turn = photo_service.rotate(library, a, "left", EXIFTOOL)
                self.assertEqual((None, []), (turn.refused, turn.errors))
            return real(library, operation, *args, **kwargs)
        with mock.patch.object(file_changes, "write_fields", side_effect=turn_first):
            self.index()
        held = baked_files.held(a)
        now = images.fingerprint(images.opened(a, upright=True))
        self.assertNotEqual(now, before)
        self.assertEqual(before, held.fingerprint, "the bake describes the picture as planned...")
        self.assertNotEqual(now, held.fingerprint, "...which its decode now tells apart: the read side rejects it")

    def test_a_file_changed_between_the_index_and_the_bake_is_decoded_and_not_baked_with_the_old_picture(self):
        a = self.picture("a.jpg", (200, 40, 40))
        bake_folders, ran = bake_after_index.bake_folders, []

        def after_a_change(*args, **kwargs):
            Image.new("RGB", SIZE, (10, 10, 240)).save(a, "JPEG")   # another program saves another picture
            later = os.stat(a).st_mtime + 5
            os.utime(a, (later, later))
            ran.append(bake_folders(*args, **kwargs))
        with mock.patch.object(bake_after_index, "bake_folders", after_a_change):
            self.index()
        (baked,) = ran
        self.assertEqual(0, baked.written)
        self.assertEqual({baking.NO_VECTOR: 1}, dict(baked.cannot))
        self.assertIsNone(getattr(baked_files.held(a), "fingerprint", None), "the old picture's bake was written into the new one")

    def test_a_refused_folder_is_said_and_roots_changed_ends_the_run(self):
        quay = paths.stored(os.path.join(self.home.root, "Quay"))
        os.makedirs(quay)
        for folder in (self.folder, quay):
            for name, colour in (("a.jpg", (200, 40, 40)), ("b.jpg", (40, 200, 40))):
                Image.new("RGB", SIZE, colour).save(os.path.join(folder, name), "JPEG")
        with mock.patch.object(bake_after_index, "bake_folders"):
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", self.folder, quay])
        self.assertEqual(0, result.exit_code, result.output)

        said = []
        with mock.patch.object(batches, "MAX_BATCH", 1):
            baked = bake_after_index.bake_folders(self.library, EXIFTOOL, DETECTOR, [self.folder, quay], {}, told=said.append)
        self.assertEqual((0, {"Select up to 1 photos at a time (2 were named)": 2}),
                         (baked.written, {why.rstrip("."): n for why, n in baked.refused.items()}))
        self.assertIn("2 folder(s) refused: Select up to 1 photos at a time (2 were named)", said[0])

        planned = []
        real_planned = bake_after_index._planned

        def planning(library, et, detector, folder, *args):
            planned.append(folder)
            return real_planned(library, et, detector, folder, *args)
        with mock.patch.object(file_changes.roots_service, "pinned", side_effect=roots_service.RootsChanged("moved")), \
                mock.patch.object(bake_after_index, "_planned", side_effect=planning):
            baked = bake_after_index.bake_folders(self.library, EXIFTOOL, DETECTOR, [self.folder, quay], {}, told=said.append)
        self.assertEqual([self.folder], planned, "the run went on after the roots changed")
        self.assertTrue(baked.roots_changed)
        self.assertIn("1 folder(s) refused: %s" % bake_after_index.ROOTS_CHANGED, said[1])

    def test_a_share_that_stops_answering_in_the_bake_names_no_change_to_undo(self):
        photos = [self.picture(name, colour) for name, colour in (("a.jpg", (200, 40, 40)), ("b.jpg", (40, 200, 40)),
                                                                  ("c.jpg", (40, 40, 200)))]
        found, real = bake.WRITTEN[bake.FACES_FOUND], field_values.read_one

        def read_one(et, path, *args, **kwargs):
            if paths.key(path) == paths.key(photos[1]):
                raise field_values.NotAnswered("ExifTool did not answer in 300 s")
            return real(et, path, *args, **kwargs)
        with mock.patch.object(file_changes.field_values, "read_one", side_effect=read_one):
            result = file_changes.write_fields(self.library, baking.INDEX_OPERATION, EXIFTOOL, photos, [found],
                                               lambda _path, _held: file_changes.Plan(after={found: ["0"]}))
        said = " ".join(result.refused.split())
        self.assertIn("ExifTool stopped answering after 1 of 3 photos: 1 file(s) were written; the rest", said)
        self.assertNotIn("History", said)
        self.assertNotIn("undone", said)
        self.assertNoBakeListed()

    def test_a_face_missing_from_the_rows_but_held_by_a_current_bake_is_left_alone_and_does_not_return(self):
        self.two_faces_in_a()
        a = self.picture("a.jpg", (200, 40, 40))
        self.index()
        self.assertBaked(a, 2)
        stamp = os.stat(a).st_mtime_ns
        self.delete_the_second_face(a)
        for held, (name, colour) in enumerate((("b.jpg", (40, 200, 40)), ("c.jpg", (40, 40, 200))), start=1):
            self.picture(name, colour)   # the folder indexed and baked again
            result = self.index()
            self.assertIn("1 file(s) written, %d already held it." % held, " ".join(result.output.split()))
            self.assertIsInstance(baked_files.plan(self.db_path, a, DETECTOR), baking.Current)
            self.assertEqual(2, len(baked_files.held(a).faces), "the file keeps the bake it holds")
            self.assertEqual(stamp, os.stat(a).st_mtime_ns, "a current bake was rewritten")
            self.assertEqual([FACE], self.boxes(a), "the file's bake gave the face back")


if __name__ == "__main__":
    unittest.main()
