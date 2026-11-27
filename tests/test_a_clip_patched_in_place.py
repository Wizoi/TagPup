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

A clip's save as its writer makes it (AClipSavedInPlace; in_place.patch_clip, the packet segment_edits.new_packet's): a
clip with no XMP is given a box at its end and no byte it held changes (the outside bytes are compared here, in the
test, never by the patch: DECISIONS 2026-11-03); a clip with a tail or two XMP boxes is not patched, nothing written and
ExifTool not asked; one open in another program is refused at once, a sentence; one killed after its record is saved,
part-way or after its last byte is told OLD, TORN (put back to its old bytes and size) or NEW by its bytes. An XMP box
of any size is saved (owner, 2026-11-12): a Premiere packet of about 200 KB, mostly XML, as the 1 live box over 64 KB
holds, takes a keyword in its padding, and a save of it killed half way is put back.

A clip's QuickTime title (DECISIONS 2026-11-08; AClipsQuickTimeTitle, by hand, and AClipSavedInPlace, a real one): a
clear frees its one title item in moov/udta/meta/ilst (four type bytes, `free`), whatever else the patch writes, and an
undo gives it back to the one free box of that item list holding exactly its text, the clip byte for byte as before; two
title items, one directly in udta (ffmpeg's .mov), none the walk finds (a title in an 'mdta' keys meta), a free box
holding another text, and a give back to a clip holding a title already, are the rewrite's. A clear cut short is settled by its four bytes.

A clip's Windows Tags (DECISIONS 2026-11-09; AClipsWindowsTags, by hand, and AClipSavedInPlace, a real ffmpeg clip given
a hand-built Xtra as Windows writes one): their move into XMP frees the one moov/udta/Xtra (four type bytes, `free`) in
the same patch that writes the XMP, and an undo gives it back to the one free box of udta holding exactly those texts,
the clip byte for byte as before, a free box another program left in udta beside it (72+ live clips hold one) not
taken; no Xtra, an empty one (10 live), two, one holding no WM/Category, and a give back to a clip holding an Xtra again
are the rewrite's, nothing written, not even the XMP. A clear cut short is settled by its four bytes.
"""
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus  # noqa: E402
import own_home  # noqa: E402
import test_xmp_packet_edits_what_it_owns as shapes  # noqa: E402
from tagpup import config  # noqa: E402
from tagpup.core import fields, processes  # noqa: E402
from tagpup.core.result import Refused  # noqa: E402
from tagpup.files import clip_patch, containers, exiftool_session, in_place, segment_edits, xmp_packet  # noqa: E402
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


TITLE = "Slipway at dusk"


def data_atom(text, kind=b"\0\0\0\1"):
    """A title item's value: a `data` atom of type `kind` (UTF-8, 1, as every live title's is), locale 0, `text`."""
    return box(b"data", kind + bytes(4) + text.encode("utf-8"))


def title_item(text=TITLE):
    return box(b"\xa9nam", data_atom(text))


def meta(*items, handler=b"mdir", full=True):
    """moov/udta/meta holding an hdlr of `handler` and an ilst of `items`: an MP4's (a full box's version and flags
    first) or, `full` False, QuickTime's form (its children at once)."""
    hdlr = box(b"hdlr", bytes(8) + handler + bytes(12) + b"\0")
    return box(b"meta", (bytes(4) if full else b"") + hdlr + box(b"ilst", b"".join(items)))


def titled_moov(*in_udta):
    return box(b"moov", MVHD + TRAK + box(b"udta", b"".join(in_udta)))


#: Windows Tags of the clips built here: fictional, in the shape the live ones have (tag paths).
WINDOWS_TAGS = ("People/Wren Ashcombe", "Trips/Coast", "Trips/Pier")


def xtra_entry(name, *values, kind=8):
    """One entry of a Microsoft Xtra box: `name` and its `values`, each UTF-16LE text ended by a NUL (type 8, as every
    live WM/Category value is)."""
    held = b"".join(struct.pack(">IH", 6 + len(data), kind) + data
                    for data in ((text + "\0").encode("utf-16-le") for text in values))
    return struct.pack(">II", 12 + len(name) + len(held), len(name)) + name + struct.pack(">I", len(values)) + held


