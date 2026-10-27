"""The owner asks what a bake would do before any is written (`bake-photos`, a dry run; scenario F3,
tagpup.services.baking_report; docs/PHASE6_DESIGN.md, section 9).

A library of two folders holds a mix: photos to bake, one already baked, one never examined
by the face model, one with a face of another model, one changed since its vector was made, one whose file is not a
picture. The dry run counts each, by folder and in all, says never examined as that and never as none found, decodes for
its sample only the photos it can plan, and leaves the library, its journal, its snapshots and every file as they were,
byte for byte; a second run says the same. A photo never examined until the new face model's pass looked at it and
found no face is then to bake, with no regions. Ctrl+C after the first folder ends the run with that folder's totals, and nothing written. A share that stops answering ExifTool ends the run at that folder, which
is counted and named by its number, and no later folder is read.

Real ExifTool, on JPEGs made here, in a home of the test's own; rows as the indexer stores them (photo_rows.add_read,
embeddings.put with the file's stamp, faces through services.faces.record_batch with the detector that ran); the face model
a stand-in, as no model's weights are loaded.
"""
import hashlib
import os
import sys
import threading
import unittest
from unittest import mock

from click.testing import CliRunner

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_file_holds_what_exiftool_kept import BakeCase  # noqa: E402

import baked_files  # noqa: E402
from stand_in_models import NewFaces  # noqa: E402
import tagpup_cli  # noqa: E402
from tagpup.core import paths, validation  # noqa: E402
from tagpup.files import bake, field_values, images  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import baking, baking_report, face_pass  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db, embeddings  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402

FACES = validation.SCRFD_ADAFACE
DETECTOR = face_service.detector_of(NewFaces(), None)


