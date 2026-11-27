"""The index writes the bake on its own (docs/PLAN_KEEP_IN_STEP.md, cut 2; scenarios 2, 11, 13 and 14): the owner indexes a
folder -- by hand, or the queue does after a sync -- and runs no bake command.

What the owner lives through, each with the CLI's index over a real ExifTool and the stand-in models (stand_in_models):
- a new folder is indexed: each file then holds a bake that matches its row, and the next index of the folder rewrites
  no file that holds it already;
- History never lists a bake, and a keyword save before one is undone past it;
- the process is killed at each step of a file's write: the next start settles it, the fingerprint row follows, and the
  finished record is gone;
- the index's own decode of a photo is its fingerprint: the bake decodes no photo again;
- a write whose picture came back changed (field_values.PictureChanged, #2377) is discarded and made again, and the
  index's line says how many were;
- a share stops answering part-way through the bake: what it says names no change in History to undo;
- a face deleted from the library (one of a photo's two) is left in the file: a bake under the library's keys of the
  picture is never rewritten (owner, DECISIONS 2026-10-27), the file's mtime does not move, and the face does not come
  back in the library (the index takes faces from a bake only for a photo with no vector or never examined);
- a PNG is baked as a JPEG is;
- the owner turns a photo in the app between the bake's plan of its folder and its file's write (#1925): the bake
  describes the picture as planned, which the photo's decode now tells apart, so the read side rejects it;
- a photo whose vector Suggest kept (no fingerprint taken: its row forgotten) was never baked (9,882 such in
  photo_index, 2026-10-28): sync owes its folder to the index, which bakes its file with no model and no GPU turn and
  records its row; a file holding its bake already, or one ExifTool cannot write (read-only), is given its row with no
  write, so sync is quiet after; a share that stops answering in the second of two folders leaves only that one owed;
- a row is recorded only for a file that holds the library's bake, or whose write no retry changes (read-only): a share
  that stops answering while its folder is planned, a file another program holds open (it is baked once let go), a file
  another program changed between the plan and the write (a conflict), and a bake that falls over between two folders
  of a new index (the index's rows of the folder not reached are forgotten) each leave their photos owed, and the next
  index bakes them; so do a new folder's photos whose share stops answering while the bake reads the folder, a new
  photo another program holds open through its first index, and a Ctrl+C part-way through a folder's write, after which
  the files written keep their rows and the rest are owed;
- a folder of many photos says how far its write has got as each chunk of its files ends, and a share that stops
  answering mid-chunk stops the lines: the page can say it has not moved (photo_index, 2026-10-28: 759 files baked over
  57 minutes with no line). The lines change nothing written: the files not reached are owed as before.

Lifted: the deleted `bake-photos` command's tests of a PNG and of a turn between the plan and the write
(test_a_bake_is_written_folder_by_folder), now of the index's bake.
"""
import json
import os
import stat
import sys
import unittest
from unittest import mock

from click.testing import CliRunner
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baked_files  # noqa: E402
import page_writes  # noqa: E402
import own_home  # noqa: E402
from stand_in_models import Hold, NewFaces, RefusableFaces  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths, processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import bake, exiftool_session, field_values, images  # noqa: E402
from tagpup.ml import clip  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import bake_after_index, baking, batches, file_changes, tagging  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photos as photo_service  # noqa: E402
from tagpup.services import search, sync  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db, file_journal  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
SIZE = (640, 480)
DETECTOR = face_service.detector_of(NewFaces())
FACE = [300, 200, 400, 330]
SECOND = [60, 60, 160, 180]


