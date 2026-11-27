"""Scenario P12: a face in the background of its picture is one answer everywhere it is asked (#2738, #2761).

A face under 2% of its picture's shorter side is in the background (tagpup.core.clustering.is_background), its picture's
size the one the photo's row stores (photos.width, height). The photo's strip, Organize's suggested people, Suggest, Name
faces from tags and cluster-faces each ask it so: before, the strip and Organize looked at the file, Suggest and
cluster-faces asked whether the face was a tenth of the photo's largest and under 2,000 px, and Name faces from tags
whether it was under 2,000 px, so a 45 x 45 face on a 2400 x 2300 photo was hidden on the photo, yet offered by Suggest
and named by the job.

A face named by hand is named whatever its size: the strip shows it, Name faces from tags leaves it be, and cluster-faces
holds its name in every round, so its person stays in the gallery the others are read against. And the last pass asks no
size: a small face on a sized photo, as like a named person as the tag needs, is named (DECISIONS 2026-11-12).

A library whose sizes are partly filled (migration 39's fill runs at the index; a photo read before it has none) holds
both: a photo whose row stores its size, and one whose row stores none. On the second the rule does not apply (DECISIONS
2026-11-12, "A FACE ON A PICTURE OF NO STORED SIZE IS NOT IN THE BACKGROUND"), whatever the file on disk says: every
place shows and names its small face alike.

Rows are made as the code that makes them makes them: the photos by the indexer's record (tests/photo_rows.py, which
reads the size of a picture on disk and stores none for a path with no file yet), the tree by tagpup.store.taxonomy, the
faces by tagpup.store.faces; the pages through Flask's test client.
"""
import math
import os
import sys
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_rows  # noqa: E402
from face_rows import FACE_MODEL  # noqa: E402
import photo_rows  # noqa: E402
import stand_in_models  # noqa: E402
import web_client  # noqa: E402
import fake_exiftool  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.files import field_values  # noqa: E402
from tagpup.services import faces_from_tags, identities  # noqa: E402
from tagpup.services import photos as photos_service  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db, faces, people, taxonomy  # noqa: E402

WREN = "Wren Halloway"
ODA = "Oda Castellane"
BRAM = "Bram Ostrander"      # a person with no face anywhere: the name left for a face by elimination
TAMSIN = "Tamsin Vorhees"    # a person named by hand on one small face only
DATE = "2020:06:01 10:00:00"
PICTURE = (2400, 2300)       # 2% of 2,300 is 46
BIG = [100, 100, 700, 700]
SMALL = [1000, 1000, 1045, 1045]   # 45 x 45: under 46, and an area of 2,025, over the old rules' 2,000
BESIDE = [900, 100, 1500, 700]
FARTHER = [1600, 100, 2200, 700]


def at(degrees):
    """A unit vector `degrees` round a circle: two are as alike as the cosine of the gap."""
    angle = math.radians(degrees)
    vector = np.zeros(8, dtype="float32")
    vector[0], vector[1] = math.cos(angle), math.sin(angle)
    return vector.tobytes()


def apart():
    """A unit vector unlike every face `at` makes (0.0 to each)."""
    vector = np.zeros(8, dtype="float32")
    vector[2] = 1.0
    return vector.tobytes()


class StandInFaces(stand_in_models.Faces):
    def ready(self):
        """The run's turn on the graphics card: nothing to wait for here."""

    def detect_and_embed_faces(self, path):
        raise AssertionError("the photo's faces are on file: nothing is detected")


