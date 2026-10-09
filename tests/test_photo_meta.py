"""tagpup.core.photo_meta: what a photo's raw metadata says about the photo -- rating, camera, size
and place -- read one way, from rows made as the indexer makes them (tests/photo_rows.as_read: each
field under its group's name and again bare), never from a shape typed here.

The look at photo_index's 68,466 rows that decided the rules is in the module's docstring: no row
holds a width, a height or an Orientation, so those are tested on records a library read with more
fields would hold, and the rest on the spellings the library has.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import photo_rows  # noqa: E402

from tagpup.core import paths, photo_meta  # noqa: E402

PHOTO = os.path.join(os.path.sep, "photos", "2024", "IMG_0001.jpg")


def meta_of(**fields):
    """The Meta of the record an index read of a photo whose ExifTool answer was `fields`."""
    return photo_meta.extract(photo_rows.as_read(PHOTO, fields)["raw_metadata"])


class TheRating(unittest.TestCase):
    def test_the_xmp_rating_is_read(self):
        self.assertEqual(5, meta_of(**{"XMP:Rating": 5}).rating)

    def test_xmp_comes_before_exif_and_exif_before_the_bare_name(self):
        self.assertEqual(3, meta_of(**{"XMP:Rating": 3, "EXIF:Rating": 5}).rating)
        self.assertEqual(4, meta_of(**{"EXIF:Rating": 4}).rating)
        self.assertEqual(2, photo_meta.extract({"Rating": 2}).rating, "a row holding only the bare name")

    def test_unrated_is_zero_and_rejected_is_minus_one(self):
        self.assertEqual(0, meta_of(**{"XMP:Rating": 0}).rating)
        self.assertEqual(-1, meta_of(**{"XMP:Rating": -1}).rating)

    def test_a_photo_without_one_has_none(self):
        self.assertIsNone(meta_of(**{"XMP:Subject": ["Trips/Coast"]}).rating)

    def test_a_value_that_is_not_a_rating_is_passed_over_for_the_next_source(self):
        self.assertEqual(4, meta_of(**{"XMP:Rating": 9, "EXIF:Rating": 4}).rating, "out of range")
        self.assertEqual(4, meta_of(**{"XMP:Rating": "lots", "EXIF:Rating": 4}).rating, "not a number")
        self.assertEqual(4, meta_of(**{"XMP:Rating": 3.5, "EXIF:Rating": 4}).rating, "not whole")
        self.assertEqual(4, meta_of(**{"XMP:Rating": True, "EXIF:Rating": 4}).rating, "a yes is not a one")
        self.assertIsNone(meta_of(**{"XMP:Rating": 9}).rating)

    def test_a_number_written_as_text_or_a_whole_float_is_read(self):
        self.assertEqual(5, meta_of(**{"XMP:Rating": "5"}).rating)
        self.assertEqual(-1, meta_of(**{"XMP:Rating": "-1"}).rating)
        self.assertEqual(3, meta_of(**{"XMP:Rating": 3.0}).rating)


class TheCamera(unittest.TestCase):
    def test_make_and_model_are_read_trimmed(self):
        found = meta_of(**{"EXIF:Make": "  Harbourlight ", "EXIF:Model": "HL 400\x00\x00"})
        self.assertEqual(("Harbourlight", "HL 400"), (found.make, found.model))

    def test_exif_comes_before_xmp(self):
        found = meta_of(**{"EXIF:Make": "Harbourlight", "XMP:Make": "Other", "XMP:Model": "Only XMP"})
        self.assertEqual(("Harbourlight", "Only XMP"), (found.make, found.model))

    def test_an_empty_make_is_passed_over(self):
        found = meta_of(**{"EXIF:Make": "", "XMP:Make": "Harbourlight"})
        self.assertEqual("Harbourlight", found.make)
        self.assertIsNone(meta_of(**{"EXIF:Make": "  "}).make)

    def test_a_make_that_is_not_text_is_left_out(self):
        self.assertIsNone(meta_of(**{"EXIF:Make": 42}).make)
        self.assertIsNone(photo_meta.extract({"Make": ["a", "b"]}).make, "two values are no make")
        self.assertEqual("Harbourlight", photo_meta.extract({"Make": ["Harbourlight"]}).make)


def gear_of(**fields):
    return photo_meta.gear(photo_rows.as_read(PHOTO, fields)["raw_metadata"])


class TheCameraAsItIsCalled(unittest.TestCase):
    """photo_index's models: Canon's say the make, a Pixel's and a Sony's do not (13,542 of 62,877 photos holding both)."""

    def test_a_model_that_begins_with_its_make_is_the_name(self):
        self.assertEqual("Harbourlight HL 400", photo_meta.camera_name("Harbourlight", "Harbourlight HL 400"))
        self.assertEqual("Harbourlight HL 400", photo_meta.camera_name("HARBOURLIGHT", "Harbourlight HL 400"),
                         "without case")
        self.assertEqual("Harbourlight HL 400", photo_meta.camera_name("Harbourlight Imaging Corp.", "Harbourlight HL 400"),
                         "a make of several words is compared by its first")

    def test_a_model_holding_a_word_of_the_make_anywhere_is_the_name(self):
        # Live shapes (photo_index, counted 2026-10-09).
        self.assertEqual("KODAK CX4310 DIGITAL CAMERA",
                         photo_meta.camera_name("EASTMAN KODAK COMPANY", "KODAK CX4310 DIGITAL CAMERA"))
        self.assertEqual("Canon PowerShot ELPH 100 HS", photo_meta.camera_name("Canon", "Canon PowerShot ELPH 100 HS"))
        self.assertEqual("Tidewater Skiff 8", photo_meta.camera_name("Tidewater", "Skiff 8"))
        self.assertEqual("Tidewater Fishtidewater 8", photo_meta.camera_name("Tidewater", "Fishtidewater 8"),
                         "a word inside another, not at its start, is not a word of the make")

    def test_the_live_pairs(self):
        """Make and model pairs photo_index holds (counted 2026-10-09): the name each is called by."""
        pairs = (("Canon", "Canon EOS R6m2", "Canon EOS R6m2"),
                 ("Apple", "iPhone 12", "Apple iPhone 12"),
                 ("NIKON", "COOLPIX P1", "NIKON COOLPIX P1"),
                 ("LG Electronics", "LG-TP260", "LG-TP260"),
                 ("EASTMAN KODAK COMPANY", "KODAK CX4310 DIGITAL CAMERA", "KODAK CX4310 DIGITAL CAMERA"),
                 ("HTC", "HTCONE", "HTCONE"),
                 ("OLYMPUS IMAGING CORP.", "FE45,X40", "OLYMPUS IMAGING CORP. FE45,X40"),
                 ("Google", "Pixel 8 Pro", "Google Pixel 8 Pro"),
                 ("SONY", "DSC-P8", "SONY DSC-P8"))
        for make, model, name in pairs:
            with self.subTest(make=make, model=model):
                self.assertEqual(name, photo_meta.camera_name(make, model))

    def test_a_word_of_the_make_that_names_no_maker_does_not_make_the_model_the_name(self):
        self.assertEqual("Tidewater Imaging Digital Camera Mk2", photo_meta.camera_name("Tidewater Imaging", "Digital Camera Mk2"))
        self.assertEqual("XY Corp. Corp Box", photo_meta.camera_name("XY Corp.", "Corp Box"), "generic")
        self.assertEqual("Zeta HQ HQ 5", photo_meta.camera_name("Zeta HQ", "HQ 5"), "under three letters")
        self.assertEqual("Fjord X-T4", photo_meta.camera_name("FJORD", "Fjord X-T4"), "without case")

    def test_a_model_that_does_not_is_put_after_the_make(self):
        self.assertEqual("Tidewater Skiff 8", photo_meta.camera_name("Tidewater", "Skiff 8"))
        self.assertEqual("SONY DSC-X8", photo_meta.camera_name("SONY", "DSC-X8"))

    def test_one_alone_is_the_name_and_none_is_none(self):
        self.assertEqual("Skiff 8", photo_meta.camera_name(None, "Skiff 8"))
        self.assertEqual("Tidewater", photo_meta.camera_name("Tidewater", None))
        self.assertIsNone(photo_meta.camera_name(None, None))
        self.assertIsNone(photo_meta.camera_name("", ""))

    def test_the_camera_is_made_of_the_fields_make_and_model_are_read_from(self):
        self.assertEqual("Tidewater Skiff 8", gear_of(**{"EXIF:Make": "Tidewater", "EXIF:Model": "Skiff 8"}).camera)
        self.assertEqual("Tidewater Skiff 8", photo_meta.gear({"Make": "Tidewater", "Model": "Skiff 8"}).camera,
                         "a row holding only the bare names")
        self.assertEqual("Tidewater Skiff 8", gear_of(**{"XMP:Make": "Tidewater", "XMP:Model": "Skiff 8"}).camera)

    def test_a_scan_or_a_screenshot_has_no_camera_and_no_lens(self):
        self.assertEqual(photo_meta.NO_GEAR, gear_of(**{"XMP:Subject": ["Trips/Coast"]}))
        self.assertEqual(photo_meta.NO_GEAR, photo_meta.gear({}))
        for damaged in (None, "text", [], 5):
            self.assertEqual(photo_meta.NO_GEAR, photo_meta.gear(damaged))


class TheLens(unittest.TestCase):
    """The lens fields are ExifTool's names: not one of photo_index's rows holds one (the indexer does not ask), so these
    are the records a library read with them asked for would hold, made as the index makes a read."""

    def test_the_lens_model_is_read_under_its_group_and_bare(self):
        self.assertEqual("EF24-70mm f/2.8L II USM", gear_of(**{"EXIF:LensModel": "EF24-70mm f/2.8L II USM  "}).lens)
        self.assertEqual("EF24-70mm f/2.8L II USM", photo_meta.gear({"LensModel": "EF24-70mm f/2.8L II USM"}).lens,
                         "a row holding only the bare name")

    def test_the_groups_the_lens_may_come_in_are_all_read_in_order(self):
        self.assertEqual("In EXIF", gear_of(**{"EXIF:LensModel": "In EXIF", "XMP:LensModel": "In XMP"}).lens)
        self.assertEqual("In XMP", gear_of(**{"XMP:LensModel": "In XMP", "Composite:LensID": "An id"}).lens)
        self.assertEqual("An id", gear_of(**{"Composite:LensID": "An id"}).lens)

    def test_a_number_is_no_lens(self):
        # Run without print conversion, a LensID is a number.
        self.assertIsNone(gear_of(**{"Composite:LensID": 61182}).lens)
        self.assertEqual("A name", gear_of(**{"Composite:LensID": 61182, "EXIF:LensModel": "A name"}).lens)
        self.assertIsNone(gear_of(**{"EXIF:LensModel": "  "}).lens)

    def test_the_lens_maker_goes_before_a_name_that_lacks_it(self):
        self.assertEqual("Tidewater 24-70mm F2.8",
                         gear_of(**{"EXIF:LensModel": "24-70mm F2.8", "EXIF:LensMake": "Tidewater"}).lens)
        self.assertEqual("Tidewater 24-70mm F2.8",
                         gear_of(**{"EXIF:LensModel": "Tidewater 24-70mm F2.8", "EXIF:LensMake": "tidewater"}).lens)
        self.assertIsNone(gear_of(**{"EXIF:LensMake": "Tidewater"}).lens, "a maker alone is no lens")

    def test_unicode_is_kept(self):
        self.assertEqual("Objectif Élan 35mm", gear_of(**{"EXIF:LensModel": "Objectif Élan 35mm"}).lens)

    def test_load_reads_a_column_and_says_nothing_of_a_damaged_one(self):
        self.assertEqual({"Make": "A"}, photo_meta.load('{"Make": "A"}'))
        self.assertEqual({}, photo_meta.load(None))
        self.assertEqual({}, photo_meta.load("not json"))
        self.assertEqual(photo_meta.EMPTY, photo_meta.from_json("not json"))


class TheSize(unittest.TestCase):
    """No row of photo_index holds a size (the fields are not asked for): these are the records a
    library read with ImageWidth, ImageHeight, ExifImageWidth and Orientation would hold."""

    def test_the_pictures_own_size_is_read(self):
        found = meta_of(**{"File:ImageWidth": 4000, "File:ImageHeight": 3000})
        self.assertEqual((4000, 3000), (found.width, found.height))

    def test_the_size_the_exif_block_declares_is_the_next_source(self):
        found = meta_of(**{"EXIF:ExifImageWidth": 1600, "EXIF:ExifImageHeight": 1200})
        self.assertEqual((1600, 1200), (found.width, found.height))

    def test_a_picture_stored_on_its_side_is_shown_the_other_way_round(self):
        for turn in (5, 6, 7, 8, "6"):
            found = meta_of(**{"File:ImageWidth": 4000, "File:ImageHeight": 3000, "EXIF:Orientation": turn})
            self.assertEqual((3000, 4000), (found.width, found.height), turn)
        for turn in (1, 2, 3, 4, 9, "x"):
            found = meta_of(**{"File:ImageWidth": 4000, "File:ImageHeight": 3000, "EXIF:Orientation": turn})
            self.assertEqual((4000, 3000), (found.width, found.height), turn)

    def test_a_width_without_its_height_is_no_size(self):
        found = meta_of(**{"File:ImageWidth": 4000, "EXIF:ExifImageHeight": 1200})
        self.assertEqual((None, None), (found.width, found.height), "never one source's width with another's height")

    def test_a_size_that_is_not_a_picture_is_left_out(self):
        for width, height in ((0, 3000), (-4000, 3000), ("wide", 3000), (4000.5, 3000), (True, 3000)):
            found = meta_of(**{"File:ImageWidth": width, "File:ImageHeight": height})
            self.assertEqual((None, None), (found.width, found.height), (width, height))

    def test_a_pair_that_is_not_one_is_passed_over_for_the_next(self):
        found = meta_of(**{"File:ImageWidth": 0, "File:ImageHeight": 0,
                           "EXIF:ExifImageWidth": 1600, "EXIF:ExifImageHeight": 1200})
        self.assertEqual((1600, 1200), (found.width, found.height))


class ThePlace(unittest.TestCase):
    """As the library holds it: Composite:GPSLatitude and Composite:GPSLongitude, signed decimal
    degrees, numbers (the empty text in 46 rows, a 0 in 190)."""

    def test_the_signed_pair_is_read(self):
        found = meta_of(**{"Composite:GPSLatitude": -33.8688, "Composite:GPSLongitude": 151.2093})
        self.assertEqual((-33.8688, 151.2093), (found.latitude, found.longitude))
        west = meta_of(**{"Composite:GPSLatitude": 48.85, "Composite:GPSLongitude": -122.4})
        self.assertEqual((48.85, -122.4), (west.latitude, west.longitude))

    def test_a_whole_degree_is_a_float(self):
        found = meta_of(**{"Composite:GPSLatitude": 48, "Composite:GPSLongitude": -122})
        self.assertEqual((48.0, -122.0), (found.latitude, found.longitude))
        self.assertIsInstance(found.latitude, float)

    def test_the_exif_pair_alone_is_not_read_because_it_has_no_sign(self):
        found = meta_of(**{"EXIF:GPSLatitude": 48.85, "EXIF:GPSLongitude": 122.4})
        self.assertEqual((None, None), (found.latitude, found.longitude))

    def test_one_coordinate_alone_is_no_place(self):
        """64 rows of photo_index hold a longitude and no latitude."""
        for fields in ({"Composite:GPSLongitude": 151.2}, {"Composite:GPSLatitude": 12.5},
                       {"Composite:GPSLatitude": "", "Composite:GPSLongitude": 151.2}):
            found = meta_of(**fields)
            self.assertEqual((None, None), (found.latitude, found.longitude), fields)

    def test_the_pair_a_camera_without_a_fix_writes_is_no_place(self):
        found = meta_of(**{"Composite:GPSLatitude": 0, "Composite:GPSLongitude": 0})
        self.assertEqual((None, None), (found.latitude, found.longitude))

    def test_a_zero_on_one_axis_is_a_place(self):
        found = meta_of(**{"Composite:GPSLatitude": 0, "Composite:GPSLongitude": 36.8})
        self.assertEqual((0.0, 36.8), (found.latitude, found.longitude))

    def test_text_that_is_a_number_is_read_and_other_text_is_not(self):
        found = meta_of(**{"Composite:GPSLatitude": "48.85", "Composite:GPSLongitude": "-122.4"})
        self.assertEqual((48.85, -122.4), (found.latitude, found.longitude))
        for bad in ("", "north", "nan", "inf", float("nan"), float("inf"), True, [1, 2]):
            found = meta_of(**{"Composite:GPSLatitude": bad, "Composite:GPSLongitude": 10.0})
            self.assertEqual((None, None), (found.latitude, found.longitude), bad)

    def test_a_coordinate_out_of_range_is_no_place(self):
        for latitude, longitude in ((91, 10), (-91, 10), (10, 181), (10, -181)):
            found = meta_of(**{"Composite:GPSLatitude": latitude, "Composite:GPSLongitude": longitude})
            self.assertEqual((None, None), (found.latitude, found.longitude), (latitude, longitude))


class WhatIsNotThere(unittest.TestCase):
    def test_a_photo_with_none_of_it_is_empty(self):
        self.assertEqual(photo_meta.EMPTY, meta_of(**{"XMP:Subject": ["Trips/Coast"]}))
        self.assertEqual(photo_meta.EMPTY, meta_of())

    def test_a_row_never_read_or_damaged_is_empty(self):
        for raw in ({}, None, [], "text", 7):
            self.assertEqual(photo_meta.EMPTY, photo_meta.extract(raw), repr(raw))

    def test_the_json_of_a_row_is_read_and_damaged_json_is_empty(self):
        self.assertEqual(4, photo_meta.from_json('{"XMP:Rating": 4}').rating)
        for text in (None, "", "{not json", "[1]", "null"):
            self.assertEqual(photo_meta.EMPTY, photo_meta.from_json(text), repr(text))

    def test_one_bad_value_does_not_cost_the_photo_the_others(self):
        found = meta_of(**{"XMP:Rating": "lots", "EXIF:Make": "Harbourlight", "Composite:GPSLatitude": 200,
                           "Composite:GPSLongitude": 10})
        self.assertEqual((None, "Harbourlight", None, None), (found.rating, found.make, found.latitude, found.width))


class TheFoldersOfRowForms(unittest.TestCase):
    """paths.row_parent and paths.row_name: the folder a row-form path is in, without the machine's
    map, for a library that holds a root and one that holds none."""

    def test_a_rooted_row_is_in_the_folder_above_it_up_to_the_root(self):
        self.assertEqual("@pictures/2024/Coast", paths.row_parent("@pictures/2024/Coast/IMG_1.jpg"))
        self.assertEqual("@pictures/2024", paths.row_parent("@pictures/2024/Coast"))
        self.assertEqual("@pictures", paths.row_parent("@pictures/2024"))
        self.assertIsNone(paths.row_parent("@pictures"), "the root is a top")

    def test_a_native_row_is_in_its_dirname_up_to_the_drive_or_the_share(self):
        sep = os.sep
        root = "C:" + sep if os.name == "nt" else sep
        deep = root + sep.join(["Photos", "2024", "a.jpg"])
        self.assertEqual(root + sep.join(["Photos", "2024"]), paths.row_parent(deep))
        self.assertEqual(root + "Photos", paths.row_parent(root + sep.join(["Photos", "2024"])))
        self.assertEqual(root, paths.row_parent(root + "Photos"))
        self.assertIsNone(paths.row_parent(root))

    @unittest.skipUnless(os.name == "nt", "a share is a Windows spelling")
    def test_a_share_is_a_top(self):
        self.assertEqual("\\\\server\\share\\Pics", paths.row_parent("\\\\server\\share\\Pics\\a.jpg"))
        self.assertEqual("\\\\server\\share\\", paths.row_parent("\\\\server\\share\\Pics"))
        self.assertIsNone(paths.row_parent("\\\\server\\share\\"))

    def test_a_name_with_no_folder_is_in_none(self):
        self.assertIsNone(paths.row_parent("a.jpg"))
        self.assertIsNone(paths.row_parent(""))
        self.assertIsNone(paths.row_parent(None))

    def test_the_name_of_a_folder_is_its_last_name(self):
        self.assertEqual("Coast", paths.row_name("@pictures/2024/Coast"))
        self.assertEqual("pictures", paths.row_name("@pictures"))
        self.assertEqual("Coast", paths.row_name(os.sep.join(["", "Photos", "Coast"])))
        self.assertEqual("", paths.row_name(""))
        if os.name == "nt":
            self.assertEqual("C:\\", paths.row_name("C:\\"), "a drive is named as it is")
            self.assertEqual("\\\\server\\share\\", paths.row_name("\\\\server\\share\\"))


if __name__ == "__main__":
    unittest.main()