class ALibraryToBake(BakeCase):
    """A library whose photos are indexed as the indexer stores them, for a bake's dry run and its apply
    (tests/test_a_bake_is_written_folder_by_folder.py); no tests of its own."""

    def setUp(self):
        super().setUp()
        library_settings.stamp(self.library, {"faces.model": FACES, "paths.exiftool": baked_files.EXIFTOOL},
                               operation=library_settings.FROM_REPLACED)
        # The test pictures are 16 x 12: their faces are a pixel or two across. The floor of a baked face (#1973) is lowered
        # to one pixel for them; the test of the floor itself puts it back.
        patcher = mock.patch.object(images, "NEW_FACE_FLOOR", 1)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.makedirs(os.path.join(self.folder, "Second"))

    def write(self, work):
        conn = db.connect(self.library.path)
        try:
            work(conn)
            conn.commit()
        finally:
            conn.close()

    def photo(self, name, faces=None):
        """A photo read by the index, its photo vector stamped as the file is now, and `faces` (a count) found by the
        library's detector; None, never looked for."""
        path = self.make_read(name)
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            photo_key = library_settings.photo_key_on(conn)
        finally:
            conn.close()
        stamp = embeddings.stamp_of(path)
        self.write(lambda conn: embeddings.put(conn, path, photo_key, stamp[0], stamp[1],
                                               baked_files.unit(bake.PHOTO_DIM, 1).tobytes(), None))
        if faces is not None:
            found = [{"box": [n % 8 * 2, n // 8 * 1.5, n % 8 * 2 + 1.5, n // 8 * 1.5 + 1.25],
                      "embedding": baked_files.unit(bake.FACE_DIM, 10 + n).tolist(), "prob": 0.9} for n in range(faces)]
            conn = db.connect(self.library.path)
            try:
                face_service.record_batch(conn, {path: found}, model=FACES, detector=DETECTOR, overwrite=True)
            finally:
                conn.close()
        return path

    def baked(self, path):
        """The photo baked through the one writer, as a bake would plan it."""
        with field_values.session(baked_files.EXIFTOOL) as et:
            held = field_values.read(et, [path], list(bake.WRITTEN.values())).get(paths.key(path))
        width, height, _oriented, turn = images.shown_shape(path)
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            plan = baking.plan_photo(conn, path, baking.keys(conn, DETECTOR), lambda: held, lambda: (width, height, turn),
                                     lambda: images.fingerprint(images.opened(path, upright=True)))
        finally:
            conn.close()
        self.assertEqual((1, []), (lambda r: (r.changed, r.errors))(self.bake([path], plan.values)))

    def a_mix(self):
        self.photo("a.jpg", faces=2)
        self.baked(self.photo("b.jpg", faces=1))
        self.photo("never.jpg")
        other = self.photo("c.jpg", faces=2)
        self.write(lambda conn: store_faces.insert(conn, other, [1, 1, 3, 3], face_service.vector_bytes([0.5] * 8),
                                                   model="facenet-vggface2"))
        self.outside(self.photo("changed.jpg", faces=1), "-XMP:Description=Harbour at dusk")
        # Its bytes gone bad and its stamp kept (the library's checks pass): only reading the file finds it out.
        broken = self.photo("broken.jpg", faces=1)
        stat = os.stat(broken)
        with open(broken, "wb") as handle:
            handle.write(b"x" * stat.st_size)
        os.utime(broken, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.photo(os.path.join("Second", "many.jpg"), faces=3)

    def home_as_it_is(self):
        """{file under the home: (size, mtime, sha256)}: the library, its journal and snapshots, and every photo. The
        shared-memory index a WAL reader maps, and an empty WAL a reader opens, are left out: SQLite's, holding nothing
        of the library's."""
        found = {}
        for folder, _dirs, files in os.walk(self.home.root):
            for name in files:
                path = os.path.join(folder, name)
                if name.endswith("-shm") or (name.endswith("-wal") and not os.path.getsize(path)):
                    continue
                with open(path, "rb") as handle:
                    found[path] = (os.path.getsize(path), os.stat(path).st_mtime_ns, hashlib.sha256(handle.read()).digest())
        return found

    def cli(self, *args):
        runtime = Runtime(build_clip=lambda settings: None, build_faces=lambda settings: NewFaces())
        self.addCleanup(runtime.forget, self.library)
        with mock.patch.object(tagpup_cli, "get_runtime", return_value=runtime):
            return CliRunner().invoke(tagpup_cli.cli, ["--db", self.library.path, "bake-photos"] + list(args))


class ABakeIsRehearsedFolderByFolder(ALibraryToBake):

    def test_a_dry_run_counts_each_kind_by_folder_writes_nothing_and_says_the_same_again(self):
        self.a_mix()
        before = self.home_as_it_is()
        first = self.cli("--verbose")
        self.assertEqual(0, first.exit_code, first.output)
        out = first.output
        self.assertIn("Folder 1 of 2:\n  6 photo(s): 1 to bake, 1 already current", out)
        self.assertIn("Folder 2 of 2:\n  1 photo(s): 1 to bake, 0 already current", out)
        total = out[out.index("All 2 folder(s):"):]
        self.assertIn("7 photo(s): 2 to bake, 1 already current (left alone), 4 cannot "
                      "be baked.", total)
        self.assertIn("To bake, by format: .jpg 2.", total)
        self.assertNotIn("XMP", out, "Extended XMP is ExifTool's detail, not the owner's (2026-10-25)")
        for said in ("1 never examined by the face model: run face-pass on its folder first",
                     "1 with a face holding no vector under the face model: run face-pass on its folder",
                     "1 with no photo vector of its picture as it is now under the photo model: index its folder (sync queues it)",
                     "1 whose file could not be reached or read: the file or the share did not answer: run again when it does"):
            self.assertIn("Cannot be baked: %s" % said, total)
        self.assertNotIn("none found", out.lower())
        self.assertIn("Sample: 3 photo(s) decoded for their fingerprint", total,
                      "of the seven sampled, only the three it can plan and read: never the refused or the unreadable")
        self.assertIn("1 had a fingerprint row, 1 of them matched it", total, "the photo baked through the writer")
        self.assertIn("Nothing changed", out)
        self.assertEqual(before, self.home_as_it_is(), "no row, no journal entry, no snapshot, no file touched")

        again = self.cli("--verbose")
        self.assertEqual(out, again.output, "a second run says the same")
        self.assertEqual(before, self.home_as_it_is())

    def test_a_photo_the_face_pass_found_no_face_in_is_to_bake_and_no_longer_never_examined(self):
        """The live shape (photo_index, 2026-10-25: 10,407 photos with no face and no record of the new model looking):
        refused as never examined until the pass looks at it; then it holds no face and the model's record, and is to bake
        as a photo with no regions."""
        self.photo("a.jpg", faces=1)
        self.photo("nobody.jpg")
        before = baking_report.dry_run(self.library, baked_files.EXIFTOOL, DETECTOR).total
        self.assertEqual((2, 1, 1), (before.photos, before.to_bake, before.cannot[baking.NEVER_EXAMINED]))
        (passed,) = face_pass.run(self.library.path, NewFaces({}), self.folder, apply=True)
        self.assertEqual((1, 1), (passed.done, passed.none_found))
        after = baking_report.dry_run(self.library, baked_files.EXIFTOOL, DETECTOR).total
        self.assertEqual((2, 2, 0), (after.photos, after.to_bake, sum(after.cannot.values())))

    def test_a_share_that_stops_answering_ends_the_run_at_that_folder(self):
        self.a_mix()
        os.makedirs(os.path.join(self.folder, "Third"))
        self.photo(os.path.join("Third", "z.jpg"), faces=1)
        before = self.home_as_it_is()
        real, asked = field_values.read, []

        def read(et, photo_paths, *args, **kwargs):
            # The share holding Second stops answering: ExifTool's deadline passes, and every photo of the read is
            # NotAnswered, as field_values.read answers a timeout.
            asked.append(os.path.basename(os.path.dirname(photo_paths[0])))
            if asked[-1] == "Second":
                return {paths.key(one): field_values.NotAnswered("ExifTool did not answer in 300 s") for one in photo_paths}
            return real(et, photo_paths, *args, **kwargs)
        with mock.patch.object(baking_report.field_values, "read", side_effect=read):
            stalled = self.cli("--verbose")
        out = stalled.output
        self.assertEqual(1, stalled.exit_code, out)
        self.assertEqual("Second", asked[-1])
        self.assertNotIn("Third", asked, "the folder after it is not read")
        self.assertIn("Folder 2 of 3:\n  0 photo(s): 0 to bake", out, "a folder of photos not answered is counted")
        self.assertNotIn("Folder 3 of 3", out)
        total = out[out.index("All 2 folder(s) (stopped: the photos planned so far):"):]
        self.assertIn("6 photo(s): 1 to bake, 1 already current", total)
        self.assertIn("Not read: 1 photo(s) ExifTool did not answer for", total)
        self.assertIn("Stopped: ExifTool did not answer in its deadline for the photos of folder 2: is its share", total)
        self.assertEqual(before, self.home_as_it_is())

    def test_stopped_during_a_folders_read_it_blames_no_share(self):
        self.photo("a.jpg", faces=1)
        self.photo("b.jpg", faces=1)
        before = self.home_as_it_is()
        asked = threading.Event()

        def read(et, photo_paths, *args, **kwargs):
            asked.set()   # Ctrl+C between the batches: field_values.read leaves out the photos it did not reach
            return {}
        with mock.patch.object(baking_report.field_values, "read", side_effect=read):
            run = baking_report.dry_run(self.library, baked_files.EXIFTOOL, DETECTOR, stop=asked)
        self.assertTrue(run.stopped)
        self.assertIsNone(run.stalled)
        self.assertEqual((0, 0), (run.total.photos, run.total.not_answered), "a stop the owner asked for counts nothing")
        self.assertEqual(before, self.home_as_it_is())

    def test_stopped_after_the_first_folder_it_gives_that_folders_totals_and_writes_nothing(self):
        self.a_mix()
        before = self.home_as_it_is()
        asked = threading.Event()
        heard = []

        def folder_done(tally, number, folders):
            heard.append((tally.photos, number, folders))
            asked.set()   # Ctrl+C while the first folder is planned
        run = baking_report.dry_run(self.library, baked_files.EXIFTOOL, DETECTOR, stop=asked, heard=folder_done)
        self.assertTrue(run.stopped)
        self.assertEqual([(6, 1, 2)], heard)
        self.assertEqual((6, 1, 1), (run.total.photos, run.total.to_bake, run.total.current))
        self.assertEqual(before, self.home_as_it_is())


if __name__ == "__main__":
    unittest.main()
