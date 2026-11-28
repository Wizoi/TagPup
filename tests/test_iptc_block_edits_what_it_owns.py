"""A JPEG's IPTC block as ExifTool lays it out, edited in the three datasets TagPup owns and in nothing else (F1).

The owner's photos carry a Photoshop APP13 segment: 8BIM resources, the IPTC-IIM datasets in 0x0404 and the IPTCDigest
(0x0425) beside them. TagPup's own writer (docs/PLAN_OWN_METADATA.md, M3) changes a keyword, a caption or a title by
rewriting 0x0404 alone: every other resource keeps its bytes (the digest too, left stale: DECISIONS 2026-11-03), every
other dataset keeps its bytes and its place, the dummy resource at the end gives or takes the room so the segment keeps
its length when the change fits, and a block it does not wholly understand is refused, never repaired. The bodies here
are laid out by hand from the layout (not by the code under test) in the shapes counted in the three libraries; the
values are fictional (docs/domains/files.md, "The IPTC block editor").

That a save which moves nothing changes no byte is F12's (tests/test_a_photos_fields_read_and_written.py, on the
corpus's `iptc_alone`, `iptc_utf8`, `tagged` and `two_app13`); this file keeps the reads and the decisions inside a
block.
"""
import hashlib
import os
import random
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

from tagpup.files import containers  # noqa: E402
from tagpup.files import iptc_block as ib  # noqa: E402

HEAD = b"Photoshop 3.0\0"
UTF8 = b"\x1b%G"
KEYWORDS, CAPTION, TITLE = "IPTC:Keywords", "IPTC:Caption-Abstract", "IPTC:ObjectName"
PAD = 0x0DAC


def dataset(record, number, value):
    return bytes([0x1C, record, number]) + len(value).to_bytes(2, "big") + value


def resource(number, data):
    return b"8BIM" + number.to_bytes(2, "big") + b"\0\0" + len(data).to_bytes(4, "big") + data + b"\0" * (len(data) % 2)


def body(block, digest=True, pad=0, thumbnail=True):
    """An APP13 body as kr-track's: resolution info, the IPTC block, a thumbnail, the digest of the block as it was."""
    parts = [resource(0x03ED, bytes(16)), resource(0x0404, block)]
    if thumbnail:
        parts.append(resource(0x040C, b"\xff\xd8" + bytes(37) + b"\xff\xd9"))
    if digest:
        parts.append(resource(0x0425, hashlib.md5(block).digest()))
    if pad:
        parts.append(resource(PAD, bytes(pad - 12)))
    return HEAD + b"".join(parts)


#: ExifTool's layout of a block it wrote (kr-track: 1,896 of 2,059 blocks read begin 1:90 1:00 2:00 ... 2:25).
EXIFTOOL = (dataset(1, 90, UTF8) + dataset(1, 0, b"\0\x04") + dataset(2, 0, b"\0\x04") + dataset(2, 55, b"20190714")
            + dataset(2, 60, b"102231+0000") + dataset(2, 62, b"20190714") + dataset(2, 63, b"102231+0000")
            + dataset(2, 25, b"People/Ida Corwell") + dataset(2, 25, b"Places/Lakeside"))
#: A block no ExifTool IPTC write has touched (photo_index: no 1:90 in 400 of 1,461, no 2:00 in 1,109, a caption ending
#: in one NUL in 1,041, zero bytes after the last dataset in 754).
WLPG = (dataset(2, 55, b"20080601") + dataset(2, 5, b"Summer\0") + dataset(2, 120, b"\0")
        + dataset(2, 25, b"Rowan Thackeray") + dataset(2, 80, b"Jo Brannock") + b"\0\0")


def records(data):
    """[(record, dataset, value)] of the 0x0404 block of `data`, walked here by hand."""
    at = data.index(b"8BIM\x04\x04")
    length = int.from_bytes(data[at + 8:at + 12], "big")
    block, out, i = data[at + 12:at + 12 + length], [], 0
    while i < len(block) and block[i] == 0x1C:
        n = int.from_bytes(block[i + 3:i + 5], "big")
        out.append((block[i + 1], block[i + 2], block[i + 5:i + 5 + n]))
        i += 5 + n
    return out


def others(data):
    """Every resource but 0x0404 and the dummy, as bytes, walked here by hand."""
    out, at = [], len(HEAD)
    while at < len(data):
        number, length = int.from_bytes(data[at + 4:at + 6], "big"), int.from_bytes(data[at + 8:at + 12], "big")
        whole = 12 + length + length % 2
        if number not in (0x0404, PAD):
            out.append(data[at:at + whole])
        at += whole
    return out


