"""Scenario P11: the same face gets the same nearest person in Suggest, Identify, the strip, the boxes and Name faces from
tags (#1737), on a person who changed over the years.

Wren was photographed as a child (1990 to 1994) and as an adult (2018 to 2022); Oda as an adult (2018 to 2022). A face
taken in 2020 is very like Wren's child face (0.996), like Oda's (0.940) and not like adult Wren (0.087): compared with
each person's faces of the years around its own, it is Oda, wherever it is asked -- Suggest's people, Identify's grid
for Oda, the strip on the open photo, the list of the boxes and the plan of Name faces from tags. Before cut 2 of phase
4a the strip and the boxes compared it with every year and said Wren while Suggest said Oda.

A photo dated only by its folder name has no Date Taken, so no year to choose by: every matcher reads dates.era_year of
Date Taken (B-2 of the branch, decided), and Suggest and Identify both compare its face with every year (Wren).

Rows are made as the code that makes them makes them: the photos by the indexer's record (tests/photo_rows.py), the
tree by tagpup.store.taxonomy, the faces by tagpup.store.faces; the pages through Flask's test client.
"""
import datetime
import math
import os
import sys
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_rows  # noqa: E402
from face_rows import FACENET  # noqa: E402
import photo_rows  # noqa: E402
import stand_in_models  # noqa: E402
import web_client  # noqa: E402
import fake_exiftool  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import faces_from_tags  # noqa: E402
from tagpup.services import identify  # noqa: E402
from tagpup.services import photos as photos_service  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db, faces, people, taxonomy  # noqa: E402
from tagpup.web import app as web  # noqa: E402

WREN = "Wren Halloway"
ODA = "Oda Castellane"


def at(degrees):
    """A unit vector `degrees` round a circle: two are as alike as the cosine of the gap."""
    angle = math.radians(degrees)
    vector = np.zeros(8, dtype="float32")
    vector[0], vector[1] = math.cos(angle), math.sin(angle)
    return vector.tobytes()


class StandInFaces(stand_in_models.Faces):
    def ready(self):
        """The run's turn on the graphics card: nothing to wait for here."""

    def detect_and_embed_faces(self, path, *, max_side):
        raise AssertionError("the photo's faces are on file: nothing is detected")