def xtra(*texts, before=b"", kind=b"Xtra"):
    """An Xtra box as Windows writes a clip's Tags: entries `before`, then WM/Category holding `texts`."""
    return box(kind, before + (xtra_entry(b"WM/Category", *texts) if texts else b""))


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

    def test_a_shorter_packet_leaves_none_of_the_old_ones_tail_in_the_free_box(self):
        old = OLD.replace(b"      \n" + END, b"Hiking\n" + END)
        self.assertEqual(1, old[len(old) - 30:].count(b"Hiking"))
        data = FTYP + MOOV + xmp_box(old) + MDAT
        for cut in (40, 36):
            with self.subTest(cut=cut):
                found, after, _ = self.placed(data, sized(len(old) - cut, ("Lakes",)), clip_patch.IN_PLACE)
                self.assertNotIn(b"Hiking", after, "the old packet's tail inside the new free box is blanked")
                self.assertEqual(len(data), found.size)

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
                self.assertEqual(clip_patch.APPENDED, clip_patch.patch(layout_of(nested), new).how)

    def test_a_movs_xmp_in_udta_takes_the_free_box_after_it_and_moov_keeps_its_size(self):
        data = FTYP + mov(xmp_box(OLD, b"XMP_"), box(b"free", bytes(60))) + MDAT
        new = sized(len(OLD) + 50)
        found, after, held = self.placed(data, new, clip_patch.IN_PLACE)
        self.assertEqual(new, held)
        self.assertEqual([("moov", "udta", "XMP_")], [path for path, _ in layout_of(after).xmp])
        self.assertEqual(clip_patch.APPENDED, clip_patch.patch(layout_of(data), sized(len(OLD) + 69)).how,
                         "a .mov's XMP_ does not grow: it is retired and the packet appended")

    def test_an_mp4s_xmp_in_the_item_list_that_does_not_fit_is_retired_and_appended_with_mdat_unmoved(self):
        # The owner's concert clip (#2908): moov/udta/meta/ilst/XMP_ of 341 bytes, a title item beside it, mdat after.
        ilst = box(b"ilst", xmp_box(OLD, b"XMP_") + box(b"\xa9nam", b"x" * 20))
        data = FTYP + box(b"moov", MVHD + box(b"udta", box(b"meta", bytes(4) + ilst))) + MDAT
        new = sized(len(OLD) + 400, ("Lakes",))
        found, after, held = self.placed(data, new, clip_patch.APPENDED)
        old = layout_of(data).xmp[0][1]
        self.assertEqual(new, held)
        self.assertEqual(((old.offset + 4, b"free"), (old.payload, b" " * len(OLD)), (len(data), after[len(data):])),
                         found.ranges)
        self.assertEqual(data.index(MDAT), after.index(MDAT), "mdat did not move")
        self.assertEqual([("uuid",)], [path for path, _ in layout_of(after).xmp])
        self.assertNotIn(b"Hiking", after)

    def test_a_packet_one_byte_longer_than_its_slot_is_appended_or_the_rewrites(self):
        data = FTYP + MOOV + xmp_box(OLD) + MDAT
        found, after, held = self.placed(data, sized(len(OLD) + 1, ("Lakes",)), clip_patch.APPENDED)
        old = layout_of(data).xmp[0][1]
        self.assertEqual(((old.offset + 4, b"free"), (old.payload, b" " * len(OLD)), (len(data), after[len(data):])),
                         found.ranges)
        self.assertNotIn(b"Hiking", after, "the retired box keeps none of the old packet")
        self.assertEqual(1, after.count(b"Lakes"))
        self.assertEqual(["ftyp", "moov", "free", "mdat", "uuid"], [one.type for one in layout_of(after).boxes])
        nested = FTYP + mov(xmp_box(OLD, b"XMP_")) + MDAT
        self.assertEqual(clip_patch.APPENDED, clip_patch.patch(layout_of(nested), sized(len(OLD) + 1)).how)

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

    def test_zero_padding_after_the_last_box_has_the_packet_written_over_it(self):
        """A Canon clip as the owner has it (2014 Leavenworth, moov first, then mdat, then 9,556 zero bytes): the box lands
        where a reader's walk ends, over the zeros, and the file is the length it was; one longer than the zeros grows it."""
        for zeros, new, size in ((9556, sized(len(OLD)), None), (20, sized(len(OLD)), "longer"), (8, sized(len(OLD)), "shorter")):
            with self.subTest(zeros=zeros, size=size):
                whole = FTYP + MOOV + MDAT
                data = whole + bytes(zeros)
                layout = layout_of(data)
                self.assertEqual(zeros > 0, layout.tail is not None and layout.tail.blank)
                found = clip_patch.patch(layout, new)
                self.assertEqual(clip_patch.APPENDED, found.how)
                (at, written), = found.ranges
                self.assertEqual(len(whole), at, "over the zeros, where the walk ended")
                after = applied(data, found)
                self.assertEqual(max(len(data), len(whole) + len(written)), len(after))
                read = layout_of(after)
                self.assertEqual(1, len(read.xmp), "the walk now finds the box")
                self.assertEqual(whole, after[:len(whole)], "no byte of a box moved")
                self.assertTrue(read.tail is None or read.tail.blank)

    def test_a_tail_of_anything_but_zeros_is_still_the_rewrites(self):
        whole = FTYP + MOOV + MDAT
        for name, tail in (("one byte set", bytes(100) + b"\x01" + bytes(100)),
                           ("more zeros than are read", bytes(containers.BLANK_MOST + 1))):
            with self.subTest(name):
                layout = layout_of(whole + tail)
                self.assertFalse(layout.tail.blank)
                self.assertIsNone(clip_patch.patch(layout, sized(len(OLD))))

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
                self.assertEqual(((xmp.offset + 4, b"free"), (xmp.payload, b" " * len(OLD)),
                                  (held.file_size, xmp_box(sized(len(OLD) + 6000)))), appended.ranges)

    def test_a_free_run_of_4_gb_or_more_after_the_box_is_the_rewrites(self):
        """A 32-bit size cannot say the free box the run would leave, and no clip has one (0 of 2,184): not placed."""
        xmp = Box("uuid", 1100, 24 + len(OLD), 24, uuid=XMP_UUID)
        free = Box("free", xmp.end, 5 * GB, 16)
        boxes = (Box("ftyp", 0, 32, 8), Box("moov", 32, 1068, 8, children=(Box("mvhd", 40, 108, 8),)), xmp, free,
                 Box("mdat", free.end, 64, 8))
        self.assertIsNone(clip_patch.patch(Video(free.end + 64, boxes), sized(len(OLD))))


