"""Owner scenario: two libraries that each hold the same folder on disk.

The owner keeps more than one library, and both can hold the same folder -- a shared
drive of an event both cover, each having indexed it on its own. Nothing about a write
in one, or the watcher's or sync's re-reading of the shared files in the other, should
let one library's tags, rows or tree reach the other's; a library that does not hold a
folder is refused Suggest there, and refused a write there too (test_rows_only_in_folders_added.py,
test_roots_scenarios.py (AFolderTheLibraryDoesNotHold) already cover that half of this story).

ExifTool is stood in for (exiftool() below; test_sync.py's for sync); the
photo files and both libraries' rows are real.

Both libraries index the shared folder, and each index writes the bake (docs/PLAN_KEEP_IN_STEP.md, cut 2): a bake under
the libraries' keys of the picture as it is now is the other's to adopt, never rewritten (owner, DECISIONS 2026-10-27).
Beta indexed the folder before any bake, its photo vectors another machine's (a CPU's, not alpha's card's); alpha
bakes; beta's next index writes no file; alpha turns a photo and bakes it again; beta's next index adopts that, and a
third round writes nothing in either. Real ExifTool and the CLI's index, the stand-in models.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_exiftool  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402


from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402

import baked_files  # noqa: E402
from stand_in_models import Hold, NewFaces, RefusableFaces  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import metadata  # noqa: E402
from tagpup.ml import clip  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import bake_after_index, baking, photos, sync, tagging  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()


def exiftool(files):
    """A session whose photos hold the keywords in `files` (path -> tags), which keeps each keyword write in `files` as a
    file would (the journal reads back what it wrote). Returns (session, et)."""
    et = mock.MagicMock()
    et.get_tags.side_effect = lambda paths, tags=None, params=None: [
        {"SourceFile": p, "XMP:Subject": list(files.get(p, []))} for p in paths]

    def set_tags(paths, tags=None, params=None):
        for path in paths:
            if "XMP:Subject" in (tags or {}):
                value = tags["XMP:Subject"]
                files[path] = list(value) if isinstance(value, list) else [value]
            elif "-XMP:Subject=" in (params or []):
                files[path] = []

    def execute(*args):
        if "-XMP:Subject=" in args:
            files[args[-1]] = []
        return ""

    et.set_tags.side_effect = set_tags
    et.execute.side_effect = execute
    session = mock.MagicMock()
    session.return_value.__enter__.return_value = et
    session.return_value.__exit__.return_value = False
    return session, et


class Reads:
    """What sync's stand-in ExifTool answers, as test_sync.py's does."""

    def __init__(self, truth):
        self.truth = truth

    def whole(self, reader, file_paths, people=None, bakes=False):
        return [metadata.structure(path, dict(self.truth.get(os.path.basename(path), {})), people,
                                   metadata.stamp_before_read(path)) for path in file_paths]

    def session(self, *args, **kwargs):
        reads = self

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_tags(self, batch, tags=None, params=None):
                return [{"SourceFile": path.replace(os.sep, "/"), **reads.truth.get(os.path.basename(path), {})}
                        for path in batch]

        return Session()


class SharedFolderTwoLibraries(unittest.TestCase):
    """Both alpha and beta hold Pictures/2025-10 Meet, each having indexed it on its
    own; alpha alone holds Pictures/Alpha Only."""

    def setUp(self):
        fake_exiftool.writes_no_file(self)
        self.home = own_home.for_test(self, prefix="shared_folder_")
        self.pictures = os.path.join(self.home.root, "Pictures")
        self.meet = os.path.join(self.pictures, "2025-10 Meet")
        self.alpha_only = os.path.join(self.pictures, "Alpha Only")
        os.makedirs(self.meet)
        os.makedirs(self.alpha_only)
        self.alpha_path = self.home.library("alpha.db")
        self.beta_path = self.home.library("beta.db")
        library_actions.create(self.alpha_path)
        library_actions.create(self.beta_path)
        self.alpha = Library(self.alpha_path)
        self.beta = Library(self.beta_path)
        self.truth = {}

    def photo(self, folder, name, **fields):
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"jpeg")
        self.truth[name] = dict(fields)
        return path

    def indexed(self, db_path, *photo_paths):
        conn = db.connect(db_path)
        try:
            for path in photo_paths:
                photo_rows.add_read(conn, path, self.truth[os.path.basename(path)])
            conn.commit()
        finally:
            conn.close()

    def tags_of(self, db_path, photo_path):
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            row = conn.execute("SELECT tags FROM photos WHERE path = ?", (photo_path,)).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            conn.close()

    def tree_tags(self, db_path):
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            return {row[0] for row in conn.execute("SELECT tag FROM tag_taxonomy")}
        finally:
            conn.close()

    def run_sync(self, library, **kwargs):
        reads = Reads(self.truth)
        with mock.patch("tagpup.files.field_values.Fields.whole", autospec=True,
                        side_effect=reads.whole), \
                mock.patch("tagpup.files.exiftool_session.ExifToolSession", side_effect=reads.session):
            return sync.sync(library, apply=True, **kwargs)

    def test_a_write_through_one_library_never_reaches_the_others_tree_or_rows(self):
        meet_photo = self.photo(self.meet, "meet_01.jpg")
        self.indexed(self.alpha_path, meet_photo)
        self.indexed(self.beta_path, meet_photo)

        files = {meet_photo: []}
        session, _et = exiftool(files)
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", session):
            result = tagging.change_tags(self.alpha, [meet_photo], ["Trips/Meet"], [], "exiftool")
        self.assertEqual((1, 1), (result.attempted, result.changed))

        self.assertEqual(["Trips/Meet"], self.tags_of(self.alpha_path, meet_photo))
        self.assertNotIn("Trips/Meet", self.tree_tags(self.alpha_path))

        # beta was never asked, and shares no cache with alpha's request.
        self.assertEqual([], self.tags_of(self.beta_path, meet_photo))
        self.assertNotIn("Trips/Meet", self.tree_tags(self.beta_path))

    def test_betas_sync_of_the_shared_file_grows_no_tree_and_touches_only_what_it_holds(self):
        meet_photo = self.photo(self.meet, "meet_01.jpg")
        alpha_private = self.photo(self.alpha_only, "private.jpg")
        self.indexed(self.alpha_path, meet_photo, alpha_private)
        self.indexed(self.beta_path, meet_photo)

        # alpha writes the shared file; on disk, beta's row now describes it wrongly.
        files = {meet_photo: []}
        session, _et = exiftool(files)
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", session):
            tagging.change_tags(self.alpha, [meet_photo], ["Trips/Meet"], [], "exiftool")
        later = os.stat(meet_photo).st_mtime + 10
        os.utime(meet_photo, (later, later))
        self.truth["meet_01.jpg"] = {"XMP:Subject": ["Trips/Meet"]}

        self.run_sync(self.beta, roots=[self.pictures])

        # beta caught up on the file it holds...
        self.assertEqual(["Trips/Meet"], self.tags_of(self.beta_path, meet_photo))
        # ...but sync alone grew no tree node in either library (only indexing does)...
        self.assertNotIn("Trips/Meet", self.tree_tags(self.beta_path))
        self.assertNotIn("Trips/Meet", self.tree_tags(self.alpha_path))
        # ...and made no row for a folder beta never held, alpha's private one.
        self.assertIsNone(self.tags_of(self.beta_path, alpha_private))

    def paths_of(self, db_path):
        conn = db.connect(db.readonly_uri(db_path), uri=True)
        try:
            return sorted(row[0] for row in conn.execute("SELECT path FROM photos"))
        finally:
            conn.close()

    def test_a_file_deleted_from_the_shared_folder_is_removed_by_each_library_from_its_own_rows_alone(self):
        # The owner deletes a photo of the shared folder, and one of alpha's own, in Explorer (DECISIONS 2026-10-28).
        gone, kept = self.photo(self.meet, "meet_01.jpg"), self.photo(self.meet, "meet_02.jpg")
        alpha_private = self.photo(self.alpha_only, "private.jpg")
        self.indexed(self.alpha_path, gone, kept, alpha_private)
        self.indexed(self.beta_path, gone, kept)
        os.remove(gone)
        os.remove(alpha_private)

        alpha = self.run_sync(self.alpha, roots=[self.pictures])
        self.assertTrue(alpha.ok, alpha.message())
        self.assertEqual(2, alpha.details["changed"]["removed"])
        self.assertEqual([kept], self.paths_of(self.alpha_path))
        self.assertEqual(sorted([gone, kept]), self.paths_of(self.beta_path), "alpha's sync reached beta's rows")

        beta = self.run_sync(self.beta)
        self.assertTrue(beta.ok, beta.message())
        self.assertEqual(1, beta.details["changed"]["removed"])
        self.assertEqual([kept], self.paths_of(self.beta_path))
        self.assertEqual([kept], self.paths_of(self.alpha_path))


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class TwoLibrariesAdoptEachOthersBake(unittest.TestCase):
    """Alpha and beta, of the same models, each index Pictures/2025-10 Meet; the index writes the bake."""

    FACE = [300, 200, 400, 330]

    def setUp(self):
        self.home = own_home.for_test(self, prefix="shared_bake_")
        self.meet = paths.stored(os.path.join(self.home.root, "Pictures", "2025-10 Meet"))
        os.makedirs(self.meet)
        self.libraries = {}
        for name in ("alpha", "beta"):
            db_path = self.home.library(name + ".db")
            self.assertEqual(1, library_actions.create(db_path).changed)
            self.libraries[name] = db_path
        self.detector = RefusableFaces({"a.jpg": [(self.FACE, 0.99, 1)]}, vector=baked_files.face_vector)
        vector = baked_files.unit(1152, 99).tolist()
        for patcher in (mock.patch.object(clip.ClipModel, "_init_model"),
                        mock.patch.object(clip.ClipModel, "picture_input", return_value="input"),
                        mock.patch.object(clip.ClipModel, "embed_input", return_value=vector),
                        mock.patch.object(Runtime, "gpu_turn", lambda runtime, what, *args, **kwargs: Hold()),
                        mock.patch.object(runtimes, "on_the_card", return_value=True),
                        mock.patch.object(Runtime, "faces", lambda runtime, library, settings=None: self.detector)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def picture(self, name, colour):
        path = os.path.join(self.meet, name)
        Image.new("RGB", (640, 480), colour).save(path, "JPEG")
        return path

    def index(self, name):
        """The CLI's index of the folder in library `name`: what it says of its bake."""
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.libraries[name], "index", self.meet])
        if isinstance(result.exception, BaseException) and not isinstance(result.exception, SystemExit):
            raise result.exception
        self.assertEqual(0, result.exit_code, result.output)
        said = " ".join(result.output.split())
        return said[said.index("Bake:"):] if "Bake:" in said else ""

    def boxes(self, name, path):
        conn = db.connect(db.readonly_uri(self.libraries[name]), uri=True)
        try:
            return [json.loads(box) for path_of, box in conn.execute(
                "SELECT p.path, f.box FROM faces f JOIN photos p ON p.id = f.photo_id").fetchall()
                if paths.key(path_of) == paths.key(path)]
        finally:
            conn.close()

    def current_in(self, name, path):
        return baked_files.plan(self.libraries[name], path, face_service.detector_of(NewFaces()))

    def stamps(self, *photo_paths):
        return {path: os.stat(path).st_mtime_ns for path in photo_paths}

    def test_each_adopts_the_others_bake_and_neither_rewrites_a_file(self):
        a, b = self.picture("a.jpg", (200, 40, 40)), self.picture("b.jpg", (40, 200, 40))
        with mock.patch.object(clip.ClipModel, "embed_input", return_value=baked_files.unit(1152, 98).tolist()), \
                mock.patch.object(bake_after_index, "bake_folders"):
            self.index("beta")   # before any bake, on a machine whose vectors differ from alpha's
        self.assertIn("Bake: 2 file(s) written, 0 already held it.", self.index("alpha"))
        baked = self.stamps(a, b)

        self.assertIn("Bake: 0 file(s) written, 2 already held it.", self.index("beta"))
        self.assertEqual(baked, self.stamps(a, b), "beta rewrote alpha's bake with its own vectors")
        self.assertEqual(self.boxes("alpha", a), self.boxes("beta", a))
        self.assertEqual(1, len(baked_files.held(a).faces))
        for name in ("alpha", "beta"):
            self.assertIsInstance(self.current_in(name, a), baking.Current, name)

        turned = photos.rotate(Library(self.libraries["alpha"]), a, "right", EXIFTOOL)
        self.assertTrue(turned.changed, turned.refused or turned.errors)
        self.index("alpha")   # alpha bakes the turned picture
        held = baked_files.held(a)
        self.assertIsInstance(self.current_in("alpha", a), baking.Current)
        rebaked = self.stamps(a, b)
        self.assertNotEqual(baked[a], rebaked[a], "alpha baked the turned picture")

        self.index("beta")
        self.assertEqual(rebaked, self.stamps(a, b), "beta rewrote alpha's bake of the turned picture")
        self.assertEqual(held.fingerprint, baked_files.held(a).fingerprint)
        self.assertIsInstance(self.current_in("beta", a), baking.Current, "beta adopted it")

        for name in ("alpha", "beta"):
            self.index(name)
            self.assertEqual(rebaked, self.stamps(a, b), "a third round wrote a file in " + name)


if __name__ == "__main__":
    unittest.main()