class AFaceInTheBackgroundIsOneAnswer(unittest.TestCase):
    def setUp(self):
        self.files = fake_exiftool.standing_in(self)
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.path = self.home.library("library.db")
        self.library = Library(self.path)
        self.folder = os.path.join(self.home.root, "Pictures")

        def tree(conn):
            with people.tree_edit(conn):
                taxonomy.add_path(conn, "People", root_has_face=1)
                for name in (WREN, ODA, BRAM, TAMSIN):
                    taxonomy.add_path(conn, "People/" + name)

        db.write_with_connection(self.path, tree)
        # Wren and Oda named by hand on photos of their own, of the same year.
        self.face(self.photo("club/wren.jpg", (60, 40)), [10, 10, 110, 110], 0, WREN)
        self.face(self.photo("club/oda.jpg", (60, 40)), [10, 10, 110, 110], 90, ODA)

    def photo(self, name, size, keywords=(), stored=True):
        """An indexed photo of 2020 with a picture of `size` on disk: on disk when it is read (`stored`: its row stores the
        size), or put there after (its row stores none, as a photo read before migration 39's fill)."""
        photo_path = os.path.join(self.folder, *name.split("/"))
        os.makedirs(os.path.dirname(photo_path), exist_ok=True)
        self.files.hold(photo_path, EXIF__DateTimeOriginal=DATE)
        fields = {"XMP:Subject": list(keywords), "EXIF:DateTimeOriginal": DATE}

        def picture():
            Image.new("RGB", size, (70, 90, 110)).save(photo_path, "JPEG")

        if stored:
            picture()
        db.write_with_connection(self.path, lambda conn: photo_rows.add_read(conn, photo_path, fields))
        if not stored:
            picture()
        return photo_path

    def face(self, photo_path, box, degrees, name=None):
        """A face in `box`, `degrees` round the circle (or `apart()` when `degrees` is None), named by hand as `name` when
        given."""
        def add(conn):
            face_id = faces.insert(conn, photo_path, box, apart() if degrees is None else at(degrees), model=FACE_MODEL)
            if name:
                conn.execute("UPDATE faces SET name = ?, name_source = 'manual' WHERE id = ?", (name, face_id))
                face_rows.give_their_person(conn, face_id)
                people.rebuild_photos(conn, [photo_path])
            return face_id
        return db.write_with_connection(self.path, add)

    def look(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def stored_sizes(self, *face_ids):
        return [self.look("SELECT width, height FROM photos WHERE id = (SELECT photo_id FROM faces WHERE id = ?)",
                          (face_id,))[0] for face_id in face_ids]

    # ---- The places a face is asked whether it is in the background ------------------------------------------------

    def strip(self, photo_path):
        """The ids of the faces the photo's strip shows."""
        answer = self.client.get("/library/api/photo-faces", query_string={"path": photo_path}).get_json()
        return [each["id"] for each in answer["faces"]]

    def organize(self, photo_path):
        """The people Organize's Suggested People offers for the photo, selected alone."""
        answer = self.client.post("/library/api/photo-faces/suggestions", json={"paths": [photo_path]}).get_json()
        return [each["name"] for each in answer["suggestions"].get(photo_path, [])]

    def suggested(self, photo_path):
        """The people Suggest offers by a face for the photo, handed the page's record of it as a run is."""
        with field_values.opened(None) as fields:
            record = photos_service.scan_folder(self.library, os.path.dirname(photo_path), fields)
        meta = next(each for each in record.values() if os.path.basename(each["path"]) == os.path.basename(photo_path))
        index = PhotoIndex(self.path)
        index.load()
        try:
            run = stand_in_models.run_for(index, StandInFaces(), dim=4)
            found = run.suggester.suggest_for_photo(meta["path"], [1.0, 0.0, 0.0, 0.0], target_metadata=meta)
        finally:
            index.close()
        return [item["tag"].split("/")[-1] for item in found["suggested_tags"] if item.get("has_face_match")]

    def from_tags(self, face_id):
        """The person Name faces from tags would give the face."""
        plan = faces_from_tags.plan(self.library)
        return [choice.person.name for choice in plan.work.choices if choice.face_id == face_id]

    def name_of(self, face_id):
        return self.look("SELECT name FROM faces WHERE id = ?", (face_id,))[0][0]

    def cluster_faces(self):
        index = PhotoIndex(self.path)
        index.load()
        try:
            identities.resolve(index)
        finally:
            index.close()

    # ---- The scenario -------------------------------------------------------------------------------------------------

    def test_a_small_face_is_hidden_and_never_offered_on_a_sized_photo_and_shown_and_offered_on_one_of_no_size(self):
        small = {}
        for stored, name in ((True, "regatta/sized.jpg"), (False, "regatta/not_sized.jpg")):
            photo = self.photo(name, PICTURE, ["People/" + WREN, "People/" + ODA], stored=stored)
            self.face(photo, BIG, 0, WREN)
            small[stored] = (photo, self.face(photo, SMALL, 90))
        self.assertEqual([PICTURE, (None, None)], self.stored_sizes(small[True][1], small[False][1]))

        def answers(photo, face_id):
            return {"strip": face_id in self.strip(photo), "organize": ODA in self.organize(photo),
                    "suggest": ODA in self.suggested(photo), "from tags": self.from_tags(face_id) == [ODA]}

        everywhere = ("strip", "organize", "suggest", "from tags")
        self.assertEqual(dict.fromkeys(everywhere, False), answers(*small[True]),
                         "a face in the background of its stored picture was shown or offered somewhere")
        self.assertEqual(dict.fromkeys(everywhere, True), answers(*small[False]),
                         "a face on a picture of no stored size was put in the background somewhere")
        self.assertEqual([PICTURE, (None, None)], self.stored_sizes(small[True][1], small[False][1]),
                         "asking filled a size: the photo of no stored size was not asked as one")

    def test_cluster_faces_names_no_face_in_the_background_by_elimination(self):
        # Two strangers unlike each other (0.0): the one named Bram is no likeness for the last pass, which asks no size
        # and names a face from the likeness a name is offered at when the photo's tag bears it out (DECISIONS
        # 2026-11-12).
        stranger = {}
        for stored, name, degrees in ((True, "harbour/sized.jpg", 180), (False, "harbour/not_sized.jpg", 270)):
            photo = self.photo(name, PICTURE, ["People/" + WREN, "People/" + BRAM], stored=stored)
            self.face(photo, BIG, 0, WREN)
            stranger[stored] = self.face(photo, SMALL, degrees)
        self.cluster_faces()
        self.assertEqual({True: None, False: BRAM}, {stored: self.name_of(face_id) for stored, face_id in stranger.items()},
                         "the face left beside Wren was named Bram by elimination on the wrong photo")

    def test_a_person_named_by_hand_only_on_a_small_face_still_names_a_face_like_them(self):
        # Tamsin's one face is small on a sized photo, named by hand. On another photo tagged Tamsin and Wren, beside
        # Wren and a stranger (so no face is named by elimination), a face 0.41 like hers: under naming with no one
        # looking (0.52), over the offer the tag bears out (0.31), so the last pass names it, when her face is in the
        # gallery. The rounds put a hand-named face in the background unnamed: Tamsin left the gallery after the first.
        mine = self.face(self.photo("quay/tamsin.jpg", PICTURE, ["People/" + TAMSIN]), SMALL, 180, TAMSIN)
        quay = self.photo("quay/three.jpg", PICTURE, ["People/" + TAMSIN, "People/" + WREN])
        self.face(quay, BIG, 0, WREN)
        like = self.face(quay, BESIDE, 246)
        stranger = self.face(quay, FARTHER, None)
        self.assertEqual([PICTURE], self.stored_sizes(mine))
        self.cluster_faces()
        self.assertEqual({"mine": TAMSIN, "like": TAMSIN, "stranger": None},
                         {"mine": self.name_of(mine), "like": self.name_of(like), "stranger": self.name_of(stranger)},
                         "a person named by hand on a face in the background named no face like them")

    def test_the_last_pass_names_a_small_face_like_a_named_person_on_a_sized_photo(self):
        # The last pass asks no size (DECISIONS 2026-11-12, item 4): a small face very like Oda, on a sized photo tagged
        # with her and Wren, beside Wren, is named Oda -- someone genuinely small in frame.
        photo = self.photo("regatta/crowd.jpg", PICTURE, ["People/" + WREN, "People/" + ODA])
        self.face(photo, BIG, 0, WREN)
        small = self.face(photo, SMALL, 95)
        self.assertEqual([PICTURE], self.stored_sizes(small))
        self.cluster_faces()
        self.assertEqual(ODA, self.name_of(small), "the last pass left a small face very like Oda on her tagged photo")


if __name__ == "__main__":
    unittest.main()