class AClipsQuickTimeTitle(unittest.TestCase):
    """A clear's and an undo's title ranges (clip_patch.patch's `title`) on clips built by hand."""

    def cleared(self, data, packet):
        """`data` with its title cleared (the packet None: the XMP as it is), its ranges in order and apart, every box
        but the title item where it was; the bytes after."""
        found = clip_patch.patch(layout_of(data), packet, "")
        self.assertIsNotNone(found)
        after = applied(data, found)
        untitled = [each for each in kept(layout_of(data)) if each[0][-1] != containers.TITLE_ITEM]
        self.assertEqual(untitled, kept(layout_of(after)))
        return after

    def given_back(self, data, title=TITLE):
        """The undo's patch of `data` giving `title` back, its free boxes read as in_place reads them; None or bytes."""
        layout = layout_of(data)
        freed = tuple((one, data[one.payload:one.end]) for one in clip_patch.freed_titles(layout))
        found = clip_patch.patch(layout, None, title, freed)
        return None if found is None else applied(data, found)

    def test_a_cleared_title_is_freed_and_its_undo_gives_it_back_byte_for_byte_in_each_shape(self):
        free = box(b"free", bytes(400))
        shapes = {
            "an MP4's meta, moov first": FTYP + titled_moov(meta(title_item())) + MDAT,
            "QuickTime's meta, moov last": FTYP + MDAT + titled_moov(meta(box(b"\xa9ART", data_atom("x")), title_item(),
                                                                          full=False)),
            "an XMP box with a free run after it, beside the title": FTYP + titled_moov(meta(title_item()))
                                                                     + xmp_box(OLD) + free + MDAT,
            "an XMP box ending the file": FTYP + MDAT + titled_moov(meta(title_item())) + xmp_box(OLD),
        }
        for name, data in shapes.items():
            for packet in (None, sized(len(OLD) + 300, ("Lakes",))):
                with self.subTest(name, packet=packet is not None):
                    item = layout_of(data).title_item
                    self.assertIsNotNone(item)
                    cleared = self.cleared(data, packet)
                    self.assertEqual(cleared[:item.offset + 4] + b"free", cleared[:item.offset + 8])
                    self.assertEqual(((), None), (layout_of(cleared).titles, self.given_back(cleared, "Slipway")),
                                     "no title left, and none given back of another text")
                    back = self.given_back(cleared)
                    self.assertEqual(data if packet is None else applied(data, clip_patch.patch(layout_of(data), packet)),
                                     back)

    def test_a_title_the_patch_does_not_understand_is_the_rewrites(self):
        cleared = {
            "two title items (1 live clip)": FTYP + titled_moov(meta(title_item(), title_item("Pier"))) + MDAT,
            "a title directly in udta (ffmpeg's .mov)": FTYP + box(b"wide") + MDAT + mov(box(b"\xa9nam", b"x")),
            "one in udta and one in the item list": FTYP + titled_moov(title_item(), meta(title_item())) + MDAT,
            "a title in an 'mdta' meta (no live clip)": FTYP + titled_moov(meta(title_item(), handler=b"mdta")) + MDAT,
            "a title the walk does not find: an 'mdta' keys meta in moov, its item numbered (no live clip)":
                FTYP + box(b"moov", MVHD + TRAK + meta(box(b"\0\0\0\1", data_atom(TITLE)), handler=b"mdta")) + MDAT,
        }
        for name, data in cleared.items():
            with self.subTest(name):
                self.assertIsNone(clip_patch.patch(layout_of(data), None, ""))
                self.assertIsNone(clip_patch.patch(layout_of(data), OLD, ""), "nor its XMP written without it")
        freed = {
            "a free box of another text": box(b"free", data_atom("Pier")),
            "a free box of a text that is not UTF-8": box(b"free", data_atom(TITLE, b"\0\0\0\x15")),
            "a free box of two data atoms": box(b"free", data_atom(TITLE) + data_atom(TITLE)),
            "two free boxes of the text": box(b"free", data_atom(TITLE)) + box(b"free", data_atom(TITLE)),
        }
        for name, items in freed.items():
            with self.subTest(name):
                self.assertIsNone(self.given_back(FTYP + titled_moov(meta(items)) + MDAT))
        held = FTYP + titled_moov(meta(box(b"free", data_atom(TITLE)), title_item("Pier"))) + MDAT
        self.assertEqual((), clip_patch.freed_titles(layout_of(held)))
        self.assertIsNone(self.given_back(held), "a title item there already: another program wrote one")