class AFaceGetsOneNearestPersonEverywhere(unittest.TestCase):
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
                for tag in ("People/" + WREN, "People/" + ODA):
                    taxonomy.add_path(conn, tag)

        db.write_with_connection(self.path, tree)
        for year in range(1990, 1995):   # Wren as a child
            self.face(self.photo("school/wren_%d.jpg" % year, year), 0, WREN)
        for year in range(2018, 2023):   # Wren and Oda as adults, each on photos of their own
            self.face(self.photo("club/wren_%d.jpg" % year, year), 90, WREN)
            self.face(self.photo("club/oda_%d.jpg" % year, year), 25, ODA)

    def photo(self, name, year=None, keywords=()):
        """An indexed photo, a picture on disk, taken in `year` (its Date Taken) or undated."""
        photo_path = os.path.join(self.folder, *name.split("/"))
        os.makedirs(os.path.dirname(photo_path), exist_ok=True)
        Image.new("RGB", (60, 40), (70, 90, 110)).save(photo_path, "JPEG")
        fields = {"XMP:Subject": list(keywords)}
        if year is not None:
            fields["EXIF:DateTimeOriginal"] = "%d:06:01 10:00:00" % year
            self.files.hold(photo_path, EXIF__DateTimeOriginal=fields["EXIF:DateTimeOriginal"])
        db.write_with_connection(self.path, lambda conn: photo_rows.add_read(conn, photo_path, fields))
        return photo_path

    def face(self, photo_path, degrees, name=None):
        """A face `degrees` round the circle, named by hand as `name` when given."""
        def add(conn):
            face_id = faces.insert(conn, photo_path, [10, 10, 110, 110], at(degrees), model=FACENET)
            if name:
                conn.execute("UPDATE faces SET name = ?, name_source = 'manual' WHERE id = ?", (name, face_id))
                face_rows.give_their_person(conn, face_id)
                people.rebuild_photos(conn, [photo_path])
            return face_id
        return db.write_with_connection(self.path, add)

    def node(self, name):
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            return conn.execute("SELECT id FROM tag_taxonomy WHERE tag = ?", ("People/" + name,)).fetchone()[0]
        finally:
            conn.close()

    # ---- The five places a face is asked who it looks like ----------------------------------------------------------

    def suggested(self, photo_path):
        """The person Suggest offers for the photo's face, handed the page's record of it as a run is."""
        record = photos_service.scan_folder(self.library, os.path.dirname(photo_path), None)
        meta = next(each for each in record.values() if os.path.basename(each["path"]) == os.path.basename(photo_path))
        index = PhotoIndex(self.path)
        index.load()
        try:
            run = stand_in_models.run_for(index, StandInFaces(), dim=4)
            found = run.suggester.suggest_for_photo(meta["path"], [1.0, 0.0, 0.0, 0.0], target_metadata=meta)
        finally:
            index.close()
        offered = [item["tag"] for item in found["suggested_tags"] if item.get("has_face_match")]
        return [tag.split("/")[-1] for tag in offered]

    def card(self, face_id, person):
        """The face's card in Identify's grid of `person`."""
        tuner = web.create_app("tuner", startup=self.library)
        tuner.testing = True
        answer = tuner.test_client().get("/api/unmatched-faces/person-matches",
                                          query_string={"person_id": self.node(person)}).get_json()
        return next(card for card in answer["faces"] if card["id"] == face_id)

    def identified(self, face_id, person):
        """The name Identify's grid of `person` suggests for the face's card."""
        return self.card(face_id, person)["suggested_name"]

    def strip(self, photo_path, face_id):
        answer = self.client.get("/library/api/photo-faces", query_string={"path": photo_path}).get_json()
        return next(each["suggestion"] for each in answer["faces"] if each["id"] == face_id)

    def boxes(self, face_id):
        return self.client.get("/library/api/face-matches", query_string={"id": face_id}).get_json()[0]["name"]

    def from_tags(self, face_id):
        plan = faces_from_tags.plan(self.library)
        return [choice.person.name for choice in plan.work.choices if choice.face_id == face_id]

    # ---- The scenario ---------------------------------------------------------------------------------------------

    def test_a_face_of_2020_is_the_person_of_its_years_in_every_place_it_is_asked(self):
        photo = self.photo("regatta/start_line.jpg", 2020, ["People/" + WREN, "People/" + ODA])
        face = self.face(photo, 5)
        answers = {"suggest": self.suggested(photo)[:1], "identify": [self.identified(face, ODA)],
                   "strip": [self.strip(photo, face)], "boxes": [self.boxes(face)], "from tags": self.from_tags(face)}
        self.assertEqual({where: [ODA] for where in answers}, answers,
                         "a face like Wren's child face was not the person of its own years everywhere")
        # Identify's grid of Wren ranks and bands the face by how like Wren of 2020 it is, the number the boxes give.
        listed = self.client.get("/library/api/face-matches", query_string={"id": face}).get_json()
        card = self.card(face, WREN)
        self.assertAlmostEqual(next(each["similarity"] for each in listed if each["name"] == WREN),
                               card["person_similarity"], places=3)
        self.assertIsNone(card["band"], "a face 0.09 like Wren of its years was shown in a band of Wren's")

    def test_the_faces_past_the_cap_are_chosen_by_the_likeness_the_grid_shows(self):
        # Two unnamed faces of 2020 on photos naming Wren, and room for one: the one like Wren of her years (0.87) is shown,
        # not the one like only her child face (1.00 to it, 0.03 to Wren of 2020), which a raw maximum over every year chose.
        alike = self.face(self.photo("harbour/pier.jpg", 2020, ["People/" + WREN]), 60)
        child_like = self.face(self.photo("harbour/jetty.jpg", 2020, ["People/" + WREN]), 2)
        with mock.patch.object(identify, "UNCLUSTERED_LIMIT", 1):
            tuner = web.create_app("tuner", startup=self.library)
            tuner.testing = True
            answer = tuner.test_client().get("/api/unmatched-faces/person-matches",
                                              query_string={"person_id": self.node(WREN)}).get_json()
        shown = [card["id"] for card in answer["faces"]]
        self.assertEqual(2, answer["unclustered_total"])
        self.assertEqual([alike], shown, "the face past the cap was chosen by a rule the grid does not show")
        self.assertNotIn(child_like, shown)

    def test_a_named_face_whose_date_taken_is_shifted_is_compared_by_its_new_year(self):
        # The boxes of a face of 2020 say Oda; then Wren's photo of 1994, her child face, is found to be of 2020 and shifted
        # by Time Shift: the same boxes, asked again in the same running app, compare the face with that face too (Wren).
        photo = self.photo("regatta/start_line.jpg", 2020, ["People/" + WREN, "People/" + ODA])
        face = self.face(photo, 5)
        self.assertEqual(ODA, self.boxes(face))
        minutes = int((datetime.datetime(2020, 6, 1) - datetime.datetime(1994, 6, 1)).total_seconds() // 60)
        shifted = photos_service.shift_date_taken(self.library, [os.path.join(self.folder, "school", "wren_1994.jpg")],
                                                  minutes, "exiftool")
        self.assertEqual(1, shifted.changed)
        self.assertEqual(WREN, self.boxes(face), "the named faces kept the year their photo had before Time Shift")

    def test_a_photo_dated_only_by_its_folder_is_compared_with_every_year_by_suggest_and_identify(self):
        photo = self.photo("2020 Regatta/start_line.jpg", None, ["People/" + WREN, "People/" + ODA])
        face = self.face(photo, 5)
        answers = {"suggest": self.suggested(photo)[:1], "identify": [self.identified(face, ODA)],
                   "strip": [self.strip(photo, face)], "boxes": [self.boxes(face)]}
        self.assertEqual({where: [WREN] for where in answers}, answers,
                         "an undated face was compared with some years only somewhere")


if __name__ == "__main__":
    unittest.main()
