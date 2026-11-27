"""A clip whose XMP packet Adobe wrote, its text properties attributes of rdf:Description (the two shapes of the live
clips' packets, docs/domains/files.md, "The XMP packet editor"), edited by tagpup.files.xmp_packet, placed in the clip
by tagpup.files.clip_patch and read back by ExifTool (its -validate too) and ffprobe (every frame decoded), in a home of
the test's own. ExifTool reads an attribute as the editor reads it before the edit, and what the editor wrote after it.
Skipped, saying so, on a machine without ExifTool or ffmpeg. The values are fictional.
"""
import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus  # noqa: E402
import own_home  # noqa: E402
import test_xmp_packet_edits_what_it_owns as shapes  # noqa: E402
from tagpup import config  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.files import clip_patch, containers, xmp_packet  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
TOOLS = config.locate_ffmpeg()
#: ExifTool's tag for each property the edits here change, read with -n.
TAGS = {"dc:subject": "XMP:Subject", "dc:description": "XMP:Description", "xmp:CreateDate": "XMP:CreateDate",
        "exif:DateTimeOriginal": "XMP:DateTimeOriginal", "tiff:Orientation": "XMP:Orientation",
        "xmpMM:DocumentID": "XMP:DocumentID"}


def as_exiftool_reads(name, value):
    """`value` of property `name` as ExifTool answers it: a date with colons in its day and a space before its time."""
    if name in ("xmp:CreateDate", "exif:DateTimeOriginal"):
        return value[:10].replace("-", ":") + " " + value[11:]
    return value


@unittest.skipIf(EXIFTOOL is None or TOOLS.problem, "ExifTool or ffmpeg is not installed on this machine")
class AnAdobeClipEditedAndReadBack(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.folder = own_home.for_class(cls, "tagpup_adobe_clip_").root
        cls.made = os.path.join(cls.folder, "made.mp4")
        corpus.clip(cls.made, ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10",
                               "-t", "1", "-pix_fmt", "yuv420p"], TOOLS.ffmpeg)

    def placed(self, path, packet, how):
        with open(path, "rb") as handle:
            found = clip_patch.patch(containers.read(handle), packet)
        self.assertEqual(how, found.how)
        with open(path, "r+b") as handle:
            for at, written in found.ranges:
                handle.seek(at)
                handle.write(written)
        with open(path, "rb") as handle:
            (_, box), = containers.read(handle).xmp
            handle.seek(box.payload)
            held = handle.read(box.end - box.payload)
        self.assertEqual((packet, b""), (held[:len(packet)], held[len(packet):].strip()))

    def read_back(self, path, values):
        """ExifTool's reading of each of `values` ({property: value}) and its -validate; ffprobe's XMP and frames."""
        with ExifToolSession(executable=EXIFTOOL) as session:
            held = session.get_tags([path], tags=[TAGS[name] for name in values], params=["-a", "-n"])[0]
            checked = json.loads(session.execute("-validate", "-warning", "-a", "-j", path))[0]
        for name, value in values.items():
            found = held.get(TAGS[name])
            if isinstance(value, list):
                found = found if isinstance(found, list) else [found]
            self.assertEqual(as_exiftool_reads(name, value), str(found) if isinstance(value, str) else found, name)
        self.assertEqual("0 0 0", checked.get("ExifTool:Validate"), checked)
        probe = processes.run([TOOLS.ffprobe, "-v", "error", "-export_xmp", "1", "-count_frames", "-show_entries",
                               "format_tags=xmp:stream=nb_read_frames", "-of", "json", path],
                              capture_output=True, timeout=60)
        self.assertEqual(b"", probe.stderr)
        out = json.loads(probe.stdout)
        self.assertEqual(["10"], [stream["nb_read_frames"] for stream in out["streams"]])
        return out["format"]["tags"]["xmp"]

    def clip_holding(self, packet):
        path = os.path.join(self.folder, self.id().rsplit(".", 1)[-1] + ".mp4")
        shutil.copyfile(self.made, path)
        self.placed(path, packet, clip_patch.APPENDED)
        return path

    def test_premieres_packet_its_date_orientation_and_a_keyword_changed_in_place(self):
        path = self.clip_holding(corpus.PREMIERE)
        self.read_back(path, xmp_packet.read(corpus.PREMIERE))
        changes = {"dc:subject": ["Beach", "Wren Ashcombe"], "dc:description": "Kites & <dunes>",
                   "xmp:CreateDate": "2018-06-03T08:00:00Z", "tiff:Orientation": "6"}
        edited = xmp_packet.edit(corpus.PREMIERE, changes)
        self.assertFalse(edited.length_changed)
        self.placed(path, edited.body, clip_patch.IN_PLACE)
        xmp = self.read_back(path, {**changes, "xmpMM:DocumentID": "xmp.did:7c2d0e4a"})
        self.assertIn('xmp:CreateDate="2018-06-03T08:00:00Z"', xmp)
        self.assertIn("<rdf:li>Wren Ashcombe</rdf:li>", xmp)

    def test_the_bare_packet_opened_for_a_keyword_and_its_date_cleared(self):
        path = self.clip_holding(shapes.BARE)
        self.read_back(path, xmp_packet.read(shapes.BARE))
        changes = {"dc:subject": ["Pier"], "dc:description": "The pier at noon",
                   "exif:DateTimeOriginal": "2017-08-19T10:00:00+01:00"}
        edited = xmp_packet.edit(shapes.BARE, changes, grow=True)
        self.assertTrue(edited.length_changed)
        self.placed(path, edited.body, clip_patch.GROWN)
        xmp = self.read_back(path, changes)
        self.assertIn("</rdf:Description>", xmp)
        cleared = xmp_packet.edit(edited.body, {"exif:DateTimeOriginal": None}, grow=True)
        self.placed(path, cleared.body, clip_patch.IN_PLACE)
        self.read_back(path, {"dc:subject": ["Pier"]})
        with ExifToolSession(executable=EXIFTOOL) as session:
            self.assertNotIn("XMP:DateTimeOriginal", session.get_tags([path], tags=["XMP:DateTimeOriginal"])[0])


if __name__ == "__main__":
    unittest.main()
