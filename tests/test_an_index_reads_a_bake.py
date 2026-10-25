"""An index reads a photo's bake instead of working it out on the graphics card (docs/PHASE6_DESIGN.md, sections 3, 7 and
8; tagpup.services.bake_reading, tagpup_cli.py's index).

What the owner lives through, adding a folder whose photos were baked:

* a folder baked under the library's keys is indexed and no model is asked: each photo's vector and faces are the bake's,
  a JPEG, a TIFF and a photo of 60 faces whose bake spans Extended XMP alike; a bake that found none gives no face and the
  faces_detected row a detection finding none writes; the photo's raw_metadata holds nothing of the bake;
* a bake whose picture changed after it was baked, one not whole, one made under another photo model, one under another
  face model: that side is worked out (the other read when it is the library's), and the line at the end counts each;
* a turned photo (Orientation 6, 12 such in photo_index), and one turned by an XMP tiff:Orientation alone, which ExifTool
  answers as no EXIF Orientation and Pillow turns all the same: its faces' boxes, read from the bake, are the boxes a
  detection stores;
* a face named or excluded on a photo indexed again keeps its decision when the bake is read;
* an index killed part-way leaves every row whole and the next run completes the folder;
* the index's read (`-struct`) answers every field of METADATA_FIELDS as the plain read does.

CLIP and the face model are stood in for (tests/test_no_test_loads_a_model.py); a stand-in asked while `refused` raises
and is noted. Pillow and ExifTool are real; the bakes are written by tests/baked_files.py (the writer is a later cut);
the library is stamped with the new face model and made by the picker's Create in a home of its own.
"""
import json
import os
import sys
import unittest
from unittest import mock

import numpy as np
from click.testing import CliRunner
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baked_files  # noqa: E402
import own_home  # noqa: E402
from stand_in_models import NewFaces  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import fields, orientation, paths, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import bake, images  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.files.metadata import MetadataExtractor  # noqa: E402
from tagpup.ml import clip  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import face_people  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import file_changes, file_only  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
FACES = validation.SCRFD_ADAFACE
SIZE = (640, 480)


class _Hold:
    def release(self):
        pass


