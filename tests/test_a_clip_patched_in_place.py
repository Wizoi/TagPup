"""A clip's new XMP packet placed in the file as it is (tagpup.files.clip_patch; docs/PLAN_OWN_METADATA.md, M7): the
ranges written over the clip's bytes, read back.

Layouts are built by hand, byte by byte, in the shapes the live clips have (an MP4 as ExifTool writes it, a .mov with
moov/udta/XMP_, moov first or last, a free run after the XMP box, 64-bit boxes) and every shape the patch must refuse to
the rewrite (a tail of garbage or a box cut short, two XMP boxes, no moov, two, a fragment, a last box of size 0). Each
patch is applied and the clip read again with containers.read: whatever the patch did, every box that is neither XMP nor
free keeps its offset and its size, so moov's chunk offsets (stco/co64) stay true and mdat has not moved. Offsets past
2 GB and 4 GB are read from Box objects built by hand. Then real clips made by ffmpeg in a home of the test's own are
patched and read back by ExifTool (its -validate too) and ffprobe, which decodes every frame; skipped, saying so, on a
machine without either.
"""
import io
import json
import os
import shutil
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from tagpup import config  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.files import clip_patch, containers  # noqa: E402
from tagpup.files.containers import XMP_UUID, Box, Video  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
TOOLS = config.locate_ffmpeg()
BEGIN = b"<?xpacket begin='\xef\xbb\xbf' id='W5M0MpCehiHzreSzNTczkc9d'?>"
END = b"<?xpacket end='w'?>"
GB = 2 ** 30


def box(kind, payload=b""):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def large_box(kind, payload=b""):
    return struct.pack(">I", 1) + kind + struct.pack(">Q", 16 + len(payload)) + payload


def xmp_box(body, kind=b"uuid"):
    return box(b"uuid", XMP_UUID + body) if kind == b"uuid" else box(kind, body)


def packet(words=("Hiking",), pad=200, wrapped=True):
    """An XMP packet whose dc:subject holds `words`, `pad` spaces of padding and its line break before the end."""
    items = b"".join(b"<rdf:li>%s</rdf:li>" % word.encode() for word in words)
    xml = (b"<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
           b"<rdf:Description rdf:about='' xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:subject><rdf:Bag>" + items
           + b"</rdf:Bag></dc:subject></rdf:Description></rdf:RDF></x:xmpmeta>")
    return BEGIN + xml + b"\n" + b" " * pad + b"\n" + END if wrapped else xml


def sized(length, words=("Hiking",)):
    """A wrapped packet of exactly `length` bytes."""
    return packet(words, length - len(packet(words, 0)))


FTYP = box(b"ftyp", b"isom\0\0\2\0isomiso2mp41")
MVHD = box(b"mvhd", bytes(100))
TRAK = box(b"trak", bytes(40))
MOOV = box(b"moov", MVHD + TRAK)
MDAT = box(b"mdat", bytes(64))
OLD = packet(("Hiking",), 300)


def mov(*in_udta):
    """A .mov's moov with udta holding `in_udta`, ended as QuickTime ends a udta (four zero bytes)."""
    return box(b"moov", MVHD + TRAK + box(b"udta", b"".join(in_udta) + bytes(4)))


def kept(layout):
    """(path, offset, size) of every box that is neither XMP nor free, the children walked too."""
    found = []

    def walk(boxes, path):
        for one in boxes:
            if one.is_xmp or one.type in containers.FREE_TYPES:
                continue
            found.append((path + (one.type,), one.offset, one.size))
            walk(one.children, path + (one.type,))
    walk(layout.boxes, ())
    return found


def applied(data, found):
    """`data` with the patch's ranges written over it, as a file written at those offsets would hold."""
    out, last = bytearray(data), 0
    for at, written in found.ranges:
        assert last <= at <= len(out), (last, at, len(out))
        out[at:at + len(written)] = written
        last = at + len(written)
    assert len(out) == found.size, (len(out), found.size)
    return bytes(out)


def layout_of(data):
    return containers.read(io.BytesIO(data))


