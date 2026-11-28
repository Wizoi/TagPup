"""A JPEG's EXIF block as cameras and ExifTool lay it out, edited in the nine fields TagPup owns and in nothing else (F1).

TagPup's own writer (docs/PLAN_OWN_METADATA.md, M3) changes a caption, a keyword, a date or the orientation by
rewriting those tags' entries and values inside the block: every other byte -- the other tags, the MakerNote, IFD1 and
its thumbnail, GPS -- stays where it was, a longer value is paid for out of the slack after the last byte any IFD points
at, and a block it does not wholly understand is refused, never repaired. The editor is given one segment's body; a
file with two EXIF segments (2 of 1,500 photo_index files sampled) is its caller's to refuse. The values here are
fictional; the layouts are the ones the census counted (docs/domains/files.md, "The EXIF block editor").

How a laid-out block reads in either byte order, and that a write changes its targets only, gives the bytes back when
undone and changes nothing the second time, is F12's (tests/test_a_photos_fields_read_and_written.py, on the corpus's
`tagged`, `tagged_ii`, `camera`, `maker_note` and `no_taken`); this file keeps the decisions inside a block: the slack
and growth, the type-2 XP tags, where a new tag is laid, Orientation's range, the MakerNote, the refusals and the
damage loops.
"""
import os
import struct
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

sys.path.insert(0, os.path.join(WORKSPACE_DIR, "tests"))
from corpus import block, layout_tiff, note_ifd, text, ucs2  # noqa: E402
from tagpup.files import containers  # noqa: E402
from tagpup.files import exif_block as eb  # noqa: E402

OWNED_TAGS = {tag for _where, tag, _kind in eb.FIELDS.values()}


def entry_at(body, tag):
    """The offset in `body` of the IFD entry of `tag`."""
    layout = containers.exif_tiff(body[6:])
    return next(6 + ifd.offset + 2 + 12 * n for ifd in layout.ifds for n, e in enumerate(ifd.entries) if e[0] == tag)


def patched(body, tag, field, value):
    """`body` with the `field` (2 type, 4 count, 8 value: its offset in the entry) of `tag`'s entry set to `value`."""
    order = "<" if body[6:8] == b"II" else ">"
    at = entry_at(body, tag) + field
    data = struct.pack(order + ("H" if field == 2 else "I"), value)
    return body[:at] + data + body[at + len(data):]


LITTLE, BIG = block("<"), block(">")


def unowned(body):
    """What the editor must keep of `body`: each IFD's entries but the owned tags', and every byte range an IFD points
    at but the owned IFDs' tables and owned values. Walked here, so that a broken editor cannot agree with itself."""
    layout = containers.exif_tiff(body[6:])
    edited = {layout.named[where].offset for where in ("ifd0", "exif") if where in layout.named}
    kept = {"head": body[6:14]}
    for ifd in layout.ifds:
        kept[("entries", ifd.offset)] = ([e for e in ifd.entries if ifd.offset not in edited or e[0] not in OWNED_TAGS],
                                         ifd.next)
    for start, end, owner in layout.spans:
        mine = (owner[0] == "ifd" and owner[1] in edited) or (owner[0] in edited and owner[1] in OWNED_TAGS)
        if not mine:
            kept[owner] = (start, body[6 + start:6 + end])
    return kept


def changed_only_where_owned(test, before, after):
    """Every byte that differs is inside an owned IFD's table or an owned value of `after`, or zero there."""
    old, new = before[6:], after[6:]
    layout = containers.exif_tiff(new)
    edited = {layout.named[where].offset for where in ("ifd0", "exif") if where in layout.named}
    ours = [(s, e) for s, e, o in layout.spans if (o[0] == "ifd" and o[1] in edited) or (
        o[0] in edited and o[1] in OWNED_TAGS)]
    old = old + bytes(max(0, len(new) - len(old)))
    for at in range(len(new)):
        if old[at] != new[at] and new[at]:
            test.assertTrue(any(s <= at < e for s, e in ours), "byte %d changed outside what TagPup owns" % at)


