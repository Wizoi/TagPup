"""Text goes to ExifTool and comes back as the same text.

pyexiftool encodes every argument and decodes every answer with the locale's code
page unless told otherwise -- cp1252 on this machine -- while ExifTool reads and
writes UTF-8. So a keyword written as "Zoë" reached the file as "Zo?" (ExifTool
replaces the byte it cannot read), a caption the file held correctly came back as
"Ã¼" where it said "ü" and was stored in the index that way, and a photo whose name
has a character cp1252 lacks could not be named to ExifTool at all.

Everything here runs on a JPEG made for the test, never a real photo.
"""
import contextlib
import os
import shutil
import sys
import tempfile
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tagpup.core import processes  # noqa: E402
import own_home  # noqa: E402
from tagpup.core import fields  # noqa: E402
from tagpup.files import bake, field_values  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import file_changes  # noqa: E402
import test_a_file_holds_what_exiftool_kept as kept  # noqa: E402


#: Where the machine has ExifTool; the checkout's settings are not read.
EXIFTOOL = own_home.installed_exiftool()

NAME = "Zoë Marchetti"            # in cp1252, but not ASCII
OUTSIDE_CP1252 = "Ωmega Relay"      # Greek capital omega: not in cp1252 at all


def _jpeg(folder, name):
    from PIL import Image
    path = os.path.join(folder, name)
    Image.new("RGB", (8, 8), (200, 100, 50)).save(path, "JPEG")
    return path