class AClipsWindowsTags(unittest.TestCase):
    """A clear's and an undo's Xtra ranges (clip_patch.patch's `category`) on clips built by hand."""

    def tagged(self, data, category, packet=None):
        """The patch of `data` writing `category` (() a clear, texts an undo), its boxes read as in_place reads them
        (clip_patch.xtra_boxes); None or the bytes after."""
        layout = layout_of(data)
        read = tuple((one, data[one.offset:one.end]) for one in clip_patch.xtra_boxes(layout, category))
        found = clip_patch.patch(layout, packet, None, (), category, read)
        return None if found is None else applied(data, found)

    def test_a_clear_frees_the_xtra_with_the_xmp_and_its_undo_gives_it_back_byte_for_byte_in_each_shape(self):
        others = xtra_entry(b"WM/EncodingTime", "x") + xtra_entry(b"WM/MediaClassPrimaryID", "y")
        shapes = {
            "an MP4, moov first, the Xtra last in udta (1,872 live)": FTYP + titled_moov(meta(), xtra(*WINDOWS_TAGS))
                                                                     + MDAT,
            "a .mov, moov last, other Windows entries first (196 live)": FTYP + MDAT + titled_moov(
                xtra(*WINDOWS_TAGS, before=others), meta(title_item())),
            "a free box of another program's after it in udta (14 live)": FTYP + titled_moov(
                meta(), xtra(*WINDOWS_TAGS), box(b"free", bytes(40))) + MDAT,
            "a free box of this clip's Tags' shape before it, holding other texts": FTYP + titled_moov(
                xtra("Trips/Coast", kind=b"free"), xtra(*WINDOWS_TAGS)) + MDAT,
            "an XMP box ending the file, which the packet overwrites": FTYP + MDAT + titled_moov(xtra(*WINDOWS_TAGS))
                                                                       + xmp_box(OLD),
        }
        for name, data in shapes.items():
            for packet in (None, sized(len(OLD) + 300, ("Lakes",))):
                with self.subTest(name, packet=packet is not None):
                    one = layout_of(data).xtra
                    cleared = self.tagged(data, (), packet)
                    self.assertIsNotNone(cleared)
                    self.assertEqual(cleared[:one.offset + 4] + b"free", cleared[:one.offset + 8], "four bytes freed")
                    self.assertEqual([each for each in kept(layout_of(data)) if each[0][-1] != "Xtra"],
                                     kept(layout_of(cleared)), "no other box moves")
                    self.assertEqual((None, None), (layout_of(cleared).xtra, self.tagged(cleared, WINDOWS_TAGS[:2])),
                                     "no Xtra left, and none given back of other texts")
                    xmp_only = data if packet is None else applied(data, clip_patch.patch(layout_of(data), packet))
                    self.assertEqual(xmp_only, self.tagged(cleared, WINDOWS_TAGS))
                    self.assertEqual(xmp_only, self.tagged(cleared, WINDOWS_TAGS[::-1]),
                                     "the texts as a read gives them, in any order (fields.same_read, #2802)")

    def test_an_xtra_the_patch_does_not_understand_is_the_rewrites_and_nothing_is_written(self):
        cleared = {
            "no Xtra (76 live)": FTYP + titled_moov(meta()) + MDAT,
            "an empty Xtra (10 live)": FTYP + titled_moov(meta(), box(b"Xtra")) + MDAT,
            "an Xtra holding no WM/Category": FTYP + titled_moov(box(b"Xtra", xtra_entry(b"WM/EncodingTime", "x")))
                                              + MDAT,
            "an Xtra whose entries do not fill it": FTYP + titled_moov(box(b"Xtra", xtra_entry(b"WM/Category", "x")
                                                                           + bytes(3))) + MDAT,
            "two Xtra (no live clip)": FTYP + titled_moov(xtra(*WINDOWS_TAGS), xtra(*WINDOWS_TAGS)) + MDAT,
        }
        for name, data in cleared.items():
            with self.subTest(name):
                self.assertIsNone(self.tagged(data, ()))
                self.assertIsNone(self.tagged(data, (), OLD), "nor its XMP written without it")
        freed = {
            "a free box of other texts": xtra("Trips/Coast", kind=b"free"),
            "two free boxes of the texts": xtra(*WINDOWS_TAGS, kind=b"free") * 2,
            "an Xtra there again, Windows' Tags written since": xtra(*WINDOWS_TAGS, kind=b"free") + xtra("Trips/Coast"),
        }
        for name, in_udta in freed.items():
            with self.subTest(name):
                self.assertIsNone(self.tagged(FTYP + titled_moov(in_udta) + MDAT, WINDOWS_TAGS))

    def test_the_four_bytes_are_written_only_over_the_type_they_were_read_as(self):
        """The patch reads each box whole and checks its type in the bytes it read, not only in the walk's layout."""
        data = FTYP + titled_moov(meta(), xtra(*WINDOWS_TAGS)) + MDAT
        layout = layout_of(data)
        one = layout.xtra
        moved = data[one.offset:one.offset + 4] + b"skip" + data[one.offset + 8:one.end]
        self.assertIsNone(clip_patch.patch(layout, None, None, (), (), ((one, moved),)))


