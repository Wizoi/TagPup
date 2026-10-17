"""tagpup.core.photo_meta: the camera and the lens a photo's raw metadata names, read one way, from rows made as the
indexer makes them (tests/photo_rows.as_read: each field under its group's name and again bare), never from a shape typed
here.

The look at photo_index's 68,466 rows that decided the rules is in the module's docstring.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import photo_rows  # noqa: E402

from tagpup.core import paths, photo_meta  # noqa: E402

PHOTO = os.path.join(os.path.sep, "photos", "2024", "IMG_0001.jpg")


class TheCamera(unittest.TestCase):
    def test_make_and_model_are_read_trimmed(self):
        self.assertEqual("Harbourlight HL 400", gear_of(**{"EXIF:Make": "  Harbourlight ", "EXIF:Model": "HL 400\x00\x00"}).camera)

    def test_exif_comes_before_xmp(self):
        found = gear_of(**{"EXIF:Make": "Harbourlight", "XMP:Make": "Other", "XMP:Model": "Only XMP"})
        self.assertEqual("Harbourlight Only XMP", found.camera)

    def test_an_empty_make_is_passed_over(self):
        self.assertEqual("Harbourlight", gear_of(**{"EXIF:Make": "", "XMP:Make": "Harbourlight"}).camera)
        self.assertIsNone(gear_of(**{"EXIF:Make": "  "}).camera)

    def test_a_make_that_is_not_text_is_left_out(self):
        self.assertIsNone(gear_of(**{"EXIF:Make": 42}).camera)
        self.assertIsNone(photo_meta.gear({"Make": ["a", "b"]}).camera, "two values are no make")
        self.assertEqual("Harbourlight", photo_meta.gear({"Make": ["Harbourlight"]}).camera)


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

    def test_a_lens_id_that_is_hex_bytes_is_no_name(self):
        # Without print conversion a Nikon's Composite:LensID is the lens's eight bytes (docs/findings.md, #1020).
        self.assertIsNone(gear_of(**{"Composite:LensID": "A1 40 18 37 2C 34 A4 06"}).lens)
        self.assertIsNone(gear_of(**{"LensID": "137"}).lens)
        self.assertEqual("A name", gear_of(**{"Composite:LensID": "A1 40 18 37 2C 34 A4 06", "EXIF:LensModel": "A name"}).lens)
        # A hex-looking name in the lens's own field is still the lens's name.
        self.assertEqual("ADE", gear_of(**{"EXIF:LensModel": "ADE"}).lens)
        self.assertEqual("Pixel 8 Pro back camera 6.9mm f/1.68",
                         gear_of(**{"Composite:LensID": "Pixel 8 Pro back camera 6.9mm f/1.68"}).lens)

    def test_the_lens_maker_goes_before_a_name_that_lacks_it(self):
        self.assertEqual("Tidewater 24-70mm F2.8",
                         gear_of(**{"EXIF:LensModel": "24-70mm F2.8", "EXIF:LensMake": "Tidewater"}).lens)
        self.assertEqual("Tidewater 24-70mm F2.8",
                         gear_of(**{"EXIF:LensModel": "Tidewater 24-70mm F2.8", "EXIF:LensMake": "tidewater"}).lens)
        self.assertIsNone(gear_of(**{"EXIF:LensMake": "Tidewater"}).lens, "a maker alone is no lens")

    def test_unicode_is_kept(self):
        self.assertEqual("Objectif Élan 35mm", gear_of(**{"EXIF:LensModel": "Objectif Élan 35mm"}).lens)


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
