"""docs/findings.md, #791: faces on the open photo, in TagPup's Organize -- what the server gives and takes.

The page draws a box over each face from `/api/photo-faces` (which says the pixel size the boxes are
in, and each box on the picture as it is shown, turned by its Orientation), suggests people from `/api/face-matches` and names, takes a
name off or rules out a face through TagTuner's own views of one face (`/api/face/match`, `/api/face/unmatch`,
`/api/faces/exclude`), served by TagPup too. The photo's tag is written by the page's own save of its
keywords; these tests stand in for that with the index record a save leaves (photos.record_tags), and check
that the tag and the faces agree in both apps. A face's name and the photo's person tag are one decision (#861,
tests/test_faces_and_tags_in_step.py); ExifTool is a table of files, for the writes the server makes of the tag.

Rows are made as the code that makes them makes them: the photo by the indexer's record (tests/photo_rows.py),
the tree by tagpup.store.taxonomy, the faces by tagpup.store.faces.
"""
import io
import json
import math
import os
import sys
import unittest
import unittest.mock

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_rows  # noqa: E402
from face_rows import FACE_MODEL  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402
import fake_exiftool  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.store import db, faces, people, taxonomy  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402

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
        self.files = fake_exiftool.standing_in(self)
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.path = self.home.library("library.db")
        self.library = Library(self.path)
        self.folder = os.path.join(self.home.root, "Pictures")
        self.plant_tree()

    def plant_tree(self):
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

    def face(self, photo_path, box=(10, 10, 110, 110), degrees=0, embedding=True, **columns):
        """A face `degrees` round the circle; `embedding` bytes give it those instead."""
        def add(conn):
            face_id = faces.insert(conn, photo_path, list(box), at(degrees), model=FACE_MODEL)
            if embedding is not True:
                face_rows.add_vector_of_face(conn, face_id, embedding)
            for column, value in columns.items():
                conn.execute("UPDATE faces SET %s = ? WHERE id = ?" % column, (value, face_id))
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

    def post(self, route, body):
        return self.client.post("/library/api/" + route, json=body)

    def row(self, face_id):
        return self.look("SELECT name, name_source, excluded, tag_id FROM faces WHERE id = ?", (face_id,))[0]

    def tuner(self):
        app = web.create_app("tuner", startup=self.library, runtime=Runtime())
        app.testing = True
        return app.test_client()


class WhatTheBoxesAreDrawnOn(Case):
    def test_the_answer_says_the_pixels_the_boxes_are_in(self):
        photo = self.photo("regatta_001.jpg", size=(4000, 3000))
        self.face(photo, box=(1000, 500, 1400, 900))
        answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        self.assertEqual([4000, 3000], answer["size"])
        self.assertEqual([4000, 3000], answer["shown_size"])
        self.assertEqual([1000, 500, 1400, 900], answer["faces"][0]["box"])
        self.assertEqual([1000, 500, 1400, 900], answer["faces"][0]["shown_box"])
        self.assertNotIn("turned", answer)

    def test_a_file_that_cannot_be_read_has_no_size_and_so_no_box(self):
        photo = self.photo("gone_001.jpg")
        self.face(photo)
        os.remove(photo)
        answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        self.assertIsNone(answer["size"])
        self.assertIsNone(answer["shown_size"])
        self.assertIsNone(answer["faces"][0]["shown_box"])
        self.assertEqual(1, len(answer["faces"]), "the faces are still there, to be named")

    def test_an_unnamed_face_behind_the_subject_is_left_out_and_a_named_one_never(self):
        photo = self.photo("track_001.jpg", size=(4000, 3000))
        big = self.face(photo, box=(1000, 500, 1400, 900))
        self.face(photo, box=(2000, 500, 2050, 550))                        # 50 px of 3000: under 2%, nobody
        small_named = self.face(photo, box=(2400, 500, 2450, 550), name=WREN)   # as small, named
        answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        self.assertEqual({big, small_named}, {each["id"] for each in answer["faces"]})
        self.assertEqual(2, answer["total"])
        self.assertEqual(1, answer["unmatched"])
        self.assertEqual(3, len(self.look("SELECT id FROM faces")), "the library keeps the face")

    def test_a_photo_with_no_faces_has_none_to_box(self):
        photo = self.photo("empty_001.jpg")
        answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        self.assertEqual([], answer["faces"])


