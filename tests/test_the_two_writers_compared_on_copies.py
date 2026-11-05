"""`metadata-check`: TagPup's own JPEG writer and ExifTool's, each given the app's seven writes on fresh copies of a
library's photos in a sandbox, compared by what each copy holds (tagpup.services.metadata_check; scenario F1;
docs/PLAN_OWN_METADATA.md, M4 step 4c).

Photos of the shapes the writers part on agree: simple ones, where a keyword, person or caption added is the own
writer's patch of the XMP alone and ExifTool's rewrite (a disagreement, counted by its direction, never a difference);
one with two APP13 segments (both rewrite it); one whose packet the XMP editor refuses (its caption cleared is
ExifTool's patch and the own writer's rewrite); one whose packet holds tiff:Orientation and whose EXIF holds an IFD1
Orientation; one another program wrote last, holding an IPTCDigest (the XMP toolkit differs by design, KNOWN; the
digest does not). A photo gone from its folder part way through the run is counted gone and the run goes on; a second
run while one holds the sandbox is refused, the first's sandbox untouched; a run interrupted (Ctrl+C) stops, says so
(exit status 2) and leaves no sandbox, and a sandbox a killed run left is removed by the next; a disk without room for
the copies stops the run before the first; a difference is exit status 1, counts only unless --reveal. Nothing of the
library or its photos is written.

Real JPEGs written by the real ExifTool in a home of the test's own (test_file_journal.FilesCase); the sandbox is in the
home's temporary folder. Breaks that fail these, each tried on a copy of the code (2026-11-05): the whole read in the
writers' session (its -G winning over -G0:1) fails test_a_photo_whose_thumbnail_holds_an_orientation_turns_alike, by the
original's read naming no IFD0 or IFD1 Orientation (the comparison itself fails only through EXIF:ThumbnailOffset, which
KNOWN names by its IFD1 group: both writers leave IFD0's and IFD1's Orientation alike); KNOWN without the thumbnail's
offset fails it too; KNOWN without the XMP toolkit fails
test_known_tags_differ_by_design_and_a_difference_is_exit_status_one_naming_nothing; a stale tag compared with
ExifTool's side and not the original's fails test_simple_photos_agree_and_nothing_of_the_library_is_written; a
disagreement counted as a difference fails test_a_packet_the_editor_refuses_is_a_disagreement_not_a_difference; _copied
reading a photo whose stamp says it is gone fails test_a_photo_gone_part_way_is_counted_and_the_run_goes_on; sandbox()
not taking its lock fails test_a_second_run_is_refused_while_one_holds_the_sandbox; a sandbox a killed run left not
removed first, or Ctrl+C not caught, fails test_an_interrupted_run_leaves_no_sandbox; no disk check fails
test_a_disk_without_room_stops_the_run_before_a_copy; the CLI exiting 0 on a difference fails
test_known_tags_differ_by_design_and_a_difference_is_exit_status_one_naming_nothing.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_photo_patched_in_place import PRINT_FLAGS, read  # noqa: E402
from test_file_journal import EXIFTOOL, FilesCase, field_of, requires_exiftool, write_outside  # noqa: E402

from click.testing import CliRunner  # noqa: E402

from tagpup.core import byte_lock  # noqa: E402
from tagpup.core.result import Refused  # noqa: E402
from tagpup.files import containers, field_values, xmp_packet  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import metadata_check  # noqa: E402
from tagpup_cli import cli  # noqa: E402

#: Every operation of a photo holding keywords, a caption and a Date Taken: written, none held already.
ALL = {name for name, _planner in metadata_check.OPERATIONS}


@requires_exiftool
class TheTwoWritersComparedOnCopies(FilesCase):
    def setUp(self):
        super().setUp()
        self.temp = os.path.join(self.home.root, "Temp")
        os.makedirs(self.temp)
        patched = mock.patch.object(tempfile, "tempdir", self.temp)
        patched.start()
        self.addCleanup(patched.stop)
        self.sandbox = os.path.join(self.temp, metadata_check.SANDBOX)

    def photo(self, name, **kwargs):
        return self.make(name, taken="2024:07:04 10:00:00", caption="Boats at dusk", **kwargs)

    def check(self, **kwargs):
        found = metadata_check.check(self.library, EXIFTOOL, **kwargs)
        self.assertFalse(os.path.exists(self.sandbox), "the sandbox was left")
        return found

    def assert_agreed(self, found, photos):
        self.assertEqual(({}, None, {"read": photos}), (found["differences"], found["stopped"], found["looked"]),
                         "\n".join(metadata_check.lines(found)))

    def test_simple_photos_agree_and_nothing_of_the_library_is_written(self):
        made = [self.photo("a.jpg"), self.photo("b.jpg", tags=("Beach", "People/Rowan Thackeray")),
                self.make("c.jpg", tags=())]
        held = {path: read(path) for path in made}
        library = read(self.library.path)
        found = self.check()
        self.assert_agreed(found, 3)
        self.assertEqual((3, 3), (found["jpegs"], found["sampled"]))
        self.assertEqual(ALL, set(found["how"]))
        self.assertEqual({"last keyword taken off": 1, "caption cleared": 1, "Date Taken shifted": 1}, found["skipped"],
                         "c.jpg holds no keyword to take off, no caption, no Date Taken")
        # A keyword, person or caption added is the own writer's patch of the XMP alone where ExifTool's header writer,
        # writing every copy, rewrites (XPKeywords takes a new entry in IFD0): counted by its direction, no difference.
        self.assertEqual({"rewrite, own patch": 2, "rewrite, own rewrite": 1}, found["how"]["keyword added"])
        self.assertIn("keyword added: 3 written, 1 agreed on patch or rewrite", "\n".join(metadata_check.lines(found)))
        self.assertEqual((held, library), ({path: read(path) for path in made}, read(self.library.path)))

    def test_a_photo_with_two_app13_segments_is_rewritten_by_both(self):
        a = self.photo("a.jpg")
        held = read(a)
        iptc = containers.read(io.BytesIO(held)).of_kind("iptc")[0]
        body = b"Photoshop 3.0\0" + PRINT_FLAGS
        with open(a, "wb") as handle:
            handle.write(held[:iptc.end] + b"\xff\xed" + (len(body) + 2).to_bytes(2, "big") + body + held[iptc.end:])
        found = self.check()
        self.assert_agreed(found, 1)
        self.assertEqual({"rewrite, own rewrite": 1}, found["how"]["keyword added"])

    def test_a_packet_the_editor_refuses_is_a_disagreement_not_a_difference(self):
        a = self.photo("a.jpg", tags=("Beach",))
        held = read(a)
        xmp = containers.read(io.BytesIO(held)).of_kind("xmp")[0]
        segment = held[xmp.offset:xmp.end]
        element = (b"<dc:subject>\n   <rdf:Bag>\n    <rdf:li>Beach</rdf:li>\n   </rdf:Bag>\n  </dc:subject>\n")
        self.assertIn(element, segment, "ExifTool lays the packet out otherwise now")
        about = segment.index(b"rdf:about=''") + len(b"rdf:about=''")
        segment = segment[:about] + b" dc:subject='Beach'" + segment[about:].replace(element, b"")
        end = segment.rindex(b"<?xpacket end=")
        segment = segment[:end] + b" " * (len(element) - len(b" dc:subject='Beach'")) + segment[end:]
        with open(a, "wb") as handle:
            handle.write(held[:xmp.offset] + segment + held[xmp.end:])
        self.assertIsInstance(xmp_packet.read(segment[4 + len(containers.XMP_HEAD):]), containers.Refusal)
        self.assertEqual("Beach", field_of(a, "XMP:Subject"))
        found = self.check()
        self.assert_agreed(found, 1)
        # The caption cleared: ExifTool's header writer patches it, the own writer, refusing the packet, rewrites.
        self.assertEqual({"patch, own rewrite": 1}, found["how"]["caption cleared"])
        self.assertIn("caption cleared: 1 written, 0 agreed on patch or rewrite, 0 held it already (ExifTool patch, own "
                      "rewrite 1)", "\n".join(metadata_check.lines(found)))

    def test_a_photo_whose_thumbnail_holds_an_orientation_turns_alike(self):
        from PIL import Image

        a = self.photo("a.jpg")
        thumb = os.path.join(self.home.root, "thumb.jpg")
        Image.new("RGB", (8, 6), (20, 40, 60)).save(thumb, "JPEG")
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.execute("-ThumbnailImage<=" + thumb, "-IFD0:Orientation#=1", "-XMP-tiff:Orientation#=1",
                       "-overwrite_original", a)
            et.execute("-IFD1:Orientation#=1", "-overwrite_original", a)
        real, reads = metadata_check._whole, []

        def whole(*args):
            reads.append(real(*args))
            return reads[-1]
        with mock.patch.object(metadata_check, "_whole", whole):
            found = self.check()
        # The whole read tells IFD0's Orientation from IFD1's, so a side that turned one and not the other differs.
        original = next(iter(reads[0].values()))
        self.assertEqual((1, 1), (original.get("EXIF:IFD0:Orientation"), original.get("EXIF:IFD1:Orientation")),
                         sorted(original))
        self.assert_agreed(found, 1)
        self.assertEqual(1, sum(found["how"]["turned right"].values()))

    def test_a_photo_gone_part_way_is_counted_and_the_run_goes_on(self):
        a, b, c = self.photo("a.jpg"), self.photo("b.jpg"), self.photo("c.jpg")
        real, gone = field_values.write, []

        def write(*args, **kwargs):
            if not gone:
                os.remove(b)                 # another program deletes it while a.jpg is compared
                gone.append(b)
            return real(*args, **kwargs)
        with mock.patch.object(field_values, "write", write):
            found = self.check()
        self.assertEqual(({}, None, {"read": 2, "gone": 1}), (found["differences"], found["stopped"], found["looked"]))
        self.assertTrue(os.path.exists(a) and os.path.exists(c))

    def test_a_second_run_is_refused_while_one_holds_the_sandbox(self):
        self.photo("a.jpg")
        os.makedirs(os.path.join(self.sandbox, "photo"))       # the running one's
        held = byte_lock.Lock(self.sandbox + ".lock")
        self.assertTrue(held.acquire())
        try:
            with self.assertRaises(Refused) as refused:
                metadata_check.check(self.library, EXIFTOOL)
            result = CliRunner().invoke(cli, ["--db", self.library.path, "metadata-check"])
        finally:
            held.release()
        self.assertEqual(metadata_check.TWO_AT_ONCE, str(refused.exception))
        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn(metadata_check.TWO_AT_ONCE, result.output)
        self.assertTrue(os.path.isdir(os.path.join(self.sandbox, "photo")), "the running one's sandbox was touched")

    def test_an_interrupted_run_leaves_no_sandbox(self):
        self.photo("a.jpg")
        self.photo("b.jpg")
        os.makedirs(os.path.join(self.sandbox, "photo"))       # left by a run that was killed
        with open(os.path.join(self.sandbox, "photo", "original.jpg"), "wb") as handle:
            handle.write(b"left")
        real, written = field_values.write, []

        def write(*args, **kwargs):
            written.append(args[1])
            if len(written) == 3:
                raise KeyboardInterrupt
            return real(*args, **kwargs)
        with mock.patch.object(field_values, "write", write):
            result = CliRunner().invoke(cli, ["--db", self.library.path, "metadata-check"])
        self.assertEqual(2, result.exit_code, result.output)
        self.assertIn("stopped: %s" % metadata_check.INTERRUPTED, result.output)
        self.assertFalse(os.path.exists(self.sandbox), "the sandbox was left")
        self.assertTrue(all(path.startswith(self.sandbox) for path in written), "a write outside the sandbox")

    def test_a_disk_without_room_stops_the_run_before_a_copy(self):
        self.photo("a.jpg")
        free = shutil.disk_usage(self.temp).free
        with mock.patch.object(metadata_check, "MARGIN", free), \
                mock.patch.object(field_values, "write", side_effect=AssertionError("a copy was written")):
            found = self.check()
        self.assertEqual(({}, {}), (found["how"], found["looked"]))
        self.assertTrue(found["stopped"].startswith("the sandbox's disk has less than"), found["stopped"])

    def test_known_tags_differ_by_design_and_a_difference_is_exit_status_one_naming_nothing(self):
        # A photo another program wrote last (its packet's toolkit) holding an IPTCDigest, as Photoshop's and WLPG's do:
        # ExifTool's rewrite names itself, the own writer's patch leaves the toolkit (KNOWN); neither updates the digest.
        # Allowed, they agree; not allowed, the toolkit is a difference.
        a = self.photo("a.jpg", tags=("Beach", "People/Rowan Thackeray"))
        write_outside(a, {"Photoshop:IPTCDigest": "new", "XMP-x:XMPToolkit": "Adobe XMP Core 5.6-c140 79.160451"})
        self.assert_agreed(self.check(), 1)
        with mock.patch.object(metadata_check, "KNOWN", frozenset()):
            result = CliRunner().invoke(cli, ["--db", self.library.path, "metadata-check"])
            revealed = CliRunner().invoke(cli, ["--db", self.library.path, "metadata-check", "--reveal"])
        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("the sides differ in XMP:XMP-x:XMPToolkit", result.output)
        self.assertNotIn("IPTCDigest", result.output)
        for private in ("Rowan", "a.jpg", "Boats"):
            self.assertNotIn(private, result.output)
        self.assertEqual(1, revealed.exit_code, revealed.output)
        self.assertIn("a.jpg", revealed.output)


if __name__ == "__main__":
    unittest.main()