class AnExifBlockReadAndWritten(unittest.TestCase):
    def edited(self, body, changes, **kw):
        result = eb.edit(body, changes, **kw)
        self.assertIsInstance(result, containers.Edited, result)
        self.assertEqual(unowned(body), unowned(result.body))
        changed_only_where_owned(self, body, result.body)
        return result

    def test_a_longer_value_goes_into_the_slack_and_a_shorter_one_stays(self):
        layout = containers.exif_tiff(LITTLE[6:])
        tiff = self.edited(LITTLE, {"ImageDescription": "A day at the lake, then rain"}).body[6:]
        self.assertEqual(len(LITTLE) - 6, len(tiff))
        self.assertEqual(layout.used + (layout.used & 1), tiff.index(b"A day at the lake, then rain\0"))
        self.assertNotIn(b"A day at the lake\0", tiff)
        shorter = self.edited(LITTLE, {"ImageDescription": "Lake"}).body
        self.assertEqual(LITTLE.index(b"A day"), shorter.index(b"Lake\0" + bytes(13)))

    def test_too_little_slack_does_not_fit_and_grow_adds_bytes_at_the_end_alone(self):
        body = block(">", slack=8)
        change = {"XPComment": "Kites over the dunes in the wind"}
        found = eb.edit(body, change)
        self.assertIsInstance(found, eb.DoesNotFit)
        grown = self.edited(body, change, grow=True)
        self.assertTrue(grown.length_changed)
        self.assertEqual(len(body) + found.needed, len(grown.body))
        self.assertEqual(change["XPComment"], eb.read(grown.body)["XPComment"])
        # bytes after the last pointed-at byte that are not zero are no slack: a value goes after them, grown
        tail = block("<", slack=0, tail=b"\x01junk")
        self.assertIsInstance(eb.edit(tail, change), eb.DoesNotFit)
        self.assertEqual(tail.index(b"\x01junk"), self.edited(tail, change, grow=True).body.index(b"\x01junk"))
        many = {"XPKeywords": "k" * 40000}
        self.assertIsInstance(eb.edit(body, many, grow=True), containers.Refusal)
        self.assertIsInstance(eb.edit(body, many, grow=True, limit=99999), containers.Edited)

    def test_an_xp_tag_of_type_2_reads_as_ucs2_and_is_written_as_type_1(self):
        # 1,392 of 1,500 photo_index XPComments sampled are type 2; 337 of them hold ASCII bytes, which are read
        # as ExifTool reads them, two bytes a UCS-2 character
        body = block(ifd0={0x9C9C: (2, ucs2("Pier")), 0x9C9E: (2, b"Pier;Gulls\0")})
        held = eb.read(body)
        self.assertEqual("Pier", held["XPComment"])
        self.assertEqual(b"Pier;Gulls".decode("utf-16-le"), held["XPKeywords"])
        self.assertEqual(body, self.edited(body, held).body)
        result = self.edited(body, {"XPComment": "Pier at dusk"}).body
        self.assertEqual(struct.pack("<HHI", 0x9C9C, 1, len(ucs2("Pier at dusk"))),
                         result[entry_at(result, 0x9C9C):entry_at(result, 0x9C9C) + 8])

    def test_a_new_tag_is_laid_over_zero_bytes_nothing_points_at_and_never_over_others(self):
        def with_gap(gap):
            return b"Exif\0\0" + layout_tiff("<", [
                ("ifd0", [(0x0112, 3, struct.pack("<H", 1)), (0x8769, 4, lambda p, _at: struct.pack("<I", p.get("exif", 0)))],
                 gap),
                ("exif", [(0x9000, 7, b"0232")], b"")], 0, b"")

        result = self.edited(with_gap(bytes(16)), {"ImageDescription": "Oak"})
        self.assertEqual("Oak", eb.read(result.body)["ImageDescription"])
        # 0xAB bytes lie between IFD0's table and the Exif IFD, named by nothing: a new entry is not laid over them
        marked = with_gap(b"\xab" * 16)
        self.assertIsInstance(eb.edit(marked, {"ImageDescription": "Oak"}), containers.Refusal)
        self.assertIsInstance(eb.edit(marked, {"ImageDescription": "Oak"}, grow=True), containers.Refusal)
        # the same when the table is the last thing any IFD points at (the bytes lie at or past `used`)
        last = b"Exif\0\0" + layout_tiff("<", [("ifd0", [(0x0112, 3, struct.pack("<H", 1))], b"\xab" * 16)], 0, b"")
        self.assertIsInstance(eb.edit(last, {"ImageDescription": "Oak"}), containers.Refusal)
        self.assertIsInstance(eb.edit(last, {"ImageDescription": "Oak"}, grow=True), containers.Refusal)
        # an IFD whose table is followed by data (the ~3,000 absent tags of the sample) takes no new entry, grown or not
        self.assertIsInstance(eb.edit(block(ifd0={}), {"ImageDescription": "Lake"}, grow=True), containers.Refusal)

    def test_orientation_takes_one_to_eight_and_an_odd_one_held_is_left_alone(self):
        self.assertEqual(8, eb.read(self.edited(BIG, {"Orientation": 8}).body)["Orientation"])
        # (a value equal to the one held is not looked at: True, given for a held 1, changes no byte)
        for bad in (0, 9, "6", 6.0):
            self.assertIsInstance(eb.edit(BIG, {"Orientation": bad}), containers.Refusal)
        zero = block(">", ifd0={0x0112: (3, b"\0\0")})  # 1 of 3,000 photo_index files in the census
        self.assertEqual(zero, self.edited(zero, {"Orientation": 0}).body)
        # stored as a LONG (2 of photo_index's 1,064 sampled Orientations): read, and written back as a SHORT
        long = block(">", ifd0={0x0112: (4, struct.pack(">I", 6))})
        self.assertEqual(6, eb.read(long)["Orientation"])
        turned = self.edited(long, {"Orientation": 8}).body
        self.assertEqual(struct.pack(">HHIH", 0x0112, 3, 1, 8), turned[entry_at(turned, 0x0112):][:10])

    def test_a_value_exif_cannot_carry_is_refused(self):
        for change in ({"ImageDescription": "a\0b"}, {"ImageDescription": 3}, {"DateTimeOriginal": "2019-07-14"},
                       {"SubSecTime": "4a"}, {"XPComment": "a" + chr(0xD800)}, {"Rating": "3"}):
            with self.subTest(change):
                self.assertIsInstance(eb.edit(LITTLE, change), containers.Refusal)
        utf8 = self.edited(LITTLE, {"ImageDescription": "Caf" + chr(0xE9) + " at the lake"}).body
        self.assertIn(b"Caf\xc3\xa9 at the lake\0", utf8)

    def test_a_maker_note_that_keeps_its_pointers_inside_it_leaves_the_slack_free(self):
        notes = {"no note": LITTLE, "tiff-relative": block("<", note=note_ifd("<")),
                 "moved, with Canon's footer": block(">", make=b"Canon", note=note_ifd(">", 64, footer=True))}
        for why, body in notes.items():
            with self.subTest(why):
                self.assertFalse(self.edited(body, {"XPComment": "Kites over the dunes"}).length_changed)
        # a note that is no IFD (46 of 1,500 photo_index notes sampled) may point anywhere: a value is written in its
        # place, and nothing beside the note is taken, grown or not
        nokia = block("<", note=b"Nokia\0" + b"\xff" * 40)
        self.assertEqual("Kelp", eb.read(self.edited(nokia, {"XPComment": "Kelp"}).body)["XPComment"])
        for grow in (False, True):
            found = eb.edit(nokia, {"XPComment": "Kites over the dunes"}, grow=grow)
            self.assertIsInstance(found, containers.Refusal)
            self.assertIn("not an IFD", found.reason)

    def test_what_it_does_not_wholly_understand_is_refused(self):
        lake = block("<", ifd0={0x010E: (2, text("A day at the lake"))})
        twice_at = entry_at(LITTLE, 0x010E)
        shared = patched(patched(LITTLE, 0x0131, 4, 18), 0x0131, 8, struct.unpack("<I", LITTLE[
            twice_at + 8:twice_at + 12])[0])
        refused = {  # why: (body, change, words of the refusal); counts are of the sample, read and edited
            "a new tag where data follows the table (415 / 396 / 399 ImageDescriptions)": (
                block(ifd0={}), {"ImageDescription": "Lake"}, "data follows its table"),
            "a new tag in an IFD out of tag order (8 / 0 / 0)": (
                block(ifd0={0x0112: (3, b"\1\0")}, sort=False), {"Orientation": None, "ImageDescription": "Lake"},
                "not in tag order"),
            "a MakerNote the Canon footer does not account for (1 / 0 / 0)": (
                block(">", make=b"Canon", note=note_ifd(">", 64, footer=True, told=32)),
                {"XPComment": "Kites over the dunes"}, "may point outside"),
            # each of the rest: no instance in the sample (read: 0 of 2,297 blocks refused), refused
            "not an EXIF segment": (b"Exif\1\0" + LITTLE[6:], {"XPComment": "Kelp"}, "not an EXIF segment"),
            "over a segment's limit": (LITTLE, {"XPComment": "Kelp"}, "over the", {"limit": len(LITTLE) - 1}),
            "a pointer out of the block": (patched(lake, 0x010E, 8, 9999), {"XPComment": "Kelp"}, "cannot be"),
            "an entry of no TIFF type": (patched(lake, 0x0131, 2, 99), {"XPComment": "Kelp"}, "cannot be"),
            "strips": (block(ifd0={0x0111: (4, struct.pack("<I", 8))}), {"XPComment": "Kelp"}, "strips"),
            "an owned tag twice": (LITTLE[:twice_at + 12] + LITTLE[twice_at:twice_at + 12] + LITTLE[twice_at + 24:],
                                   {"XPComment": "Kelp"}, "twice"),
            "an owned tag of another type": (patched(lake, 0x010E, 2, 7), {"XPComment": "Kelp"}, "TIFF type"),
            "a caption not UTF-8": (block(ifd0={0x010E: (2, b"Caf\xe9 by the lake\0")}), {"XPComment": "Kelp"},
                                    "not UTF-8"),
            "two tags, one value": (shared, {"ImageDescription": "Kelp"}, "shares its bytes"),
            "a date and no Exif IFD": (block(exif=False), {"DateTimeOriginal": "2019:07:14 10:22:31"}, "no Exif"),
        }
        for why, (body, change, words, *kw) in refused.items():
            with self.subTest(why):
                for grow in (False, True):
                    found = eb.edit(body, change, grow=grow, **(kw[0] if kw else {}))
                    self.assertIsInstance(found, containers.Refusal)
                    self.assertIn(words, found.reason)

    def test_an_ifd_loop_and_a_note_pointing_past_the_block_end_well(self):
        loop = bytearray(block("<", thumb=False))
        gps = containers.exif_tiff(bytes(loop[6:])).named["gps"]
        loop[6 + gps.end - 4:6 + gps.end] = struct.pack("<I", 8)  # GPS's next IFD is IFD0 again
        far = b"\0\1" + struct.pack("<HHII", 7, 2, 8, 40000) + bytes(30)
        for body in (bytes(loop), block("<", note=far)):
            found = eb.edit(body, {"XPComment": "Kites over the dunes"})
            self.assertIsInstance(found, (containers.Edited, containers.Refusal))
            if isinstance(found, containers.Edited):
                self.assertEqual(unowned(body), unowned(found.body))


