"""Scenario P8: the new face model's pass over a folder (tagpup.services.face_pass), with the model stood in for
(tests/stand_in_models.NewFaces): what each old face keeps, what is made new, what is listed or failed, a photo turned by
its Orientation looked at as shown, a run stopped part-way and resumed, run twice, undone from History, a dry run, and two libraries holding one folder; a photo
with no face recorded as looked at, so a second run looks at nothing, and what Suggest and an index make of it after.

Photos are real files (Pillow), rows as the index makes them (tests/photo_rows.py), faces as it stores them
(tests/face_rows.py). Each library in a home of the test's own (tests/own_home.py)."""
import io
import json
import os
import struct
import sys
import unittest
import unittest.mock

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import FACENET, add_face, on_facenet, person_of  # noqa: E402
from stand_in_models import NewFaces  # noqa: E402

from tagpup.core import orientation  # noqa: E402
from tagpup.services import face_pass  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db, faces_detected, journal, schema  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

NEW = NewFaces.key
#: The name the new model's detections are recorded under, as the index, Suggest and a bake spell it; and MTCNN's.
DETECTOR = face_service.detector_of(NewFaces(), None)
MTCNN = faces_detected.detector({"min_face_size": 20, "confidence_threshold": 0.9, "mtcnn_thresholds": [0.6, 0.7, 0.7]})
#: The journal's own record, and the counters every write moves: not what the pass is to leave as it found.
BOOKKEEPING = ("changes", "change_rows", "generations", "sqlite_sequence")


def facenet(seed):
    return struct.pack("<8f", *[((seed * 7 + i) % 13) / 13.0 for i in range(8)])


def crop(seed):
    return b"\xff\xd8\xff\xe0" + bytes((seed + i) % 256 for i in range(600)) + b"\xff\xd9"