class APacketPlacedInAClip(unittest.TestCase):

    def placed(self, data, new, how):
        """Patch `data` with `new`, read it back: the one XMP box holds the packet (`new`, or widened), the walk reaches
        the end, and every other box is where it was. Answers (patch, the bytes after, the XMP payload)."""
        before = layout_of(data)
        found = clip_patch.patch(before, new)
        self.assertIsNotNone(found)
        self.assertEqual(how, found.how)
        after_bytes = applied(data, found)
        after = layout_of(after_bytes)
        self.assertIsInstance(after, Video)
        self.assertIsNone(after.tail)
        self.assertEqual(kept(before), kept(after))
        (_, xmp), = after.xmp
        return found, after_bytes, after_bytes[xmp.payload:xmp.end]

    def test_a_packet_of_the_same_length_is_written_over_the_old_one(self):
        data = FTYP + MOOV + xmp_box(OLD) + MDAT
        found, _, held = self.placed(data, sized(len(OLD), ("Hiking", "Lakes")), clip_patch.IN_PLACE)
        self.assertEqual(sized(len(OLD), ("Hiking", "Lakes")), held)
        self.assertEqual(1, len(found.ranges))
        self.assertEqual(len(data), found.size)

    def test_a_shorter_packet_leaves_a_free_box_where_it_ended(self):
        data = FTYP + MOOV + xmp_box(OLD) + MDAT
        _, after, held = self.placed(data, sized(len(OLD) - 40), clip_patch.IN_PLACE)
        self.assertEqual(sized(len(OLD) - 40), held)
        self.assertEqual(["ftyp", "moov", "uuid", "free", "mdat"], [one.type for one in layout_of(after).boxes])
        self.assertEqual(40, layout_of(after).boxes[3].size)

    def test_a_longer_packet_takes_the_free_run_after_it_64_bit_boxes_and_all(self):
        run = box(b"free", bytes(30)) + box(b"skip", bytes(20)) + large_box(b"wide", bytes(50))
        data = FTYP + MOOV + xmp_box(OLD) + run + MDAT
        new = sized(len(OLD) + len(run) - 30)
        _, after, held = self.placed(data, new, clip_patch.IN_PLACE)
        self.assertEqual(new, held)
        self.assertEqual(["ftyp", "moov", "uuid", "free", "mdat"], [one.type for one in layout_of(after).boxes])
        self.assertEqual(30, layout_of(after).boxes[3].size)

    def test_one_to_seven_bytes_left_over_are_spaces_in_the_wrapped_packets_padding(self):
        for left in range(1, 8):
            with self.subTest(left=left):
                data = FTYP + MOOV + xmp_box(OLD) + MDAT
                new = sized(len(OLD) - left)
                _, _, held = self.placed(data, new, clip_patch.IN_PLACE)
                self.assertEqual(len(OLD), len(held))
                self.assertEqual(new.replace(b" ", b""), held.replace(b" ", b""))
                self.assertTrue(held.endswith(b" " * left + b"\n" + END))

    def test_one_to_seven_bytes_left_over_a_packet_with_no_padding_retires_the_box_or_is_the_rewrites(self):
        bare = packet(("Hiking",), wrapped=False)
        for new in (bare, BEGIN + bare + END):
            with self.subTest(wrapped=new != bare):
                data = FTYP + MOOV + xmp_box(new + b"   ") + MDAT
                found, after, held = self.placed(data, new, clip_patch.APPENDED)
                self.assertEqual(new, held)
                self.assertEqual(b"free", after[len(FTYP + MOOV) + 4:len(FTYP + MOOV) + 8])
                nested = FTYP + mov(xmp_box(new + b"   ", b"XMP_")) + MDAT
                self.assertIsNone(clip_patch.patch(layout_of(nested), new))

    def test_a_movs_xmp_in_udta_takes_the_free_box_after_it_and_moov_keeps_its_size(self):
        data = FTYP + mov(xmp_box(OLD, b"XMP_"), box(b"free", bytes(60))) + MDAT
        new = sized(len(OLD) + 50)
        found, after, held = self.placed(data, new, clip_patch.IN_PLACE)
        self.assertEqual(new, held)
        self.assertEqual([("moov", "udta", "XMP_")], [path for path, _ in layout_of(after).xmp])
        self.assertIsNone(clip_patch.patch(layout_of(data), sized(len(OLD) + 69)), "a .mov's XMP_ does not grow")

    def test_a_packet_one_byte_longer_than_its_slot_is_appended_or_the_rewrites(self):
        data = FTYP + MOOV + xmp_box(OLD) + MDAT
        found, after, held = self.placed(data, sized(len(OLD) + 1), clip_patch.APPENDED)
        self.assertEqual(((len(FTYP + MOOV) + 4, b"free"), (len(data), after[len(data):])), found.ranges)
        self.assertEqual(["ftyp", "moov", "free", "mdat", "uuid"], [one.type for one in layout_of(after).boxes])
        nested = FTYP + mov(xmp_box(OLD, b"XMP_")) + MDAT
        self.assertIsNone(clip_patch.patch(layout_of(nested), sized(len(OLD) + 1)))

    def test_an_xmp_box_whose_run_reaches_the_end_grows_there(self):
        for tail in (b"", box(b"free", bytes(10))):
            with self.subTest(free_after=bool(tail)):
                data = FTYP + MDAT + MOOV + xmp_box(OLD) + tail
                new = sized(len(OLD) + 500)
                found, after, held = self.placed(data, new, clip_patch.GROWN)
                self.assertEqual(new, held)
                self.assertEqual(len(FTYP + MDAT + MOOV) + 24 + len(new), found.size)
                self.assertEqual(found.size, len(after))

    def test_a_clip_with_no_xmp_gets_a_uuid_box_at_its_end_moov_first_or_last_or_a_mov(self):
        for data in (FTYP + MOOV + MDAT, FTYP + MDAT + MOOV, FTYP + box(b"wide") + MDAT + mov(box(b"\xa9nam", b"x"))):
            with self.subTest(boxes=[one.type for one in layout_of(data).boxes]):
                found, after, held = self.placed(data, OLD, clip_patch.APPENDED)
                self.assertEqual(OLD, held)
                self.assertEqual(data, after[:len(data)])
                self.assertEqual(((len(data), xmp_box(OLD)),), found.ranges)

    def test_an_xmp_box_that_fits_is_placed_even_when_the_last_box_runs_to_the_end(self):
        to_end = struct.pack(">I", 0) + b"mdat" + bytes(64)
        data = FTYP + MOOV + xmp_box(OLD) + to_end
        self.placed(data, sized(len(OLD)), clip_patch.IN_PLACE)
        self.assertIsNone(clip_patch.patch(layout_of(data), sized(len(OLD) + 1)), "an append would lie inside mdat")
        self.assertIsNone(clip_patch.patch(layout_of(FTYP + MOOV + to_end), OLD))

    def test_a_clip_whose_walk_stops_short_is_the_rewrites_even_when_the_packet_fits(self):
        whole = FTYP + MOOV + xmp_box(OLD) + MDAT
        shapes = {
            "garbage": whole + b"\x00\x00\x00\x10\x01\x02\x03\x04" + bytes(8),
            "a box past the end of the file": whole + struct.pack(">I", 4000) + b"mdat" + bytes(10),
            "a file cut in a box header": whole + b"\x00\x00",
            "a file cut in the middle of mdat": whole[:-10],
        }
        for name, data in shapes.items():
            with self.subTest(name):
                layout = layout_of(data)
                self.assertIsNotNone(layout.tail)
                self.assertIsNone(clip_patch.patch(layout, sized(len(OLD))))
                self.assertIsNone(clip_patch.patch(layout_of(data.replace(xmp_box(OLD), b"")), OLD))

    def test_what_this_does_not_understand_is_the_rewrites(self):
        shapes = {
            "two XMP boxes (a .mov ExifTool wrote after an append)": FTYP + mov(xmp_box(OLD, b"XMP_")) + xmp_box(OLD) + MDAT,
            "two top-level XMP boxes": FTYP + MOOV + xmp_box(OLD) + MDAT + xmp_box(OLD),
            "no moov": FTYP + MDAT,
            "two moov": FTYP + MOOV + MDAT + MOOV,
            "a fragmented clip": FTYP + MOOV + box(b"moof", bytes(16)) + MDAT,
            "a moov whose child claims more than moov holds": FTYP + box(b"moov", MVHD + struct.pack(">I", 999) + b"trak") + MDAT,
            "a udta whose child claims more than udta holds": FTYP + box(b"moov", MVHD + box(b"udta", struct.pack(">I", 99) + b"XMP_")) + MDAT,
        }
        for name, data in shapes.items():
            with self.subTest(name):
                self.assertIsNone(clip_patch.patch(layout_of(data), sized(len(OLD))))
        self.assertIsNone(clip_patch.patch(containers.Unreadable("not a clip"), OLD))

    def test_offsets_past_2_and_4_gb_are_written_where_the_boxes_are(self):
        for mdat_size in (3 * GB, 5 * GB):
            with self.subTest(gb=mdat_size // GB):
                moov = Box("moov", 32 + mdat_size, 1000, 8, children=(Box("mvhd", 40 + mdat_size, 108, 8),))
                xmp = Box("uuid", moov.end, 24 + len(OLD), 24, uuid=XMP_UUID)
                free = Box("free", xmp.end, 5000, 16)
                boxes = (Box("ftyp", 0, 32, 8), Box("mdat", 32, mdat_size, 16), moov, xmp, free)
                layout = Video(free.end + 100, boxes + (Box("free", free.end, 100, 8),))
                new = sized(len(OLD) + 4000)
                found = clip_patch.patch(layout, new)
                self.assertEqual(clip_patch.IN_PLACE, found.how)
                self.assertEqual(((xmp.offset, xmp_box(new)), (xmp.offset + 24 + len(new),
                                  struct.pack(">I", 5000 + 100 - 4000) + b"free")), found.ranges)
                self.assertEqual(layout.file_size, found.size)
                grown = clip_patch.patch(layout, sized(len(OLD) + 6000))
                self.assertEqual((clip_patch.GROWN, xmp.offset + 24 + len(OLD) + 6000), (grown.how, grown.size))
                held = Video(free.end + 100, boxes + (Box("mdat", free.end, 100, 8),))
                appended = clip_patch.patch(held, sized(len(OLD) + 6000))
                self.assertEqual(((xmp.offset + 4, b"free"), (held.file_size, xmp_box(sized(len(OLD) + 6000)))),
                                 appended.ranges)

    def test_a_free_run_of_4_gb_or_more_after_the_box_retires_it_and_appends(self):
        xmp = Box("uuid", 1100, 24 + len(OLD), 24, uuid=XMP_UUID)
        free = Box("free", xmp.end, 5 * GB, 16)
        boxes = (Box("ftyp", 0, 32, 8), Box("moov", 32, 1068, 8, children=(Box("mvhd", 40, 108, 8),)), xmp, free,
                 Box("mdat", free.end, 64, 8))
        layout = Video(free.end + 64, boxes)
        found = clip_patch.patch(layout, sized(len(OLD)))
        self.assertEqual(clip_patch.APPENDED, found.how)
        self.assertEqual((xmp.offset + 4, b"free"), found.ranges[0])
        self.assertEqual(layout.file_size, found.ranges[1][0])


@unittest.skipIf(EXIFTOOL is None or TOOLS.problem, "ExifTool or ffmpeg is not installed on this machine")
class AClipMadeByFfmpegReadBack(unittest.TestCase):
    """Clips made by the real ffmpeg, patched, and read back by ExifTool and ffprobe."""

    @classmethod
    def setUpClass(cls):
        cls.folder = own_home.for_class(cls, "tagpup_clip_patch_").root
        cls.made = {}
        for name, extra in (("first.mp4", ("-movflags", "+faststart")), ("last.mp4", ()), ("clip.mov", ())):
            path = os.path.join(cls.folder, "made_" + name)
            processes.run([TOOLS.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10",
                           "-t", "1", "-pix_fmt", "yuv420p", *extra, path], check=True, capture_output=True, timeout=60)
            cls.made[name] = path

    def clip(self, name):
        path = os.path.join(self.folder, self.id().rsplit(".", 1)[-1] + "_" + name)
        shutil.copyfile(self.made[name], path)
        return path

    def patched(self, path, new, how):
        with open(path, "rb") as handle:
            before = containers.read(handle)
        found = clip_patch.patch(before, new)
        self.assertIsNotNone(found, path)
        self.assertEqual(how, found.how)
        with open(path, "r+b") as handle:
            for at, written in found.ranges:
                handle.seek(at)
                handle.write(written)
        with open(path, "rb") as handle:
            after = containers.read(handle)
        self.assertEqual(kept(before), kept(after))
        self.assertEqual(found.size, os.path.getsize(path))
        return after

    def read_back(self, path, words):
        """ExifTool's XMP:Subject and its -validate, ffprobe's XMP and frames decoded: each as written, nothing wrong."""
        with ExifToolSession(executable=EXIFTOOL) as session:
            subject = session.get_tags([path], tags=["XMP:Subject"], params=["-a"])[0].get("XMP:Subject")
            checked = json.loads(session.execute("-validate", "-warning", "-a", "-j", path))[0]
        self.assertEqual(list(words), subject if isinstance(subject, list) else [subject])
        self.assertEqual("0 0 0", checked.get("ExifTool:Validate"), checked)
        probe = processes.run([TOOLS.ffprobe, "-v", "error", "-export_xmp", "1", "-count_frames", "-show_entries",
                               "format_tags=xmp:stream=nb_read_frames", "-of", "json", path],
                              capture_output=True, timeout=60)
        self.assertEqual(b"", probe.stderr)
        out = json.loads(probe.stdout)
        self.assertIn("<rdf:li>%s</rdf:li>" % words[0], out["format"]["tags"]["xmp"])
        self.assertEqual(["10"], [stream["nb_read_frames"] for stream in out["streams"]])

    def test_appended_overwritten_widened_and_grown_each_read_back(self):
        for name in self.made:
            with self.subTest(name):
                path = self.clip(name)
                self.patched(path, packet(("Wren Ashcombe", "Hiking"), 400), clip_patch.APPENDED)
                self.read_back(path, ("Wren Ashcombe", "Hiking"))
                slot = len(packet(("Wren Ashcombe", "Hiking"), 400))
                self.patched(path, sized(slot, ("Wren Ashcombe", "Lakes")), clip_patch.IN_PLACE)
                self.read_back(path, ("Wren Ashcombe", "Lakes"))
                self.patched(path, sized(slot - 3, ("Wren Ashcombe", "Lake")), clip_patch.IN_PLACE)
                self.read_back(path, ("Wren Ashcombe", "Lake"))
                self.patched(path, packet(("Wren Ashcombe",) + tuple("word %d" % i for i in range(200))),
                             clip_patch.GROWN)
                self.read_back(path, ("Wren Ashcombe",) + tuple("word %d" % i for i in range(200)))

    def test_exiftools_xmp_box_before_mdat_is_retired_and_the_packet_appended(self):
        path = self.clip("first.mp4")
        with ExifToolSession(executable=EXIFTOOL) as session:
            session.set_tags([path], tags={"XMP:Subject": ["Hiking"]}, params=["-m", "-overwrite_original"])
        with open(path, "rb") as handle:
            self.assertEqual(["ftyp", "moov", "free", "uuid", "mdat"], [one.type for one in containers.read(handle).boxes])
        words = ("Wren Ashcombe",) + tuple("word %d" % i for i in range(200))
        after = self.patched(path, packet(words), clip_patch.APPENDED)
        self.assertEqual(["ftyp", "moov", "free", "free", "mdat", "uuid"], [one.type for one in after.boxes])
        self.read_back(path, words)

    def test_a_movs_xmp_written_by_exiftool_is_patched_in_udta(self):
        path = self.clip("clip.mov")
        with ExifToolSession(executable=EXIFTOOL) as session:
            session.set_tags([path], tags={"XMP:Subject": ["Hiking"]}, params=["-m", "-overwrite_original"])
        with open(path, "rb") as handle:
            (where, xmp), = containers.read(handle).xmp
        self.assertEqual(("moov", "udta", "XMP_"), where)
        self.patched(path, sized(xmp.size - 8, ("Wren Ashcombe",)), clip_patch.IN_PLACE)
        self.read_back(path, ("Wren Ashcombe",))

    def test_exiftool_writing_a_mov_that_holds_an_appended_box_leaves_two_and_every_later_patch_is_the_rewrites(self):
        path = self.clip("clip.mov")
        self.patched(path, packet(("Wren Ashcombe",)), clip_patch.APPENDED)
        with ExifToolSession(executable=EXIFTOOL) as session:
            session.set_tags([path], tags={"XMP:Subject": ["Hiking"]}, params=["-m", "-overwrite_original"])
        with open(path, "rb") as handle:
            layout = containers.read(handle)
        self.assertEqual([("moov", "udta", "XMP_"), ("uuid",)], [where for where, _ in layout.xmp])
        self.assertIsNone(clip_patch.patch(layout, packet(("Hiking",))))


if __name__ == "__main__":
    unittest.main()
