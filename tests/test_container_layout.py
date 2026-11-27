"""What tagpup.files.containers reads from a clip's boxes and a JPEG's segments, on files built by hand byte by byte: the
layouts ExifTool and an append at the end leave (checked by hand on 2026-11-03 against ExifTool 13.59 and ffmpeg 9.0.2:
an MP4 gets a top-level XMP uuid box, a .mov gets moov/udta/XMP_, a uuid box after mdat is read back), the live shapes
(2 .mov files that start with a skip box, a clip with zeros after mdat, 3 clips over 4 GB, 617 with a 64-bit box size),
and every way the bytes can stop being a layout: a file cut short, a box that claims more than the file holds, garbage, a
read that fails part-way. None of them raises."""
import io
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_clip_dated_through_the_journal import (  # noqa: E402
    DISAGREEING, SHAPES, THREE_TRACKS, VERSION_ONE, ZERO_HEADERS, clip_of)
from corpus import exif_segment, iptc_segment, resource, segment, xmp_segment  # noqa: E402
from tagpup.files import containers  # noqa: E402

XMP_UUID = bytes.fromhex("BE7ACFCB97A942E89C71999491E3AFAC")


def box(kind, payload=b""):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def large_box(kind, payload=b""):
    return struct.pack(">I", 1) + kind + struct.pack(">Q", 16 + len(payload)) + payload


def uuid_box(usertype, payload=b""):
    return struct.pack(">I", 24 + len(payload)) + b"uuid" + usertype + payload


FTYP = box(b"ftyp", b"isom\0\0\2\0isomiso2mp41")
MOOV = box(b"moov", box(b"mvhd", bytes(100)) + box(b"trak", bytes(40)))
MDAT = box(b"mdat", bytes(64))
XMP = uuid_box(XMP_UUID, b"<x:xmpmeta/>" + b" " * 200)