class AFreedBoxOfEitherKind(unittest.TestCase):
    """A title item and an Xtra are freed and given back by one mechanism (clip_patch._retyped), keyed by the box's kind."""

    def test_a_clear_and_a_give_back_of_either_kind_go_through_the_one_mechanism(self):
        titled = FTYP + titled_moov(meta(box(b"free", data_atom(TITLE)))) + MDAT
        tagged = FTYP + titled_moov(meta(), xtra(*WINDOWS_TAGS)) + MDAT
        freed_tags = FTYP + titled_moov(meta(), xtra(*WINDOWS_TAGS, kind=b"free")) + MDAT
        layout = layout_of(titled)
        title_read = tuple((one, titled[one.payload:one.end]) for one in clip_patch.freed_titles(layout))
        asked = {
            "a title given back": lambda: clip_patch.patch(layout, None, TITLE, title_read),
            "an Xtra freed": lambda: clip_patch.patch(layout_of(tagged), None, None, (), (), tuple(
                (one, tagged[one.offset:one.end]) for one in clip_patch.xtra_boxes(layout_of(tagged), ()))),
            "an Xtra given back": lambda: clip_patch.patch(layout_of(freed_tags), None, None, (), WINDOWS_TAGS, tuple(
                (one, freed_tags[one.offset:one.end]) for one in clip_patch.xtra_boxes(layout_of(freed_tags), WINDOWS_TAGS))),
        }
        for name, ask in asked.items():
            with self.subTest(name), mock.patch.object(clip_patch, "_retyped", wraps=clip_patch._retyped) as through:
                found = ask()
                self.assertIsNotNone(found)
                self.assertEqual(1, len(found.ranges))
                through.assert_called_once()


class APixelClipsXmpItem(unittest.TestCase):
    """The owner's concert clip (#2908): its only XMP is an `XMP_` item of the item list holding a `data` atom that wraps a
    bare packet (a Google camera's), 341 bytes, in moov before mdat. A keyword save reads the packet out of the atom,
    retires the item and appends the packet in a box of its own: the clip is not refused, mdat does not move."""

    def test_a_save_reads_the_packet_out_of_the_items_data_atom_and_appends_the_new_one(self):
        inner = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                 b'<rdf:Description rdf:about="" xmlns:GCamera="http://ns.google.com/photos/1.0/camera/" '
                 b'GCamera:GFileMetadata="AAAA"/></rdf:RDF></x:xmpmeta>\n')
        item = box(b"XMP_", box(b"data", b"\0\0\0\1\0\0\0\0" + inner))
        ilst = box(b"ilst", item + box(b"\xa9nam", b"x" * 20))
        data = FTYP + box(b"moov", MVHD + box(b"udta", box(b"meta", bytes(4) + ilst))) + MDAT
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "pixel.mp4")
            with open(path, "wb") as handle:
                handle.write(data)
            saved = []
            stale = in_place.patch_clip(path, lambda old: segment_edits.new_packet(old, keywords(("Wren Ashcombe",))),
                                        lambda record, before: saved.append(record))
            self.assertIsNotNone(stale, "the clip was refused: its packet was read with the data atom's bytes")
            with open(path, "rb") as handle:
                after = handle.read()
        self.assertEqual(data.index(MDAT), after.index(MDAT), "mdat did not move")
        self.assertEqual(1, len(saved))
        self.assertIn(b"GCamera:GFileMetadata", after[len(data):], "the packet's own properties are kept")
        self.assertIn(b"Wren Ashcombe", after[len(data):])
        self.assertEqual(1, after.count(b"Wren Ashcombe"))


class FfmpegClips(unittest.TestCase):
    """Clips made by the real ffmpeg (once, and kept: tests/corpus.py) in a home of the class's own, copied for each test, and read back by ExifTool and
    ffprobe."""

    @classmethod
    def setUpClass(cls):
        cls.folder = own_home.for_class(cls, "tagpup_clip_patch_").root
        cls.made = {}
        for name, extra in (("first.mp4", ("-movflags", "+faststart")), ("last.mp4", ()), ("clip.mov", ())):
            path = os.path.join(cls.folder, "made_" + name)
            corpus.clip(path, ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10",
                               "-t", "1", "-pix_fmt", "yuv420p", *extra], TOOLS.ffmpeg)
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

@unittest.skipIf(EXIFTOOL is None or TOOLS.problem, "ExifTool or ffmpeg is not installed on this machine")
class AClipMadeByFfmpegReadBack(FfmpegClips):
    """Clips made by the real ffmpeg, patched, and read back by ExifTool and ffprobe."""

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

    def test_a_clip_with_no_xmp_is_given_the_blank_packet_edited_and_it_reads_back(self):
        """The first save of a clip with no XMP (2,104 live): xmp_packet.blank() edited, appended in a uuid box; its
        padding takes the first save, so the box is the blank's length."""
        blank = xmp_packet.blank()
        self.assertEqual({}, xmp_packet.read(blank))
        words, caption = ("Wren Ashcombe", "Hiking"), "At the lake"
        edited = xmp_packet.edit(blank, {"dc:subject": list(words), "dc:description": caption,
                                         "lr:hierarchicalSubject": ["People/Wren Ashcombe"]})
        self.assertEqual(len(blank), len(edited.body))
        for name in self.made:
            with self.subTest(name):
                path = self.clip(name)
                self.patched(path, edited.body, clip_patch.APPENDED)
                self.read_back(path, words)
                with ExifToolSession(executable=EXIFTOOL) as session:
                    held = session.get_tags([path], tags=["XMP:Description", "XMP:HierarchicalSubject"],
                                            params=["-a"])[0]
                self.assertEqual((caption, "People/Wren Ashcombe"),
                                 (held.get("XMP:Description"), held.get("XMP:HierarchicalSubject")))

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