class _Faces(NewFaces):
    """The new face model, which raises when the test refuses it (and notes the photo either way)."""
    refused = False

    def detect_and_embed(self, picture):
        if self.refused:
            self.asked.append(os.path.basename(picture.path))
            raise AssertionError("the face model was asked of %s" % os.path.basename(picture.path))
        return super().detect_and_embed(picture)


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
        self.db_path = self.home.library("harbour.db")
        library_actions.bring_up_to_date(self.db_path)
        library_settings.stamp(Library(self.db_path), {"faces.model": FACES}, operation=library_settings.FROM_REPLACED)
        self.assertEqual(1, library_actions.create(self.db_path).ok)
        self.photo_key = store_embeddings.model_key(**library_settings.read(Library(self.db_path)).embedder)
        self.config = baked_files.config_in(self.home.root)
        self.refused, self.on_card = False, []
        self.computed = baked_files.unit(bake.PHOTO_DIM, 99).tolist()

        def card(what, value=None):
            def asked(*args, **kwargs):
                self.on_card.append(what)
                if self.refused:
                    raise AssertionError("CLIP was asked: %s" % what)
                return value
            return asked
        self.detector = _Faces()
        for patcher in (mock.patch.object(clip.ClipModel, "_init_model", side_effect=card("load")),
                        mock.patch.object(clip.ClipModel, "picture_input", side_effect=card("input", "input")),
                        mock.patch.object(clip.ClipModel, "embed_input", side_effect=card("embed", self.computed)),
                        mock.patch.object(Runtime, "gpu_turn", lambda *args, **kwargs: _Hold()),
                        mock.patch.object(Runtime, "faces", lambda runtime, library, settings=None: self.detector)):
            patcher.start()
            self.addCleanup(patcher.stop)

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

    def bake(self, path, areas, seed=1, of=None, **changed):
        """Bake into `path` its vector (seed `seed`) and faces at `areas`, against its upright picture -- or `of`'s, a
        picture the file no longer holds."""
        upright = images.opened(of or path, upright=True)
        faces = [(area, baked_files.unit(bake.FACE_DIM, 10 * seed + n)) for n, area in enumerate(areas)]
        held = baked_files.baked(baked_files.unit(bake.PHOTO_DIM, seed), faces, upright.size, photo_model=self.photo_key,
                                 face_model=FACES, fingerprint=images.fingerprint(upright))
        held.update(changed)
        baked_files.write(path, self.config, held)
        return held

    def index(self, folder):
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", folder])
        self.assertEqual(0, result.exit_code, result.output)
        return result

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
        self.assertIn("Baked into the files: 4 photo(s) read from their file, 0 worked out on the graphics card; 0 bake(s) "
                      "stale (the picture changed after it was baked), 0 unreadable; 4 photo(s) needed no graphics card.",
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

    def test_a_bake_not_of_the_library_or_not_of_the_picture_is_worked_out_and_counted(self):
        folder = self.folder("Harbour Mixed")
        read = self.picture(folder, "read.jpg", "navy")
        stale = self.picture(folder, "stale.jpg", "maroon")
        corrupt = self.picture(folder, "corrupt.jpg", "olive")
        foreign = self.picture(folder, "foreign.jpg", "teal")
        unbaked = self.picture(folder, "unbaked.jpg", "purple")
        held = self.bake(read, [baked_files.area(0)], seed=1)
        # The picture edited after it was baked: the bake's fingerprint is of the picture it held then.
        before = self.picture(self.folder("Before"), "stale before.jpg", "red")
        self.bake(stale, [baked_files.area(0)], seed=2, of=before)
        self.bake(corrupt, [baked_files.area(0)], seed=3, **{bake.PHOTO_VECTOR: "not a vector"})
        other = self.bake(foreign, [baked_files.area(1)], seed=4, **{bake.PHOTO_MODEL: "ViT-B-32|openai|cropped|1.0|native"})
        # Baked under the library's old face model: its vector read, its faces detected.
        old_faces = self.picture(folder, "old faces.jpg", "maroon")
        own = self.bake(old_faces, [baked_files.area(2)], seed=5, **{bake.FACE_MODEL: validation.MTCNN_FACENET})
        box = [300, 200, 400, 330]
        self.detector.found = {name: [(box, 0.99, n)] for n, name in enumerate(
            ("stale.jpg", "corrupt.jpg", "foreign.jpg", "unbaked.jpg", "old faces.jpg"))}
        result = self.index(folder)
        self.assertTrue(np.allclose(bake.parse(own).photo_vector, self.vector(old_faces), atol=1e-6))
        self.assertEqual([box], [found for found, *_ in self.faces(old_faces)])
        self.assertHoldsTheBake(read, held, SIZE)
        for path in (stale, corrupt, unbaked):
            self.assertTrue(np.allclose(self.computed, self.vector(path)), path)
            self.assertEqual([box], [found for found, *_ in self.faces(path)], path)
        # Another photo model's bake: its vector worked out, its faces (the library's model's) read.
        self.assertTrue(np.allclose(self.computed, self.vector(foreign)))
        self.assertEqual([box_of(baked_files.area(1), *SIZE)], [found for found, *_ in self.faces(foreign)])
        self.assertTrue(np.allclose(bake.parse(other).faces[0].vector, self.faces(foreign)[0][1], atol=1e-3))
        self.assertEqual(["corrupt.jpg", "old faces.jpg", "stale.jpg", "unbaked.jpg"], sorted(self.detector.asked))
        # CLIP is asked of the four whose vector the bake did not give, the old faces' photo not among them.
        self.assertEqual(4, self.on_card.count("embed"))
        self.assertIn("Baked into the files: 3 photo(s) read from their file, 5 worked out on the graphics card; 1 bake(s) "
                      "stale (the picture changed after it was baked), 1 unreadable; 1 photo(s) needed no graphics card.",
                      " ".join(result.output.split()))

    def test_a_turned_photos_faces_read_from_its_bake_are_the_boxes_a_detection_stores(self):
        folder = self.folder("Harbour Turned")
        detected = self.picture(folder, "turned detected.jpg", "navy", turned=6)
        baked = self.picture(folder, "turned baked.jpg", "navy", turned=6)
        xmp_detected = self.picture(folder, "xmp turned detected.jpg", "navy", xmp_turned=6)
        xmp_baked = self.picture(folder, "xmp turned baked.jpg", "navy", xmp_turned=6)
        box = [100, 50, 200, 180]
        self.detector.found = {"turned detected.jpg": [(box, 0.99, 1)], "xmp turned detected.jpg": [(box, 0.99, 2)]}
        # The bake's region, as the writer will make it: the stored box shown upright, a fraction of the upright picture.
        upright = orientation.to_upright(box, *SIZE, 6)
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
        file_only.write_fields(EXIFTOOL, [path], list(after), lambda _path, _held: file_changes.Plan(after=after))
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
        self.detector = _Faces({"b unbaked.jpg": [([300, 200, 400, 330], 0.99, 1)]}, kill_at="b unbaked.jpg")
        with self.assertRaises(NewFaces.Killed):
            CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", folder], catch_exceptions=False)
        # Every photo recorded read holds its vector, its fingerprint and its detection; a vector kept before its photo's
        # batch was saved is on a row still unread (mtime NULL), read again by the next run: none is half written.
        rows = self.query("SELECT p.mtime IS NOT NULL, (SELECT COUNT(*) FROM embeddings e WHERE e.photo_id = p.id), "
                          "(SELECT COUNT(*) FROM photo_fingerprints f WHERE f.photo_id = p.id), "
                          "(SELECT COUNT(*) FROM faces_detected d WHERE d.photo_id = p.id) FROM photos p")
        self.assertLessEqual({tuple(counts) for read, *counts in rows if read}, {(1, 1, 1)})
        self.assertEqual(set(), {tuple(counts[1:]) for read, *counts in rows if not read} - {(0, 0)})
        self.detector = _Faces({"b unbaked.jpg": [([300, 200, 400, 330], 0.99, 1)]})
        self.index(folder)
        self.assertEqual(3, self.query("SELECT COUNT(*) FROM photos")[0][0])
        self.assertEqual(3, self.query("SELECT COUNT(*) FROM faces_detected")[0][0])
        self.assertEqual(3, self.query("SELECT COUNT(*) FROM photos WHERE mtime IS NOT NULL")[0][0])
        self.assertEqual(2, self.query("SELECT COUNT(*) FROM faces")[0][0])

    def test_the_indexs_read_answers_every_field_as_the_plain_read_does(self):
        folder = self.folder("Harbour Fields")
        path = self.picture(folder, "fields.jpg", "navy")
        self.bake(path, [baked_files.area(0), baked_files.area(1)])
        flat, hierarchical = fields.expand_tag_fields(["Places/Harbour", "Family/Rowan Thackeray"])
        after = dict(fields.keyword_fields(flat, hierarchical))
        after.update(fields.caption_fields("Boats at the harbour wall"))
        after.update({"XMP:Title": "Harbour wall", "XMP:Rating": 4, "EXIF:GPSLatitude": 51.5, "EXIF:GPSLatitudeRef": "N",
                      "EXIF:GPSLongitude": 1.25, "EXIF:GPSLongitudeRef": "W"})
        written = file_only.write_fields(EXIFTOOL, [path], list(after), lambda _path, _held: file_changes.Plan(after=after))
        self.assertEqual(1, written.changed, written.errors)
        extractor = MetadataExtractor(EXIFTOOL)
        (plain,), (struct,) = extractor.batch_read([path]), extractor.batch_read([path], bakes=True)
        self.assertTrue(struct.pop("baked"))
        self.assertEqual(plain, struct)
        for field in ("XMP:Subject", "XMP:Title", "XMP:Rating", "Composite:GPSLatitude"):
            self.assertIn(field, plain["raw_metadata"], field)


if __name__ == "__main__":
    unittest.main()
