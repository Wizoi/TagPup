"""An index reads a photo's bake instead of working it out on the graphics card (docs/PHASE6_DESIGN.md, sections 3, 7 and
8; tagpup.services.bake_reading, tagpup_cli.py's index).

What the owner lives through, adding a folder whose photos were baked:

* a library made by the picker's Create alone is on the models every live library is on: a folder baked under their keys
  is indexed from its files and no model is asked;
* a folder baked under the library's keys is indexed and no model is asked: each photo's vector and faces are the bake's,
  a JPEG, a TIFF and a photo of 60 faces whose bake spans Extended XMP alike; a bake that found none gives no face and the
  faces_detected row a detection finding none writes; the photo's raw_metadata holds nothing of the bake;
* a bake whose picture changed after it was baked, one not whole, one made under another photo model, one under another
  face model: that side is worked out (the other read when it is the library's), and the line at the end counts each,
  a run whose every bake is another model's included, a detection that failed not counted as the card's work;
* that line reaches the server's index job whole, after the new faces, and so Activity's run;
* a turned photo (Orientation 6, 12 such in photo_index), and one turned by an XMP tiff:Orientation alone, which ExifTool
  answers as no EXIF Orientation and Pillow turns all the same: its faces' boxes, read from the bake, are the boxes a
  detection stores;
* a face named or excluded on a photo indexed again keeps its decision when the bake is read;
* an index killed part-way leaves every row whole and the next run completes the folder;
* the index's read (`-struct`) answers every field of METADATA_FIELDS as the plain read does;
* a photo baked as a bake run bakes it (planned from the library, written through file_changes.write_fields), then tagged
  in the app, is still current: the write restamps its vector; then edited outside: an edit of its metadata alone
  leaves the bake its picture's (its decode's fingerprint equals its row: current with no index; read again, no card),
  an edit of its pixels has no vector of its picture until indexed again (worked out, to bake again);
* a folder baked on the desktop and copied by hand to the server (each copy a new mtime) is read there under the
  server library's keys, no card asked; a copy baked under another photo model gives its faces and not its vector, and a
  copy whose picture was repainted is not trusted (PHASE6_DESIGN, sections 5 and 6).
* a library made before the fingerprints' table (at 33, as the three live libraries are) is brought to 34 as the index
  opens it, and the baked photo's fingerprint is recorded.

What the owner lives through when a photo's file changes after it was indexed, no bake in it
(photo_fingerprints.same_picture, the one rule; the index's closing line, tagpup_cli.KEPT_LINE, counts each):

* another program writes a caption into one photo and crops another: the caption's photo keeps its vector and faces,
  no model asked, its vector stamped again with the file; the cropped one is worked out again; the next run has nothing
  to do;
* a turned photo (Orientation 6) whose vector is current and that was never examined for faces (no face row, no record
  of a look: 223 such in photo_index, 2026-10-27) is looked at for faces, its vector kept, and its fingerprint row with
  it;
* a photo whose stamp describes its file but that has no fingerprint row (every photo indexed before 34) is left
  alone; once its file changes it is worked out again, nothing to compare its decode with;
* a photo replaced while the run waits for the card (behind Suggest), after the check that kept it by its fingerprint
  decoded it: worked out again from the new picture, its old vector never recorded as the new picture's;
* an index that stops as it records the batch leaves the stamp stale; the next run restamps it with no model;
* a watcher's sync after another program wrote keywords into a folder: every photo is kept by its fingerprint, so the
  index builds no model, imports no torch and takes no turn on the graphics card (it never waits behind Suggest or
  another index for it), and still stamps each again with its fingerprint row; a run with one photo cropped takes the
  turn once;
* a photo cropped outside and then Suggested (Suggest keeps the cropped picture's vector), then restored from a copy: the
  index works it out again, and its fingerprint row is the restored picture's -- Suggest's vector forgot the row of the
  picture before the crop, which told the index the cropped picture's vector was the restored one's;
* --force-reembed works the photo out again, its vector and its faces;
* on a machine whose models run on the processor, the bake line says so.

CLIP and the face model are stood in for (tests/test_no_test_loads_a_model.py); a stand-in asked while `refused` raises
and is noted. Pillow and ExifTool are real; the bakes are written through the one writer (tests/baked_files.py, or
planned and written as a bake run does: bake_as_planned); the library is made by the picker's Create alone, in a home of
its own.
"""
import itertools
import json
import os
import shutil
import sys
import unittest
from unittest import mock
from unittest.mock import MagicMock

import numpy as np
from click.testing import CliRunner
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exiftool_fields  # noqa: E402
import baked_files  # noqa: E402
import migration_names  # noqa: E402
import own_home  # noqa: E402
from stand_in_models import Hold, NewFaces, RefusableFaces  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
import page_writes  # noqa: E402
from tagpup.core import fields, orientation, paths, processes, progress_line, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import bake, field_values, images  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.ml import clip  # noqa: E402
from tagpup.ml.clip import ClipModel  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import face_people  # noqa: E402
from tagpup.services import job_runs  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import bake_after_index, bake_reading, baking, file_changes, file_only, indexing, search, tagging  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db, schema  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
FACES = validation.SCRFD_ADAFACE
#: The keys every live library's vectors are under (photo_index, kr-track and renton_parkrun, read 2026-10-25: their
#: settings, and `embeddings.model` and `face_embeddings.model`, each one key): what a bake made from them holds.
LIVE_PHOTO_KEY = "ViT-SO400M-14-SigLIP2|webli|full-frame|1.4|native"
LIVE_FACE_MODEL = "scrfd-adaface"
SIZE = (640, 480)
#: The detector the stand-in face model's detections are recorded under.
DETECTOR = face_service.detector_of(NewFaces())


def _faces(found=None, kill_at=None):
    """The new face model, refusable, its vectors as long as the face model's (a bake holds no other)."""
    return RefusableFaces(found, kill_at, vector=baked_files.face_vector)


def box_of(area, width, height):
    """The pixel box an MWG Area (X, Y centre, W, H) covers in a picture shown as stored."""
    x, y, w, h = area
    return [round((x - w / 2) * width), round((y - h / 2) * height), round((x + w / 2) * width),
            round((y + h / 2) * height)]