class AnExifBlockDamagedAnywhere(unittest.TestCase):
    """A block cut short or with one byte changed is refused, does not fit, or is edited whole; nothing else."""

    CHANGES = {"ImageDescription": "Lake", "XPComment": "Kites over the dunes", "Orientation": 6, "CreateDate": None}
    BODY = block(">", note=note_ifd(">"))

    def check(self, body):
        self.assertIsInstance(eb.read(body), (dict, containers.Refusal))
        result = eb.edit(body, self.CHANGES)
        if not isinstance(result, containers.Edited):
            self.assertIsInstance(result, (containers.Refusal, eb.DoesNotFit))
            return 0
        self.assertEqual(len(body), len(result.body))
        held = eb.read(result.body)
        self.assertEqual(("Lake", "Kites over the dunes", 6), tuple(held.get(n) for n in list(self.CHANGES)[:3]))
        self.assertNotIn("CreateDate", held)
        self.assertEqual(unowned(body), unowned(result.body))
        changed_only_where_owned(self, body, result.body)
        return 1

    def test_cut_short_at_every_seventh_byte(self):
        self.assertEqual(1, self.check(self.BODY))
        # cut anywhere before the slack, the thumbnail or an IFD is cut: refused
        self.assertEqual(0, sum(self.check(self.BODY[:end]) for end in range(0, len(self.BODY) - 64, 7)))

    def test_one_byte_changed_anywhere(self):
        edited = 0
        for at in range(len(self.BODY)):
            flip = (0x01, 0x20, 0x80)[at % 3]
            with self.subTest(at=at, flip=flip):
                edited += self.check(self.BODY[:at] + bytes([self.BODY[at] ^ flip]) + self.BODY[at + 1:])
        self.assertGreater(edited, len(self.BODY) // 4)


if __name__ == "__main__":
    unittest.main()