class ABoxSitsOnItsFaceInATurnedPhoto(Case):
    """A phone photo stored sideways (EXIF Orientation 6), and every other Orientation, mirrored ones too: its faces were
    found and kept in the stored pixels, and the page shows the picture upright (/api/photo-file, as the page asks for it).
    Each face's box the page draws (`shown_box` in `shown_size`) covers that face on the picture the page is served --
    before, the page drew the stored box over the upright picture, beside the faces, and said the photo was turned."""

    STORED = (80, 48)
    FACES = ([8, 6, 24, 18], [52, 28, 72, 42])   # two bright blocks, the faces, in the stored pixels

    def turned_photo(self, name, value, fmt="JPEG"):
        photo_path = os.path.join(self.folder, name)
        os.makedirs(self.folder, exist_ok=True)
        picture = Image.new("RGB", self.STORED, (0, 0, 0))
        for block in self.FACES:
            picture.paste((255, 255, 255), tuple(block))
        exif = Image.Exif()
        exif[0x0112] = value
        picture.save(photo_path, fmt, exif=exif.tobytes(), **({"quality": 100} if fmt == "JPEG" else {}))
        db.write_with_connection(self.path, lambda conn: photo_rows.add_read(conn, photo_path, {"XMP:Subject": []}))
        for block in self.FACES:
            self.face(photo_path, box=block)
        return photo_path

    def served(self, photo_path):
        """The picture the page shows: what it asks /api/photo-file for (web/common/file-addresses.js photoFileUrl)."""
        reply = self.client.get("/library/api/photo-file", query_string={"path": photo_path, "size": 800})
        self.assertEqual(200, reply.status_code)
        return Image.open(io.BytesIO(reply.data)).convert("L")

    def test_each_box_covers_its_face_on_the_picture_as_shown(self):
        for value in range(1, 9):
            with self.subTest(orientation=value):
                photo = self.turned_photo("phone_%d.jpg" % value, value)
                answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
                shown = self.served(photo)
                self.assertEqual(list(shown.size), answer["shown_size"])
                self.assertEqual(list(self.STORED), answer["size"], "the stored size stays, for TagTuner")
                self.assertEqual(sorted(self.FACES), sorted(face["box"] for face in answer["faces"]))
                for face in answer["faces"]:
                    x1, y1, x2, y2 = face["shown_box"]
                    inside = shown.crop((x1 + 2, y1 + 2, x2 - 2, y2 - 2))
                    self.assertGreater(inside.getextrema()[0], 200, "the box %s is not on its face" % face["shown_box"])
                    bright = shown.point(lambda level: 255 if level > 128 else 0).crop((x1 - 2, y1 - 2, x2 + 2, y2 + 2))
                    self.assertEqual((2, 2, 2 + x2 - x1, 2 + y2 - y1), bright.getbbox(), "the box is the face, no more")

    def test_a_tiff_is_shown_as_its_boxes_are_kept(self):
        # Pillow turns a TIFF by its Orientation as it loads it, so its boxes are kept on the picture as shown already.
        photo = self.turned_photo("scan.tif", 6, fmt="TIFF")
        answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        self.assertEqual(answer["size"], answer["shown_size"])
        for face in answer["faces"]:
            self.assertEqual(face["box"], face["shown_box"])


