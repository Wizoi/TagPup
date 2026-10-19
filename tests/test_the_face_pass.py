"""Scenario P8: the new face model's pass over a folder (tagpup.services.face_pass), with the model stood in for
(tests/stand_in_models.NewFaces): what each old face keeps, what is made new, what is listed, skipped or failed, a run
stopped part-way and resumed, run twice, undone from History, a dry run, and two libraries holding one folder.

Photos are real files (Pillow), rows as the index makes them (tests/photo_rows.py), faces as it stores them
(tests/face_rows.py). Each library in a home of the test's own (tests/own_home.py)."""
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
from face_rows import FACENET, add_face, person_of  # noqa: E402
from stand_in_models import NewFaces  # noqa: E402

from tagpup.services import face_pass  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

NEW = NewFaces.key
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
        path = self.home.library(name)
        schema.ensure(path)
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
            face_id = add_face(conn, path, box, facenet(seed), prob=0.99, **columns)
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
        return [operation for (operation,) in self.query(
            "SELECT operation FROM changes WHERE status = 'applied' AND undone IS NULL ORDER BY id", db_path=db_path)]

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
            self.assertEqual([old], [face_id for face_id, _vector in store_faces.unnamed_embeddings(conn)],
                             "New Person's pool reads FaceNet's vectors alone")
            self.assertEqual([old], [row[0] for row in store_faces.identify_candidates(conn)], "nor does Identify's queue")
        finally:
            conn.close()
        self.assertEqual([], self.query("SELECT 1 FROM faces_detected"), "the pass records no detection")

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

    def test_a_photo_whose_only_detections_are_under_the_floor_writes_nothing_and_is_counted_not_as_no_face(self):
        """Nothing is held for it (a photo is done when a face holds a vector), so it is looked at again as a photo with no
        face found is: the same count of detections each run, nothing written."""
        self.photo("tiny.jpg")
        before = self.dump()
        found = self.run_pass(NewFaces({"tiny.jpg": [((5, 5, 35, 70), 0.9, 2)]}))
        self.assertEqual((1, 0, 0, 1, 0, []), (found.looked, found.new, found.none_found, found.too_small, found.done,
                                              found.changes))
        self.assertEqual((before, []), (self.dump(), self.changes()))

    def test_a_turned_photo_is_skipped_and_one_that_does_not_decode_or_fails_is_reported_and_nothing_is_written(self):
        _turned, turned_path = self.photo("turned.jpg", turned=True)
        self.face(turned_path, [10, 10, 110, 130], 1)
        broken, _ = self.photo("broken.jpg", broken=True)
        failing, failing_path = self.photo("failing.jpg")
        self.face(failing_path, [10, 10, 110, 130], 2)
        self.photo("empty.jpg")
        before = self.dump()
        model = NewFaces({"turned.jpg": [((10, 10, 110, 130), 0.9, 1)], "failing.jpg": None})
        found = self.run_pass(model)
        self.assertEqual((1, 1, 0, 0), (found.skipped, found.none_found, found.matched, found.new))
        self.assertEqual(sorted([broken, failing]), sorted(photo for photo, _why in found.failed))
        self.assertNotIn("turned.jpg", model.asked, "a turned photo is not detected: its boxes are not converted")
        self.assertEqual(before, self.dump())
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
        ((summary,),) = self.query("SELECT summary FROM changes")
        self.assertEqual({"matched": 2, "new": 2}, {key: value for key, value in json.loads(summary).items()
                                                     if key != "folder"}, "the change says what was committed")
        self.assertEqual({"face_embeddings": {"insert": 4}, "faces": {"insert": 2}, "face_crops": {"insert": 2}},
                         journal.history(self.db_path, limit=1)[0]["rows"],
                         "History counts a vector for each matched face, and each new face with its crop and vector")
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

    def test_an_undo_is_refused_once_a_new_face_is_named_and_a_matched_face_named_since_keeps_its_name(self):
        found = self.four_photos()
        (change_id,) = self.run_pass(NewFaces(found)).changes
        old, new = self.query("SELECT id FROM faces WHERE photo_id = (SELECT MIN(id) FROM photos) ORDER BY id")
        conn = db.connect(self.db_path)
        person_of(conn, "Rowan Thackeray")
        store_faces.name(conn, [old[0]], "Rowan Thackeray")
        conn.commit()
        conn.close()
        self.assertEqual([], journal.refusals(self.db_path, [change_id])[change_id])
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
                    (change_id,) = [i for (i,) in test.query("SELECT id FROM changes")]
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
                                   self.query("SELECT summary FROM changes ORDER BY id")])
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