class FacePass(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.folder = os.path.join(self.home.root, "Harbour Regatta")
        os.makedirs(self.folder)
        self.db_path = self.library("harbour.db")

    def library(self, name):
        """A library on FaceNet, as the live libraries were when the pass ran over them."""
        path = self.home.library(name)
        schema.ensure(path)
        on_facenet(path)
        return path

    def photo(self, name, db_path=None, turned=False, broken=False):
        """A photo file in the folder, and its row: (photo id, path)."""
        path = os.path.join(self.folder, name)
        if not os.path.exists(path):
            if broken:
                with open(path, "wb") as handle:
                    handle.write(b"not a picture")
            else:
                exif = Image.Exif()
                if turned:
                    exif[0x0112] = 6
                Image.new("RGB", (400, 300), (90, 120, 150)).save(path, exif=exif)
        conn = db.connect(db_path or self.db_path)
        try:
            photo_id = photo_rows.add_unread(conn, path)
            conn.commit()
        finally:
            conn.close()
        return photo_id, path

    def face(self, path, box, seed, db_path=None, **columns):
        conn = db.connect(db_path or self.db_path)
        try:
            # The old detector's face, holding FaceNet's vector, as each face did before the pass.
            face_id = add_face(conn, path, box, facenet(seed), model=FACENET, prob=0.99, **columns)
            conn.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, ?)", (face_id, crop(seed)))
            conn.commit()
        finally:
            conn.close()
        return face_id

    def query(self, sql, params=(), db_path=None):
        conn = db.connect(db.readonly_uri(db_path or self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def dump(self, db_path=None):
        """Every table but the journal's own and the counters, row for row, byte for byte."""
        tables = [name for (name,) in self.query("SELECT name FROM sqlite_master WHERE type = 'table'", db_path=db_path)
                  if name not in BOOKKEEPING]
        return {table: sorted(map(repr, self.query('SELECT * FROM "%s"' % table, db_path=db_path))) for table in tables}

    def run_pass(self, model, apply=True, db_path=None):
        (found,) = face_pass.run(db_path or self.db_path, model, self.folder, apply=apply)
        return found

    def vectors(self, face_id, db_path=None):
        return [model for (model,) in self.query("SELECT model FROM face_embeddings WHERE face_id = ? ORDER BY model",
                                                 (face_id,), db_path)]

    def changes(self, db_path=None):
        """The changes that stand, the library's stamp of its settings left out (it is the library's first, not the pass's)."""
        return [operation for (operation,) in self.query(
            "SELECT operation FROM changes WHERE status = 'applied' AND undone IS NULL ORDER BY id", db_path=db_path)
            if operation not in library_settings.STAMPS]

    # ---- What each face keeps, and what is made ---------------------------------------------------------------

    def test_a_named_face_keeps_its_person_box_and_crop_and_is_given_the_new_vector(self):
        photo, path = self.photo("start.jpg")
        named = self.face(path, [100, 50, 200, 170], 1, name="Rowan Thackeray", name_source="manual")
        before = self.query("SELECT f.name, f.tag_id, f.box, f.name_source, c.jpeg FROM faces f"
                            " JOIN face_crops c ON c.face_id = f.id WHERE f.id = ?", (named,))
        found = self.run_pass(NewFaces({"start.jpg": [((104, 52, 198, 172), 0.9, 5)]}))
        self.assertEqual((1, 0, []), (found.matched, found.new, found.unmatched))
        self.assertEqual(before, self.query("SELECT f.name, f.tag_id, f.box, f.name_source, c.jpeg FROM faces f"
                                            " JOIN face_crops c ON c.face_id = f.id WHERE f.id = ?", (named,)))
        self.assertEqual([FACENET, NEW], self.vectors(named))
        self.assertEqual([(face_service.vector_bytes(NewFaces.vector(5)),)],
                         self.query("SELECT vector FROM face_embeddings WHERE face_id = ? AND model = ?", (named, NEW)))
        self.assertEqual({photo: {named}}, store_faces.vectors_under(db.connect(db.readonly_uri(self.db_path), uri=True),
                                                                     NEW, [photo]))

    def test_two_old_faces_for_one_detection_the_closer_takes_it_and_the_other_is_listed_untouched(self):
        photo, path = self.photo("pair.jpg")
        near = self.face(path, [100, 100, 200, 200], 1, name="Rowan Thackeray", name_source="manual")
        far = self.face(path, [130, 100, 230, 200], 2, name="Maren Oakhollow", name_source="manual")
        excluded = self.face(path, [300, 10, 380, 90], 3, excluded=1, excluded_reason="manual")
        untouched = self.dump()["faces"]
        found = self.run_pass(NewFaces({"pair.jpg": [((102, 100, 202, 200), 0.95, 7), ((302, 12, 380, 92), 0.9, 8)]}))
        self.assertEqual((2, 0), (found.matched, found.new))
        self.assertEqual([(photo, far)], found.unmatched)
        self.assertEqual([FACENET, NEW], self.vectors(near))
        self.assertEqual([FACENET], self.vectors(far), "a face no detection took has nothing new")
        self.assertEqual([FACENET, NEW], self.vectors(excluded))
        self.assertEqual(untouched, self.dump()["faces"], "no face row moved: names, boxes and exclusions stand")
        self.assertEqual([(1,)], self.query("SELECT excluded FROM faces WHERE id = ?", (excluded,)))

    def test_a_named_face_beside_an_unnamed_duplicate_takes_the_detection_though_the_duplicate_overlaps_it_more(self):
        """The live shape (one in photo_index, counted 2026-10-18): a named face and an unnamed duplicate of it, at SAME_FACE
        or more, where the model finds one face. The named face takes it (owner, 2026-10-18: named faces pair first), even
        though the duplicate's box is the closer, so the person keeps a vector under the new model; the duplicate is
        listed."""
        photo, path = self.photo("twice.jpg")
        named = self.face(path, [100, 100, 200, 200], 1, name="Rowan Thackeray", name_source="manual")
        duplicate = self.face(path, [96, 100, 196, 200], 2)
        found = self.run_pass(NewFaces({"twice.jpg": [((95, 100, 195, 200), 0.95, 7)]}))
        self.assertEqual((1, 0, [(photo, duplicate)]), (found.matched, found.new, found.unmatched))
        self.assertEqual([FACENET, NEW], self.vectors(named))
        self.assertEqual([FACENET], self.vectors(duplicate))

    def test_a_new_detection_is_a_new_unnamed_face_with_its_crop_and_the_new_vector_alone_and_facenet_readers_leave_it_out(self):
        photo, path = self.photo("crowd.jpg")
        old = self.face(path, [10, 10, 110, 130], 1)
        found = self.run_pass(NewFaces({"crowd.jpg": [((12, 10, 110, 130), 0.9, 2), ((200, 60, 300, 180), 0.8, 3)]}))
        self.assertEqual((1, 1), (found.matched, found.new))
        ((new, box, prob, name, excluded),) = self.query(
            "SELECT id, box, prob, name, excluded FROM faces WHERE photo_id = ? AND id <> ?", (photo, old))
        self.assertEqual(([200, 60, 300, 180], 0.8, None, 0), (json.loads(box), round(prob, 3), name, excluded))
        self.assertEqual([NEW], self.vectors(new))
        ((jpeg,),) = self.query("SELECT jpeg FROM face_crops WHERE face_id = ?", (new,))
        self.assertTrue(jpeg.startswith(b"\xff\xd8"), "the crop is a JPEG cut from the picture")
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            self.assertEqual([old], [face_id for face_id, _vector in store_faces.unnamed_embeddings(conn, store_faces.model_key(conn))],
                             "New Person's pool reads FaceNet's vectors alone")
            self.assertEqual([old], [row[0] for row in store_faces.identify_candidates(conn)], "nor does Identify's queue")
        finally:
            conn.close()
        self.assertEqual([], self.query("SELECT 1 FROM faces_detected"), "a photo holding a face records no detection")

    def test_no_new_face_is_made_under_the_floor_a_matched_small_face_still_gets_its_vector_and_the_next_run_has_nothing_to_do(self):
        """Owner, 2026-10-18: the new detector found 31 smudges under 40 px in 40 new faces of one folder. A detection
        under NEW_FACE_FLOOR on its shorter side that no old face takes is not made, and is counted; one an old face takes
        is given its vector at any size."""
        photo, path = self.photo("small.jpg")
        old = self.face(path, [10, 10, 40, 38], 1)
        found = self.run_pass(NewFaces({"small.jpg": [((10, 10, 40, 38), 0.9, 2), ((200, 60, 260, 130), 0.8, 3),
                                                      ((300, 100, 330, 160), 0.7, 4)]}))
        self.assertEqual((1, 1, 1), (found.matched, found.new, found.too_small))
        self.assertEqual([FACENET, NEW], self.vectors(old))
        self.assertEqual([[200, 60, 260, 130]], [json.loads(box) for (box,) in self.query(
            "SELECT box FROM faces WHERE photo_id = ? AND id <> ?", (photo, old))])
        before, changes = self.dump(), self.changes()
        model = NewFaces({"small.jpg": []})
        again = self.run_pass(model)
        self.assertEqual((1, 0, 0, 0, []), (again.done, again.matched, again.new, again.too_small, again.changes))
        self.assertEqual([], model.asked)
        self.assertEqual((before, changes), (self.dump(), self.changes()))

    def looked_at(self):
        """[(photo id, detector, found)] of faces_detected."""
        return self.query("SELECT photo_id, detector, found FROM faces_detected ORDER BY photo_id")

    def test_a_photo_with_no_face_is_recorded_as_looked_at_and_a_second_run_looks_at_nothing(self):
        """The live shape (photo_index, 2026-10-25: 10,407 photos with no face and no record, after a whole pass): the
        model found no face, so the photo is recorded as looked at under the model's name, and is done. A dry run
        records nothing."""
        photo, path = self.photo("empty.jpg")
        before = self.dump()
        rehearsed = self.run_pass(NewFaces({}), apply=False)
        self.assertEqual((1, 1), (rehearsed.looked, rehearsed.none_found))
        self.assertEqual(before, self.dump(), "a dry run records nothing")
        found = self.run_pass(NewFaces({}))
        self.assertEqual((1, 1, 0, [], []), (found.looked, found.none_found, found.done, found.failed, found.changes))
        self.assertEqual([(photo, DETECTOR, 0)], self.looked_at())
        stat = os.stat(path)
        self.assertEqual([(stat.st_mtime, stat.st_size)], self.query("SELECT mtime, size FROM faces_detected"))
        self.assertEqual([], self.changes(), "not journaled: no change in History for it")
        recorded = self.dump()
        model = NewFaces({})
        again = self.run_pass(model)
        self.assertEqual((0, 1, 0), (again.looked, again.done, again.none_found))
        self.assertEqual([], model.asked, "a second run looks at nothing")
        self.assertEqual(recorded, self.dump())

    def test_suggest_reads_the_record_under_the_model_that_made_it_alone(self):
        """Suggest asks "detected before" under its own face model's name (face_service.detector_of): one on the new
        model detects this photo no more, one on MTCNN is not told the photo was looked at. The library's MTCNN record
        of no face (one row a photo) gives way to the new model's."""
        _photo, path = self.photo("empty.jpg")
        mtcnn_only, mtcnn_path = self.photo("mtcnn.jpg")
        conn = db.connect(self.db_path)
        faces_detected.record(conn, mtcnn_path, MTCNN, None, 0)
        conn.commit()
        conn.close()
        self.run_pass(NewFaces({}))
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            self.assertEqual((True, False), (faces_detected.detected(conn, path, DETECTOR),
                                             faces_detected.detected(conn, path, MTCNN)))
            self.assertTrue(faces_detected.detected(conn, mtcnn_path, DETECTOR))
        finally:
            conn.close()
        self.assertIn((mtcnn_only, DETECTOR, 0), self.looked_at())

    def test_a_photo_whose_only_detections_are_under_the_floor_is_recorded_and_a_second_run_looks_at_nothing(self):
        """No face is made of a detection under the floor, so the photo holds none, as one with no face found: it is
        recorded as looked at, with the count after the floor (0, as the index and Suggest record it), and counted as
        both."""
        photo, _path = self.photo("tiny.jpg")
        found = self.run_pass(NewFaces({"tiny.jpg": [((5, 5, 35, 70), 0.9, 2)]}))
        self.assertEqual((1, 0, 1, 1, 0, []), (found.looked, found.new, found.none_found, found.too_small, found.done,
                                              found.changes))
        self.assertEqual([(photo, DETECTOR, 0)], self.looked_at())
        self.assertEqual([], self.query("SELECT 1 FROM faces"))
        model = NewFaces({"tiny.jpg": [((5, 5, 35, 70), 0.9, 2)]})
        again = self.run_pass(model)
        self.assertEqual((0, 1, 0), (again.looked, again.done, again.too_small))
        self.assertEqual([], model.asked)

    def test_a_photo_given_a_face_meanwhile_is_not_recorded_and_left_for_the_next_run(self):
        """The server (Name Faces, an index) gives the photo a face while the model looks: what the model found is no
        longer the photo's whole answer, so nothing is recorded."""
        photo, path = self.photo("busy.jpg")
        test = self

        class Meanwhile(NewFaces):
            def detect_and_embed(self, picture):
                test.face(path, [200, 10, 300, 130], 2)
                return super().detect_and_embed(picture)
        found = self.run_pass(Meanwhile({}))
        self.assertEqual(([(photo, face_pass.CHANGED)], 0), (found.failed, found.none_found))
        self.assertEqual([], self.looked_at())

    def test_a_photo_recorded_then_given_a_face_is_done_by_it_and_looked_at_again_once_it_is_deleted(self):
        """An index forced again (record_batch, overwrite) under the new model finds a face: the pass's record gives way
        to the index's and the photo is done by its face's vector. That face deleted, its record goes with it
        (faces_detected.forget_faceless), and the next run looks at the photo again."""
        photo, path = self.photo("later.jpg")
        self.run_pass(NewFaces({}))
        conn = db.connect(self.db_path)
        try:
            face_service.record_batch(conn, {path: [{"box": [40, 40, 140, 160], "prob": 0.9,
                                                     "embedding": NewFaces.vector(3)}]},
                                      model=NEW, overwrite=True, detector=DETECTOR)
        finally:
            conn.close()
        self.assertEqual([(photo, DETECTOR, 1)], self.looked_at())
        model = NewFaces({})
        self.assertEqual((1, 0), (self.run_pass(model).done, len(model.asked)))
        conn = db.connect(self.db_path)
        store_faces.delete(conn, [face_id for (face_id,) in conn.execute("SELECT id FROM faces")])
        conn.commit()
        conn.close()
        self.assertEqual([], self.looked_at())
        model = NewFaces({})
        self.assertEqual(1, self.run_pass(model).none_found)
        self.assertEqual(["later.jpg"], model.asked)

    def test_a_turned_photo_is_looked_at_as_shown_its_boxes_kept_stored_its_crop_upright(self):
        """Photos stored sideways (Orientation 6: 400 x 300 stored, 300 x 400 shown), as a phone keeps them. The model
        finds their faces on the picture as shown; an old face (a box in the stored pixels) takes the detection of the
        same face (clustering.pairs, on stored boxes: no second face made beside it), and a face no old face takes is
        made new, its box in the stored pixels and its crop cut as a person sees the face. A second run looks at neither
        (DECISIONS 2026-10-27; #1975: the turned photos were skipped, and an index found no face in them)."""
        _held, held_path = self.photo("turned.jpg", turned=True)
        old = self.face(held_path, [100, 50, 220, 110], 1, name="Rowan Thackeray", name_source="manual")
        fresh, fresh_path = self.photo("turned-new.jpg", turned=True)
        new_box = [200, 150, 320, 210]   # 120 wide and 60 high as stored: 60 wide and 120 high as shown

        def shown(box):
            return orientation.to_upright(box, 400, 300, 6)
        found = self.run_pass(NewFaces({"turned.jpg": [(shown([104, 52, 218, 112]), 0.9, 1)],
                                        "turned-new.jpg": [(shown(new_box), 0.9, 2)]}))
        self.assertEqual((2, 1, 1, [], []), (found.looked, found.matched, found.new, found.unmatched, found.failed))
        self.assertEqual([FACENET, NEW], self.vectors(old))
        self.assertEqual([("[100, 50, 220, 110]", "Rowan Thackeray")],
                         self.query("SELECT f.box, f.name FROM faces f JOIN photos p ON p.id = f.photo_id"
                                    " WHERE p.path = ?", (held_path,)))
        ((box, jpeg),) = self.query("SELECT f.box, c.jpeg FROM faces f JOIN face_crops c ON c.face_id = f.id"
                                    " WHERE f.photo_id = ?", (fresh,))
        self.assertEqual(new_box, json.loads(box))
        self.assertEqual((60, 120), Image.open(io.BytesIO(jpeg)).size, "the face as a person sees it")
        again = NewFaces({"turned.jpg": [(shown([104, 52, 218, 112]), 0.9, 1)],
                          "turned-new.jpg": [(shown(new_box), 0.9, 2)]})
        self.assertEqual((2, []), (self.run_pass(again).done, again.asked))

    def test_a_photo_that_does_not_decode_or_fails_is_reported_and_nothing_is_written(self):
        broken, _ = self.photo("broken.jpg", broken=True)
        failing, failing_path = self.photo("failing.jpg")
        self.face(failing_path, [10, 10, 110, 130], 2)
        empty, _ = self.photo("empty.jpg")
        broken_blank, _ = self.photo("broken-blank.jpg", broken=True)
        failing_blank, _ = self.photo("failing-blank.jpg")
        before = self.dump()
        model = NewFaces({"failing.jpg": None, "failing-blank.jpg": None})
        found = self.run_pass(model)
        self.assertEqual((1, 0, 0), (found.none_found, found.matched, found.new))
        self.assertEqual(sorted([broken, failing, broken_blank, failing_blank]),
                         sorted(photo for photo, _why in found.failed))
        self.assertEqual([(empty, DETECTOR, 0)], self.looked_at(),
                         "only the photo the model looked at and found no face in: never a failed one")
        after = self.dump()
        self.assertEqual({table: rows for table, rows in before.items() if table != "faces_detected"},
                         {table: rows for table, rows in after.items() if table != "faces_detected"})
        self.assertEqual([], self.changes(), "a folder that wrote nothing has no change")

    def test_a_photo_whose_faces_change_while_the_model_looks_is_left_for_the_next_run(self):
        photo, path = self.photo("busy.jpg")
        self.face(path, [10, 10, 110, 130], 1)
        test = self

        class Meanwhile(NewFaces):
            def detect_and_embed(self, picture):
                test.face(path, [200, 10, 300, 130], 2)   # the server adds a face beside the run
                return super().detect_and_embed(picture)
        found = self.run_pass(Meanwhile({"busy.jpg": [((10, 10, 110, 130), 0.9, 1)]}))
        self.assertEqual([photo], [each for each, _why in found.failed])
        self.assertEqual([], self.query("SELECT 1 FROM face_embeddings WHERE model = ?", (NEW,)))

    def test_a_photo_removed_or_moved_while_the_model_looks_is_left_alone(self):
        """Sync removes one photo's row and moves another's (the file renamed) while the model looks at them. Neither has
        an old face, so their faces read the same; the pass writes nothing for either -- no row made again at the path it
        listed, which undo would leave behind -- and looks at them again next run."""
        gone, gone_path = self.photo("gone.jpg")
        moved, moved_path = self.photo("moved.jpg")
        renamed = os.path.join(self.folder, "renamed.jpg")
        test = self

        class Meanwhile(NewFaces):
            def detect_and_embed(self, picture):
                name = os.path.basename(picture.path)
                if name == "gone.jpg":
                    store_photos.forget_photo(test.db_path, gone_path)
                else:
                    conn = db.connect(test.db_path)
                    try:
                        store_photos.move_rows_in(conn, {moved_path: renamed})
                        conn.commit()
                    finally:
                        conn.close()
                return super().detect_and_embed(picture)
        found = self.run_pass(Meanwhile({"gone.jpg": [((10, 10, 110, 130), 0.9, 1)],
                                         "moved.jpg": [((10, 10, 110, 130), 0.9, 2)]}))
        self.assertEqual(sorted([gone, moved]), sorted(each for each, _why in found.failed))
        self.assertEqual((0, 0), (found.matched, found.new))
        self.assertEqual([(moved,)], self.query("SELECT id FROM photos"), "no row is made again at a listed path")
        self.assertEqual([], self.query("SELECT 1 FROM faces"))
        self.assertEqual([], self.changes(), "nothing written, nothing to undo")

    # ---- Stopped, resumed, run again, undone ---------------------------------------------------------------------

    def four_photos(self, db_path=None):
        """Four photos of one face each, and what the model finds in them."""
        found = {}
        for n in range(1, 5):
            _photo, path = self.photo("lap%d.jpg" % n, db_path)
            self.face(path, [20 * n, 10, 20 * n + 100, 130], n, db_path)
            found["lap%d.jpg" % n] = [((20 * n, 10, 20 * n + 100, 130), 0.9, n), ((250, 50, 350, 170), 0.7, 10 + n)]
        return found

    def test_stopped_after_two_photos_a_resumed_run_does_the_other_two_as_a_second_change(self):
        found = self.four_photos()
        with self.assertRaises(NewFaces.Killed):
            self.run_pass(NewFaces(found, kill_at="lap3.jpg"))
        first = self.dump()
        operation = face_pass.OPERATION % NEW
        self.assertEqual([operation], self.changes())
        ((summary,),) = self.query("SELECT summary FROM changes WHERE operation = ?", (operation,))
        self.assertEqual({"matched": 2, "new": 2}, {key: value for key, value in json.loads(summary).items()
                                                     if key != "folder"}, "the change says what was committed")
        self.assertEqual({"face_embeddings": {"insert": 4}, "faces": {"insert": 2}},
                         journal.history(self.db_path, limit=1)[0]["rows"],
                         "History counts a vector for each matched face, and each new face with its vector (its crop, a"
                         " cache, is not recorded: journal.CACHES)")
        model = NewFaces(found)
        resumed = self.run_pass(model)
        self.assertEqual(["lap3.jpg", "lap4.jpg"], model.asked)
        self.assertEqual((2, 2, 2), (resumed.done, resumed.matched, resumed.new))
        self.assertEqual(1, len(resumed.held), "the earlier change of the folder is named")
        self.assertEqual([operation, operation], self.changes())
        after = self.dump()
        for table in ("faces", "face_crops", "face_embeddings"):
            self.assertTrue(set(first[table]) <= set(after[table]), "the first two photos' rows stand as they were")

    def test_run_twice_the_second_writes_nothing(self):
        found = self.four_photos()
        self.run_pass(NewFaces(found))
        before, changes = self.dump(), self.changes()
        model = NewFaces(found)
        again = self.run_pass(model)
        self.assertEqual((4, 0, 0, []), (again.done, again.matched, again.new, again.changes))
        self.assertEqual([], model.asked)
        self.assertEqual((before, changes), (self.dump(), self.changes()))

    def test_undoing_the_folder_takes_exactly_what_the_pass_added_and_it_may_be_run_again(self):
        # A new face's own crop and vector, inserted by the same change, do not refuse its undo (#1675).
        found = self.four_photos()
        # A photo MTCNN found no face in, as the index records it, in which the new model finds one.
        nobody, _path = self.photo("nobody.jpg")
        found["nobody.jpg"] = [((40, 40, 140, 160), 0.8, 30)]
        conn = db.connect(self.db_path)
        conn.execute("INSERT INTO faces_detected (photo_id, detector, mtime, size, found, at)"
                     " VALUES (?, '{\"min_face_size\": 40}', 0, 0, 0, '2026-10-01 10:00:00')", (nobody,))
        conn.commit()
        conn.close()
        before = self.dump()
        written = self.run_pass(NewFaces(found))
        (change_id,) = written.changes
        rehearsal = journal.rehearse_undo(self.db_path, change_id)
        self.assertEqual((None, True), (rehearsal.refused, rehearsal.exact))
        journal.undo(self.db_path, change_id)
        self.assertEqual(before, self.dump(), "every table as before, the MTCNN record of no face included")
        self.assertEqual([("undone",)], self.query("SELECT status FROM changes WHERE id = ?", (change_id,)))
        self.assertEqual(4, self.run_pass(NewFaces(found)).matched, "undone, the folder is not done")
        self.assertEqual(1, len(self.changes()))

    def test_an_undo_of_the_folder_leaves_a_photo_recorded_as_looked_at_and_the_next_run_does_not_look_again(self):
        """The record is a fact of the photo, not a row of the change: the undo takes the faces and vectors and leaves it,
        and its photo is done when the folder is run again."""
        found = self.four_photos()
        nobody, _path = self.photo("nobody.jpg")
        (change_id,) = self.run_pass(NewFaces(found)).changes
        self.assertEqual([(nobody, DETECTOR, 0)], self.looked_at())
        journal.undo(self.db_path, change_id)
        self.assertEqual([(nobody, DETECTOR, 0)], self.looked_at())
        model = NewFaces(found)
        again = self.run_pass(model)
        self.assertEqual((4, 1), (again.matched, again.done))
        self.assertNotIn("nobody.jpg", model.asked)

    def test_an_undo_is_refused_once_a_new_face_is_named_and_a_matched_face_named_since_keeps_its_name(self):
        found = self.four_photos()
        (change_id,) = self.run_pass(NewFaces(found)).changes
        old, new = self.query("SELECT id FROM faces WHERE photo_id = (SELECT MIN(id) FROM photos) ORDER BY id")
        conn = db.connect(self.db_path)
        person_of(conn, "Rowan Thackeray")
        store_faces.name(conn, [old[0]], "Rowan Thackeray")
        conn.commit()
        conn.close()
        self.assertIsNone(journal_service.refusals(Library(self.db_path), [{"id": change_id}])[change_id])
        conn = db.connect(self.db_path)
        store_faces.name(conn, [new[0]], "Rowan Thackeray")
        conn.commit()
        conn.close()
        before = self.dump()
        with self.assertRaises(journal.Refusal) as refused:
            journal.undo(self.db_path, change_id)
        self.assertIn("faces %d is not what change %d left" % (new[0], change_id), str(refused.exception))
        self.assertEqual(before, self.dump(), "a refusal writes nothing")

    def test_a_folder_undone_while_the_run_goes_on_is_not_added_to_the_next_photo_opens_a_new_change(self):
        found = self.four_photos()
        test = self

        class UndoneMeanwhile(NewFaces):
            def detect_and_embed(self, picture):
                if os.path.basename(picture.path) == "lap3.jpg":
                    (change_id,) = [i for (i,) in test.query("SELECT id FROM changes WHERE operation = ?",
                                                             (face_pass.OPERATION % NEW,))]
                    journal.undo(test.db_path, change_id)
                return super().detect_and_embed(picture)
        written = self.run_pass(UndoneMeanwhile(found))
        self.assertEqual(2, len(written.changes))
        self.assertEqual(1, len(self.changes()), "the undone change stays undone; the new one holds photos 3 and 4")
        ((summary,),) = self.query("SELECT summary FROM changes WHERE id = ?", (written.changes[1],))
        self.assertEqual(2, json.loads(summary)["matched"])

    def test_a_folders_change_is_named_for_the_model_alone_and_each_folder_holds_its_own(self):
        """History and the MCP `history` show a change's operation without reveal: it names the model, never the folder (a
        path, and paths can name people). The folder is in the summary, as the library holds it (behind reveal), and is what
        a run reads to name the changes a folder already holds -- a folder beside it holds its own."""
        found = self.four_photos()
        prizes = os.path.join(self.folder, "Prizes")
        os.makedirs(prizes)
        _photo, path = self.photo(os.path.join("Prizes", "cup.jpg"))
        self.face(path, [10, 10, 110, 130], 9)
        found["cup.jpg"] = [((10, 10, 110, 130), 0.9, 9)]
        written = face_pass.run(self.db_path, NewFaces(found), self.folder, apply=True)
        self.assertEqual(["face pass (%s)" % NEW] * 2, self.changes())
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            folders = [store_roots.to_row(conn, self.folder), store_roots.to_row(conn, prizes)]
        finally:
            conn.close()
        self.assertEqual(folders, [json.loads(summary)["folder"] for (summary,) in
                                   self.query("SELECT summary FROM changes WHERE operation = ? ORDER BY id",
                                              (face_pass.OPERATION % NEW,))])
        read = []
        kept_named = journal.kept_named
        with unittest.mock.patch.object(journal, "kept_named", lambda *args: read.append(args) or kept_named(*args)):
            again = face_pass.run(self.db_path, NewFaces(found), self.folder, apply=False)
        self.assertEqual([each.changes for each in written], [each.held for each in again])
        self.assertEqual(1, len(read), "the changes a run's folders hold are read once a run, not once a folder")

    # ---- A dry run; two libraries -------------------------------------------------------------------------------

    def test_a_dry_run_counts_the_same_and_writes_nothing(self):
        found = self.four_photos()
        before = {name: self.query('SELECT * FROM "%s"' % name) for (name,) in
                  self.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
        rehearsed = self.run_pass(NewFaces(found), apply=False)
        self.assertEqual(before, {name: self.query('SELECT * FROM "%s"' % name) for name in before},
                         "not a row of any table, the journal's included")
        written = self.run_pass(NewFaces(found))
        self.assertEqual((4, 4, [], []), (rehearsed.matched, rehearsed.new, rehearsed.unmatched, rehearsed.changes))
        self.assertEqual((rehearsed.matched, rehearsed.new), (written.matched, written.new))
        self.assertEqual(written.changes, self.run_pass(NewFaces(found), apply=False).held,
                         "a dry run names the change the folder already holds")

    def test_two_libraries_holding_one_folder_each_get_their_own_vectors(self):
        other = self.library("parkrun.db")
        found = self.four_photos()
        self.four_photos(other)
        self.run_pass(NewFaces(found))
        self.assertEqual([(8,)], self.query("SELECT COUNT(*) FROM face_embeddings WHERE model = ?", (NEW,)), "4 matched, 4 new")
        self.assertEqual([(0,)], self.query("SELECT COUNT(*) FROM face_embeddings WHERE model = ?", (NEW,), other))
        model = NewFaces(found)
        self.assertEqual(4, self.run_pass(model, db_path=other).matched, "the other library's photos are not done")
        self.assertEqual(4, len(model.asked))


if __name__ == "__main__":
    unittest.main()