class TheFileIsLookedAtOnceAndNotForeverOnAShare(Case):
    """#836: the size and the orientation come from one open of the file, and a share that is away gives
    no size and no box, never a stalled request."""

    def test_one_open_for_the_size_and_the_orientation(self):
        from tagpup.files import images
        photo = self.photo("regatta_020.jpg")
        self.face(photo)
        real = images.shown_shape
        opened = []
        with unittest.mock.patch.object(images, "shown_shape", lambda path: opened.append(path) or real(path)):
            answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        self.assertEqual([photo], opened)
        self.assertEqual([60, 40], answer["size"])

    def test_a_share_that_is_away_gives_no_size_and_does_not_stall(self):
        from tagpup.services import photos as photo_service
        photo = self.photo("regatta_021.jpg")
        self.face(photo)
        with unittest.mock.patch.object(photo_service.shares, "on_a_network_drive", return_value=True), \
                unittest.mock.patch.object(photo_service.shares, "bounded", return_value=("away", None)) as asked:
            answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
            details = self.tuner().get("/library/api/photo-details", query_string={"path": photo}).get_json()
        self.assertIsNone(answer["size"])
        self.assertIsNone(answer["faces"][0]["shown_box"])
        self.assertEqual(1, len(answer["faces"]), "the faces are still there, to be named")
        self.assertIsNone(details["size"])
        self.assertTrue(asked.called)

    def test_a_slow_look_at_a_local_file_does_not_mark_the_drive_away(self):
        import time

        from tagpup.files import images, shares
        shares.forget()
        self.addCleanup(shares.forget)
        photo = self.photo("regatta_023.jpg")
        self.face(photo)
        real = images.shown_shape

        def slow(path):
            time.sleep(0.15)   # the scenario: a local look three times as slow as SHAPE_WAIT (0.05 s, below)
            return real(path)

        with unittest.mock.patch.object(images, "shown_shape", slow), \
                unittest.mock.patch("tagpup.services.photos.SHAPE_WAIT", 0.05):
            first = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
            second = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        self.assertEqual(([60, 40], [60, 40]), (first["size"], second["size"]), "a slow local look lost the size")
        self.assertEqual({}, shares._away, "a local disk was taken as away")

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
                unittest.mock.patch("tagpup.services.photos.shares.on_a_network_drive", return_value=True), \
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
        photo_rows.tag(self.path, photo, ["People/" + WREN])
        self.assertEqual([(None,)] * 3, self.look("SELECT name FROM faces ORDER BY id"),
                         "three faces and one person are not decided by the tag alone (#788)")
        reply = self.post("face/match", {"face_id": second, "person_name": WREN})
        self.assertEqual({"success": True, "changed": 1, "tags_written": 0}, reply.get_json())
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
        photo_rows.tag(self.path, photo, ["People/" + WREN])        # one face, one person: named by the tag
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

    def test_a_name_taken_off_is_nobody_and_the_person_goes_from_the_photo(self):
        # #861 reverses #791's "the tag stays": a face that is not them is not a person of the photo, unless
        # another face carries them (tests/test_faces_and_tags_in_step.py has the cases).
        photo = self.photo("regatta_009.jpg", ["People/" + WREN])
        self.files.keep_tags(photo, ["People/" + WREN])
        face = self.face(photo)
        photo_rows.tag(self.path, photo, ["People/" + WREN])
        self.assertEqual(WREN, self.row(face)[0])
        self.post("face/unmatch", {"face_id": face})
        self.assertEqual((None, "manual"), self.row(face)[:2])
        self.assertEqual([], self.look("SELECT name FROM photo_people"))
        self.assertEqual([], self.files.tags_of(photo))
        photo_rows.tag(self.path, photo, ["People/" + WREN])          # saved again: not renamed
        self.assertEqual((None, "manual"), self.row(face)[:2])

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


class TheStripSuggestsWhatTheBoxesListFirst(Case):
    """#828: two matchers answered "who does it look like" in one app. The strip's suggestion is the first of
    `/api/face-matches`, the shared matrix, kept at the same threshold."""

    def strip(self, photo):
        answer = self.client.get("/library/api/photo-faces", query_string={"path": photo}).get_json()
        return {each["id"]: each for each in answer["faces"]}

    def boxes(self, face):
        return self.client.get("/library/api/face-matches", query_string={"id": face}).get_json()

    def test_a_named_face_on_the_same_photo_is_a_candidate_for_neither(self):
        """Two faces in a photo are two people (main session, 2026-10-22): the boxes and the strip leave the photo's own
        named face out, though it is the closest, and both offer the person of another photo first."""
        elsewhere = self.photo("regatta_030.jpg")
        self.face(elsewhere, degrees=0, name=WREN, name_source="manual")
        photo = self.photo("regatta_031.jpg")
        self.face(photo, degrees=25, name=ODA, name_source="manual")
        unnamed = self.face(photo, box=(30, 5, 50, 25), degrees=20)
        listed = self.boxes(unnamed)
        shown = self.strip(photo)[unnamed]
        self.assertEqual(WREN, listed[0]["name"])
        self.assertNotIn(ODA, [candidate["name"] for candidate in listed], "a face of the photo itself was a candidate")
        self.assertEqual(listed[0]["name"], shown["suggestion"])
        self.assertAlmostEqual(listed[0]["similarity"], shown["similarity"], places=3)

    def test_a_face_below_the_offer_gets_none_and_an_excluded_face_gets_none(self):
        elsewhere = self.photo("regatta_032.jpg")
        self.face(elsewhere, degrees=0, name=WREN, name_source="manual")
        photo = self.photo("regatta_033.jpg")
        far = self.face(photo, degrees=80)
        out = self.face(photo, box=(30, 5, 50, 25), degrees=10, excluded=1)
        near = self.face(photo, box=(90, 5, 110, 25), degrees=10)
        shown = self.strip(photo)
        self.assertIsNone(shown[far]["suggestion"])
        self.assertIsNone(shown[out]["suggestion"])
        self.assertEqual(WREN, shown[near]["suggestion"])
        self.assertEqual(self.boxes(near)[0]["name"], shown[near]["suggestion"])

    def test_a_face_named_since_the_last_look_is_a_candidate_at_the_next(self):
        elsewhere = self.photo("regatta_034.jpg")
        self.face(elsewhere, degrees=0, name=WREN, name_source="manual")
        photo = self.photo("regatta_035.jpg")
        unnamed = self.face(photo, degrees=40)
        self.assertEqual(WREN, self.strip(photo)[unnamed]["suggestion"])
        later = self.photo("regatta_036.jpg")
        self.face(later, degrees=40, name=ODA, name_source="manual")
        shown = self.strip(photo)[unnamed]
        self.assertEqual(ODA, shown["suggestion"], "the shared matrix was read again once a face was named")
        self.assertEqual(self.boxes(unnamed)[0]["name"], shown["suggestion"])


