"""The owner asks what a bake would do before any is written (`bake-photos`, a dry run; scenario F3,
tagpup.services.baking_report; docs/PHASE6_DESIGN.md, section 9).

A library of two folders holds a mix: photos to bake, one already baked, one never examined
by the face model, one with a face of another model, one changed since its vector was made, one whose file is not a
picture. The dry run counts each, by folder and in all, says never examined as that and never as none found, and leaves
the library, its journal, its snapshots and every file as they were, byte for byte; a second run says the same. --apply
is refused with a sentence. Ctrl+C after the first folder ends the run with that folder's totals, and nothing written.

Real ExifTool, on JPEGs made here, in a home of the test's own; rows as the indexer stores them (photo_rows.add_read,
embeddings.put with the file's stamp, faces through services.faces.record_batch with the detector that ran); the face model
a stand-in, as no model is loaded.
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
from tagpup.services import baking, baking_report  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db, embeddings  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402

FACES = validation.SCRFD_ADAFACE
DETECTOR = face_service.detector_of(NewFaces(), None)


class ABakeIsRehearsedFolderByFolder(BakeCase):

    def setUp(self):
        super().setUp()
        library_settings.stamp(self.library, {"faces.model": FACES, "paths.exiftool": baked_files.EXIFTOOL},
                               operation=library_settings.FROM_REPLACED)
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
                                               baked_files.unit(bake.PHOTO_DIM, 1).tobytes()))
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
                                     images.fingerprint(images.opened(path, upright=True)))
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

    def test_a_dry_run_counts_each_kind_by_folder_writes_nothing_and_says_the_same_again(self):
        self.a_mix()
        before = self.home_as_it_is()
        first = self.cli()
        self.assertEqual(0, first.exit_code, first.output)
        out = first.output
        self.assertIn("Folder 1 of 2:\n  6 photo(s): 1 to bake, 1 already current", out)
        self.assertIn("Folder 2 of 2:\n  1 photo(s): 1 to bake, 0 already current", out)
        total = out[out.index("All 2 folder(s):"):]
        self.assertIn("7 photo(s): 2 to bake, 1 already current (skipped: --apply goes on from where it stopped), 4 cannot "
                      "be baked.", total)
        self.assertIn("To bake, by format: .jpg 2.", total)
        self.assertNotIn("XMP", out, "Extended XMP is ExifTool's detail, not the owner's (2026-10-25)")
        for said in ("1 never examined by the face model", "1 with a face holding no vector under the face model",
                     "1 whose file changed since its photo vector was made", "1 whose file could not be read"):
            self.assertIn("Cannot be baked: %s" % said, total)
        self.assertNotIn("none found", out.lower())
        self.assertIn("Sample: 6 photo(s) decoded for their fingerprint", total, "the unreadable one is not decoded")
        self.assertIn("1 had a fingerprint row, 1 of them matched it", total, "the photo baked through the writer")
        self.assertIn("Nothing changed", out)
        self.assertEqual(before, self.home_as_it_is(), "no row, no journal entry, no snapshot, no file touched")

        again = self.cli()
        self.assertEqual(out, again.output, "a second run says the same")
        self.assertEqual(before, self.home_as_it_is())

    def test_apply_is_refused_with_a_sentence(self):
        self.photo("a.jpg", faces=1)
        before = self.home_as_it_is()
        refused = self.cli("--apply")
        self.assertEqual(1, refused.exit_code)
        self.assertIn("writing the bake is cut 3; this is a dry run", refused.output)
        self.assertNotIn("Folder", refused.output)
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