class AnIptcBlockReadAndWritten(unittest.TestCase):
    def edited(self, data, changes):
        result = ib.edit(data, changes)
        self.assertIsInstance(result, containers.Edited, result)
        return result

    def refused(self, data, changes=None):
        result = ib.read(data) if changes is None else ib.edit(data, changes)
        self.assertIsInstance(result, containers.Refusal, result)
        return result.reason

    def test_a_lone_surrogate_is_refused_as_the_exif_editor_refuses_it(self):
        for field, value in ((KEYWORDS, ["Beach", "Sea\ud800"]), (CAPTION, "Rain\udc00")):
            self.refused(body(EXIFTOOL), {field: value})
            self.refused(body(WLPG), {field: value})

    def test_every_owned_dataset_reads_in_its_character_set(self):
        self.assertEqual({KEYWORDS: ["People/Ida Corwell", "Places/Lakeside"]}, ib.read(body(EXIFTOOL)))
        # a trailing NUL is read off; an empty caption reads ''
        self.assertEqual({TITLE: "Summer", CAPTION: "", KEYWORDS: ["Rowan Thackeray"]}, ib.read(body(WLPG)))
        self.assertEqual({}, ib.read(HEAD))

    def test_a_keyword_added_changes_only_the_iptc_block_and_leaves_the_digest_stale(self):
        data = body(EXIFTOOL)
        result = self.edited(data, {KEYWORDS: ["People/Ida Corwell", "Places/Lakeside", "Kites by the lake"]})
        self.assertTrue(result.length_changed)      # no dummy resource yet: the segment must grow
        self.assertEqual(others(data), others(result.body))
        digest = next(r for r in others(result.body) if r[4:6] == b"\x04\x25")[12:]
        self.assertEqual(hashlib.md5(EXIFTOOL).digest(), digest)
        self.assertEqual(records(data) + [(2, 25, b"Kites by the lake")], records(result.body))
        # and back: the same bytes, the room it gave up laid as the dummy resource
        back = self.edited(result.body, {KEYWORDS: ["People/Ida Corwell", "Places/Lakeside"]})
        self.assertFalse(back.length_changed)
        self.assertEqual(data, back.body[:len(data)])
        self.assertEqual(resource(PAD, bytes(len(back.body) - len(data) - 12)), back.body[len(data):])

    def test_a_change_that_fits_keeps_the_length_and_the_dummy_resource_takes_the_room(self):
        data = body(EXIFTOOL, pad=60)
        longer = self.edited(data, {CAPTION: "Kites over the dunes"})       # 25 bytes of dataset, of 60
        self.assertFalse(longer.length_changed)
        self.assertEqual(len(data), len(longer.body))
        self.assertEqual(others(data), others(longer.body))
        self.assertIn(b"8BIM\x0d\xac", longer.body[len(data) - 60:])
        self.assertEqual(data, self.edited(longer.body, {CAPTION: None}).body)
        # more than the dummy holds: the segment grows, and no dummy is left
        grown = self.edited(data, {CAPTION: "A long afternoon of kites over the dunes, then rain"})
        self.assertTrue(grown.length_changed)
        self.assertNotIn(b"8BIM\x0d\xac", grown.body)
        # shorter by less than a resource takes: no room for a dummy, so the length changes
        title = self.edited(body(EXIFTOOL), {TITLE: "Dunes and kites"}).body
        self.assertTrue(self.edited(title, {TITLE: "Dunes and kite"}).length_changed)

    def test_no_marker_stays_cp1252_while_every_value_fits(self):
        data = body(WLPG)
        latin = self.edited(data, {CAPTION: "Café by the lake"}).body
        self.assertIn((2, 120, "Café by the lake".encode("cp1252")), records(latin))
        self.assertNotIn(90, [r[1] for r in records(latin) if r[0] == 1])
        self.assertEqual("Café by the lake", ib.read(latin)[CAPTION])

    def test_a_value_outside_cp1252_gives_the_block_utf8_and_a_record_1(self):
        data = body(WLPG)
        result = self.edited(data, {KEYWORDS: ["Rowan Thackeray", "Zoő Brannock"]}).body
        found = records(result)
        self.assertEqual([(1, 0, b"\0\x04"), (1, 90, UTF8)], sorted(r for r in found if r[0] == 1))
        self.assertEqual(1, found[0][0])
        self.assertIn((2, 25, "Zoő Brannock".encode("utf-8")), found)
        self.assertEqual(["Rowan Thackeray", "Zoő Brannock"], ib.read(result)[KEYWORDS])
        # the marker stays: a later cp1252 letter is written in UTF-8
        cafe = self.edited(result, {CAPTION: "Café"}).body
        self.assertIn((2, 120, "Café".encode("utf-8")), records(cafe))
        # a block already marked UTF-8 writes UTF-8
        marked = self.edited(body(EXIFTOOL), {TITLE: "Café"}).body
        self.assertIn((2, 5, "Café".encode("utf-8")), records(marked))

    def test_unmarked_text_outside_ascii_elsewhere_refuses_a_change_of_character_set(self):
        data = body(dataset(2, 90, "Lakesidé".encode("cp1252")) + dataset(2, 25, b"Kites"))
        self.assertIn("UTF-8", self.refused(data, {KEYWORDS: ["Zoő"]}))
        self.assertIsInstance(ib.edit(data, {KEYWORDS: ["Café"]}), containers.Edited)

    def test_version_datasets_added_as_exiftool_adds_them(self):
        result = records(self.edited(body(WLPG), {KEYWORDS: ["Rowan Thackeray", "Kites"]}).body)
        self.assertEqual([(2, 25, b"Rowan Thackeray"), (2, 25, b"Kites")], [r for r in result if r[1] == 25])
        self.assertEqual((2, 0, b"\0\x04"), result[-1])
        self.assertNotIn(1, [r[0] for r in result])
        # a new dataset goes at the end of its record, in dataset order, as ExifTool puts it
        added = records(self.edited(body(EXIFTOOL), {TITLE: "Dunes", CAPTION: "Kites"}).body)
        self.assertEqual([(2, 5, b"Dunes"), (2, 120, b"Kites")], added[-2:])
        # a record with no version dataset gets one; an edit that makes no change adds none
        data = body(dataset(1, 90, UTF8) + dataset(2, 0, b"\0\x04") + dataset(2, 25, b"Kites"))
        self.assertIn((1, 0, b"\0\x04"), records(self.edited(data, {TITLE: "Dunes"}).body))
        self.assertEqual(data, self.edited(data, {KEYWORDS: ["Kites"]}).body)

    def test_a_dataset_held_is_rewritten_in_its_place_and_a_cleared_one_goes(self):
        result = records(self.edited(body(WLPG), {TITLE: "Winter", KEYWORDS: None, CAPTION: ""}).body)
        self.assertEqual([(2, 55), (2, 5), (2, 120), (2, 80), (2, 0)], [r[:2] for r in result])
        self.assertEqual(b"Winter", result[1][2])
        cleared = records(self.edited(body(WLPG), {CAPTION: "Kites", TITLE: None}).body)
        self.assertEqual([(2, 55), (2, 120), (2, 25), (2, 80), (2, 0)], [r[:2] for r in cleared])

    def test_zero_bytes_after_the_last_dataset_are_dropped_as_exiftool_drops_them(self):
        data = body(WLPG)
        result = self.edited(data, {TITLE: "Winter"}).body
        self.assertEqual(records(data)[2:], records(result)[2:-1])
        end = result.index(b"Jo Brannock") + len(b"Jo Brannock")
        self.assertEqual(dataset(2, 0, b"\0\x04"), result[end:end + 7])
        self.assertLessEqual(result.index(b"8BIM\x04\x0c") - end - 7, 1)     # the resource's even-length pad

    def test_values_are_cut_as_a_write_cuts_them_and_no_control_character_is_written(self):
        result = ib.read(self.edited(HEAD, {CAPTION: "y" * 2100, TITLE: "t" * 80, KEYWORDS: ["k" * 70]}).body)
        self.assertEqual({CAPTION: "y" * 2000, TITLE: "t" * 64, KEYWORDS: ["k" * 64]}, result)
        self.assertEqual("Two\nlines", ib.read(self.edited(HEAD, {CAPTION: "Two\nlines"}).body)[CAPTION])
        for bad in ("Kites\0", "Kites" + chr(7)):
            self.assertIn("control", self.refused(HEAD, {CAPTION: bad}))
        self.assertIn("list", self.refused(HEAD, {KEYWORDS: "Kites"}))
        self.assertIn("text", self.refused(HEAD, {CAPTION: ["Kites"]}))
        self.assertIn("owns", self.refused(HEAD, {"IPTC:City": "Lakeside"}))
        # a repeat, a blank, a keyword already held, a value held cut at IPTC's 64 bytes: the same values, no byte moved
        data = body(EXIFTOOL)
        same = {KEYWORDS: ["People/Ida Corwell", "Places/Lakeside", "Places/Lakeside", " "], CAPTION: "", TITLE: None}
        self.assertEqual(data, self.edited(data, same).body)
        long = "Places/" + "x" * 70
        cut = self.edited(data, {KEYWORDS: [long]}).body
        self.assertEqual([(2, 25, long[:64].encode())], [r for r in records(cut) if r[:2] == (2, 25)])
        self.assertEqual(cut, self.edited(cut, {KEYWORDS: [long]}).body)

    def test_a_segment_with_no_iptc_block_is_given_one_before_the_dummy(self):
        data = HEAD + resource(0x03ED, bytes(16)) + resource(PAD, bytes(48))
        result = self.edited(data, {KEYWORDS: ["Kites"]})
        self.assertFalse(result.length_changed)
        self.assertEqual([(2, 25, b"Kites"), (2, 0, b"\0\x04")], records(result.body))
        self.assertEqual(data[:len(HEAD) + 28], result.body[:len(HEAD) + 28])

    def test_what_it_does_not_wholly_understand_is_refused(self):
        cases = {
            "Photoshop": b"Adobe_CM\0" + bytes(8),
            "no resource": body(EXIFTOOL) + b"\0\0",
            "two IPTC": body(EXIFTOOL) + resource(0x0404, dataset(2, 25, b"Kites")),
            "whole dataset": body(EXIFTOOL[:-3]),
            "stray": body(EXIFTOOL + b"\x01\x02"),
            "extended": body(bytes([0x1C, 2, 120, 0x80, 0x04]) + (5).to_bytes(4, "big") + b"Kites"),
            "twice": body(dataset(2, 120, b"Kites") + dataset(2, 120, b"Dunes")),
            "utf-8 text": body(dataset(1, 90, UTF8) + dataset(2, 25, b"Caf\xe9")),
            "cp1252 text": body(dataset(2, 25, b"Caf\x81")),
            "NUL inside": body(dataset(2, 25, b"Ki\0tes")),
            "one UTF-8 marker": body(dataset(1, 90, b"\x1b-A") + dataset(2, 25, b"Kites")),
            "two markers": body(dataset(1, 90, UTF8) + dataset(1, 90, UTF8)),
            "over": HEAD + resource(0x0404, dataset(2, 120, bytes(32000)) + dataset(2, 120, bytes(32000)))
            + resource(0x040C, bytes(2000)),
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.refused(data)
                self.refused(data, {KEYWORDS: ["Kites"]})
        self.assertIn("over", self.refused(HEAD, {KEYWORDS: ["Kite %d" % n for n in range(5000)]}))

    def test_a_block_laid_wrong_is_refused_by_the_editors_own_checks(self):
        # the read-back and the `kept` check stand between a fault in the layout and a file: each fault refused
        data = body(WLPG, pad=40)
        real = ib._rebuilt
        faults = {
            "a value cut": lambda b, m, c: (real(b, m, c)[0].replace(b"Kites", b"Kite\0"), real(b, m, c)[1]),
            "an unowned dataset dropped": lambda b, m, c: (
                real(b, m, c)[0].replace(dataset(2, 80, b"Jo Brannock"), b""), real(b, m, c)[1]),
            "an unowned dataset changed": lambda b, m, c: (
                real(b, m, c)[0].replace(b"Jo Brannock", b"Jo Brannick"), real(b, m, c)[1]),
        }
        for name, fault in faults.items():
            with self.subTest(name), mock.patch.object(ib, "_rebuilt", fault):
                self.assertIn("does not read back", self.refused(data, {KEYWORDS: ["Kites"]}))

    def test_hostile_bytes_never_raise_and_never_give_a_block_that_does_not_read_back(self):
        rng = random.Random(7)
        seed = body(WLPG, pad=40)
        for count in range(1500):
            data = bytearray(seed if count % 3 else seed[:rng.randrange(len(seed))])
            for _ in range(rng.randrange(1, 4)):
                if data:
                    data[rng.randrange(len(data))] = rng.randrange(256)
            data = bytes(data)
            held = ib.read(data)
            result = ib.edit(data, {KEYWORDS: ["Kites"], CAPTION: "Dunes"})
            if isinstance(held, containers.Refusal):
                self.assertIsInstance(result, containers.Refusal)
                continue
            self.assertIsInstance(result, (containers.Edited, containers.Refusal))
            if isinstance(result, containers.Edited):
                self.assertEqual({**held, KEYWORDS: ["Kites"], CAPTION: "Dunes"}, ib.read(result.body))
                self.assertEqual(others(data), others(result.body))
                self.assertEqual(result.length_changed, len(result.body) != len(data))
        self.assertEqual(0x0404, containers.IPTC_RESOURCE)


if __name__ == "__main__":
    unittest.main()