class _StallsAfter:
    """An ExifTool session that answers its first `answered` reads and then does not, as a share that goes away
    part-way through a folder's read: field_values.read's batch read sees ExifTool time out."""

    def __init__(self, et, answered):
        self.et, self.answered = et, answered

    def get_tags(self, *args, **kwargs):
        if self.answered <= 0:
            raise exiftool_session.ExifToolTimeout("ExifTool did not answer in 300 s")
        self.answered -= 1
        return self.et.get_tags(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.et, name)


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

    def index(self, *folders):
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", *(folders or (self.folder,))])
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
        """Delete SECOND of the photo at `path` from the library's rows, by its id; its photo keeps FACE."""
        (face_id,) = [face_id for face_id, path_of, box in self.query(
            "SELECT f.id, p.path, f.box FROM faces f JOIN photos p ON p.id = f.photo_id")
            if paths.key(path_of) == paths.key(path) and json.loads(box) == SECOND]
        self.assertEqual(1, db.write_with_connection(
            self.db_path, lambda conn: conn.execute("DELETE FROM faces WHERE id = ?", (face_id,)).rowcount))

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
        saved = tagging.save_photo(self.library, a, "Ferry at the quay", ["Harbour/Ferry"], None, EXIFTOOL, "",
                                   base=page_writes.base(self.library, a))
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
        # ExifTool's rewrite saves no patch: its write never reaches PATCH_SAVED.
        for n, step in enumerate(step for step in file_changes.STEPS if step != file_changes.PATCH_SAVED):
            with self.subTest(step=step):
                path = self.picture("crash %d.jpg" % n, (20 * n, 90, 160))
                with mock.patch.object(file_changes, "_reached", side_effect=crash_at(step)):
                    with self.assertRaises(Crash):
                        self.index()
                self.assertEqual([baking.INDEX_OPERATION], [each.operation for each in file_journal.unfinished(self.db_path)])
                self.assertEqual([], [entry for entry in journal_service.history(self.library)["changes"]
                                      if entry["operation"] == baking.INDEX_OPERATION], "in flight, it is listed nowhere")
                # the new file beside the photo that a crash at "scratch written" left: a restart within
                # SCRATCH_STALE of it is told to try again later; the settle here is the one after that
                with mock.patch.object(file_changes, "SCRATCH_STALE", 0):
                    file_changes.settle(self.library, EXIFTOOL)
                self.assertNoBakeListed()
                bake_after_index.bake_folders(self.library, EXIFTOOL, DETECTOR, [self.folder], {})
                self.assertBaked(path, 0)
                self.assertNoBakeListed()

    def test_a_write_that_changed_the_picture_is_made_again_and_the_line_says_so(self):
        path = self.picture("a.jpg", (200, 40, 40))
        write, calls = field_values.write, []

        def damaged_once(*args, **kwargs):
            # The journal's writes (file_changes._written: the bake's), not the identity mint's before them.
            if "scratch_written" in kwargs:
                calls.append(args[1])
                if len(calls) == 1:
                    raise field_values.PictureChanged("its write changed the picture's bytes; the new file was discarded")
            return write(*args, **kwargs)
        with mock.patch.object(field_values, "write", damaged_once):
            said = " ".join(self.index().output.split())
        self.assertEqual([path, path], calls)
        self.assertIn("Bake: 1 file(s) written", said)
        self.assertIn(bake_after_index._SAID_REFUSED % 1, said)
        self.assertBaked(path, 1)
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
            return ran[-1]
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
            baked = bake_after_index.bake_folders(self.library, EXIFTOOL, DETECTOR, [self.folder, quay], {})
        said.append(baked.said())
        self.assertEqual((0, {"Select up to 1 photos at a time (2 were named)": 2}),
                         (baked.written, {why.rstrip("."): n for why, n in baked.refused.items()}))
        self.assertIn("2 folder(s) refused: Select up to 1 photos at a time (2 were named)", said[0])

        planned = []
        real_planned = bake_after_index._planned

        def planning(library, fields, detector, folder, *args):
            planned.append(folder)
            return real_planned(library, fields, detector, folder, *args)
        with mock.patch.object(file_changes.roots_service, "pinned", side_effect=roots_service.RootsChanged("moved")), \
                mock.patch.object(bake_after_index, "_planned", side_effect=planning):
            baked = bake_after_index.bake_folders(self.library, EXIFTOOL, DETECTOR, [self.folder, quay], {})
        said.append(baked.said())
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

    def test_a_large_folder_says_each_chunk_written_and_a_share_that_stops_mid_chunk_stops_the_lines(self):
        photos = [self.picture("heat %d.jpg" % number, (40 * number, 90, 160)) for number in range(5)]
        with mock.patch.object(bake_after_index, "bake_folders"):
            self.index()
        heard = lambda facts: [(found.get("files_done"), found.get("files")) for found in facts]
        real = field_values.read_one

        def read_one(et, path, *args, **kwargs):
            if paths.key(path) == paths.key(photos[3]):
                raise field_values.NotAnswered("ExifTool did not answer in 300 s")
            return real(et, path, *args, **kwargs)
        lines = []
        with mock.patch.object(batches, "CHUNK", 2), \
                mock.patch.object(file_changes.field_values, "read_one", side_effect=read_one):
            baked = bake_after_index.bake_folders(self.library, EXIFTOOL, DETECTOR, [self.folder], {}, each=lines.append)
        self.assertTrue(baked.stalled)
        self.assertEqual([(None, None), (2, 5)], heard(lines), "no line after the share stopped answering")
        self.assertIn(", 2 of 5 of its files reached: ", lines[1]["sentence"])
        self.assertEqual(3, baked.written, baked)
        for path in photos[:3]:
            self.assertBaked(path, 0)

        lines = []
        with mock.patch.object(batches, "CHUNK", 2):
            baked = bake_after_index.bake_folders(self.library, EXIFTOOL, DETECTOR, [self.folder], {}, each=lines.append)
        self.assertEqual((2, 3), (baked.written, baked.current), baked)
        self.assertEqual([(None, None)], heard(lines), "two files to write are one chunk")
        for path in photos:
            self.assertBaked(path, 0)
        self.assertNoBakeListed()

    def test_a_progress_line_that_cannot_be_said_does_not_end_the_write(self):
        """A repo-run server restarting on a .py save orphans its indexer: the stdout pipe's reader is gone and the
        line raises. The write goes on to its last file, the failure is logged once, no row is left planned."""
        photos = [self.picture("heat %d.jpg" % number, (40 * number, 90, 160)) for number in range(7)]
        with mock.patch.object(bake_after_index, "bake_folders"):
            self.index()
        lines = []

        def each(line):
            lines.append(line)
            if len(lines) == 3:   # the folder's line, the first chunk's, the second chunk's: this one raises
                raise OSError("the pipe is closed")
        with mock.patch.object(batches, "CHUNK", 2), \
                self.assertLogs(bake_after_index.logger, "WARNING") as logged:
            baked = bake_after_index.bake_folders(self.library, EXIFTOOL, DETECTOR, [self.folder], {}, each=each)
        self.assertEqual(7, baked.written, baked)
        self.assertEqual(1, len(logged.records), "the failure is logged once, not per chunk")
        self.assertEqual(3, len(lines), "a line was said after one failed")
        for path in photos:
            self.assertBaked(path, 0)
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

    def suggest_keeps_a_vector(self, *photo_paths):
        """Suggest keeps a vector of each photo through its own writer (PhotoEmbeddings.keep): stamped with the file as it
        is, no fingerprint taken, so the photo's fingerprint row is forgotten (photo_fingerprints.follows)."""
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            key = library_settings.photo_key_on(conn)
        finally:
            conn.close()
        index = search.PhotoIndex(self.db_path, key)
        self.assertTrue(index.open())
        try:
            kept = search.PhotoEmbeddings(None, index, model_key=key)
            for path in photo_paths:
                self.assertTrue(kept.keep(path, baked_files.unit(1152, 7).tolist()), path)
        finally:
            index.close()
        for path in photo_paths:
            self.assertEqual([], self.fingerprint_row(path))

    def looked(self):
        """What a dry run of sync finds: (photos owed only their bake, folders owed, in step)."""
        found = sync.sync(self.library, apply=False, exiftool_path=EXIFTOOL)
        counts = found.details["counts"]
        return counts.get("owed_no_fingerprint"), counts["owed_folders"], found.details["in_step"]

    def index_with_no_model(self, *folders):
        """The index, failing if it takes a turn of the graphics card or makes a vector."""
        with mock.patch.object(Runtime, "gpu_turn", side_effect=AssertionError("a GPU turn was taken")), \
                mock.patch.object(clip.ClipModel, "embed_input", side_effect=AssertionError("a vector was made")):
            return " ".join(self.index(*folders).output.split())

    def test_a_photo_whose_vector_suggest_kept_is_baked_by_the_next_index_with_no_model(self):
        a, b = self.picture("a.jpg", (200, 40, 40)), self.picture("b.jpg", (40, 200, 40))
        with mock.patch.object(bake_after_index, "bake_folders"):
            self.index()   # indexed, not baked
        self.assertEqual([None, None], [getattr(baked_files.held(path), "fingerprint", None) for path in (a, b)])
        self.suggest_keeps_a_vector(a, b)
        self.assertEqual((2, 1, False), self.looked(), "a photo never baked is work the index owes")

        said = self.index_with_no_model()
        self.assertIn("2 photo(s) in 1 folder(s) hold a current vector whose picture was never fingerprinted", said)
        self.assertIn("Bake: 2 file(s) written, 0 already held it.", said)
        self.assertBaked(a, 1)
        self.assertBaked(b, 0)
        self.assertNoBakeListed()
        self.assertEqual((0, 0, True), self.looked())

        stamps = {path: os.stat(path).st_mtime_ns for path in (a, b)}
        self.assertNotIn("Bake:", self.index_with_no_model(), "nothing owed: no bake run")
        self.assertEqual(stamps, {path: os.stat(path).st_mtime_ns for path in (a, b)})

        # Another program writes a keyword into a baked file, a sync reads its row again, and Suggest, finding its
        # vector stale, keeps a new one: the user action that leaves a file holding its bake with no fingerprint row
        # (photo_fingerprints.adopt's instance).
        done = processes.run([EXIFTOOL, "-overwrite_original", "-XMP-dc:Subject+=Harbour/Pier", a],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(1, sync.sync(self.library, apply=True, exiftool_path=EXIFTOOL).details["changed"]["from_files"])
        self.suggest_keeps_a_vector(a)
        stamp = os.stat(a).st_mtime_ns
        said = self.index_with_no_model()
        self.assertIn("Bake: 0 file(s) written, 2 already held it; 1 fingerprint(s) recorded with no write", said)
        self.assertEqual(stamp, os.stat(a).st_mtime_ns, "a file holding its bake was written")
        self.assertBaked(a, 1)
        self.assertEqual((0, 0, True), self.looked())

    def test_a_file_that_cannot_be_written_is_said_and_given_its_row_so_sync_is_quiet(self):
        a = self.picture("a.jpg", (200, 40, 40))
        with mock.patch.object(bake_after_index, "bake_folders"):
            self.index()
        self.suggest_keeps_a_vector(a)
        os.chmod(a, stat.S_IREAD)
        self.addCleanup(os.chmod, a, stat.S_IREAD | stat.S_IWRITE)
        said = self.index_with_no_model()
        self.assertIn("Bake: 0 file(s) written, 0 already held it; 1 fingerprint(s) recorded with no write; 1 failed", said)
        self.assertIsNone(getattr(baked_files.held(a), "fingerprint", None))
        self.assertEqual(1, len(self.fingerprint_row(a)), "a refused write left its photo owed: sync queues it forever")
        self.assertEqual((0, 0, True), self.looked())

    def test_a_share_that_stops_answering_in_the_second_folder_leaves_only_that_one_owed(self):
        quay = paths.stored(os.path.join(self.home.root, "Quay"))
        os.makedirs(quay)
        made = {folder: [os.path.join(folder, name) for name in ("a.jpg", "b.jpg")] for folder in (self.folder, quay)}
        for folder_paths in made.values():
            for path, colour in zip(folder_paths, ((200, 40, 40), (40, 200, 40))):
                Image.new("RGB", SIZE, colour).save(path, "JPEG")
        with mock.patch.object(bake_after_index, "bake_folders"):
            self.index(self.folder, quay)
        self.suggest_keeps_a_vector(*made[self.folder], *made[quay])
        self.assertEqual((4, 2, False), self.looked())
        real = field_values.read_one

        def read_one(et, path, *args, **kwargs):
            if paths.key(os.path.dirname(path)) == paths.key(quay):
                raise field_values.NotAnswered("ExifTool did not answer in 300 s")
            return real(et, path, *args, **kwargs)
        with mock.patch.object(file_changes.field_values, "read_one", side_effect=read_one):
            said = self.index_with_no_model(self.folder, quay)
        self.assertIn("Bake: 2 file(s) written", said)
        for path in made[self.folder]:
            self.assertBaked(path, 1 if os.path.basename(path) == "a.jpg" else 0)
        for path in made[quay]:
            self.assertEqual([], self.fingerprint_row(path), "a file not written was given a row")
        self.assertEqual((2, 1, False), self.looked(), "the folder the share did not answer for is owed again")

    def never_baked(self, *names):
        """Photos of the folder indexed, not baked, and their vectors kept by Suggest: owed only their bake."""
        made = [self.picture(name, colour) for name, colour in zip(names, ((200, 40, 40), (40, 200, 40), (40, 40, 200)))]
        with mock.patch.object(bake_after_index, "bake_folders"):
            self.index()
        self.suggest_keeps_a_vector(*made)
        return made

    def test_a_share_that_stops_answering_while_its_folder_is_read_leaves_every_photo_not_written_owed(self):
        a, b = self.never_baked("a.jpg", "b.jpg")
        real = field_values.read

        def folder_read(et, photo_paths, *args, **kwargs):
            # The folder's one read (baking.FolderRead), in batches of one: the share goes away after the first.
            if len(photo_paths) > 1:
                return real(_StallsAfter(et, 1), photo_paths, *args, **kwargs)
            return real(et, photo_paths, *args, **kwargs)
        with mock.patch.object(field_values, "READ_BATCH", 1), \
                mock.patch.object(field_values, "read", side_effect=folder_read):
            baked = bake_after_index.bake_folders(self.library, EXIFTOOL, DETECTOR, [self.folder], {})
        self.assertEqual((True, 0, 0), (baked.stalled, baked.written, baked.adopted))
        self.assertEqual([[], []], [self.fingerprint_row(path) for path in (a, b)],
                         "a photo planned to bake and never written was given a row")
        self.assertEqual((2, 1, False), self.looked())
        said = self.index_with_no_model()
        self.assertIn("Bake: 2 file(s) written", said)
        self.assertBaked(a, 1)
        self.assertBaked(b, 0)
        self.assertEqual((0, 0, True), self.looked())

    def test_a_file_another_program_holds_open_is_said_and_stays_owed_until_it_is_let_go(self):
        (a,) = self.never_baked("a.jpg")
        held = open(a, "rb")   # Explorer's preview, a scanner, another machine: ExifTool cannot replace the file
        self.addCleanup(held.close)
        said = self.index_with_no_model()
        self.assertIn("Bake: 0 file(s) written, 0 already held it; 1 failed", said)
        self.assertNotIn("recorded with no write", said)
        self.assertEqual([], self.fingerprint_row(a), "a write that may pass next time was given a row")
        self.assertEqual((1, 1, False), self.looked())
        held.close()
        self.assertIn("Bake: 1 file(s) written", self.index_with_no_model())
        self.assertBaked(a, 1)
        self.assertEqual((0, 0, True), self.looked())

    def test_a_file_another_program_changes_between_the_plan_and_the_write_is_a_conflict_and_gets_no_row(self):
        (a,) = self.never_baked("a.jpg")
        named = [EXIFTOOL, "-overwrite_original", "-XMP-mwg-rs:RegionInfo={AppliedToDimensions={W=640,H=480,Unit=pixel},"
                 "RegionList=[{Area={X=0.5,Y=0.5,W=0.1,H=0.1,Unit=normalized},Name=Ada Quill,Type=Face}]}", a]
        once = []

        def another_program_names_a_face(step):
            if step == "plan committed" and not once:
                once.append(step)
                done = processes.run(named, capture_output=True, text=True, timeout=60)
                self.assertEqual(0, done.returncode, done.stderr)
        with mock.patch.object(file_changes, "_reached", side_effect=another_program_names_a_face):
            said = self.index_with_no_model()
        self.assertEqual(["plan committed"], once)
        self.assertIn("1 conflict(s), left as they are", said)
        self.assertNotIn("recorded with no write", said)
        self.assertEqual([], self.fingerprint_row(a), "a photo whose write met a conflict was given a row")

    def test_a_bake_that_falls_over_between_two_folders_leaves_the_second_owed_and_the_next_index_bakes_it(self):
        quay = paths.stored(os.path.join(self.home.root, "Quay"))
        os.makedirs(quay)
        made = {folder: [os.path.join(folder, name) for name in ("a.jpg", "b.jpg")] for folder in (self.folder, quay)}
        for folder_paths in made.values():
            for path, colour in zip(folder_paths, ((200, 40, 40), (40, 200, 40))):
                Image.new("RGB", SIZE, colour).save(path, "JPEG")
        real, planned = bake_after_index._planned, []

        def falls_over_at_the_second(library, fields, detector, folder, *args):
            planned.append(folder)
            if len(planned) == 2:
                raise RuntimeError("the bake fell over")
            return real(library, fields, detector, folder, *args)
        with mock.patch.object(bake_after_index, "_planned", side_effect=falls_over_at_the_second):
            said = " ".join(self.index(self.folder, quay).output.split())
        self.assertIn("Bake: stopped (RuntimeError)", said)
        first, second = (made[paths.stored(folder)] for folder in planned)
        for path in first:
            self.assertBaked(path, 1 if os.path.basename(path) == "a.jpg" else 0)
        self.assertEqual([[], []], [self.fingerprint_row(path) for path in second],
                         "the index's rows of a folder the bake never reached keep it from being owed")
        self.assertEqual((2, 1, False), self.looked())
        self.assertIn("Bake: 2 file(s) written", self.index_with_no_model(self.folder, quay))
        for path in second:
            self.assertBaked(path, 1 if os.path.basename(path) == "a.jpg" else 0)
        self.assertEqual((0, 0, True), self.looked())

    def test_a_ctrl_c_between_the_index_and_its_bake_leaves_the_recorded_photos_owed_and_the_next_index_bakes_them(self):
        """The index records a photo's row as each batch is saved; a Ctrl+C before the bake starts (here while the faces it
        made are matched) used to leave the photo in step with an unbaked file."""
        a, b = self.picture("a.jpg", (200, 40, 40)), self.picture("b.jpg", (40, 200, 40))
        with mock.patch.object(tagpup_cli, "_match_new_faces", side_effect=KeyboardInterrupt):
            interrupted = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", self.folder])
        self.assertEqual(1, interrupted.exit_code, interrupted.output)
        self.assertEqual([[], []], [self.fingerprint_row(path) for path in (a, b)])
        self.assertEqual((2, 1, False), self.looked())
        self.assertIn("Bake: 2 file(s) written", self.index_with_no_model(self.folder))
        self.assertEqual((0, 0, True), self.looked())

    def test_a_new_folders_share_that_stops_answering_while_the_bake_reads_it_leaves_its_photos_owed(self):
        a, b = self.picture("a.jpg", (200, 40, 40)), self.picture("b.jpg", (40, 200, 40))
        real_read, real_bake = field_values.read, bake_after_index.bake_folders

        def folder_read(et, photo_paths, *args, **kwargs):
            # The bake's one read of the folder (baking.FolderRead), in batches of one: the share goes away after the first.
            if len(photo_paths) > 1:
                return real_read(_StallsAfter(et, 1), photo_paths, *args, **kwargs)
            return real_read(et, photo_paths, *args, **kwargs)

        def bake(*args, **kwargs):
            with mock.patch.object(field_values, "READ_BATCH", 1), \
                    mock.patch.object(field_values, "read", side_effect=folder_read):
                return real_bake(*args, **kwargs)
        with mock.patch.object(bake_after_index, "bake_folders", side_effect=bake):
            said = " ".join(self.index().output.split())
        self.assertIn("ended at a folder whose share stopped answering", said)
        self.assertEqual([[], []], [self.fingerprint_row(path) for path in (a, b)],
                         "the index's row of a photo the bake did not write keeps it from being owed")
        self.assertEqual((2, 1, False), self.looked(), "an unbaked new photo was called in step")
        self.assertIn("Bake: 2 file(s) written", self.index_with_no_model())
        self.assertBaked(a, 1)
        self.assertBaked(b, 0)
        self.assertEqual((0, 0, True), self.looked())

    def test_a_new_photo_another_program_holds_open_through_its_first_index_stays_owed_until_it_is_let_go(self):
        a = self.picture("a.jpg", (200, 40, 40))
        held = open(a, "rb")   # Explorer's preview, a scanner, another machine: ExifTool cannot replace the file
        self.addCleanup(held.close)
        said = " ".join(self.index().output.split())
        self.assertIn("Bake: 0 file(s) written, 0 already held it; 1 failed", said)
        self.assertEqual([], self.fingerprint_row(a), "a write that may pass next time kept the index's row")
        self.assertEqual((1, 1, False), self.looked(), "an unbaked new photo was called in step")
        held.close()
        self.assertIn("Bake: 1 file(s) written", self.index_with_no_model())
        self.assertBaked(a, 1)
        self.assertEqual((0, 0, True), self.looked())

    def test_a_ctrl_c_part_way_through_a_folders_write_keeps_the_rows_of_the_files_written(self):
        a, b = self.picture("a.jpg", (200, 40, 40)), self.picture("b.jpg", (40, 200, 40))
        stops_at_the_second = crash_at("file writing", nth=2)

        def reached(step):
            try:
                stops_at_the_second(step)
            except Crash:
                raise KeyboardInterrupt from None
        with mock.patch.object(file_changes, "_reached", side_effect=reached):
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", self.folder])
        self.assertNotEqual(0, result.exit_code, "the Ctrl+C stopped the run")
        self.assertBaked(a, 1)
        self.assertEqual([], self.fingerprint_row(b), "a file the Ctrl+C kept from its write kept the index's row")
        self.assertEqual((1, 1, False), self.looked())
        self.assertIn("Bake: 1 file(s) written, 1 already held it", self.index_with_no_model())
        self.assertBaked(b, 0)
        self.assertEqual((0, 0, True), self.looked())


if __name__ == "__main__":
    unittest.main()
