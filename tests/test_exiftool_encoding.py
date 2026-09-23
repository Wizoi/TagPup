"""Text goes to ExifTool and comes back as the same text.

pyexiftool encodes every argument and decodes every answer with the locale's code
page unless told otherwise -- cp1252 on this machine -- while ExifTool reads and
writes UTF-8. So a keyword written as "Zoë" reached the file as "Zo?" (ExifTool
replaces the byte it cannot read), a caption the file held correctly came back as
"Ã¼" where it said "ü" and was stored in the index that way, and a photo whose name
has a character cp1252 lacks could not be named to ExifTool at all.

Everything here runs on a JPEG made for the test, never a real photo.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from exiftool_session import ExifToolSession  # noqa: E402


def _exiftool_path():
    import configparser

    config = configparser.ConfigParser(interpolation=None)
    config_path = os.path.join(WORKSPACE_DIR, "config.ini")
    default = os.path.join(
        os.environ.get("USERPROFILE", ""), r"AppData\Local\Programs\ExifTool\exiftool.exe"
    )
    if os.path.exists(config_path):
        config.read(config_path, encoding="utf-8")
        default = os.path.expandvars(config.get("paths", "exiftool", fallback=default))
    return default if os.path.exists(default) else None


EXIFTOOL = _exiftool_path()

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
        out = subprocess.run([EXIFTOOL, "-charset", "filename=utf8", "-b", "-" + tag, path],
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
        subprocess.run([EXIFTOOL, "-@", args], capture_output=True, timeout=60, check=True)
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


if __name__ == "__main__":
    unittest.main()