def keywords(words):
    """The journal's values of a keyword save of `words`, as tagging's planner hands them to the writer."""
    return {field: fields.field_values(value)
            for field, value in fields.keyword_fields(*fields.expand_tag_fields(words)).items()}


class Crash(Exception):
    """The process killed."""


@unittest.skipIf(EXIFTOOL is None or TOOLS.problem, "ExifTool or ffmpeg is not installed on this machine")
class AClipSavedInPlace(FfmpegClips):
    """A keyword save of a real clip through in_place.patch_clip, the journal's `saving` a stand-in that keeps the
    record."""

    def setUp(self):
        self.saved = []
        # The patch never asks ExifTool: only the test's own read back, after it, does.
        self.no_exiftool = mock.patch.object(exiftool_session.ExifToolSession, "__init__",
                                             side_effect=AssertionError("ExifTool asked by a clip's patch"))

    def save(self, path, words):
        with self.no_exiftool:
            return in_place.patch_clip(path, lambda old: segment_edits.new_packet(old, keywords(words)),
                                       lambda record, before: self.saved.append((record, before)))

    def bytes_of(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def test_a_clip_with_no_xmp_gets_a_box_at_its_end_and_every_byte_it_held_is_kept(self):
        for name in self.made:
            with self.subTest(name):
                path, self.saved = self.clip(name), []
                before = self.bytes_of(path)
                self.assertEqual(("IPTC:Keywords", "EXIF:XPKeywords"), self.save(path, ("Wren Ashcombe", "Hiking")))
                after = self.bytes_of(path)
                self.assertEqual(before, after[:len(before)])
                self.assertEqual(in_place.NEW, in_place.held(path, *self.saved[-1]))
                self.read_back(path, ("Wren Ashcombe", "Hiking"))
                self.save(path, ("Wren Ashcombe", "Hiking"))
                self.assertEqual((1, after), (len(self.saved), self.bytes_of(path)), "a save made twice writes nothing")

    def test_a_movs_xmp_and_the_bare_packet_last_in_an_mp4_are_saved_where_they_are(self):
        mov = self.clip("clip.mov")
        with ExifToolSession(executable=EXIFTOOL) as session:
            session.set_tags([mov], tags={"XMP:Subject": ["Hiking"]}, params=["-m", "-overwrite_original"])
        bare = self.clip("last.mp4")
        with open(bare, "ab") as handle:
            handle.write(xmp_box(shapes.BARE))
        for path, how in ((mov, clip_patch.IN_PLACE), (bare, clip_patch.GROWN)):
            with self.subTest(how):
                before = self.bytes_of(path)
                with open(path, "rb") as handle:
                    (_, box), = containers.read(handle).xmp
                self.save(path, ("Wren Ashcombe",))
                after = self.bytes_of(path)
                self.assertEqual(before[:box.offset], after[:box.offset])
                if how == clip_patch.IN_PLACE:
                    self.assertEqual((len(before), before[box.end:]), (len(after), after[box.end:]))
                else:
                    self.assertGreater(len(after), len(before))
                self.assertEqual(in_place.NEW, in_place.held(path, *self.saved[-1]))
                self.read_back(path, ("Wren Ashcombe",))

    def test_a_clip_with_a_tail_or_two_xmp_boxes_is_not_patched_and_nothing_is_written(self):
        tail = self.clip("first.mp4")
        with open(tail, "ab") as handle:
            handle.write(b"\x00\x00\x00\x10\x01\x02\x03\x04" + bytes(8))
        two = self.clip("last.mp4")
        with open(two, "ab") as handle:
            handle.write(xmp_box(OLD) + xmp_box(OLD))
        for path in (tail, two):
            with self.subTest(os.path.basename(path)):
                before = self.bytes_of(path)
                self.assertIsNone(self.save(path, ("Wren Ashcombe",)))
                self.assertEqual(([], before), (self.saved, self.bytes_of(path)))

    def test_a_share_that_fails_part_way_through_the_walk_is_could_not_be_read_not_no_patch(self):
        """#2540: a read that fails while the clip's boxes are walked raises, so the caller says the clip could not be
        read (and tries again), never that it cannot be patched."""
        path = self.clip("first.mp4")
        before = self.bytes_of(path)
        gone = OSError(64, "The specified network name is no longer available")
        with mock.patch.object(containers, "_read_video", side_effect=gone), self.assertRaises(OSError) as raised:
            self.save(path, ("Wren Ashcombe",))
        self.assertEqual(64, raised.exception.errno)
        self.assertEqual(([], before), (self.saved, self.bytes_of(path)))

    @unittest.skipUnless(os.name == "nt", "a file open elsewhere is refused by Windows' sharing")
    def test_a_clip_open_in_another_program_is_refused_at_once_with_a_sentence(self):
        path = self.clip("first.mp4")
        before = self.bytes_of(path)
        with open(path, "rb"):
            with self.assertRaises(Refused) as refused:
                self.save(path, ("Wren Ashcombe",))
        self.assertIn("open in another program", str(refused.exception))
        self.assertEqual(([], before), (self.saved, self.bytes_of(path)))
        self.save(path, ("Wren Ashcombe",))
        self.read_back(path, ("Wren Ashcombe",))

    def test_a_save_killed_part_way_is_told_by_its_bytes_and_a_torn_one_put_back(self):
        """ExifTool's uuid box before mdat, too small for the new packet: retired, and the packet appended (three
        ranges, one past the old end)."""
        words = ("Wren Ashcombe",) + tuple("word %d" % i for i in range(200))
        path = self.clip("first.mp4")
        with ExifToolSession(executable=EXIFTOOL) as session:
            session.set_tags([path], tags={"XMP:Subject": ["Hiking"]}, params=["-m", "-overwrite_original"])
        original = self.bytes_of(path)

        def killed_after_saving(record, before):
            self.saved.append((record, before))
            raise Crash("killed once its record was saved")

        def torn(handle, changes):
            (at, first), *_, (end, last) = changes
            for where, written in ((at, first), (end, last[:len(last) // 2])):
                handle.seek(where)
                handle.write(written)
            handle.flush()
            raise Crash("killed half way")

        with self.no_exiftool, self.assertRaises(Crash):
            in_place.patch_clip(path, lambda old: segment_edits.new_packet(old, keywords(words)),
                                killed_after_saving)
        self.assertEqual((original, in_place.OLD), (self.bytes_of(path), in_place.held(path, *self.saved[-1])))
        with mock.patch.object(in_place, "_write", torn), self.assertRaises(Crash):
            self.save(path, words)
        record, before = self.saved[-1]
        self.assertEqual((3, len(original), in_place.TORN),
                         (len(record["ranges"]), record["size"], in_place.held(path, record, before)))
        in_place.put_back(path, record, before)
        self.assertEqual(original, self.bytes_of(path))
        self.save(path, words)
        self.assertEqual(in_place.NEW, in_place.held(path, *self.saved[-1]))
        self.read_back(path, words)

    def test_a_premiere_packet_over_64_kb_and_a_bare_one_are_saved_and_a_save_killed_half_way_is_put_back(self):
        """No size of XMP box is refused (owner, 2026-11-12): the Premiere packet of about 200 KB takes the keywords in
        its padding, one range over its box; the bare one grows where it ends the file. A save killed half way through
        its range is TORN, put back to the clip as it was, saved again NEW, and read back."""
        words = ("Wren Ashcombe", "Hiking")

        def torn(handle, changes):
            (at, written), = changes
            handle.seek(at)
            old = handle.read(len(written))
            differ = [n for n, (a, b) in enumerate(zip(old, written)) if a != b] + [len(old)] * (len(written) > len(old))
            handle.seek(at)
            handle.write(written[:(differ[0] + differ[-1]) // 2 + 1])
            handle.flush()
            raise Crash("killed half way")

        for shape, how in ((corpus.premiere(), clip_patch.IN_PLACE), (shapes.BARE, clip_patch.GROWN)):
            with self.subTest(how):
                path, self.saved = self.clip("last.mp4"), []
                with open(path, "ab") as handle:
                    handle.write(xmp_box(shape))
                original = self.bytes_of(path)
                with mock.patch.object(in_place, "_write", torn), self.assertRaises(Crash):
                    self.save(path, words)
                record, before = self.saved[-1]
                self.assertEqual((1, in_place.TORN), (len(record["ranges"]), in_place.held(path, record, before)))
                in_place.put_back(path, record, before)
                self.assertEqual(original, self.bytes_of(path))
                self.save(path, words)
                self.assertEqual(in_place.NEW, in_place.held(path, *self.saved[-1]))
                grew = len(self.bytes_of(path)) - len(original)
                self.assertEqual(how == clip_patch.GROWN, grew > 0, grew)
                self.read_back(path, words)

    def test_a_title_clear_killed_part_way_is_told_by_its_four_bytes_and_its_undo_gives_the_title_back(self):
        path = os.path.join(self.folder, "titled.mp4")
        corpus.clip(path, ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10", "-t", "1",
                           "-pix_fmt", "yuv420p", "-metadata", "title=" + TITLE], TOOLS.ffmpeg)
        original = self.bytes_of(path)

        def titled(title, saving=None):
            with self.no_exiftool:
                return in_place.patch_clip(path, lambda old: (old, ()),
                                           saving or (lambda record, before: self.saved.append((record, before))),
                                           title=title)

        def killed_after_saving(record, before):
            self.saved.append((record, before))
            raise Crash("killed once its record was saved")

        def torn(handle, changes):
            (at, written), = changes
            handle.seek(at)
            handle.write(written[:2])
            handle.flush()
            raise Crash("killed half way")

        with self.assertRaises(Crash):
            titled("", killed_after_saving)
        record, before = self.saved[-1]
        self.assertEqual((1, 4), (len(record["ranges"]), record["ranges"][0]["len"]), "four bytes, no other")
        self.assertEqual((original, in_place.OLD), (self.bytes_of(path), in_place.held(path, record, before)))
        with mock.patch.object(in_place, "_write", torn), self.assertRaises(Crash):
            titled("")
        self.assertEqual(in_place.TORN, in_place.held(path, *self.saved[-1]))
        in_place.put_back(path, *self.saved[-1])
        self.assertEqual(original, self.bytes_of(path))
        titled("")
        self.assertEqual(in_place.NEW, in_place.held(path, *self.saved[-1]))
        with ExifToolSession(executable=EXIFTOOL) as session:
            self.assertIsNone(session.get_tags([path], tags=["QuickTime:Title"])[0].get("QuickTime:Title"))
        titled(TITLE)
        self.assertEqual((in_place.NEW, original), (in_place.held(path, *self.saved[-1]), self.bytes_of(path)),
                         "the undo gives the title back, the clip as ffmpeg made it")

    def test_windows_tags_moved_into_xmp_free_the_xtra_in_one_patch_settled_by_its_bytes_and_undone(self):
        """The one-time move (DECISIONS 2026-11-09) of a real clip's Windows Tags: its XMP appended and its Xtra freed in
        one patch, one record; killed after its record, OLD; torn, put back; written, NEW, ExifTool reading the tags in
        XMP and no Category; undone, the Xtra given back and the XMP emptied, Windows' Tags read again."""
        path = with_windows_tags(self.clip("last.mp4"), WINDOWS_TAGS)
        original = self.bytes_of(path)
        moved = {**keywords(WINDOWS_TAGS), fields.CLIP_CATEGORY: []}

        def written(values, saving=None):
            category = tuple(values[fields.CLIP_CATEGORY])
            with self.no_exiftool:
                return in_place.patch_clip(path, lambda old: segment_edits.new_packet(old, values),
                                           saving or (lambda record, before: self.saved.append((record, before))),
                                           category=category)

        def killed_after_saving(record, before):
            self.saved.append((record, before))
            raise Crash("killed once its record was saved")

        def torn(handle, changes):
            (at, four), *_ = changes
            handle.seek(at)
            handle.write(four)
            handle.flush()
            raise Crash("killed half way: the Xtra freed, the XMP not appended")

        with self.assertRaises(Crash):
            written(moved, killed_after_saving)
        record, before = self.saved[-1]
        self.assertEqual([4, len(xmp_packet.blank()) + 24], [one["len"] for one in record["ranges"]],
                         "one record: the four bytes and the appended XMP box")
        self.assertEqual((original, in_place.OLD), (self.bytes_of(path), in_place.held(path, record, before)))
        with mock.patch.object(in_place, "_write", torn), self.assertRaises(Crash):
            written(moved)
        self.assertEqual(in_place.TORN, in_place.held(path, *self.saved[-1]))
        in_place.put_back(path, *self.saved[-1])
        self.assertEqual(original, self.bytes_of(path))
        written(moved)
        self.assertEqual(in_place.NEW, in_place.held(path, *self.saved[-1]))
        subject = moved["XMP:Subject"]
        self.assertEqual((subject, None), windows_tags_read(path), "the tags in XMP, Windows' Tags gone")
        self.read_back(path, subject)
        self.assertIsNone(written(moved), "a second move finds no Xtra: the rewrite's, nothing written")
        written({**keywords(()), fields.CLIP_CATEGORY: list(WINDOWS_TAGS)})
        self.assertEqual(in_place.NEW, in_place.held(path, *self.saved[-1]))
        self.assertEqual(([], list(WINDOWS_TAGS)), windows_tags_read(path), "the undo gives Windows' Tags back")
        self.assertEqual(original, self.bytes_of(path)[:len(original)], "every byte the clip held is as it was")

    def test_a_clip_with_no_windows_tags_is_not_patched_and_nothing_is_written(self):
        for name, texts in (("no Xtra", None), ("an empty Xtra", ())):
            with self.subTest(name):
                path = self.clip("last.mp4")
                if texts is not None:
                    with_windows_tags(path, texts)
                before, self.saved = self.bytes_of(path), []
                with self.no_exiftool:
                    self.assertIsNone(in_place.patch_clip(
                        path, lambda old: segment_edits.new_packet(old, keywords(WINDOWS_TAGS)),
                        lambda record, held: self.saved.append((record, held)), category=()))
                self.assertEqual(([], before), (self.saved, self.bytes_of(path)))


def with_windows_tags(path, texts):
    """`path`, an ffmpeg MP4 with moov last and udta last in it, given an Xtra holding `texts` as Windows' Tags (none, an
    empty Xtra) at the end of its udta: udta and moov grow at the end of the file, so nothing else moves."""
    with open(path, "rb") as handle:
        layout = containers.read(handle)
    moov = layout.moov
    udta, = [child for child in moov.children if child.type == "udta"]
    assert moov.end == layout.file_size == udta.end, "moov last in the file, udta last in moov"
    added = xtra(*texts)
    with open(path, "r+b") as handle:
        for one in (moov, udta):
            handle.seek(one.offset)
            handle.write(struct.pack(">I", one.size + len(added)))
        handle.seek(layout.file_size)
        handle.write(added)
    return path


def windows_tags_read(path):
    """(ExifTool's XMP:Subject, its Microsoft:Category) of `path`, as the journal reads them (fields.field_values). The
    Category is looked up under the key ExifTool answers it by, spelled here rather than through fields.read_key, so a
    journaled write that reads it by read_key is checked against ExifTool, not against itself."""
    with ExifToolSession(executable=EXIFTOOL) as session:
        held = session.get_tags([path], tags=["XMP:Subject", fields.CLIP_CATEGORY])[0]
    return (fields.field_values(held.get("XMP:Subject")),
            fields.field_values(held.get("QuickTime:Category")) or None)


if __name__ == "__main__":
    unittest.main()
