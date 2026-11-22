"""A photo whose picture changed gets the faces of its new picture (docs/PLAN_KEEP_IN_STEP.md, scenario 4, and "Decided
2026-10-27 after the cut-1 reviews"; tagpup.services.faces.forget_for_new_picture, called by tagpup_cli.py's index).

What the owner lives through, a photo of two faces in a library, one face named by hand, the photo carrying that
person's tag, the other face excluded:

* the photo is cropped in an editor (its pixels change, its keywords do not): the next index detects it again, its old
  boxes are gone, the new picture's faces are there, and the person's tag names the face again; the exclusion is lost
  with its face (accepted: owner, 2026-10-27);
* another program writes a keyword into it (its metadata alone): its faces and their rows are left as they were, no
  model asked;
* the index is killed as it works out the cropped photo's vector, or as it detects it, or its write of the faces fails:
  no old box and no half of the new ones, and the next index finishes it;
* the cropped photo is indexed with --skip-faces (typed by hand): its old boxes go all the same, and the next index
  gives it the new picture's faces;
* two libraries hold the folder: each replaces its own faces of the cropped photo when it indexes it, not the other's.

The picture's size its row records (photos.width, height; migration 39), in the frame its face boxes are in, follows the
same reads: the cropped photo's is the new picture's after the next index, and not before (a sync reads its metadata
again and leaves the size, which the index's read of the picture records); a keyword written outside keeps it; each of
two libraries records it at its own index; a photo turned by its Orientation records its stored pixels, and keeps them
when only its Orientation is written again (a rotation in TagPup writes that alone).

A photo whose vector is stale and that has no fingerprint row (74 in photo_index, counted 2026-10-27) is not known to be
another picture, and keeps its faces as before (tests/test_an_index_reads_a_bake.py).

CLIP and the face model are stood in for (tests/test_no_test_loads_a_model.py); Pillow and ExifTool are real; the
library is made by the picker's Create alone, in a home of its own.
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
import page_writes  # noqa: E402
import own_home  # noqa: E402
from stand_in_models import Hold, NewFaces, RefusableFaces  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import bake  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.ml import clip  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import face_people, tagging  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import people as people_service  # noqa: E402
from tagpup.store import db  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
SIZE = (640, 480)
LEFT, RIGHT = [60, 60, 180, 220], [420, 60, 540, 220]
#: Where the detector finds the face that is left after the crop to the left half.
CROPPED = [70, 80, 190, 240]
PERSON = "Rowan Thackeray"


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class ACroppedPictureGetsNewFaces(unittest.TestCase):

    def setUp(self):
        self.home = own_home.for_test(self, prefix="cropped_picture_")
        self.db_path = self.made("harbour.db")
        self.detector = RefusableFaces({"pier.jpg": [(LEFT, 0.99, 1), (RIGHT, 0.98, 2)]},
                                       vector=baked_files.face_vector)
        computed = baked_files.unit(bake.PHOTO_DIM, 99).tolist()
        for patcher in (mock.patch.object(clip.ClipModel, "_init_model"),
                        mock.patch.object(clip.ClipModel, "picture_input", return_value="input"),
                        mock.patch.object(clip.ClipModel, "embed_input", return_value=computed),
                        mock.patch.object(Runtime, "gpu_turn", lambda runtime, what, *args, **kwargs: Hold()),
                        mock.patch.object(runtimes, "on_the_card", return_value=True),
                        mock.patch.object(Runtime, "faces", lambda runtime, library, settings=None: self.detector)):
            patcher.start()
            self.addCleanup(patcher.stop)
        folder = os.path.join(self.home.root, "Harbour")
        os.makedirs(folder)
        self.folder = paths.stored(folder)
        self.photo = os.path.join(self.folder, "pier.jpg")
        Image.new("RGB", SIZE, "navy").save(self.photo, "JPEG")

    def made(self, name):
        db_path = self.home.library(name)
        self.assertEqual(1, library_actions.create(db_path).changed)
        return db_path

    def index(self, db_path=None, *options):
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", db_path or self.db_path, "index", *options, self.folder])
        self.assertEqual(0, result.exit_code, result.output)
        return result

    def faces(self, db_path=None):
        """[(box, face id, name, name_source, excluded)] of the photo's faces, by box."""
        conn = db.connect(db.readonly_uri(db_path or self.db_path), uri=True)
        try:
            return sorted((json.loads(box), face_id, name, source, excluded) for face_id, box, name, source, excluded
                          in conn.execute("SELECT id, box, name, name_source, excluded FROM faces"))
        finally:
            conn.close()

    def boxes(self, db_path=None):
        return [box for box, *_ in self.faces(db_path)]

    def sizes(self, db_path=None):
        """{file name: (width, height)} of each photo's row."""
        conn = db.connect(db.readonly_uri(db_path or self.db_path), uri=True)
        try:
            return {os.path.basename(path): (width, height)
                    for path, width, height in conn.execute("SELECT path, width, height FROM photos")}
        finally:
            conn.close()

    def looks(self, db_path=None):
        """[found] of each record of a look at the photo, and the face vectors and crops no face holds (none)."""
        conn = db.connect(db.readonly_uri(db_path or self.db_path), uri=True)
        try:
            for table in ("face_embeddings", "face_crops"):
                self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM %s WHERE face_id NOT IN (SELECT id FROM faces)"
                                                 % table).fetchone()[0], table)
            return [found for (found,) in conn.execute("SELECT found FROM faces_detected")]
        finally:
            conn.close()

    def named_by_hand(self, db_path=None):
        """The photo indexed, its person's tag written as the page writes it, its left face named by hand and its right
        face excluded."""
        db_path = db_path or self.db_path
        self.index(db_path)
        library = Library(db_path)
        person = face_people.made(library, PERSON)
        tag = people_service.resolve(library, person).tag
        saved = tagging.save_photo(library, self.photo, "", [tag], None, EXIFTOOL, "",
                                   base=page_writes.base(library, self.photo))
        self.assertEqual(1, saved.changed, saved.errors)
        (_, left, *_), (_, right, *_) = self.faces(db_path)
        self.assertTrue(face_service.name_face(library, left, person).ok)
        self.assertTrue(face_service.exclude(library, [right]).ok)
        self.assertEqual([(LEFT, PERSON, "manual", 0), (RIGHT, None, "manual", 1)],
                         [(box, name, source, excluded) for box, _id, name, source, excluded in self.faces(db_path)])

    def crop(self):
        """An editor's crop to the left half, saved over the file with its XMP (the person's tag) as it was."""
        with Image.open(self.photo) as held:
            xmp = held.info["xmp"]
            cut = held.crop((0, 0, SIZE[0] // 2, SIZE[1]))
        cut.save(self.photo, "JPEG", xmp=xmp)
        self.detector.found = {"pier.jpg": [(CROPPED, 0.99, 7)]}

    def assertTheNewPictures(self, db_path=None, before=()):
        """One face, at the cropped picture's box, a new row named from the photo's tag (not by hand), not excluded."""
        (box, face_id, name, source, excluded), = self.faces(db_path)
        self.assertEqual((CROPPED, PERSON, None, 0), (box, name, source, excluded))
        self.assertNotIn(face_id, before, "an old face row kept on the new picture")
        self.assertEqual([1], self.looks(db_path), "one record of the look at the new picture")

    def test_a_cropped_photo_loses_its_old_boxes_and_its_tag_names_the_new_face(self):
        self.named_by_hand()
        before = [face_id for _box, face_id, *_ in self.faces()]
        self.crop()
        del self.detector.asked[:]
        with self.assertLogs("tagpup_cli", "INFO") as logged:
            self.index()
        self.assertIn("Its picture changed: 2 old faces of %s forgotten" % self.photo,
                      [record.getMessage() for record in logged.records])
        self.assertEqual(["pier.jpg"], self.detector.asked)
        self.assertTheNewPictures(before=before)
        # In step: the next run has nothing to do.
        self.detector.refused = True
        self.assertIn("All images are up to date!", self.index().output)

    def test_the_cropped_photos_size_is_the_new_pictures_at_the_next_index_and_not_at_a_sync_before_it(self):
        self.index()
        self.assertEqual({"pier.jpg": SIZE}, self.sizes())
        self.crop()
        synced = tagpup_cli.get_runtime().sync(Library(self.db_path), apply=True)
        self.assertIsNone(synced.refused)
        self.assertEqual(1, synced.details["counts"]["changed"], "the sync read the cropped photo again")
        self.assertEqual({"pier.jpg": SIZE}, self.sizes(), "a sync reads metadata, not the picture")
        self.index()
        self.assertEqual({"pier.jpg": (SIZE[0] // 2, SIZE[1])}, self.sizes())

    def test_a_keyword_written_outside_keeps_the_faces_and_their_rows(self):
        self.named_by_hand()
        before = self.faces()
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.execute("-XMP:Subject+=Places/Pier", "-overwrite_original", self.photo)
        self.detector.refused = True
        self.index()
        self.assertEqual(before, self.faces())
        self.assertEqual({"pier.jpg": SIZE}, self.sizes(), "its picture's size kept, read with its new metadata")

    def test_a_photo_turned_by_its_orientation_records_its_stored_pixels(self):
        """300 x 200 stored, shown 200 x 300 (Orientation 6): its face boxes are in the stored pixels, and so is its size.
        Turned again in TagPup (its Orientation written, its pixels left), the next index keeps it."""
        turned = os.path.join(self.folder, "mast.jpg")
        exif = Image.Exif()
        exif[0x0112] = 6
        Image.new("RGB", (300, 200), "teal").save(turned, "JPEG", exif=exif)
        self.index()
        self.assertEqual((300, 200), self.sizes()["mast.jpg"])
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.execute("-Orientation#=8", "-overwrite_original", turned)
        self.detector.refused = True
        self.index()
        self.assertEqual({"pier.jpg": SIZE, "mast.jpg": (300, 200)}, self.sizes())

    def test_an_index_killed_or_failing_as_it_records_leaves_no_old_box_and_no_half_and_the_next_finishes(self):
        self.named_by_hand()
        self.crop()
        # Stopped as its new vector is worked out, before it is kept.
        with mock.patch.object(clip.ClipModel, "embed_input", side_effect=NewFaces.Killed("embed")):
            with self.assertRaises(NewFaces.Killed):
                CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", self.folder],
                                   catch_exceptions=False)
        self.assertEqual([], self.boxes(), "an old box kept on the new picture")
        self.assertEqual([], self.looks(), "never examined, so the next index looks at it")
        # Stopped as it is detected.
        self.detector.kill_at = "pier.jpg"
        with self.assertRaises(NewFaces.Killed):
            CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", self.folder], catch_exceptions=False)
        self.assertEqual([], self.boxes(), "an old box kept on the new picture")
        self.assertEqual([], self.looks(), "never examined, so the next index looks at it")
        # The next run's write of the faces fails part-way, after the first of two: nothing of it is kept.
        self.detector.kill_at = None
        self.detector.found = {"pier.jpg": [(CROPPED, 0.99, 7), ([200, 300, 300, 440], 0.9, 8)]}
        real, calls = face_service._insert_detected, []

        def fails_second(conn, *args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("the disk stopped answering")
            return real(conn, *args, **kwargs)
        with mock.patch.object(face_service, "_insert_detected", fails_second):
            failed = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", self.folder])
        self.assertNotEqual(0, failed.exit_code, failed.output)
        self.assertEqual(([], []), (self.boxes(), self.looks()), "half of the new faces written")
        self.detector.found = {"pier.jpg": [(CROPPED, 0.99, 7)]}
        self.index()
        self.assertTheNewPictures()

    def test_a_cropped_photo_indexed_without_faces_loses_its_old_boxes_and_the_next_index_gives_the_new(self):
        self.named_by_hand()
        self.crop()
        self.detector.refused = True
        self.index(None, "--skip-faces")
        self.assertEqual(([], []), (self.boxes(), self.looks()), "an old box kept on the new picture")
        self.detector.refused = False
        self.index()
        self.assertTheNewPictures()

    def test_two_libraries_holding_the_folder_each_replace_their_own(self):
        other = self.made("cove.db")
        self.named_by_hand()
        self.index(other)
        theirs = self.faces(other)
        self.assertEqual([LEFT, RIGHT], [box for box, *_ in theirs])
        self.crop()
        self.index()
        self.assertTheNewPictures()
        self.assertEqual(theirs, self.faces(other), "one library's index changed the other's faces")
        self.assertEqual(({"pier.jpg": (SIZE[0] // 2, SIZE[1])}, {"pier.jpg": SIZE}), (self.sizes(), self.sizes(other)),
                         "each library's size is its own index's")
        self.index(other)
        (box, face_id, name, _source, excluded), = self.faces(other)
        self.assertEqual((CROPPED, PERSON, 0), (box, name, excluded))
        self.assertNotIn(face_id, [old for _box, old, *_ in theirs])
        self.assertEqual({"pier.jpg": (SIZE[0] // 2, SIZE[1])}, self.sizes(other))


if __name__ == "__main__":
    unittest.main()