SOS = segment(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x12\x34" * 20 + b"\xff\xd9"
JFIF = segment(0xE0, b"JFIF\0\1\1\0\0\1\0\1\0\0")


class Reading(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def read(self, data, name="clip.mp4"):
        path = os.path.join(self.folder.name, name)
        with open(path, "wb") as handle:
            handle.write(data)
        with open(path, "rb") as handle:
            return containers.read(handle)


class AClip(Reading):
    def test_an_mp4_tagged_by_exiftool_has_its_xmp_box_after_moov_with_the_free_box_beside_it(self):
        data = FTYP + MOOV + box(b"free") + XMP + MDAT
        layout = self.read(data)
        self.assertEqual(["ftyp", "moov", "free", "uuid", "mdat"], [b.type for b in layout.boxes])
        self.assertIs(True, layout.moov_first)
        self.assertFalse(layout.fragmented)
        [(where, xmp)] = layout.xmp
        self.assertEqual(("uuid",), where)
        self.assertEqual(len(FTYP + MOOV) + 8, xmp.offset)
        self.assertEqual(xmp.offset + 24, xmp.payload)
        self.assertEqual(data[xmp.payload:xmp.end], b"<x:xmpmeta/>" + b" " * 200)
        before, after = layout.slack_beside(xmp)
        self.assertEqual(["free"], [b.type for b in before])
        self.assertEqual((), after)
        self.assertEqual(["free"], [b.type for b in layout.slack_beside(layout.moov)[1]])
        self.assertIsNone(layout.tail)

    def test_an_xmp_box_appended_after_mdat_or_after_a_last_moov_is_found(self):
        moov_first = self.read(FTYP + MOOV + MDAT + XMP)
        self.assertIs(True, moov_first.moov_first)
        self.assertGreater(moov_first.xmp[0][1].offset, moov_first.last_mdat.offset)
        moov_last = self.read(FTYP + box(b"wide") + MDAT + MDAT + MOOV + XMP)
        self.assertIs(False, moov_last.moov_first)
        self.assertEqual(len(FTYP) + 8, moov_last.first_mdat.offset)
        self.assertEqual(len(FTYP) + 8 + len(MDAT), moov_last.last_mdat.offset)
        self.assertEqual(moov_last.file_size, moov_last.xmp[0][1].end)
        self.assertEqual((), moov_last.slack_beside(moov_last.moov)[0], "wide sits before mdat, not before moov")

    def test_a_mov_tagged_by_exiftool_has_its_xmp_inside_moov(self):
        udta = box(b"udta", box(b"\xa9swr", b"Lavf") + box(b"free", bytes(8)) + box(b"XMP_", b"<x:xmpmeta/>") + bytes(4))
        layout = self.read(FTYP + box(b"moov", box(b"mvhd", bytes(100)) + udta) + MDAT, "clip.mov")
        [(where, xmp)] = layout.xmp
        self.assertEqual(("moov", "udta", "XMP_"), where)
        self.assertEqual(8, xmp.header)
        self.assertEqual((["free"], []), tuple([b.type for b in side] for side in layout.slack_beside(xmp)))
        self.assertFalse(layout.moov.broken, "the four zero bytes ending a udta are its end, not a broken box")
        self.assertFalse(layout.moov.children[1].broken)

    def test_a_mov_that_starts_with_a_skip_box(self):
        layout = self.read(box(b"skip", bytes(272)) + FTYP + box(b"wide") + MDAT + MOOV, "clip.mov")
        self.assertEqual(["skip", "ftyp", "wide", "mdat", "moov"], [b.type for b in layout.boxes])

    def test_a_64_bit_box_size_and_a_last_box_of_size_0(self):
        data = FTYP + large_box(b"mdat", bytes(40)) + MOOV + struct.pack(">I", 0) + b"mdat" + bytes(30)
        layout = self.read(data)
        mdat = layout.first_mdat
        self.assertEqual((16, 56), (mdat.header, mdat.size))
        self.assertEqual("moov", layout.boxes[2].type)
        last = layout.last_mdat
        self.assertTrue(last.to_end)
        self.assertEqual(len(data), last.end)
        self.assertIsNone(layout.tail)

    def test_a_clip_over_4_gb_is_read_from_its_box_headers_alone(self):
        big = 5 * 2 ** 30
        head = FTYP + struct.pack(">I", 1) + b"mdat" + struct.pack(">Q", big)
        moov_at = len(FTYP) + big
        clip = Sparse(moov_at + len(MOOV) + len(XMP), {0: head, moov_at: MOOV + XMP})
        layout = containers.read(clip)
        self.assertEqual(["ftyp", "mdat", "moov", "uuid"], [b.type for b in layout.boxes])
        self.assertEqual(moov_at, layout.moov.offset)
        self.assertEqual(clip.size, layout.xmp[0][1].end)
        self.assertLess(clip.bytes_read, 1024, "a box's content is never read")

    def test_moof_is_a_fragmented_clip(self):
        layout = self.read(FTYP + MOOV + box(b"moof", bytes(16)) + MDAT)
        self.assertTrue(layout.fragmented)


class AClipsHeaders(Reading):
    """The creation times of a clip's movie, track and media headers (Video.headers, Box.version, .width, .created), on
    the layouts real clips have (tests/fixtures/clip_headers.json: the header bytes of each, read here and by
    tests/test_a_clip_dated_through_the_journal.py)."""

    def test_the_headers_of_each_layout_real_clips_have_are_read_with_their_versions_widths_and_times(self):
        zero_two_tracks = ["mvhd", "tkhd", "mdhd", "tkhd", "mdhd"]
        dated = 3517120800
        expected = {
            ZERO_HEADERS: (zero_two_tracks, [0, 0, 0, 0, 0], [4, 4, 4, 4, 4], [0, 0, 0, 0, 0]),
            VERSION_ONE: (zero_two_tracks, [1, 1, 0, 1, 0], [8, 8, 4, 8, 4], [0, 0, 0, 0, 0]),
            THREE_TRACKS: (zero_two_tracks + ["tkhd", "mdhd"], [0] * 7, [4] * 7, [dated] * 7),
            DISAGREEING: (zero_two_tracks + ["tkhd", "mdhd"] * 2, [0] * 9, [4] * 9, [dated] * 5 + [dated + 1] * 4),
        }
        self.assertEqual(set(SHAPES), set(expected), "a case for every layout the fixture holds")
        for shape, (types, versions, widths, created) in expected.items():
            for moov_after_mdat in (False, True):
                with self.subTest(shape=shape, moov_after_mdat=moov_after_mdat):
                    headers = self.read(clip_of(shape, moov_after_mdat)).headers
                    self.assertEqual(
                        (types, versions, widths, created),
                        ([h.type for h in headers], [h.version for h in headers], [h.width for h in headers],
                         [h.created for h in headers]))

    def test_a_header_of_another_version_or_too_short_to_hold_a_time_has_none(self):
        for content, version, width in ((bytes([2]) + bytes(3) + bytes(8), 2, None), (bytes([1]) + bytes(3) + bytes(4), 1, 8),
                                        (bytes([0]), 0, 4)):
            with self.subTest(version=version, size=len(content)):
                moov = box(b"moov", box(b"mvhd", content))
                header, = self.read(FTYP + moov + MDAT).headers
                self.assertEqual((version, width, None), (header.version, header.width, header.created))

    def test_a_media_box_whose_children_do_not_fit_has_its_clip_say_so(self):
        media = box(b"mdia", struct.pack(">I", 500) + b"mdhd" + bytes(8))
        layout = self.read(FTYP + box(b"moov", box(b"mvhd", bytes(20)) + box(b"trak", media)) + MDAT)
        self.assertEqual((False, ["mvhd"]), (layout.headers_whole, [h.type for h in layout.headers]))
        whole = box(b"mdia", box(b"mdhd", bytes(20)))
        layout = self.read(FTYP + box(b"moov", box(b"mvhd", bytes(20)) + box(b"trak", whole)) + MDAT)
        self.assertEqual((True, ["mvhd", "mdhd"]), (layout.headers_whole, [h.type for h in layout.headers]))


class AClipThatStopsBeingOne(Reading):
    def test_a_box_that_claims_more_than_the_file_holds_ends_the_walk(self):
        data = FTYP + MOOV + struct.pack(">I", 10_000) + b"mdat" + bytes(50)
        layout = self.read(data)
        self.assertEqual(["ftyp", "moov"], [b.type for b in layout.boxes])
        self.assertEqual(containers.Tail(len(FTYP + MOOV), "truncated", 10_000, "mdat"), layout.tail)
        self.assertIsNone(layout.first_mdat)
        self.assertIsNone(layout.moov_first)

    def test_a_file_cut_inside_a_box_header(self):
        layout = self.read(FTYP + MOOV + MDAT + b"\0\0\x01")
        self.assertEqual("garbage", layout.tail.reason)
        self.assertEqual(len(FTYP + MOOV + MDAT), layout.tail.offset)
        cut = self.read(FTYP + MOOV + struct.pack(">I", 1) + b"mdat" + b"\0\0")
        self.assertEqual("truncated", cut.tail.reason, "a 64-bit size cut short")

    def test_zeros_after_mdat_are_a_blank_garbage_tail(self):
        layout = self.read(FTYP + MOOV + MDAT + bytes(9556), "clip.mov")
        self.assertEqual(3, len(layout.boxes))
        self.assertEqual(containers.Tail(len(FTYP + MOOV + MDAT), "garbage", blank=True), layout.tail)

    def test_a_child_box_that_runs_past_moov_marks_moov_broken(self):
        moov = box(b"moov", box(b"mvhd", bytes(20)) + struct.pack(">I", 500) + b"udta" + bytes(8))
        layout = self.read(FTYP + moov + MDAT)
        self.assertTrue(layout.moov.broken)
        self.assertEqual(["mvhd"], [b.type for b in layout.moov.children])
        self.assertEqual("mdat", layout.boxes[-1].type, "the top level walks on")

    def test_udta_nested_in_itself_is_not_followed_down(self):
        nested = b"<x:xmpmeta/>"
        for _ in range(5000):
            nested = box(b"udta", nested)
        layout = self.read(FTYP + box(b"moov", nested) + MDAT)
        self.assertEqual(["udta"], [b.type for b in layout.moov.children[0].children])
        self.assertEqual((), layout.moov.children[0].children[0].children)

    def test_what_is_no_clip_is_unreadable(self):
        for data in (b"", b"hello, this is a text file and no clip at all", b"\x89PNG\r\n\x1a\n" + bytes(40),
                     FTYP[:10], struct.pack(">I", 4) + b"ftyp" + bytes(20)):
            self.assertIsInstance(self.read(data), containers.Unreadable, data[:12])

    def test_a_read_that_fails_part_way_is_unreadable_with_its_reason(self):
        failing = Sparse(10_000, {0: FTYP + MOOV}, fail_after=2)
        layout = containers.read(failing)
        self.assertIsInstance(layout, containers.Unreadable)
        self.assertIn("could not be read", layout.reason)
        self.assertIn("the network name is no longer available", layout.reason)


class AJpeg(Reading):
    def test_a_jpeg_with_no_xmp(self):
        data = b"\xff\xd8" + JFIF + exif_segment(0) + iptc_segment(resource(0x0404, b"\x1c\x02\x19\x00\x05Alpha")) + SOS
        layout = self.read(data, "photo.jpg")
        self.assertEqual(["other", "exif", "iptc", "sos"], [s.kind for s in layout.segments])
        self.assertIsNone(layout.xmp)
        self.assertEqual(data.index(b"\xff\xda"), layout.image_offset)
        self.assertEqual(0x0404, layout.iptc.resources[0].id)
        self.assertEqual(data.index(b"8BIM"), layout.iptc.resources[0].offset)
        self.assertEqual((0, 0), (layout.iptc.slack, layout.iptc.stray))
        self.assertIsNone(layout.tail)

    def test_two_xmp_segments_name_no_one_xmp(self):
        layout = self.read(b"\xff\xd8" + xmp_segment() + xmp_segment() + SOS, "photo.jpg")
        self.assertEqual(2, len(layout.of_kind("xmp")))
        self.assertIsNone(layout.xmp, "which of two a reader takes is not the layout's to guess")

    def test_extended_xmp_is_its_own_kind(self):
        layout = self.read(b"\xff\xd8" + xmp_segment() + segment(0xE1, b"http://ns.adobe.com/xmp/extension/\0" + bytes(60))
                           + SOS, "photo.jpg")
        self.assertEqual(["xmp", "xmp-extension", "sos"], [s.kind for s in layout.segments])
        self.assertIsNotNone(layout.xmp)

    def test_the_slack_of_each_segment(self):
        data = (b"\xff\xd8" + exif_segment(24) + xmp_segment(b"\n" + b" " * 300)
                + iptc_segment(resource(0x0404, b"\x1c\x02\x19\x00\x05Alpha"), resource(containers.PAD_RESOURCE, bytes(40))) + SOS)
        layout = self.read(data, "photo.jpg")
        self.assertEqual(24, layout.exif.slack)
        self.assertEqual(300, layout.xmp.slack, "the whitespace less the one newline a rewrite keeps")
        self.assertEqual(12 + 40, layout.iptc.slack, "the dummy 8BIM resource, whole")
        self.assertEqual(0, layout.iptc.stray)
        self.assertEqual(0, self.read(b"\xff\xd8" + xmp_segment(b"<!-- x -->") + SOS, "a.jpg").xmp.slack)

    def test_zeros_after_the_last_8bim_resource_are_stray_not_slack(self):
        layout = self.read(b"\xff\xd8" + iptc_segment(resource(0x0404, b"\x1c\x02\x19\x00\x05Alpha"), after=bytes(30))
                           + SOS, "photo.jpg")
        self.assertEqual((0, 30), (layout.iptc.slack, layout.iptc.stray))

    def test_exif_whose_structure_cannot_be_followed_has_no_slack(self):
        bad = segment(0xE1, b"Exif\0\0" + b"II*\0" + struct.pack("<I", 4000) + bytes(40))
        self.assertEqual(0, self.read(b"\xff\xd8" + bad + SOS, "photo.jpg").exif.slack)

    def test_a_jpeg_cut_before_its_picture(self):
        data = b"\xff\xd8" + JFIF + xmp_segment()
        layout = self.read(data[:-20], "photo.jpg")
        self.assertIsNone(layout.image_offset)
        self.assertEqual("truncated", layout.tail.reason)
        self.assertEqual(len(b"\xff\xd8" + JFIF), layout.tail.offset)
        self.assertEqual(["other"], [s.kind for s in layout.segments])

    def test_garbage_where_a_marker_should_be(self):
        layout = self.read(b"\xff\xd8" + JFIF + b"\x00\x11garbage" + SOS, "photo.jpg")
        self.assertEqual(containers.Tail(len(b"\xff\xd8" + JFIF), "garbage"), layout.tail)
        self.assertIsNone(layout.image_offset)

    def test_fill_bytes_before_a_marker(self):
        layout = self.read(b"\xff\xd8\xff\xff" + JFIF + SOS, "photo.jpg")
        self.assertEqual(["other", "sos"], [s.kind for s in layout.segments])


class Sparse(io.RawIOBase):
    """A read-only file of `size` bytes, zero except the `pieces` given ({offset: bytes}), counting what is read; it
    fails with the error a share that went away gives on the read after `fail_after` reads."""

    def __init__(self, size, pieces, fail_after=None):
        self.size, self.pieces, self.fail_after = size, pieces, fail_after
        self.position, self.bytes_read, self.reads = 0, 0, 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.size + offset if whence == 2 else self.position + offset
        return self.position

    def read(self, count=-1):
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            raise OSError(64, "the network name is no longer available")
        end = self.size if count < 0 else min(self.size, self.position + count)
        out = bytearray(max(0, end - self.position))
        for at, piece in self.pieces.items():
            lo, hi = max(at, self.position), min(at + len(piece), end)
            if lo < hi:
                out[lo - self.position:hi - self.position] = piece[lo - at:hi - at]
        self.bytes_read += len(out)
        self.position = end
        return bytes(out)


if __name__ == "__main__":
    unittest.main()