class TheSelectedPhotosSuggestWhoTheirFacesLookLike(Case):
    """Organize's Suggested People for a selection of a folder just indexed: who the unnamed faces of the selected
    photos look like, the people and the cut the photo's own strip offers, asked once for the whole selection."""

    def suggestions(self, paths):
        return self.post("photo-faces/suggestions", {"paths": paths})

    def test_a_shoot_selected_offers_each_person_once_a_photo_at_the_best_likeness_and_leaves_the_rest_out(self):
        elsewhere = self.photo("regatta_040.jpg")
        self.face(elsewhere, degrees=0, name=WREN, name_source="manual")
        two_alike = self.photo("regatta_041.jpg")
        self.face(two_alike, degrees=30)
        closer = self.face(two_alike, box=(30, 5, 50, 25), degrees=10)
        stranger = self.photo("regatta_042.jpg")
        self.face(stranger, degrees=80)
        named_only = self.photo("regatta_043.jpg")
        self.face(named_only, degrees=-60, name=ODA, name_source="manual")
        left_out = self.photo("regatta_044.jpg")
        self.face(left_out, degrees=5, excluded=1)
        reply = self.suggestions([two_alike, stranger, named_only, left_out, elsewhere])
        self.assertEqual(200, reply.status_code)
        answer = reply.get_json()["suggestions"]
        self.assertEqual([two_alike], list(answer), "only the photo with an unnamed face like somebody is answered")
        strip = self.client.get("/library/api/photo-faces", query_string={"path": two_alike}).get_json()
        best = next(face for face in strip["faces"] if face["id"] == closer)
        self.assertEqual([{"person": best["suggestion_person"], "name": WREN, "similarity": best["similarity"]}],
                         answer[two_alike], "one entry for the person, at the likeness of the face most like them")
        self.assertEqual("People/" + WREN, answer[two_alike][0]["person"]["tag"])

    def test_a_face_named_since_is_no_longer_offered(self):
        elsewhere = self.photo("regatta_045.jpg")
        self.face(elsewhere, degrees=0, name=WREN, name_source="manual")
        photo = self.photo("regatta_046.jpg")
        face = self.face(photo, degrees=10)
        self.assertIn(photo, self.suggestions([photo]).get_json()["suggestions"])
        self.post("face/match", {"face_id": face, "person_name": WREN})
        self.assertEqual({}, self.suggestions([photo]).get_json()["suggestions"])

    def test_a_face_the_strip_leaves_out_is_not_suggested_and_a_photo_whose_file_is_gone_still_is(self):
        elsewhere = self.photo("regatta_047.jpg")
        self.face(elsewhere, degrees=0, name=WREN, name_source="manual")
        behind = self.photo("regatta_048.jpg", size=(4000, 3000))
        self.face(behind, box=(2000, 500, 2050, 550), degrees=10)   # 50 px of 3000: under 2%, the strip leaves it out
        gone = self.photo("regatta_049.jpg")
        self.face(gone, degrees=10)
        os.remove(gone)
        answer = self.suggestions([behind, gone]).get_json()["suggestions"]
        self.assertEqual([gone], list(answer), "the strip's list and the selection's are one list")

    def test_more_than_five_hundred_photos_or_no_list_is_refused_with_a_sentence(self):
        for body in ({"paths": ["x.jpg"] * 501}, {"paths": "x.jpg"}, {}):
            reply = self.suggestions(body["paths"]) if "paths" in body else self.post("photo-faces/suggestions", body)
            self.assertEqual(400, reply.status_code, body)
            self.assertTrue(reply.get_json()["error"].endswith("."), reply.get_json())
        self.assertEqual(200, self.suggestions(["x.jpg"] * 500).status_code)


if __name__ == "__main__":
    unittest.main()