def half(vector):
    """A vector as a bake keeps it: float16."""
    return bake.decode_vector(bake.encode_vector(vector), len(vector))


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class AnIndexReadsABake(unittest.TestCase):

    def setUp(self):
        self.home = own_home.for_test(self, prefix="index_reads_a_bake_")
        self.db_path = self.made("harbour.db")
        self.photo_key = store_embeddings.model_key(**library_settings.read(Library(self.db_path)).embedder)
        self.refused, self.on_card = False, []
        #: What each turn on the graphics card was asked for, and each build of the face model: counted, not refused.
        self.turns, self.face_models = [], []
        self.computed = baked_files.unit(bake.PHOTO_DIM, 99).tolist()

        def card(what, value=None):
            def asked(*args, **kwargs):
                self.on_card.append(what)
                if self.refused:
                    raise AssertionError("CLIP was asked: %s" % what)
                return value
            return asked
        self.detector = _faces()
        for patcher in (# The bake the index writes after it records (tests/test_the_index_writes_the_bake.py) is left out:
                        # each case here makes its folder's bakes itself, and reads what the next index takes of them.
                        mock.patch.object(bake_after_index, "bake_folders"),
                        mock.patch.object(clip.ClipModel, "_init_model", side_effect=card("load")),
                        mock.patch.object(clip.ClipModel, "picture_input", side_effect=card("input", "input")),
                        mock.patch.object(clip.ClipModel, "embed_input", side_effect=card("embed", self.computed)),
                        mock.patch.object(Runtime, "gpu_turn",
                                          lambda runtime, what, *args, **kwargs: self.turns.append(what) or Hold()),
                        # The models are taken to be on the graphics card, whatever this machine has.
                        mock.patch.object(runtimes, "on_the_card", return_value=True),
                        mock.patch.object(Runtime, "faces", lambda runtime, library, settings=None:
                                          self.face_models.append(library) or self.detector)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def made(self, name):
        """A library made by the picker's Create, and nothing else."""
        db_path = self.home.library(name)
        self.assertEqual(1, library_actions.create(db_path).changed)
        return db_path

    # ---- the photos and their bakes ----------------------------------------------------------------------------------

    def folder(self, name):
        folder = paths.stored(os.path.join(self.home.root, name))
        os.makedirs(folder, exist_ok=True)
        return folder

    def picture(self, folder, name, colour, size=SIZE, turned=None, xmp_turned=None):
        """A photo; `turned` its EXIF Orientation, `xmp_turned` an XMP tiff:Orientation and no EXIF one."""
        path = os.path.join(folder, name)
        img = Image.new("RGB", size, colour)
        if turned:
            exif = Image.Exif()
            exif[0x0112] = turned
            img.save(path, "JPEG", exif=exif)
        else:
            img.save(path, "TIFF" if name.endswith(".tif") else "JPEG")
        if xmp_turned:
            with ExifToolSession(executable=EXIFTOOL) as et:
                et.execute("-XMP-tiff:Orientation#=%d" % xmp_turned, "-overwrite_original", path)
                self.assertEqual({"XMP:Orientation": xmp_turned}, {key: value for key, value in et.get_tags(
                    [path], tags=["Orientation"], params=["-n"])[0].items() if key != "SourceFile"})
        return path

    def bake(self, path, areas, seed=1, **changed):
        """Bake into `path` its vector (seed `seed`) and faces at `areas`, against its upright picture."""
        upright = images.opened(path, upright=True)
        faces = [(area, baked_files.unit(bake.FACE_DIM, 10 * seed + n)) for n, area in enumerate(areas)]
        held = baked_files.baked(baked_files.unit(bake.PHOTO_DIM, seed), faces, upright.size, photo_model=self.photo_key,
                                 face_model=FACES, fingerprint=images.fingerprint(upright))
        held.update(changed)
        baked_files.write(path, held)
        return held

    def plan(self, path):
        return baked_files.plan(self.db_path, path, DETECTOR)

    def bake_as_planned(self, photo_paths):
        """Bake the photos as a bake run does: each planned from what the library holds of it, its fingerprint its upright
        decode's, written through the one writer."""
        planned = {paths.key(path): self.plan(path) for path in photo_paths}
        for path in photo_paths:
            self.assertIsInstance(planned[paths.key(path)], baking.ToBake, path)
        written = file_changes.write_fields(Library(self.db_path), "bake", exiftool_fields.opened(self, EXIFTOOL), photo_paths, list(bake.WRITTEN.values()),
                                            lambda path, _held: file_changes.Plan(after=planned[paths.key(path)].values))
        self.assertEqual((len(photo_paths), []), (written.changed, written.errors))

    def outside(self, path, *args):
        """Another program's edit of the file's metadata."""
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.execute(*args, "-overwrite_original", path)

    def repaint(self, path, colour):
        """An editor's save of a new picture into the file, its XMP (the bake with it) kept as it was."""
        with Image.open(path) as held:
            xmp = held.info["xmp"]
        Image.new("RGB", SIZE, colour).save(path, "JPEG", xmp=xmp)
        # A solid colour of one size always compresses to the same bytes, so a save the same instant as the bake leaves
        # nothing to tell it by: an editor's save is later (docs/work/3375.md, #3388).
        later = os.stat(path).st_mtime + 2
        os.utime(path, (later, later))

    def crop(self, path):
        """An editor's crop of the picture, saved over the file."""
        with Image.open(path) as held:
            cut = held.crop((0, 0, SIZE[0] // 2, SIZE[1] // 2))
        cut.save(path, "JPEG")

    def index(self, folder, *options):
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", *options, folder])
        self.assertEqual(0, result.exit_code, result.output)
        return result

    def kept_line(self, result, stamp, same, baked, made, examined):
        """The index's closing line: its first four counts add up to the photos the library holds (each test's run
        indexes every photo of its library, or found it current)."""
        self.assertIn(tagpup_cli.KEPT_LINE % (stamp, same, baked, made, examined), " ".join(result.output.split()))
        self.assertEqual(self.query("SELECT COUNT(*) FROM photos")[0][0], stamp + same + baked + made, result.output)

    # ---- what the library holds ----------------------------------------------------------------------------------------

    def query(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def vector(self, path):
        (vector,) = [np.frombuffer(blob, dtype=np.float32) for path_of, blob in self.query(
            "SELECT p.path, e.vector FROM embeddings e JOIN photos p ON p.id = e.photo_id WHERE e.model = ?",
            (self.photo_key,)) if paths.key(path_of) == paths.key(path)]
        return vector

    def faces(self, path):
        """[(box, vector, name, excluded)] of the photo's faces under the face model, by box."""
        return sorted((json.loads(box), np.frombuffer(vector, dtype=np.float32) if vector else None, name, excluded)
                      for path_of, box, vector, name, excluded in self.query(
                          "SELECT p.path, f.box, v.vector, f.name, f.excluded FROM faces f JOIN photos p ON p.id = "
                          "f.photo_id LEFT JOIN face_embeddings v ON v.face_id = f.id AND v.model = ?", (FACES,))
                      if paths.key(path_of) == paths.key(path))

    def stamp(self, path):
        """The (mtime, size) the photo's vector is stamped with."""
        (found,) = [(mtime, size) for path_of, mtime, size in self.query(
            "SELECT p.path, e.mtime, e.size FROM embeddings e JOIN photos p ON p.id = e.photo_id WHERE e.model = ?",
            (self.photo_key,)) if paths.key(path_of) == paths.key(path)]
        return found

    def file_stamp(self, path):
        stat = os.stat(path)
        return stat.st_mtime, stat.st_size

    def boxes(self, path):
        return [box for box, *_ in self.faces(path)]

    def fingerprint(self, path):
        """[(key, hash)] of the photo's fingerprint row (none, or one)."""
        return [(key, bytes(hash)) for path_of, key, hash in self.query(
            "SELECT p.path, f.key, f.hash FROM photo_fingerprints f JOIN photos p ON p.id = f.photo_id")
            if paths.key(path_of) == paths.key(path)]

    def decoded(self, path):
        """The (key, hash) of the file's picture now, as the index takes it."""
        return images.FINGERPRINT_KEY, bytes(images.fingerprint(images.opened(path, upright=True)))

    def suggested(self, path):
        """Suggest asks the photo's vector (PhotoEmbeddings.of): not current, it is made and kept."""
        settings = library_settings.read(Library(self.db_path)).embedder
        index = search.PhotoIndex(self.db_path, self.photo_key)
        index.open()
        try:
            return search.PhotoEmbeddings(ClipModel(**settings, device="cpu"), index).of(path)
        finally:
            index.close()

    def detected(self, path):
        return [(detector, found) for path_of, detector, found in self.query(
            "SELECT p.path, d.detector, d.found FROM faces_detected d JOIN photos p ON p.id = d.photo_id")
            if paths.key(path_of) == paths.key(path)]

    def row(self, path, column):
        (value,) = [value for path_of, value in self.query("SELECT path, %s FROM photos" % column)
                    if paths.key(path_of) == paths.key(path)]
        return value

    def assertHoldsTheBake(self, path, held, size):
        found = bake.parse(held)
        self.assertTrue(np.allclose(found.photo_vector, self.vector(path), atol=1e-6), path)
        faces = self.faces(path)
        self.assertEqual(sorted(box_of(face.area, *size) for face in found.faces), [box for box, *_ in faces])
        by_box = {tuple(box_of(face.area, *size)): face.vector for face in found.faces}
        for box, vector, _name, _excluded in faces:
            self.assertTrue(np.allclose(by_box[tuple(box)], vector, atol=1e-3), (path, box))

    def said(self, output):
        """The lines of an index's output that say what it read of the bakes, each whole."""
        if not isinstance(output, str):
            output = output.output
        return [line for line in output.splitlines() if line.startswith(bake_reading.SAID)]

    # ---- the scenarios ---------------------------------------------------------------------------------------------------

    def test_a_baked_folder_is_indexed_from_its_files_and_no_model_is_asked(self):
        folder = self.folder("Harbour Baked")
        two = self.picture(folder, "two faces.jpg", "navy")
        tiff = self.picture(folder, "scan.tif", "olive")
        crowd = self.picture(folder, "crowd.jpg", "teal", size=(1600, 1200))
        empty = self.picture(folder, "empty quay.jpg", "grey")
        held = {two: self.bake(two, [baked_files.area(0), baked_files.area(1)], seed=1),
                tiff: self.bake(tiff, [baked_files.area(2)], seed=2),
                crowd: self.bake(crowd, [(0.05 + 0.1 * (n % 10), 0.08 + 0.16 * (n // 10), 0.08, 0.12)
                                         for n in range(60)], seed=3),
                empty: self.bake(empty, [], seed=4)}
        with ExifToolSession(executable=EXIFTOOL) as et:
            self.assertTrue(et.get_tags([crowd], tags=["XMP:HasExtendedXMP"])[0].get("XMP:HasExtendedXMP"),
                            "the 60 faces' bake did not span Extended XMP")
        self.refused = self.detector.refused = True
        result = self.index(folder)
        self.assertEqual([], self.on_card, result.output)
        self.assertEqual([], self.detector.asked, result.output)
        for path, size in ((two, SIZE), (tiff, SIZE), (crowd, (1600, 1200)), (empty, SIZE)):
            self.assertHoldsTheBake(path, held[path], size)
        self.assertEqual(60, len(self.faces(crowd)))
        self.kept_line(result, 0, 0, 4, 0, 0)
        self.assertIn("Baked into the files: 4 photo(s) read from their file, 0 worked out on the graphics card; 0 bake(s) "
                      "stale (the picture changed after it was baked), 0 unreadable, 0 made under other models; 4 photo(s) "
                      "needed no graphics card.",
                      " ".join(result.output.split()))
        # Nothing of the bake is the photo's metadata; its fingerprint is recorded with its row.
        for path in held:
            raw = json.loads(self.row(path, "raw_metadata"))
            self.assertEqual([], [key for key in raw if key in bake.FIELDS or "tagpup" in key.lower()], path)
        self.assertEqual(4, self.query("SELECT COUNT(*) FROM photo_fingerprints WHERE key = ?",
                                       (images.FINGERPRINT_KEY,))[0][0])

        # A bake that found none is recorded as a detection that found none: the same detector, found 0.
        self.refused = self.detector.refused = False
        other = self.folder("Harbour Unbaked")
        bare = self.picture(other, "bare quay.jpg", "grey")
        self.index(other)
        self.assertEqual([], self.faces(empty))
        self.assertEqual(1, len(self.detected(bare)))
        self.assertEqual(self.detected(bare), self.detected(empty))
        self.assertEqual(0, self.detected(empty)[0][1])

    def test_a_library_made_by_create_alone_reads_a_bake_of_the_live_libraries_keys_and_asks_no_model(self):
        self.db_path = self.made("cove.db")
        folder = self.folder("Cove Baked")
        jetty = self.picture(folder, "jetty.jpg", "navy")
        upright = images.opened(jetty, upright=True)
        held = baked_files.baked(baked_files.unit(bake.PHOTO_DIM, 7), [(baked_files.area(0), baked_files.unit(bake.FACE_DIM, 70))],
                                 upright.size, photo_model=LIVE_PHOTO_KEY, face_model=LIVE_FACE_MODEL,
                                 fingerprint=images.fingerprint(upright))
        baked_files.write(jetty, held)
        self.refused = self.detector.refused = True
        result = self.index(folder)
        self.assertEqual([], self.on_card, result.output)
        self.assertEqual([], self.detector.asked, result.output)
        self.photo_key = LIVE_PHOTO_KEY
        self.assertHoldsTheBake(jetty, held, SIZE)
        self.assertIn("Baked into the files: 1 photo(s) read from their file, 0 worked out on the graphics card",
                      " ".join(result.output.split()))

    def test_a_bake_not_of_the_library_or_not_of_the_picture_is_worked_out_and_counted(self):
        folder = self.folder("Harbour Mixed")
        read = self.picture(folder, "read.jpg", "navy")
        corrupt = self.picture(folder, "corrupt.jpg", "olive")
        foreign = self.picture(folder, "foreign.jpg", "teal")
        unbaked = self.picture(folder, "unbaked.jpg", "purple")
        held = self.bake(read, [baked_files.area(0)], seed=1)
        self.bake(corrupt, [baked_files.area(0)], seed=3, **{bake.PHOTO_VECTOR: "not a vector"})
        other = self.bake(foreign, [baked_files.area(1)], seed=4, **{bake.PHOTO_MODEL: "ViT-B-32|openai|cropped|1.0|native"})
        # Baked under the library's old face model: its vector read, its faces detected.
        old_faces = self.picture(folder, "old faces.jpg", "maroon")
        own = self.bake(old_faces, [baked_files.area(2)], seed=5, **{bake.FACE_MODEL: validation.MTCNN_FACENET})
        box = [300, 200, 400, 330]
        self.detector.found = {name: [(box, 0.99, n)] for n, name in enumerate(
            ("corrupt.jpg", "foreign.jpg", "unbaked.jpg", "old faces.jpg"))}
        result = self.index(folder)
        self.assertTrue(np.allclose(bake.parse(own).photo_vector, self.vector(old_faces), atol=1e-6))
        self.assertEqual([box], [found for found, *_ in self.faces(old_faces)])
        self.assertHoldsTheBake(read, held, SIZE)
        for path in (corrupt, unbaked):
            self.assertTrue(np.allclose(self.computed, self.vector(path)), path)
            self.assertEqual([box], [found for found, *_ in self.faces(path)], path)
        # Another photo model's bake: its vector worked out, its faces (the library's model's) read.
        self.assertTrue(np.allclose(self.computed, self.vector(foreign)))
        self.assertEqual([box_of(baked_files.area(1), *SIZE)], [found for found, *_ in self.faces(foreign)])
        self.assertTrue(np.allclose(bake.parse(other).faces[0].vector, self.faces(foreign)[0][1], atol=1e-3))
        self.assertEqual(["corrupt.jpg", "old faces.jpg", "unbaked.jpg"], sorted(self.detector.asked))
        # CLIP is asked of the three whose vector the bake did not give, the old faces' photo not among them.
        self.assertEqual(3, self.on_card.count("embed"))
        self.assertIn("Baked into the files: 3 photo(s) read from their file, 4 worked out on the graphics card; 0 bake(s) "
                      "stale (the picture changed after it was baked), 1 unreadable, 0 made under other models; 1 photo(s) "
                      "needed no graphics card.",
                      " ".join(result.output.split()))

    def test_a_bake_outlives_a_keyword_written_in_the_app_and_an_edit_of_its_picture_outside_does_not(self):
        folder = self.folder("Harbour Edited")
        kept = self.picture(folder, "kept.jpg", "navy")
        edited = self.picture(folder, "edited.jpg", "teal")
        self.detector.found = {"kept.jpg": [([300, 200, 400, 330], 0.99, 1)], "edited.jpg": [([100, 50, 200, 180], 0.99, 2)]}
        self.index(folder)
        self.bake_as_planned([kept, edited])
        for path in (kept, edited):
            self.assertIsInstance(self.plan(path), baking.Current, path)

        # The owner tags both in the app: the write restamps each photo's vector over its own change, the bake stays.
        for path in (kept, edited):
            saved = tagging.save_photo(Library(self.db_path), path, "", ["Places/Harbour"], None, exiftool_fields.opened(self, EXIFTOOL), "",
                                       base=page_writes.base(self.db_path, path))
            self.assertEqual(1, saved.changed, saved.errors)
            self.assertIsInstance(self.plan(path), baking.Current, path)
            self.assertEqual(1, len(baked_files.held(path).faces))

        # Then edits each outside: one's caption, the other's pixels. The caption's photo is the same picture (its
        # decode's fingerprint equals its row: photo_fingerprints.same_picture), the repainted one has no vector of its
        # picture until indexed again.
        self.outside(kept, "-XMP:Description=Boats at dusk")
        self.repaint(edited, "maroon")
        self.assertIsInstance(self.plan(kept), baking.Current)
        self.assertEqual(baking.NO_VECTOR, self.plan(edited).kind)
        del self.detector.asked[:], self.on_card[:]
        result = self.index(folder)
        # The caption's photo keeps its vector by its fingerprint: no bake read, no model run, stamped again.
        self.assertEqual(["Baked into the files: 0 photo(s) read from their file, 1 worked out on the graphics card; 1 bake(s) "
                          "stale (the picture changed after it was baked), 0 unreadable, 0 made under other models; 0 "
                          "photo(s) needed no graphics card."], self.said(result))
        self.kept_line(result, 0, 1, 0, 1, 0)
        self.assertEqual(["edited.jpg"], self.detector.asked, "the repainted photo's faces are looked for again")
        self.assertEqual(1, self.on_card.count("embed"))
        # The caption's edit left the bake its picture's: read again, it is current. The repainted one is to bake again,
        # under its new picture's fingerprint.
        self.assertIsInstance(self.plan(kept), baking.Current)
        again = self.plan(edited)
        self.assertIsInstance(again, baking.ToBake)
        digest = images.fingerprint(images.opened(edited, upright=True))
        self.assertEqual(digest, bake.fingerprint_of(again.values[bake.WRITTEN[bake.FINGERPRINT]][0])[1])
        self.assertNotEqual(digest, baked_files.held(edited).fingerprint)

    def test_an_unbaked_photo_captioned_outside_keeps_its_vector_and_a_cropped_one_is_worked_out_again(self):
        folder = self.folder("Harbour Unbaked Edits")
        captioned = self.picture(folder, "captioned.jpg", "navy")
        cropped = self.picture(folder, "cropped.jpg", "teal")
        self.detector.found = {"captioned.jpg": [([300, 200, 400, 330], 0.99, 1)],
                               "cropped.jpg": [([100, 50, 200, 180], 0.99, 2)]}
        self.index(folder)
        vector, boxes = self.vector(captioned), self.boxes(captioned)
        self.outside(captioned, "-XMP:Description=Boats at dusk")
        self.crop(cropped)
        self.assertNotEqual(self.file_stamp(captioned), self.stamp(captioned))
        del self.detector.asked[:], self.on_card[:]
        self.computed[:] = baked_files.unit(bake.PHOTO_DIM, 98).tolist()
        del self.turns[:]
        result = self.index(folder)
        self.assertEqual(1, len(self.turns), "the cropped photo needs the models: one turn, for the run")
        # The caption's photo: the same picture (its decode's fingerprint is its row's), its vector and faces kept, no
        # model asked, its vector stamped with the file as it is now.
        self.assertEqual(["cropped.jpg"], self.detector.asked, result.output)
        self.assertEqual(1, self.on_card.count("embed"), "the cropped photo's vector alone")
        self.assertTrue(np.allclose(vector, self.vector(captioned)))
        self.assertEqual(boxes, self.boxes(captioned))
        self.assertEqual(self.file_stamp(captioned), self.stamp(captioned))
        self.assertTrue(np.allclose(self.computed, self.vector(cropped)))
        self.assertEqual([], self.said(result), "no photo held a bake")
        self.kept_line(result, 0, 1, 0, 1, 0)
        # Both stamps now describe their files: nothing to do.
        self.refused = self.detector.refused = True
        self.assertIn("All images are up to date!", self.index(folder).output)

    def test_a_turned_photo_never_examined_for_faces_is_looked_at_and_its_vector_kept(self):
        folder = self.folder("Harbour Never Examined")
        turned = self.picture(folder, "turned.jpg", "navy", turned=6)
        failing = self.picture(folder, "failing.jpg", "olive")
        upright = orientation.to_upright([100, 50, 200, 180], *SIZE, 6)
        self.detector.found = {"turned.jpg": [(upright, 0.99, 1)]}
        self.index(folder)
        vector, boxes, stamp = self.vector(turned), self.boxes(turned), self.stamp(turned)
        self.assertEqual(1, len(boxes))
        # Never examined: no face row, no record of a look (as a photo indexed with faces skipped is).
        conn = db.connect(self.db_path, foreign_keys=True)
        try:
            conn.execute("DELETE FROM faces")
            conn.execute("DELETE FROM faces_detected")
            conn.commit()
        finally:
            conn.close()
        del self.detector.asked[:], self.on_card[:]
        self.refused = True
        # The other's detection fails this time (NotDetected): it records nothing, is not counted as looked at, and is
        # owed still.
        self.detector.found["failing.jpg"] = None
        result = self.index(folder)
        self.assertEqual(["failing.jpg", "turned.jpg"], sorted(self.detector.asked), result.output)
        self.assertEqual([], self.detected(failing))
        self.assertEqual([], self.on_card, "its vector is current: CLIP is not asked")
        self.assertEqual(boxes, self.boxes(turned), "the faces of the picture as shown")
        self.assertEqual(1, len(self.detected(turned)))
        self.assertTrue(np.allclose(vector, self.vector(turned)))
        self.assertEqual(stamp, self.stamp(turned))
        self.assertEqual([self.decoded(turned)], self.fingerprint(turned), "kept by its stamp, with its fingerprint row")
        self.kept_line(result, 2, 0, 0, 0, 1)
        self.detector.found["failing.jpg"] = []
        self.kept_line(self.index(folder), 2, 0, 0, 0, 1)
        self.detector.refused = True
        self.assertIn("All images are up to date!", self.index(folder).output)

    def test_a_matching_stamp_with_no_fingerprint_row_is_left_alone_and_a_changed_file_with_none_worked_out(self):
        folder = self.folder("Harbour Before Fingerprints")
        path = self.picture(folder, "quay.jpg", "navy")
        self.detector.found = {"quay.jpg": [([300, 200, 400, 330], 0.99, 1)]}
        self.index(folder)
        conn = db.connect(self.db_path)
        try:
            conn.execute("DELETE FROM photo_fingerprints")
            conn.commit()
        finally:
            conn.close()
        del self.detector.asked[:], self.on_card[:]
        self.refused = self.detector.refused = True
        self.assertIn("All images are up to date!", self.index(folder).output)
        self.assertEqual(0, self.query("SELECT COUNT(*) FROM photo_fingerprints")[0][0])
        # Its file then changes: with no row to compare a decode with, the photo is worked out again.
        self.outside(path, "-XMP:Description=Boats at dusk")
        self.refused = self.detector.refused = False
        result = self.index(folder)
        self.assertEqual(1, self.on_card.count("embed"))
        self.assertEqual(["quay.jpg"], self.detector.asked)
        self.assertEqual(self.file_stamp(path), self.stamp(path))
        self.assertEqual(1, self.query("SELECT COUNT(*) FROM photo_fingerprints")[0][0])
        self.kept_line(result, 0, 0, 0, 1, 0)

    def test_a_photo_suggested_while_cropped_and_then_restored_is_worked_out_again_not_kept_from_the_crop(self):
        folder = self.folder("Harbour Restored")
        path = self.picture(folder, "quay.jpg", "navy")
        self.detector.found = {"quay.jpg": [([300, 200, 400, 330], 0.99, 1)]}
        self.index(folder)
        original = os.path.join(self.home.root, "quay before the crop.jpg")
        shutil.copyfile(path, original)
        first = self.decoded(path)
        self.assertEqual([first], self.fingerprint(path))
        # Cropped in an editor, then Suggest asked of it: its vector is not current, so Suggest makes the cropped
        # picture's and keeps it.
        self.crop(path)
        self.computed[:] = baked_files.unit(bake.PHOTO_DIM, 97).tolist()
        self.suggested(path)
        self.assertTrue(np.allclose(self.computed, self.vector(path)))
        self.assertEqual([], self.fingerprint(path), "a vector of the crop beside the row of the picture before it")
        # Restored from the copy (a new mtime): the picture is the first one again; the vector is the crop's.
        shutil.copyfile(original, path)
        self.assertEqual(first, self.decoded(path))
        del self.detector.asked[:], self.on_card[:]
        self.computed[:] = baked_files.unit(bake.PHOTO_DIM, 96).tolist()
        result = self.index(folder)
        self.assertEqual(1, self.on_card.count("embed"), "the crop's vector was kept for the restored picture")
        self.assertTrue(np.allclose(self.computed, self.vector(path)))
        self.assertEqual(self.file_stamp(path), self.stamp(path))
        self.assertEqual([first], self.fingerprint(path))
        self.kept_line(result, 0, 0, 0, 1, 0)

    def test_an_index_stopped_as_it_records_leaves_the_stamp_stale_and_the_next_restamps_with_no_model(self):
        folder = self.folder("Harbour Stopped")
        path = self.picture(folder, "quay.jpg", "navy")
        self.detector.found = {"quay.jpg": [([300, 200, 400, 330], 0.99, 1)]}
        self.index(folder)
        vector, stale = self.vector(path), self.stamp(path)
        self.outside(path, "-XMP:Description=Boats at dusk")
        del self.detector.asked[:], self.on_card[:]
        self.refused = self.detector.refused = True

        def stops(index, *args, **kwargs):
            raise OSError("the share stopped answering")
        with mock.patch.object(search.PhotoIndex, "build_or_update", stops):
            stopped = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", folder])
        self.assertNotEqual(0, stopped.exit_code, stopped.output)
        self.assertEqual(stale, self.stamp(path), "nothing recorded")
        del self.turns[:], self.face_models[:]
        result = self.index(folder)
        self.assertEqual(([], []), (self.on_card, self.detector.asked), result.output)
        self.assertEqual(([], []), (self.turns, self.face_models), "restamped with no model and no turn")
        self.assertEqual(self.file_stamp(path), self.stamp(path))
        self.assertTrue(np.allclose(vector, self.vector(path)))
        self.kept_line(result, 0, 1, 0, 0, 0)

    def test_keywords_written_into_a_folder_outside_are_restamped_with_no_model_no_torch_and_no_turn(self):
        folder = self.folder("Harbour Keywords Outside")
        first = self.picture(folder, "first.jpg", "navy")
        second = self.picture(folder, "second.jpg", "teal")
        self.detector.found = {"first.jpg": [([300, 200, 400, 330], 0.99, 1)],
                               "second.jpg": [([100, 50, 200, 180], 0.99, 2)]}
        self.index(folder)
        before = {path: (self.vector(path), self.boxes(path), self.fingerprint(path)) for path in (first, second)}
        # Another program writes a keyword into each: the watcher's sync queues the folder.
        for path in (first, second):
            self.outside(path, "-XMP:Subject+=Places/Harbour")
        del self.turns[:], self.face_models[:], self.on_card[:]
        self.refused = self.detector.refused = True
        result = self.index(folder)
        self.assertEqual(([], [], []), (self.turns, self.face_models, self.on_card), result.output)
        self.assertNotIn("graphics card", result.output)
        for path in (first, second):
            vector, boxes, fingerprint = before[path]
            self.assertTrue(np.allclose(vector, self.vector(path)), path)
            self.assertEqual(boxes, self.boxes(path), path)
            self.assertEqual(self.file_stamp(path), self.stamp(path), "stamped again with the file as it is")
            self.assertEqual(fingerprint, self.fingerprint(path), "its fingerprint row recorded again")
            self.assertIn("Places/Harbour", self.row(path, "tags") or "", "its row read again")
        self.kept_line(result, 0, 2, 0, 0, 0)
        self.assertIn("All images are up to date!", self.index(folder).output)

        # Run as the server's queue runs it, in a process of its own: no model's library is imported (#1089).
        for path in (first, second):
            self.outside(path, "-XMP:Description=Boats at dusk")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        probe = ("import sys\n"
                 "import tagpup_cli\n"
                 "try:\n"
                 "    tagpup_cli.cli(sys.argv[1:], standalone_mode=False)\n"
                 "finally:\n"
                 "    print('LOADED=' + ','.join(m for m in ('open_clip', 'torch') if m in sys.modules))\n")
        done = processes.run([sys.executable, "-c", probe, "--db", self.db_path, "index", folder], cwd=root,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn(tagpup_cli.KEPT_LINE % (0, 2, 0, 0, 0), " ".join(done.stdout.split()))
        self.assertEqual("LOADED=", [line for line in done.stdout.splitlines() if line.startswith("LOADED=")][-1])
        for path in (first, second):
            self.assertEqual(self.file_stamp(path), self.stamp(path))

    def test_a_photo_replaced_while_the_run_waits_for_the_card_is_worked_out_again_not_kept_from_the_old_decode(self):
        folder = self.folder("Harbour Replaced While Waiting")
        first = self.picture(folder, "a first.jpg", "navy")
        second = self.picture(folder, "b second.jpg", "teal")
        self.detector.found = {"a first.jpg": [([300, 200, 400, 330], 0.99, 1)],
                               "b second.jpg": [([100, 50, 200, 180], 0.99, 2)]}
        self.index(folder)
        # Keywords written outside the first (kept by its fingerprint, decoded by the no-model check), the second cropped
        # (the run needs a model, so the card's turn is waited for).
        self.outside(first, "-XMP:Subject+=Places/Harbour")
        self.crop(second)
        old_vector = self.vector(first)

        def waits(runtime, what, *args, **kwargs):
            # While the run waits behind Suggest, an editor saves other pixels (a larger file) over the first photo.
            self.turns.append(what)
            Image.new("RGB", (SIZE[0] + 40, SIZE[1]), "gold").save(first, "JPEG")
            return Hold()
        del self.detector.asked[:], self.on_card[:], self.turns[:]
        self.computed[:] = baked_files.unit(bake.PHOTO_DIM, 95).tolist()
        with mock.patch.object(Runtime, "gpu_turn", waits):
            result = self.index(folder)
        self.assertEqual(1, len(self.turns), result.output)
        with open(first, "rb") as held:
            as_saved = held.read()
        self.assertFalse(np.allclose(old_vector, self.computed))
        # The first photo's vector is worked out from the picture now in the file, and its row is that picture's.
        self.assertTrue(np.allclose(self.computed, self.vector(first)), "the old picture's vector was kept")
        self.assertEqual([self.decoded(first)], self.fingerprint(first))
        self.assertEqual(2, self.on_card.count("embed"), "both photos worked out")
        self.assertEqual(["a first.jpg", "b second.jpg"], sorted(self.detector.asked))
        self.kept_line(result, 0, 0, 0, 2, 0)
        with open(first, "rb") as held:
            self.assertEqual(as_saved, held.read(), "nothing written into the replaced file")

    def test_force_reembed_works_out_a_photo_again_and_the_line_says_the_processor_on_a_machine_with_no_card(self):
        folder = self.folder("Harbour Forced")
        path = self.picture(folder, "quay.jpg", "navy")
        self.detector.found = {"quay.jpg": [([300, 200, 400, 330], 0.99, 1)]}
        self.index(folder)
        del self.detector.asked[:], self.on_card[:]
        result = self.index(folder, "--force-reembed")
        self.assertEqual(["quay.jpg"], self.detector.asked)
        self.assertEqual(1, self.on_card.count("embed"))
        self.kept_line(result, 0, 0, 0, 1, 0)
        self.assertEqual(self.file_stamp(path), self.stamp(path))

        # On the processor: one photo read from its bake, one worked out there.
        folder = self.folder("Harbour On The Processor")
        baked = self.picture(folder, "baked.jpg", "teal")
        self.bake(baked, [baked_files.area(0)])
        self.picture(folder, "unbaked.jpg", "olive")
        with mock.patch.object(runtimes, "on_the_card", return_value=False):
            said = self.said(self.index(folder))
        self.assertEqual(["Baked into the files: 1 photo(s) read from their file, 1 worked out on the processor; 0 bake(s) "
                          "stale (the picture changed after it was baked), 0 unreadable, 0 made under other models; 1 "
                          "photo(s) needed no model."], said)

    def test_a_folder_baked_on_the_desktop_and_copied_to_the_server_is_read_there_without_the_card(self):
        # Owner, 2026-10-24: bake on the desktop, copy by hand; the server's library reads the bake with no card.
        desktop = self.folder("Desktop Harbour")
        names = ("quay.jpg", "boats.jpg", "repainted.jpg")
        baked = [self.picture(desktop, name, colour) for name, colour in zip(names, ("navy", "teal", "olive"))]
        self.detector.found = {name: [([300, 200, 400, 330], 0.99, n)] for n, name in enumerate(names)}
        self.index(desktop)
        self.bake_as_planned(baked)
        made_here = {os.path.basename(path): (self.vector(path), self.faces(path)) for path in baked}

        # Copied by hand: each copy a file of its own, its mtime the copy's. One copy is then baked under another photo
        # model (the library's model changed since), one repainted.
        server = self.folder("Server Harbour")
        copies = {name: shutil.copyfile(path, os.path.join(server, name)) for name, path in zip(names, baked)}
        for path in copies.values():
            os.utime(path, (os.stat(path).st_atime, os.stat(path).st_mtime + 3600))
        with field_values.opened(EXIFTOOL) as et:
            et.write(copies["boats.jpg"], {bake.WRITTEN[bake.PHOTO_MODEL]: ["ViT-B-32|openai|cropped|1.0|native"]})
        self.repaint(copies["repainted.jpg"], "purple")

        # The server's library, whose card would answer otherwise than the desktop's did.
        self.db_path = self.made("server.db")
        del self.on_card[:]
        self.computed[:] = baked_files.unit(bake.PHOTO_DIM, 98).tolist()
        self.detector = _faces({name: [([300, 200, 400, 330], 0.99, 50 + n)] for n, name in enumerate(names)})
        said = self.said(self.index(server))
        self.assertEqual(["Baked into the files: 2 photo(s) read from their file, 2 worked out on the graphics card; 1 bake(s) "
                          "stale (the picture changed after it was baked), 0 unreadable, 0 made under other models; 1 "
                          "photo(s) needed no graphics card."], said)
        self.assertEqual(["repainted.jpg"], self.detector.asked)
        self.assertEqual(2, self.on_card.count("embed"), "the vectors of boats and of the repainted copy")
        self.assertTrue(np.allclose(made_here["quay.jpg"][0], self.vector(copies["quay.jpg"]), atol=1e-3))
        for name in ("quay.jpg", "boats.jpg"):
            (_box, vector, _name, _excluded), = self.faces(copies[name])
            self.assertTrue(np.allclose(made_here[name][1][0][1], vector, atol=1e-3), name)
        for name in ("boats.jpg", "repainted.jpg"):
            self.assertTrue(np.allclose(self.computed, self.vector(copies[name])), name)
        self.assertFalse(np.allclose(made_here["repainted.jpg"][1][0][1], self.faces(copies["repainted.jpg"])[0][1],
                                     atol=1e-3))
        # Read on the server, the quay's bake is current there: a backfill on the server skips it.
        self.assertIsInstance(self.plan(copies["quay.jpg"]), baking.Current)

    def test_bakes_all_of_other_models_are_said_and_a_failed_detection_is_not_the_cards_work(self):
        folder = self.folder("Harbour Other Models")
        both = self.picture(folder, "both other.jpg", "navy")
        self.bake(both, [baked_files.area(0)], **{bake.PHOTO_MODEL: "ViT-B-32|openai|cropped|1.0|native",
                                                  bake.FACE_MODEL: validation.MTCNN_FACENET})
        said = self.said(self.index(folder))
        self.assertEqual(["Baked into the files: 0 photo(s) read from their file, 1 worked out on the graphics card; 0 bake(s) "
                          "stale (the picture changed after it was baked), 0 unreadable, 1 made under other models; 0 "
                          "photo(s) needed no graphics card."], said)

        # Its vector read, its faces asked of a model that fails: nothing was worked out on the card.
        folder = self.folder("Harbour Failed Detection")
        failed = self.picture(folder, "failed.jpg", "teal")
        self.bake(failed, [baked_files.area(0)], **{bake.FACE_MODEL: validation.MTCNN_FACENET})
        self.detector.found = {"failed.jpg": None}
        del self.detector.asked[:], self.on_card[:]
        said = self.said(self.index(folder))
        self.assertEqual(["failed.jpg"], self.detector.asked)
        self.assertEqual([], self.on_card)
        self.assertEqual(["Baked into the files: 1 photo(s) read from their file, 0 worked out on the graphics card; 0 bake(s) "
                          "stale (the picture changed after it was baked), 0 unreadable, 0 made under other models; 1 "
                          "photo(s) needed no graphics card."], said)

    def test_a_photo_whose_face_decode_fails_is_still_indexed_with_its_vector_from_the_bake(self):
        # #415: the face model catches every exception of its own decode (a truncated JPEG), and answers NotDetected.
        folder = self.folder("Harbour Truncated")
        cut = self.picture(folder, "cut.jpg", "navy")
        whole = self.picture(folder, "whole.jpg", "teal")
        held = self.bake(cut, [baked_files.area(0)], seed=1)
        self.bake(whole, [baked_files.area(1)], seed=2)
        self.detector.undecodable = ("cut.jpg",)
        with self.assertNoLogs("tagpup_cli", "ERROR"):
            result = self.index(folder)
        self.assertEqual(2, self.query("SELECT COUNT(*) FROM photos")[0][0], result.output)
        self.assertTrue(np.allclose(bake.parse(held).photo_vector, self.vector(cut), atol=1e-6))
        self.assertEqual([], self.faces(cut))
        self.assertEqual([], self.detected(cut), "a detection that failed is not recorded as one that found none")
        self.assertEqual(1, len(self.faces(whole)))

    def test_the_line_reaches_the_servers_index_job_whole(self):
        folder = self.folder("Harbour Job")
        path = self.picture(folder, "job.jpg", "navy")
        self.bake(path, [baked_files.area(0)])
        # The bake the index writes after it records is left out (setUp): it says a run that wrote nothing.
        bake_after_index.bake_folders.return_value = bake_after_index.Baked()
        # Matching the faces fails with an error that names a path in letters the pipe's code page may not hold, over
        # two lines (#2150): its sentence is what the message ends with.
        failure = OSError("Kein Zugriff: %s\nZeile zwei" % os.path.join(
            self.home.root, "\u00c6r\u00f8sk\u00f8bing Strand", "job.jpg"))
        # The server asks for facts (tagpup.core.progress_line), as indexing.index_folder does in the child's environment.
        with mock.patch.dict(os.environ, {progress_line.ASKED: "1"}), \
                mock.patch.object(face_service, "automatch_new", side_effect=failure), \
                self.assertLogs("tagpup_cli", "ERROR"):
            printed = self.index(folder).output
        self.assertEqual([], self.said(printed), "asked for facts, the CLI prints no person's bake line")
        heard = [progress_line.heard(line) for line in printed.splitlines()]
        (read,) = [fact["sentence"] for fact in heard if fact and fact["kind"] == "bake_read"]
        (faces,) = [fact["sentence"] for fact in heard if fact and fact["kind"] == "new_faces"]
        self.assertTrue(read.startswith(bake_reading.SAID), read)
        self.assertGreater(len(read), 90, "a person's line is cut at 90 characters")
        self.assertIn(str(failure), faces)
        self.assertTrue(all(line.isascii() for line in printed.splitlines() if line.startswith(progress_line.PREFIX)),
                        "a fact is ASCII on the pipe")

        # The server's index job runs the CLI as a process of its own: what it printed, line by line as a pipe gives it
        # (a bar's frames joined by carriage returns), is what it hears.
        def cli(command, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout.readline.side_effect = iter([line + "\n" for line in printed.split("\n")] + [""])
            return proc
        library = Library(self.db_path)
        queue = indexing_jobs.IndexQueue(library.name, library)
        with mock.patch.object(indexing_jobs.IndexQueue, "_ensure_runner"), mock.patch("subprocess.Popen", cli):
            queue.start([folder], lambda each, report: indexing.index_folder(library, each, self.home.root, report))
            queue.run_pending()
        status = queue.status(folder)
        self.assertEqual("completed", status["status"], status)
        self.assertEqual(" ".join(["Folder indexed. " + faces, read, bake_after_index.Baked().said()]), status["message"])

    def test_an_index_that_could_not_say_its_facts_does_not_silence_the_next_in_the_same_process(self):
        # Two indexes in one process (a test's CliRunner): the first one's facts cannot be said, and the second is heard
        # from its first line. The real pipe whose reader is gone, and the index going on to its end, are
        # tests/test_index_progress_end_to_end.py's TheServerGoesMidIndex.
        folder = self.folder("Harbour Gone")
        path = self.picture(folder, "gone.jpg", "navy")
        self.bake(path, [baked_files.area(0)])
        bake_after_index.bake_folders.return_value = bake_after_index.Baked()
        said = print

        def closed(*args, **kwargs):
            if args and str(args[0]).startswith(progress_line.PREFIX):
                raise OSError(22, "Invalid argument")
            said(*args, **kwargs)
        with mock.patch.dict(os.environ, {progress_line.ASKED: "1"}):
            with mock.patch("builtins.print", side_effect=closed), \
                    self.assertLogs("tagpup_cli", "WARNING") as logged:
                printed = self.index(folder).output
            self.assertNotIn(progress_line.PREFIX, printed)
            self.assertEqual(1, len([record for record in logged.records if "progress lines stop" in record.getMessage()]),
                             "the failure is logged once")
            self.picture(folder, "back.jpg", "teal")   # a photo added since, for the next index to read
            again = self.index(folder).output
        heard = [progress_line.heard(line) for line in again.splitlines()]
        self.assertIn("new_faces", [fact["kind"] for fact in heard if fact], again)

    def test_a_slow_index_whose_percent_stands_still_is_heard_every_progress_every_seconds(self):
        # photo_index's 59,523 photos at under 2 a second: a whole percent is over activity.STUCK_AFTER, and the run read
        # as stuck. Here 120 photos, each read as the CLI's clock moves PROGRESS_EVERY: every photo is said, though the
        # percent stands still between some of them. Failed on the old code: 101 facts, the percent's moves alone.
        folder = self.folder("Harbour Slow")
        for number in range(120):
            self.picture(folder, "heat %03d.jpg" % number, (number, 90, 160), size=(48, 32))
        bake_after_index.bake_folders.return_value = bake_after_index.Baked()
        ticks = itertools.count()
        clock = mock.Mock(monotonic=lambda: next(ticks) * job_runs.PROGRESS_EVERY)
        with mock.patch.dict(os.environ, {progress_line.ASKED: "1"}), mock.patch.object(tagpup_cli, "time", clock, create=True):
            printed = self.index(folder).output
        heard = [progress_line.heard(line) for line in printed.splitlines()]
        said = [fact["done"] for fact in heard if fact and fact["kind"] == "phase" and fact["phase"] == "embeddings"]
        self.assertEqual(list(range(121)), said)

    def test_a_turned_photos_faces_read_from_its_bake_are_the_boxes_a_detection_stores(self):
        folder = self.folder("Harbour Turned")
        detected = self.picture(folder, "turned detected.jpg", "navy", turned=6)
        baked = self.picture(folder, "turned baked.jpg", "navy", turned=6)
        xmp_detected = self.picture(folder, "xmp turned detected.jpg", "navy", xmp_turned=6)
        xmp_baked = self.picture(folder, "xmp turned baked.jpg", "navy", xmp_turned=6)
        box = [100, 50, 200, 180]
        # The detector looks at the picture as shown and finds the face there; the bake's region, as the writer makes
        # it: the stored box shown upright, a fraction of the upright picture.
        upright = orientation.to_upright(box, *SIZE, 6)
        self.detector.found = {"turned detected.jpg": [(upright, 0.99, 1)],
                               "xmp turned detected.jpg": [(upright, 0.99, 2)]}
        shown = orientation.upright_size(*SIZE, 6)
        self.assertEqual(shown, images.opened(xmp_baked, upright=True).size, "Pillow turns the XMP-only photo")
        self.bake(baked, [orientation.area_of(upright, *shown)])
        self.bake(xmp_baked, [orientation.area_of(upright, *shown)], seed=2)
        self.index(folder)
        for path in (detected, baked, xmp_detected, xmp_baked):
            self.assertEqual([box], [found for found, *_ in self.faces(path)], os.path.basename(path))
        self.assertEqual(["turned detected.jpg", "xmp turned detected.jpg"], sorted(self.detector.asked))

    def test_a_face_named_or_excluded_keeps_its_decision_when_the_photo_is_read_again_from_its_bake(self):
        folder = self.folder("Harbour Kept")
        path = self.picture(folder, "kept.jpg", "navy")
        self.bake(path, [baked_files.area(0), baked_files.area(1)])
        self.index(folder)
        library = Library(self.db_path)
        named, ignored = [face_id for _box, face_id in sorted(
            (json.loads(box), face_id) for face_id, box in self.query("SELECT id, box FROM faces"))]
        person = face_people.made(library, "Rowan Thackeray")
        self.assertTrue(face_service.name_face(library, named, person).ok)
        self.assertTrue(face_service.exclude(library, [ignored]).ok)
        # The owner tags the photo elsewhere: its file changes, its picture does not; the index reads it again.
        flat, hierarchical = fields.expand_tag_fields(["Places/Harbour"])
        after = dict(fields.keyword_fields(flat, hierarchical))
        file_only.write_fields(exiftool_fields.opened(self, EXIFTOOL), [path], list(after), lambda _path, _held: file_changes.Plan(after=after))
        self.refused = self.detector.refused = True
        self.index(folder)
        self.assertEqual([(box_of(baked_files.area(0), *SIZE), "Rowan Thackeray", 0),
                          (box_of(baked_files.area(1), *SIZE), None, 1)],
                         [(box, name, excluded) for box, _vector, name, excluded in self.faces(path)])

    def test_an_index_killed_part_way_leaves_whole_rows_and_the_next_one_finishes(self):
        folder = self.folder("Harbour Killed")
        first = self.picture(folder, "a baked.jpg", "navy")
        self.bake(first, [baked_files.area(0)])
        for name, colour in (("b unbaked.jpg", "olive"), ("c unbaked.jpg", "teal")):
            self.picture(folder, name, colour)
        self.detector = _faces({"b unbaked.jpg": [([300, 200, 400, 330], 0.99, 1)]}, kill_at="b unbaked.jpg")
        with self.assertRaises(NewFaces.Killed):
            CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", folder], catch_exceptions=False)
        # Every photo recorded read holds its vector, its fingerprint and its detection; a vector kept before its photo's
        # batch was saved is on a row still unread (mtime NULL), read again by the next run: none is half written.
        rows = self.query("SELECT p.mtime IS NOT NULL, (SELECT COUNT(*) FROM embeddings e WHERE e.photo_id = p.id), "
                          "(SELECT COUNT(*) FROM photo_fingerprints f WHERE f.photo_id = p.id), "
                          "(SELECT COUNT(*) FROM faces_detected d WHERE d.photo_id = p.id) FROM photos p")
        self.assertLessEqual({tuple(counts) for read, *counts in rows if read}, {(1, 1, 1)})
        self.assertEqual(set(), {tuple(counts[1:]) for read, *counts in rows if not read} - {(0, 0)})
        self.detector = _faces({"b unbaked.jpg": [([300, 200, 400, 330], 0.99, 1)]})
        self.index(folder)
        self.assertEqual(3, self.query("SELECT COUNT(*) FROM photos")[0][0])
        self.assertEqual(3, self.query("SELECT COUNT(*) FROM faces_detected")[0][0])
        self.assertEqual(3, self.query("SELECT COUNT(*) FROM photos WHERE mtime IS NOT NULL")[0][0])
        self.assertEqual(2, self.query("SELECT COUNT(*) FROM faces")[0][0])

    def test_a_library_at_33_is_brought_up_as_the_index_opens_it_and_its_fingerprints_recorded(self):
        with migration_names.up_to(33):
            self.db_path = self.made("old_harbour.db")
        self.assertEqual([], self.query("SELECT name FROM sqlite_master WHERE name = 'photo_fingerprints'"))
        self.assertEqual(33, self.query("SELECT MAX(version) FROM schema_version")[0][0])
        folder = self.folder("Harbour Before")
        path = self.picture(folder, "before.jpg", "navy")
        held = self.bake(path, [baked_files.area(0)])
        self.refused = self.detector.refused = True
        self.index(folder)
        self.assertEqual(schema.LATEST, self.query("SELECT MAX(version) FROM schema_version")[0][0])
        self.assertHoldsTheBake(path, held, SIZE)
        self.assertEqual([(images.FINGERPRINT_KEY, images.fingerprint(images.opened(path, upright=True)))],
                         self.query("SELECT key, hash FROM photo_fingerprints"))

    def test_the_indexs_read_answers_every_field_as_the_plain_read_does(self):
        folder = self.folder("Harbour Fields")
        path = self.picture(folder, "fields.jpg", "navy")
        self.bake(path, [baked_files.area(0), baked_files.area(1)])
        flat, hierarchical = fields.expand_tag_fields(["Places/Harbour", "Family/Rowan Thackeray"])
        after = dict(fields.keyword_fields(flat, hierarchical))
        after.update(fields.caption_fields("Boats at the harbour wall"))
        after.update({"XMP:Title": "Harbour wall", "XMP:Rating": 4, "EXIF:GPSLatitude": 51.5, "EXIF:GPSLatitudeRef": "N",
                      "EXIF:GPSLongitude": 1.25, "EXIF:GPSLongitudeRef": "W"})
        written = file_only.write_fields(exiftool_fields.opened(self, EXIFTOOL), [path], list(after), lambda _path, _held: file_changes.Plan(after=after))
        self.assertEqual(1, written.changed, written.errors)
        with field_values.opened(EXIFTOOL) as reader:
            (plain,), (struct,) = reader.whole([path]), reader.whole([path], bakes=True)
        self.assertTrue(struct.pop("baked"))
        self.assertEqual(plain, struct)
        for field in ("XMP:Subject", "XMP:Title", "XMP:Rating", "Composite:GPSLatitude"):
            self.assertIn(field, plain["raw_metadata"], field)


if __name__ == "__main__":
    unittest.main()
