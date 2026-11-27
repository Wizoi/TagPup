"""`metadata-check`: TagPup's own JPEG writer and ExifTool's, each given the app's nine writes on fresh copies of a
library's photos in a sandbox, compared by what each copy holds (tagpup.services.metadata_check; scenario F1;
docs/PLAN_OWN_METADATA.md, M4 step 4c).

Photos of the shapes the writers part on agree: simple ones, where a keyword, person or caption added is the own
writer's patch of the XMP alone and ExifTool's rewrite (a disagreement, counted by its direction, never a difference);
one with two APP13 segments (both rewrite it); one whose packet the XMP editor refuses (its caption cleared falls to
ExifTool's rewrite on the own side too: both rewrite, and agree); one whose packet holds tiff:Orientation and whose EXIF holds an IFD1
Orientation; one another program wrote last, holding an IPTCDigest (the XMP toolkit differs by design, KNOWN; the
digest does not); one whose IFD0 is out of order, which ExifTool's write heals and the own patch leaves (the warning was
the original's: no difference). One whose IPTC block lacks its version datasets and holds an empty caption, which
the own writer's patch of the XMP leaves stale and ExifTool's rewrite gives versions and drops the empty caption (no
difference: what the file holds reads the same). A copy the own writer leaves out of order, which ExifTool reads with
every tag the same but warns of (a warning the original lacks), is a difference; so is a caption the own writer puts in
the wrong field, or a keyword it drops; so is a side whose second write of the same values changes the file. A photo gone from its folder part way through the run is counted gone and the run goes on; a second
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
test_known_tags_differ_by_design_and_a_difference_is_exit_status_one_naming_nothing; ExifTool's group set aside
whole (its warning and validation never compared), or -validate not asked, fails
test_a_copy_exiftool_heals_as_it_reads_is_a_difference; its warning compared between the sides alone, and not against
the original's, fails test_a_warning_the_original_held_is_no_difference; the version datasets not set aside,
or a dataset held empty counted against one absent, fails test_an_iptc_block_left_stale_is_no_difference; ExifTool's
validation compared by its counts, or by its first warning alone, fails
test_a_copy_with_fewer_of_the_originals_warnings_is_no_difference; no second write of the same values fails
test_a_second_write_of_the_same_values_that_changes_the_file_is_a_difference; a stale tag compared with
ExifTool's side and not the original's, or a disagreement counted as a difference, fails
test_simple_photos_agree_and_nothing_of_the_library_is_written; shares.read_whole
reading a photo whose stamp says it is gone fails test_a_photo_gone_part_way_is_counted_and_the_run_goes_on; a photo read in one read
fails test_a_photo_larger_than_a_piece_is_read_in_pieces; sandbox()
not taking its lock fails test_a_second_run_is_refused_while_one_holds_the_sandbox; a sandbox a killed run left not
removed first, or Ctrl+C not caught, fails test_an_interrupted_run_leaves_no_sandbox; one folder for every photo,
or the sandbox's removal not tried again, fails test_a_folder_another_program_holds_a_moment_is_not_in_the_way; no disk check fails
test_a_disk_without_room_stops_the_run_before_a_copy; the CLI exiting 0 on a difference fails
test_known_tags_differ_by_design_and_a_difference_is_exit_status_one_naming_nothing.
"""
import io
import json
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
from tagpup.files import containers, field_values, segment_edits, shares, xmp_packet  # noqa: E402
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
        # A keyword, person or caption added is the own writer's patch of the XMP alone where ExifTool's side
        # rewrites: counted by its direction, no difference.
        self.assertEqual({"rewrite, own patch": 2, "rewrite, own rewrite": 1}, found["how"]["keyword added"])
        self.assertEqual(3, sum(found["how"]["preserved name set"].values()), "a name with letters outside ASCII kept")
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

    def test_a_packet_the_editor_refuses_falls_to_exiftools_rewrite_on_both_sides(self):
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
        # The caption cleared: the own writer refuses the packet, so the journaled side falls to ExifTool's rewrite
        # too; the two agree, and the copies read the same (no difference).
        self.assertEqual({"rewrite, own rewrite": 1}, found["how"]["caption cleared"])
        self.assertIn("caption cleared: 1 written, 1 agreed on patch or rewrite, 0 held it already (ExifTool rewrite, own "
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
        real, reads = field_values.Oracle.whole, []

        def whole(*args):
            reads.append(real(*args))
            return reads[-1]
        with mock.patch.object(field_values.Oracle, "whole", whole):
            found = self.check()
        # The whole read tells IFD0's Orientation from IFD1's, so a side that turned one and not the other differs.
        original = next(iter(reads[0].values()))
        self.assertEqual((1, 1), (original.get("EXIF:IFD0:Orientation"), original.get("EXIF:IFD1:Orientation")),
                         sorted(original))
        self.assert_agreed(found, 1)
        self.assertEqual(1, sum(found["how"]["turned right"].values()))

    def test_a_photo_gone_part_way_is_counted_and_the_run_goes_on(self):
        a, b, c = self.photo("a.jpg"), self.photo("b.jpg"), self.photo("c.jpg")
        real, gone = field_values._write, []

        def write(*args, **kwargs):
            if not gone:
                os.remove(b)                 # another program deletes it while a.jpg is compared
                gone.append(b)
            return real(*args, **kwargs)
        with mock.patch.object(field_values, "_write", write):
            found = self.check()
        self.assertEqual(({}, None, {"read": 2, "gone": 1}), (found["differences"], found["stopped"], found["looked"]))
        self.assertTrue(os.path.exists(a) and os.path.exists(c))

    def test_a_photo_larger_than_a_piece_is_read_in_pieces(self):
        # A large photo on a slow share is read a piece at a time, each waited for on its own, never in one read.
        a = self.photo("a.jpg")
        real, asked = shares.read_span, []

        def read_span(path, start, length, stamp):
            asked.append(length)
            return real(path, start, length, stamp)
        with mock.patch.object(shares, "READ_PIECE", 100), mock.patch.object(shares, "read_span", read_span):
            found = self.check()
        self.assert_agreed(found, 1)
        self.assertEqual(-(-os.path.getsize(a) // 100), len(asked))
        self.assertLessEqual(max(asked), 100)

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
        real, written = field_values._write, []

        def write(*args, **kwargs):
            written.append(args[1])
            if len(written) == 3:
                raise KeyboardInterrupt
            return real(*args, **kwargs)
        with mock.patch.object(field_values, "_write", write):
            result = CliRunner().invoke(cli, ["--db", self.library.path, "metadata-check"])
        self.assertEqual(2, result.exit_code, result.output)
        self.assertIn("stopped: %s" % metadata_check.INTERRUPTED, result.output)
        self.assertFalse(os.path.exists(self.sandbox), "the sandbox was left")
        self.assertTrue(all(path.startswith(self.sandbox) for path in written), "a write outside the sandbox")

    def test_a_folder_another_program_holds_a_moment_is_not_in_the_way(self):
        # An antivirus scan holds a copy just written: the first photo's folder is left, and so is the sandbox at the
        # first try of its removal. The next photo has a folder of its own; the sandbox is removed on the second try.
        self.photo("a.jpg")
        self.photo("b.jpg")
        real, held = shutil.rmtree, set()

        def rmtree(path, ignore_errors=False, **kwargs):
            here = os.path.normcase(os.path.abspath(path))
            which = ("sandbox" if here == os.path.normcase(self.sandbox) and os.path.isdir(here) else
                     "photo" if os.path.dirname(here) == os.path.normcase(self.sandbox) else None)
            if which and which not in held:
                held.add(which)
                if ignore_errors:
                    return None
                raise PermissionError(32, "The process cannot access the file because it is being used")
            return real(path, ignore_errors=ignore_errors, **kwargs)
        with mock.patch.object(shutil, "rmtree", rmtree), mock.patch.object(metadata_check, "REMOVAL_RETRY", 0):
            found = self.check()
        self.assert_agreed(found, 2)
        self.assertEqual({"sandbox", "photo"}, held)

    def test_a_disk_without_room_stops_the_run_before_a_copy(self):
        self.photo("a.jpg")
        # More room than any disk has: the free space measured a moment before grows when another program frees some.
        with mock.patch.object(metadata_check, "MARGIN", 1 << 60), \
                mock.patch.object(field_values, "_write", side_effect=AssertionError("a copy was written")):
            found = self.check()
        self.assertEqual(({}, {}), (found["how"], found["looked"]))
        self.assertTrue(found["stopped"].startswith("the sandbox's disk has less than"), found["stopped"])

    def test_a_copy_exiftool_heals_as_it_reads_is_a_difference(self):
        # The own writer's patch leaves IFD0's first two entries out of order: ExifTool still reads every tag the same,
        # and says so (its warning, its validation's count); a reader that looks its tags up in order may miss one.
        self.photo("a.jpg")
        real = segment_edits.new_header

        def new_header(layout, header, values):
            made = real(layout, header, values)
            if made is None or not layout.of_kind("exif"):
                return made
            written = bytearray(made[0])
            tiff = layout.of_kind("exif")[0].offset + 4 + len(b"Exif\0\0")
            order = "big" if written[tiff:tiff + 2] == b"MM" else "little"
            first = tiff + int.from_bytes(written[tiff + 4:tiff + 8], order) + 2
            written[first:first + 24] = written[first + 12:first + 24] + written[first:first + 12]
            return bytes(written), made[1]
        with mock.patch.object(segment_edits, "new_header", new_header):
            found = self.check(reveal=True)
        said = {each["difference"] for each in found["revealed"] if each["operation"] == "keyword added"}
        self.assertEqual({"the sides differ in ExifTool:Warning",
                          "own's second write of the same values changed the file"}, said,
                         "\n".join(metadata_check.lines(found)))

    def test_a_wrong_own_write_is_still_a_difference(self):
        # The own writer puts the caption in the title, not the description, and drops the keyword it was to add: the
        # set-asides (KNOWN, empty as absent, warnings against the original's) hide neither.
        self.photo("a.jpg", tags=("Beach",))
        real = metadata_check._written

        def written(fields, path, values, own):
            if own and "XMP:Description" in values and values["XMP:Description"]:
                values = dict(values, **{"XMP:Title": values["XMP:Description"], "XMP:Description": ""})
            if own and metadata_check.KEYWORD in values.get("XMP:Subject", []):
                values = dict(values, **{"XMP:Subject": [each for each in values["XMP:Subject"]
                                                         if each != metadata_check.KEYWORD]})
            return real(fields, path, values, own)
        with mock.patch.object(metadata_check, "_written", written):
            found = self.check(reveal=True)
        said = {(each["operation"], each["difference"]) for each in found["revealed"]}
        for expected in (("caption set", "the sides differ in XMP:XMP-dc:Description"),
                         ("caption set", "the sides differ in XMP:XMP-dc:Title"),
                         ("keyword added", "the sides differ in XMP:XMP-dc:Subject")):
            self.assertIn(expected, said, "\n".join(metadata_check.lines(found)))

    def test_an_iptc_block_left_stale_is_no_difference(self):
        # Another program's IPTC block holding a keyword and an empty caption, no version dataset (1:00, 2:00): a keyword
        # added is the own writer's patch of the XMP, the block left stale; ExifTool's rewrite of the block adds the
        # versions and drops the empty caption. Both copies read the same keywords and caption.
        a = self.photo("a.jpg", tags=("Beach",))
        held = read(a)
        iptc = containers.read(io.BytesIO(held)).of_kind("iptc")[0]
        datasets = (b"\x1c\x02\x19" + (5).to_bytes(2, "big") + b"Beach" + b"\x1c\x02\x78" + bytes(2))
        resource = b"8BIM\x04\x04" + bytes(2) + len(datasets).to_bytes(4, "big") + datasets + bytes(len(datasets) % 2)
        body = b"Photoshop 3.0\0" + resource
        with open(a, "wb") as handle:
            handle.write(held[:iptc.offset] + b"\xff\xed" + (len(body) + 2).to_bytes(2, "big") + body
                         + held[iptc.end:])
        self.assertEqual("", field_of(a, "IPTC:Caption-Abstract"))
        self.assertIsNone(field_of(a, "IPTC:ApplicationRecordVersion"))
        found = self.check()
        self.assertEqual({"rewrite, own patch": 1}, found["how"]["keyword added"])
        self.assert_agreed(found, 1)

    def test_a_copy_with_fewer_of_the_originals_warnings_is_no_difference(self):
        # As ExifTool reads them (-validate): the original says three things, ExifTool's copy one, the own writer's two
        # of the original's -- no worse than it was, though its validation's counts differ from both. A warning of the
        # own copy that neither the original nor ExifTool's copy says is a difference.
        said = ("Entries in IFD0 are out of order", "[minor] Non-standard IFD0 tag 0x9c9b XPTitle",
                "[minor] IPTC doesn't match XMP")

        def row(counts, *warnings):
            found = {"ExifTool:Validate": counts, "ExifTool:Warning": warnings[0]}
            found.update(("ExifTool:Copy%d:Warning" % number, text) for number, text in enumerate(warnings[1:], 1))
            return found
        original, theirs = row("0 2 1", *said), row("0 0 1", said[2])
        self.assertEqual([], metadata_check._tags_differing(original, theirs, row("0 1 1", *said[:2]), set()))
        self.assertEqual(["ExifTool:Warning"], metadata_check._tags_differing(
            original, theirs, row("0 2 1", said[0], "Bad IFD0 directory"), set()))

    def test_a_maker_note_the_rewrite_dropped_and_the_own_writer_kept_is_no_difference(self):
        # A maker note whose entries ExifTool cannot parse (bad offsets) is dropped by its rewrite, and the original's
        # tags are gone from its read; the own writer's note is byte for byte the original's. Compared with the
        # original, as a stale field is: no difference. A maker note tag the own writer changed is one.
        original = {"MakerNotes:Canon:CanonImageType": "Canon PowerShot", "MakerNotes:Canon:FirmwareVersion": "1.0",
                    "EXIF:IFD0:Make": "Canon"}
        theirs = {"EXIF:IFD0:Make": "Canon"}
        self.assertEqual([], metadata_check._tags_differing(original, theirs, dict(original), set()))
        changed = dict(original, **{"MakerNotes:Canon:FirmwareVersion": "2.0"})
        self.assertEqual(["MakerNotes:Canon:FirmwareVersion"],
                         metadata_check._tags_differing(original, theirs, changed, set()))
        self.assertEqual(["EXIF:IFD0:Make"], metadata_check._tags_differing(
            original, theirs, dict(original, **{"EXIF:IFD0:Make": "Other"}), set()))

    def test_a_maker_note_tag_the_rewrite_changed_and_the_own_copy_reads_the_same_is_no_difference(self):   # #3112
        original = {"MakerNotes:Canon:FirmwareVersion": "1.0", "EXIF:IFD0:Make": "Canon"}
        theirs = {"MakerNotes:Canon:FirmwareVersion": "2.0", "EXIF:IFD0:Make": "Canon"}
        self.assertEqual([], metadata_check._tags_differing(original, theirs, dict(theirs), set()))
        self.assertEqual(["MakerNotes:Canon:FirmwareVersion"], metadata_check._tags_differing(
            original, theirs, dict(theirs, **{"MakerNotes:Canon:FirmwareVersion": "3.0"}), set()))

    def test_a_copy_the_second_read_leaves_out_is_a_copy_not_compared_never_silence(self):
        # A copy another program holds a moment answers the whole read and not the read of what ExifTool says of it:
        # its warnings are not "none", the copy could not be compared (ExifTool's, the own writer's or the original).
        self.photo("a.jpg")
        real = ExifToolSession.execute

        def leaving_out(left):
            def execute(session, *params, **kwargs):
                answer = real(session, *params, **kwargs)
                if field_values.READER_READ[:5] != tuple(params[:5]):
                    return answer
                rows = json.loads(answer or "[]")
                return json.dumps([row for row in rows if left not in os.path.basename(row["SourceFile"])])
            return execute
        for left in ("own.jpg", "ExifTool.jpg", "original.jpg"):
            with self.subTest(left=left), mock.patch.object(ExifToolSession, "execute", leaving_out(left)):
                found = self.check()
                self.assertIn("a copy could not be compared", found["differences"], left)

    def test_a_second_write_of_the_same_values_that_changes_the_file_is_a_difference(self):
        # The own writer's patch moves a byte of the packet's padding each time it writes: both copies read alike, and
        # the second write of the same values changes the file all the same.
        self.photo("a.jpg")
        real, writes = segment_edits.new_header, []

        def new_header(layout, header, values):
            made = real(layout, header, values)
            if made is None or not layout.of_kind("xmp"):
                return made
            writes.append(1)
            written = bytearray(made[0])
            end = bytes(written).rindex(b"<?xpacket end=")
            written[end - 2:end] = b" \n" if len(writes) % 2 else b"\n "
            return bytes(written), made[1]
        with mock.patch.object(segment_edits, "new_header", new_header):
            found = self.check()
        self.assertEqual({"own's second write of the same values changed the file"}, set(found["differences"]),
                         "\n".join(metadata_check.lines(found)))

    def test_a_warning_the_original_held_is_no_difference(self):
        # The original's first two IFD0 entries are out of order (another program wrote it so; ExifTool says "Entries in
        # IFD0 are out of order"): ExifTool's write lays the block out again and says no more of it, the own writer's
        # patch of the XMP leaves the block and its warning as they were.
        a = self.photo("a.jpg")
        held = read(a)
        exif = containers.read(io.BytesIO(held)).of_kind("exif")[0]
        tiff = exif.offset + 4 + len(b"Exif\0\0")
        order = "big" if held[tiff:tiff + 2] == b"MM" else "little"
        ifd0 = tiff + int.from_bytes(held[tiff + 4:tiff + 8], order)
        first, second = ifd0 + 2, ifd0 + 2 + 12
        with open(a, "wb") as handle:
            handle.write(held[:first] + held[second:second + 12] + held[first:second] + held[second + 12:])
        found = self.check()
        self.assertEqual({"rewrite, own patch": 1}, found["how"]["keyword added"])
        self.assert_agreed(found, 1)

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