@unittest.skipUnless(EXIFTOOL, "ExifTool is not installed")
class TestNonAsciiSurvivesTheRoundTrip(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tagpup_exiftool_encoding_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.et = ExifToolSession(executable=EXIFTOOL, timeout=60)
        self.addCleanup(self._stop)

    def _stop(self):
        if self.et.running:
            self.et.terminate()

    def file_holds(self, path, tag):
        """The tag's bytes as they are in the file, read by a separate ExifTool."""
        out = processes.run([EXIFTOOL, "-charset", "filename=utf8", "-b", "-" + tag, path],
                             capture_output=True, timeout=60)
        return out.stdout

    def test_a_keyword_is_written_to_the_file_as_utf8(self):
        photo = _jpeg(self.dir, "plain.jpg")
        self.et.set_tags([photo], tags={"XMP:Subject": [NAME]}, params=["-overwrite_original"])
        self.assertEqual(self.file_holds(photo, "XMP:Subject"), NAME.encode("utf-8"))

    def test_a_keyword_reads_back_as_written(self):
        photo = _jpeg(self.dir, "plain.jpg")
        self.et.set_tags([photo], tags={"XMP:Subject": [NAME], "IPTC:ObjectName": NAME},
                         params=["-overwrite_original", "-IPTC:CodedCharacterSet=UTF8"])
        row = self.et.get_tags([photo], tags=["XMP:Subject", "IPTC:ObjectName"])[0]
        self.assertEqual(row.get("XMP:Subject"), NAME)
        self.assertEqual(row.get("IPTC:ObjectName"), NAME)

    def test_a_value_the_file_already_holds_is_not_garbled(self):
        # Written the way another program would, from a UTF-8 argument file, so only
        # the reading is under test.
        photo = _jpeg(self.dir, "plain.jpg")
        args = os.path.join(self.dir, "args.txt")
        with open(args, "w", encoding="utf-8", newline="\n") as f:
            f.write("-overwrite_original\n-XMP:Description=%s\n%s\n" % (NAME, photo))
        processes.run([EXIFTOOL, "-@", args], capture_output=True, timeout=60, check=True)
        self.assertEqual(self.file_holds(photo, "XMP:Description"), NAME.encode("utf-8"))
        row = self.et.get_tags([photo], tags=["XMP:Description"])[0]
        self.assertEqual(row.get("XMP:Description"), NAME)

    def test_a_file_name_with_a_character_cp1252_has(self):
        photo = _jpeg(self.dir, NAME + ".jpg")
        row = self.et.get_tags([photo], tags=["File:FileName"])[0]
        self.assertEqual(row.get("File:FileName"), NAME + ".jpg")

    def test_a_file_name_with_a_character_cp1252_lacks(self):
        photo = _jpeg(self.dir, OUTSIDE_CP1252 + ".jpg")
        row = self.et.get_tags([photo], tags=["File:FileName"])[0]
        self.assertEqual(row.get("File:FileName"), OUTSIDE_CP1252 + ".jpg")


BACKSLASH, CR, LF = chr(92), chr(13), chr(10)

#: A caption another program wrote over three lines: an LF, a CRLF, a backslash, a "#" and blanks at a line's edges.
CAPTION = "Harbour at dusk" + LF + "from the pier " + BACKSLASH + " north end " + CR + LF + "#2 of 3  "
#: A keyword holding the same (one of a list: ExifTool takes each as an argument of its own).
KEYWORD = "Places/Pier" + LF + "North " + BACKSLASH + " End"


class ATextHoldingALineBreakReachesTheFileWhole(kept.BakeCase):
    """pyexiftool gives ExifTool one argument a line: a caption holding a line break was written as its first line and its
    second read as a file name ("File not found"), the save failed half done, and an undo putting one back did the same; a
    region named over two lines was baked over and could not be put back (#1882). ExifToolSession sends such a text as a
    C string (exiftool_session.CSTR). The C string is ExifTool's rewrite's (`kept.rewritten`: a save the own writer
    refuses or whose change does not fit); the own writer's patch gives ExifTool no argument."""

    def held(self, path, names):
        with field_values.opened(EXIFTOOL) as et:
            return et.read_one(path, names)

    def test_a_caption_and_a_keyword_are_saved_read_back_equal_and_undone(self):
        # Both writers of a save: the own writer's patch, which writes the XMP copies alone and leaves the EXIF and
        # IPTC copies holding what they held (fields.XMP_ONLY; DECISIONS 2026-11-03), and ExifTool's rewrite, which
        # writes every copy, each text a C string. The undo puts back what the photo held, by either.
        left = {field for leaves in fields.XMP_ONLY.values() for field in leaves}
        for patched in (True, False):
            with self.subTest(patched=patched), contextlib.nullcontext() if patched else kept.rewritten():
                a = self.make_read("patched.jpg" if patched else "rewritten.jpg")
                after = {field: [CAPTION] for field in fields.caption_fields(CAPTION)}
                after["XMP:Subject"] = ["Beach", KEYWORD]
                before = self.held(a, list(after))
                result = file_changes.write_fields(self.library, "caption", EXIFTOOL, [a], list(after),
                                                   lambda path, held: file_changes.Plan(after=after))
                self.assertEqual((1, []), (result.changed, result.errors))
                held = {field: before[field] if patched and field in left else value for field, value in after.items()}
                self.assertEqual(held, self.held(a, list(after)))
                undone = self.undo(result.details["change"])
                self.assertEqual((1, []), (undone.changed, undone.errors))
                self.assertEqual(before, self.held(a, list(after)))

    def test_a_dollar_reaches_the_file_as_it_is_and_beside_a_line_break_is_refused(self):
        # A C string gives a "$" or "@" a backslash of its own (exiftool_session.NOT_IN_CSTR): 7 live captions hold a "$".
        # ExifTool's argument, so the rewrite's: the own writer's patch gives ExifTool none, and is not refused.
        a = self.make_read("a.jpg")
        forced = kept.rewritten()
        forced.start()
        self.addCleanup(forced.stop)
        for caption, changed in (("Fare $4 @ the pier", 1), ("Fare $4" + LF + "return", 0)):
            with self.subTest(caption=caption):
                after = {field: [caption] for field in fields.caption_fields(caption)}
                before, written, last = self.held(a, list(after)), os.stat(a).st_mtime_ns, self.last_change()
                result = file_changes.write_fields(self.library, "caption", EXIFTOOL, [a], list(after),
                                                   lambda path, held: file_changes.Plan(after=after))
                self.assertEqual(changed, result.changed, result.errors)
                if changed:
                    self.assertEqual(after, self.held(a, list(after)))
                else:
                    self.assertIn("cannot be given to ExifTool", str(result.errors))
                    self.assertEqual((None, last), (result.details["change"], self.last_change()))
                    self.assertEqual((before, written), (self.held(a, list(after)), os.stat(a).st_mtime_ns))

    def test_a_bake_over_a_region_named_over_two_lines_is_undone_equal(self):
        a = self.make_read("a.jpg")
        name = "Ash" + CR + LF + "Corrin " + BACKSLASH + " Vale"
        before = self.seed_regions(a, "<mwg-rs:Name>Ash&#13;" + LF + "Corrin " + BACKSLASH + " Vale</mwg-rs:Name>")
        self.assertEqual(name, before["XMP:RegionInfo"]["RegionList"][0]["Name"], "not seeded as meant")
        values = self.values(2)
        change = self.bake([a], values).details["change"]
        self.assert_baked(a, values, 2)
        undone = self.undo(change)
        self.assertEqual((1, []), (undone.changed, undone.errors))
        self.assertEqual(["undone"], self.states(change))
        self.assertEqual(before, self.regions_of(a))
        self.assertEqual(bake.NOT_BAKED, self.bake_of(a).reason)

    def test_a_bake_over_a_region_named_with_a_dollar_and_a_line_break_is_written_and_its_undo_refused(self):
        # A coverage test: pins behaviour that already holds (no live text holds both; ExifToolSession refuses the pair).
        a = self.make_read("a.jpg")
        self.seed_regions(a, "<mwg-rs:Name>Fare $4&#10;Ash</mwg-rs:Name>")
        values = self.values(2)
        change = self.bake([a], values).details["change"]
        self.assert_baked(a, values, 2)
        written = os.stat(a).st_mtime_ns
        undone = self.undo(change)
        self.assertEqual(0, undone.changed)
        self.assertIn("cannot be given to ExifTool", str(undone.errors))
        self.assertEqual(["conflict"], self.states(change))
        self.assertEqual(written, os.stat(a).st_mtime_ns)
        self.assert_baked(a, values, 2)


if __name__ == "__main__":
    unittest.main()
