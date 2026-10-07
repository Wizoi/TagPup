"""docs/findings.md, #791: faces on the open photo, in TagPup's Organize -- what the server gives and takes.

The page draws a box over each face from `/api/photo-faces` (which now says the pixel size the boxes are
in and whether the photo is stored turned), suggests people from `/api/face-matches` and names, takes a
name off or rules out a face through TagTuner's own views of one face (`/api/face/match`, `/api/face/unmatch`,
`/api/faces/exclude`), served by TagPup too. The photo's tag is written by the page's own save of its
keywords; these tests stand in for that with the index record a save leaves (photos.record_tags), and check
that the tag and the faces agree in both apps.

Rows are made as the code that makes them makes them: the photo by the indexer's record (tests/photo_rows.py),
the tree by tagpup.store.taxonomy, the faces by tagpup.store.faces.
"""
import json
import math
import os
import sys
import unittest
import unittest.mock

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.store import db, faces, people, photos, taxonomy  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web import tuner_routes  # noqa: E402

WREN = "Wren Halloway"
ODA = "Oda Castellane"


def at(degrees):
    angle = math.radians(degrees)
    vector = np.zeros(8, dtype="float32")
    vector[0], vector[1] = math.cos(angle), math.sin(angle)
    return vector.tobytes()


def jpeg(path, size=(60, 40), orientation=None):
    """A picture on disk, declaring an EXIF Orientation when asked."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image = Image.new("RGB", size, (70, 90, 110))
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        image.save(path, "JPEG", exif=exif)
    else:
        image.save(path, "JPEG")


class Case(unittest.TestCase):
    def setUp(self):
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

    def photo(self, name, keywords=(), **picture):
        photo_path = os.path.join(self.folder, name)
        jpeg(photo_path, **picture)
        db.write_with_connection(self.path, lambda conn: photo_rows.add_read(
            conn, photo_path, {"XMP:Subject": list(keywords)}))
        return photo_path

    def face(self, photo_path, box=(10, 10, 110, 110), degrees=0, **columns):
        def add(conn):
            face_id = faces.insert(conn, photo_path, list(box), at(degrees))
            for column, value in columns.items():
                conn.execute("UPDATE faces SET %s = ? WHERE id = ?" % column, (value, face_id))
            people.rebuild_photos(conn, [photo_path])
            return face_id
        return db.write_with_connection(self.path, add)

    def look(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def post(self, route, body):
        return self.client.post("/library/api/" + route, json=body)

    def row(self, face_id):
        return self.look("SELECT name, name_source, excluded, tag_id FROM faces WHERE id = ?", (face_id,))[0]

    def tuner(self):
        app = web.create_app("tuner", startup=self.library)
        app.testing = True
        return app.test_client()


class WhatTheBoxesAreDrawnOn(Case):
    def test_the_answer_says_the_pixels_the_boxes_are_in(self):
        photo = self.photo("regatta_001.jpg", size=(4000, 3000))
        self.face(photo, box=(1000, 500, 1400, 900))
        answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        self.assertEqual([4000, 3000], answer["size"])
        self.assertFalse(answer["turned"])
        self.assertEqual([1000, 500, 1400, 900], answer["faces"][0]["box"])

    def test_a_photo_stored_turned_says_so_for_the_page_to_say_its_boxes_may_be_off(self):
        # The known limit: boxes are in the stored pixels and the picture is shown turned, so a box
        # does not sit on its face until the tabled orientation redesign (docs/ARCHITECTURE.md).
        for orientation in (2, 3, 6, 8):
            with self.subTest(orientation):
                photo = self.photo("turned_%d.jpg" % orientation, orientation=orientation, size=(60, 40))
                self.face(photo)
                answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
                self.assertTrue(answer["turned"])
                self.assertEqual([60, 40], answer["size"], "the size is the stored pixels', not the turned picture's")

    def test_an_upright_photo_is_not_turned(self):
        photo = self.photo("upright.jpg", orientation=1)
        self.face(photo)
        self.assertFalse(self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()["turned"])

    def test_a_file_that_cannot_be_read_has_no_size_and_so_no_box(self):
        photo = self.photo("gone_001.jpg")
        self.face(photo)
        os.remove(photo)
        answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        self.assertIsNone(answer["size"])
        self.assertEqual(1, len(answer["faces"]), "the faces are still there, to be named")

    def test_a_photo_with_no_faces_has_none_to_box(self):
        photo = self.photo("empty_001.jpg")
        answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        self.assertEqual([], answer["faces"])


class TheFileIsLookedAtOnceAndNotForeverOnAShare(Case):
    """#836: the size and the orientation come from one open of the file, and a share that is away gives
    no size and no box, never a stalled request."""

    def test_one_open_for_the_size_and_the_orientation(self):
        from tagpup.files import images
        photo = self.photo("regatta_020.jpg")
        self.face(photo)
        real = images.shown_shape
        opened = []
        with unittest.mock.patch.object(images, "shown_shape", lambda path: opened.append(path) or real(path)), \
                unittest.mock.patch.object(images, "shown_size", side_effect=AssertionError("a second open")):
            answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        self.assertEqual([photo], opened)
        self.assertEqual([60, 40], answer["size"])

    def test_a_share_that_is_away_gives_no_size_and_does_not_stall(self):
        from tagpup.services import photos as photo_service
        photo = self.photo("regatta_021.jpg")
        self.face(photo)
        with unittest.mock.patch.object(photo_service.shares, "bounded", return_value=("away", None)) as asked:
            answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
            details = self.tuner().get("/library/api/photo-details", query_string={"path": photo}).get_json()
        self.assertIsNone(answer["size"])
        self.assertFalse(answer["turned"])
        self.assertEqual(1, len(answer["faces"]), "the faces are still there, to be named")
        self.assertIsNone(details["size"])
        self.assertTrue(asked.called)

    def test_a_look_that_never_answers_is_given_up_on(self):
        import threading

        from tagpup.files import images, shares
        photo = self.photo("regatta_022.jpg")
        self.face(photo)
        stuck = threading.Event()
        self.addCleanup(stuck.set)
        self.addCleanup(shares._away.clear)
        started = []
        import time
        with unittest.mock.patch.object(images, "shown_shape", lambda path: started.append(1) or stuck.wait(30)), \
                unittest.mock.patch("tagpup.services.photos.SHAPE_WAIT", 0.2):
            before = time.monotonic()
            answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        self.assertLess(time.monotonic() - before, 5)
        self.assertIsNone(answer["size"])


class NamingFromABox(Case):
    def test_both_apps_serve_the_face_reads_and_writes(self):
        photo = self.photo("regatta_002.jpg")
        face = self.face(photo)
        self.assertEqual(200, self.client.get("/library/api/face-matches", query_string={"id": face}).status_code)
        self.assertEqual(200, self.client.get("/library/api/people-face-samples").status_code)
        for route in ("face/match", "face/unmatch", "faces/exclude"):
            self.assertNotEqual(404, self.post(route, {}).status_code, route)

    def test_suggestions_are_the_ones_tagtuner_gives(self):
        # The same matrix and the same call: the percentages on the photo are the grid's.
        first = self.photo("regatta_003.jpg")
        named = self.face(first, degrees=0, name=WREN, name_source="manual")
        second = self.photo("regatta_004.jpg")
        unnamed = self.face(second, degrees=20)
        mine = self.client.get("/library/api/face-matches", query_string={"id": unnamed}).get_json()
        theirs = self.tuner().get("/library/api/face-matches", query_string={"id": unnamed}).get_json()
        self.assertEqual(theirs, mine)
        self.assertEqual(WREN, mine[0]["name"])
        self.assertAlmostEqual(math.cos(math.radians(20)), mine[0]["similarity"], places=3)
        self.assertEqual(named, self.look("SELECT id FROM faces WHERE name = ?", (WREN,))[0][0])

    def test_a_photo_with_three_faces_and_one_tag_a_face_named_from_its_box_agrees_in_both_apps(self):
        photo = self.photo("regatta_005.jpg")
        first, second, third = (self.face(photo, box=(5 + 20 * n, 5, 20 + 20 * n, 25)) for n in range(3))
        # The page's save of the keyword (its own flow), as the index records it.
        photos.record_tags(self.path, photo, ["People/" + WREN])
        self.assertEqual([(None,)] * 3, self.look("SELECT name FROM faces ORDER BY id"),
                         "three faces and one person are not decided by the tag alone (#788)")
        reply = self.post("face/match", {"face_id": second, "person_name": WREN})
        self.assertEqual({"success": True, "changed": 1}, reply.get_json())
        self.assertEqual((WREN, "manual", 0), self.row(second)[:3])
        self.assertEqual([(None,), (None,)], self.look("SELECT name FROM faces WHERE id != ? ORDER BY id", (second,)))
        self.assertIsNotNone(self.row(second)[3], "the face carries the person's id beside the name")
        self.assertEqual([(WREN, "keyword")], self.look("SELECT name, source FROM photo_people"),
                         "the person stays the photo's keyword person: the tag and the face agree")
        # TagTuner's panel on the same photo.
        details = self.tuner().get("/library/api/photo-details", query_string={"path": photo}).get_json()
        self.assertEqual([None, WREN, None], [f["name"] for f in details["faces"]])
        self.assertEqual([WREN], details["people"])
        self.assertTrue(first and third)

    def test_naming_the_name_a_tag_gave_makes_it_the_persons_decision(self):
        photo = self.photo("regatta_006.jpg")
        face = self.face(photo)
        photos.record_tags(self.path, photo, ["People/" + WREN])        # one face, one person: named by the tag
        self.assertEqual((WREN, None), self.row(face)[:2])
        reply = self.post("face/match", {"face_id": face, "person_name": WREN})
        self.assertEqual(1, reply.get_json()["changed"])
        self.assertEqual((WREN, "manual"), self.row(face)[:2])
        self.assertEqual(0, self.post("face/match", {"face_id": face, "person_name": WREN}).get_json()["changed"],
                         "a second click changed nothing and says so")

    def test_a_person_already_on_another_face_of_the_photo_is_refused(self):
        photo = self.photo("regatta_007.jpg")
        first, second = self.face(photo), self.face(photo, box=(30, 5, 50, 25))
        self.post("face/match", {"face_id": first, "person_name": WREN})
        reply = self.post("face/match", {"face_id": second, "person_name": WREN})
        self.assertEqual(400, reply.status_code)
        self.assertIn("already tagged on another face", reply.get_json()["error"])
        self.assertIsNone(self.row(second)[0])

    def test_an_excluded_face_cannot_be_named_until_it_is_restored(self):
        photo = self.photo("regatta_008.jpg")
        face = self.face(photo)
        self.assertEqual(1, self.post("faces/exclude", {"face_ids": [face]}).get_json()["excluded"])
        self.assertEqual(409, self.post("face/match", {"face_id": face, "person_name": WREN}).status_code)

    def test_a_name_taken_off_is_nobody_and_the_tag_is_left_alone(self):
        # Decided: unmatching says "this face is not them", not "they are not in the photo": the
        # person tag stays, and removing it is the people pills' own click.
        photo = self.photo("regatta_009.jpg", ["People/" + WREN])
        face = self.face(photo)
        photos.record_tags(self.path, photo, ["People/" + WREN])
        self.assertEqual(WREN, self.row(face)[0])
        self.post("face/unmatch", {"face_id": face})
        self.assertEqual((None, "manual"), self.row(face)[:2])
        self.assertEqual([(WREN,)], self.look("SELECT name FROM photo_people"))
        photos.record_tags(self.path, photo, ["People/" + WREN])          # saved again: not renamed
        self.assertEqual((None, "manual"), self.row(face)[:2])

    def test_a_write_is_refused_while_the_library_is_being_clustered(self):
        photo = self.photo("regatta_010.jpg")
        face = self.face(photo)
        flag = tuner_routes.clustering.of(self.library)
        flag.set()
        self.addCleanup(tuner_routes.clustering.forget, self.library)
        try:
            self.assertEqual(409, self.post("face/match", {"face_id": face, "person_name": WREN}).status_code)
        finally:
            flag.clear()
        self.assertIsNone(self.row(face)[0])
        self.assertEqual(200, self.post("face/match", {"face_id": face, "person_name": WREN}).status_code)

    def test_a_name_that_is_not_a_name_is_refused(self):
        photo = self.photo("regatta_011.jpg")
        face = self.face(photo)
        self.assertEqual(400, self.post("face/match", {"face_id": face, "person_name": "x/y"}).status_code)
        self.assertEqual(404, self.post("face/match", {"face_id": face + 99, "person_name": WREN}).status_code)

    def test_the_faces_strip_and_the_boxes_read_one_answer(self):
        photo = self.photo("regatta_012.jpg")
        face = self.face(photo)
        self.post("face/match", {"face_id": face, "person_name": ODA})
        answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        self.assertEqual([ODA], [f["name"] for f in answer["faces"]])
        self.assertEqual(0, answer["unmatched"])
        json.dumps(answer)


if __name__ == "__main__":
    unittest.main()
