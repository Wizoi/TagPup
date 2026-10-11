"""The camera and the lens a photo was taken with, found by the words typed (tagpup.store.search_index.cameras_with: a
substring of the name photo_meta.gear gives, read from the photos' metadata; docs/ARCHITECTURE.md, "Backlog: which photo
fields are searchable"). There was a table of these words (`search_gear`, migrations 27 to 29) until migration 30.

Photos are rows as the indexer records them (tests/view_library.py: photo_rows.add_read, whose ExifTool answer holds each
field under its group's name and bare), the shapes photo_index has for a camera (a model that says its make and one that does
not; a scan with none) and the lens fields ExifTool names (photo_index holds none: its reads do not ask). Expectations are
written out by hand. Fictional makers and models.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.services import library_view  # noqa: E402
from tagpup.store import db, journal, search_index  # noqa: E402

TIDEWATER = {"EXIF:Make": "Tidewater", "EXIF:Model": "Tidewater TX R6m2"}
SKIFF = {"EXIF:Make": "Tidewater", "EXIF:Model": "Skiff 8"}
PIXEL = {"EXIF:Make": "Lanternfly", "EXIF:Model": "Pixel 8 Pro"}
LENS = {"EXIF:LensModel": "TX24-70mm f/2.8L II USM", "EXIF:LensMake": "Tidewater"}
OTHER_LENS = {"EXIF:LensModel": "Objectif Élan 35mm F1.4"}


def words(text, **parts):
    return json.dumps(dict(parts, words=text))


def look(path, sql, params=()):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class Cameras(unittest.TestCase):
    def setUp(self):
        vl = self.vl = ViewLibrary(self)
        self.big = vl.photo("Trip", "a.jpg", taken="2024:06:01 10:00:00", fields=dict(TIDEWATER, **LENS))
        self.small = vl.photo("Trip", "b.jpg", taken="2024:06:02 10:00:00", fields=SKIFF)
        self.phone = vl.photo("Trip", "c.jpg", taken="2024:06:03 10:00:00", fields=dict(PIXEL, **OTHER_LENS))
        self.scan = vl.photo("Trip", "d.jpg")   # a scan, a screenshot: no camera at all
        self.xmp_only = vl.photo("Trip", "e.jpg", fields={"XMP:Make": "Lanternfly", "XMP:Model": "Kite 2"})

    def found(self, text, **parts):
        return library_view.ids(self.vl.library, "search", words(text, **parts))["ids"]


class WhatTheWordsFind(Cameras):
    def test_the_make_the_model_and_a_partial_token_find_the_camera(self):
        cases = {"tidewater": [self.big, self.small], "tid": [self.big, self.small], "r6": [self.big], "r6m2": [self.big],
                 "skiff": [self.small], "pixel": [self.phone], "lanternfly": [self.phone, self.xmp_only],
                 "kite": [self.xmp_only]}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, self.found(text))

    def test_a_model_that_lacks_its_make_is_found_by_the_make_and_a_model_that_has_it_is_named_once(self):
        self.assertEqual([self.phone], self.found("lanternfly pixel"))
        self.assertEqual([self.small], self.found("tidewater skiff"))
        # "Tidewater TX R6m2" is the model alone: the make is not put before a model that begins with it.
        self.assertEqual([self.big], self.found("tidewater r6m2"))
        self.assertEqual([], self.found("tidewater tidewater2"))

    def test_the_lens_is_found_by_its_name_its_range_and_its_aperture(self):
        cases = {"24-70": [self.big], "24-70mm": [self.big], "tx24": [self.big], "70mm": [self.big], "usm": [self.big],
                 "f/2.8": [self.big], "2.8l": [self.big], "elan": [self.phone], "35mm": [self.phone], "F1.4": [self.phone],
                 "Élan": [self.phone]}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, self.found(text))

    def test_a_term_is_found_anywhere_in_the_name_and_every_term_must_be_found(self):
        self.assertEqual([self.big], self.found("ater r6m2"), "inside the make and the model, not only at their start")
        self.assertEqual([self.small], self.found("KIFF"), "without case")
        self.assertEqual([self.phone], self.found("elan"), "without accents")
        self.assertEqual([self.big], self.found("tidewater 24-70"))
        self.assertEqual([self.big], self.found("r6 usm"))
        self.assertEqual([], self.found("skiff 24-70"), "the camera of one photo and the lens of another")
        self.assertEqual([], self.found("kite usm"))

    def test_the_search_still_finds_a_photo_by_its_other_words(self):
        other = self.vl.photo("Tidewater cove", "f.jpg", fields=PIXEL)
        self.assertEqual({self.big, self.small, other}, set(self.found("tidewater")), "the camera and the folder's words")
        self.assertEqual([other], self.found("tidewater cove"))
        self.assertEqual({self.big, self.small, other},
                         set(self.found("tidewater", none_of=[{"kind": "year", "value": "1900"}])))

    def test_a_photo_with_no_camera_data_is_found_by_no_camera(self):
        for text in ("tidewater", "lanternfly", "usm", "pixel"):
            self.assertNotIn(self.scan, self.found(text))
        self.assertEqual([self.scan], self.found("d.jpg"), "by its name, as ever")

    def test_nothing_typed_is_fts_syntax(self):
        for text in ('"', "camera:tidewater", "camera : tidewater", "lens:usm", "NEAR(a b)", "tidewater OR lanternfly", "-tidewater",
                     "tid*", "(", chr(92), "24-", "-70", "f/", "2.8l*"):
            with self.subTest(text=text):
                self.assertIsInstance(self.found(text), list)
        self.assertEqual([], self.found("camera:tidewater"), "a column filter is text: no photo holds the word camera")
        self.assertEqual([], self.found("tidewater OR lanternfly"))

    def test_the_words_follow_the_photo_through_the_orders(self):
        for order in library_view.store.ORDERS:
            with self.subTest(order=order):
                self.assertEqual({self.big, self.small}, set(library_view.ids(
                    self.vl.library, "search", words("tidewater"), order=order)["ids"]))


class TheWordsFollowTheWrites(Cameras):
    def test_a_photo_read_again_with_another_camera_and_without_a_lens(self):
        photo_rows.add_read(self.vl.conn, self.vl.path_of(self.big), {"EXIF:Make": "Lanternfly", "EXIF:Model": "Kite 2"})
        self.vl.conn.commit()
        self.assertEqual([self.small], self.found("tidewater"))
        self.assertEqual([], self.found("24-70"))
        self.assertEqual({self.big, self.phone, self.xmp_only}, set(self.found("lanternfly")))

    def test_a_photo_read_again_with_a_lens_it_did_not_have_is_found_by_it(self):
        self.assertEqual([self.big], self.found("24-70"))
        photo_rows.add_read(self.vl.conn, self.vl.path_of(self.small), dict(SKIFF, **LENS))
        self.vl.conn.commit()
        self.assertEqual([self.big, self.small], self.found("24-70"))

    def test_a_metadata_change_through_the_journal_and_its_undo(self):
        held = look(self.vl.path, "SELECT raw_metadata FROM photos WHERE id = ?", (self.small,))[0][0]
        changed = json.loads(held)
        changed.update({"EXIF:Model": "Skiff 9", "Model": "Skiff 9", "EXIF:LensModel": "Harbour 50mm", "LensModel": "Harbour 50mm"})
        applied = journal.apply(self.vl.path, "metadata", [journal.update(
            "photos", (self.small,), {"raw_metadata": held}, {"raw_metadata": json.dumps(changed)})])
        self.assertEqual([self.small], self.found("skiff 9"))
        self.assertEqual([self.small], self.found("harbour 50mm"))
        self.assertEqual([], self.found("skiff 8"))
        journal.undo(self.vl.path, applied.change_id)
        self.assertEqual([self.small], self.found("skiff 8"))
        self.assertEqual([], self.found("skiff 9"))
        self.assertEqual([], self.found("harbour 50mm"))

    def test_a_photo_deleted_by_another_program_is_found_no_more(self):
        self.assertEqual([self.big, self.small], self.found("tidewater"), "read once: the cameras are kept")
        conn = db.connect(self.vl.path)
        try:
            conn.execute("DELETE FROM photos WHERE id = ?", (self.big,))
            conn.commit()
            self.assertEqual([], search_index.stale(conn, sample=None))
        finally:
            conn.close()
        self.assertEqual([self.small], self.found("tidewater"), "the photos' generation moved: the cameras are read again")
        self.assertEqual([], self.found("24-70"))

    def test_the_cameras_are_read_once_until_a_photo_changes(self):
        self.found("tidewater")
        with mock.patch.object(search_index._cameras, "build", wraps=search_index._cameras.build) as read:
            self.found("skiff")
            self.found("pixel")
            self.assertEqual(0, read.call_count, "the same generation of the photos")
            photo_rows.add_read(self.vl.conn, self.vl.path_of(self.small), {"EXIF:Make": "Lanternfly", "EXIF:Model": "Kite 3"})
            self.vl.conn.commit()
            self.assertEqual([self.small], self.found("kite 3"))
            self.assertEqual(1, read.call_count)

    def test_a_metadata_row_that_is_not_json_names_no_camera_and_costs_the_others_nothing(self):
        self.vl.conn.execute("UPDATE photos SET raw_metadata = '{not json' WHERE id = ?", (self.scan,))
        self.vl.conn.commit()
        self.assertEqual([self.big, self.small], self.found("tidewater"))

    def test_a_short_term_without_a_digit_is_no_camera(self):
        for text in ("on", "a", "to", "ti"):
            with self.subTest(text=text):
                self.assertNotIn(self.big, self.found(text + " d.jpg"))
                self.assertEqual([], search_index.cameras_with(self.vl.conn, text))
        for text in ("r6", "tidewater"):
            with self.subTest(text=text):
                self.assertTrue(search_index.cameras_with(self.vl.conn, text))
        self.assertEqual([self.big], self.found("r6"))
        self.assertEqual([self.big, self.small], self.found("tidewater"))

    def test_a_term_is_text_not_a_pattern(self):
        for text in ("%%%", "___", "t_dewater", "tide%", "[a-z]", "tidewater*"):
            with self.subTest(text=text):
                self.assertEqual([], self.found(text))

    def test_two_libraries_holding_one_folder_each_find_their_own_cameras(self):
        other = ViewLibrary(self, "lighthouse", home=self.vl.home)
        theirs = other.photo("Trip", "a.jpg", at=self.vl.path_of(self.big), fields=PIXEL)
        self.assertEqual([theirs], library_view.ids(other.library, "search", words("pixel"))["ids"])
        self.assertEqual([], library_view.ids(other.library, "search", words("tidewater"))["ids"])
        self.assertEqual([self.big, self.small], self.found("tidewater"))
