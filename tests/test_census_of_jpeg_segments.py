"""The census of a library's JPEG metadata segments (tagpup.services.segment_census, scripts/census_segments.py; M1 of
docs/PLAN_OWN_METADATA.md): what TagPup's own writer must meet, counted read-only from the bytes before the first scan.

One library in a home of its own, its rows as the indexer records them (photo_rows.add_read), its files hand-built
JPEGs: one holding two EXIF segments (one with a Canon MakerNote whose pointers count from the TIFF header), an XMP
packet whose dc:subject occurs twice, whose xmp:CreateDate is an attribute and which names two rdf:about values, an
extended XMP segment, an MPF segment, IPTC with no charset, a keyword outside ASCII and a digest that does not match,
and bytes after its end; one plain (UTF-8 IPTC, a digest that matches, a MakerNote moved with an OffsetSchema, which
is not trusted for the shift); two whose MakerNotes were moved as real ones were (a big-endian TIFF, a little-endian
note): a Canon note moved 4,218 bytes whose footer says so and whose OffsetSchema says 4,144 (its pointers land inside
the note shifted by either), and a Sony note of 12 entries, 4 of no TIFF type, moved 4,224 bytes with nothing saying so;
one cut short; one whose file is gone; and two on a share that stops answering: the first read waits past READ_WAIT,
the second is answered at once as away. Every case is counted and none is dropped; the answer holds no path, no name and
no value. Fictional names only.
"""
import hashlib
import json
import os
import struct
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import segment_census  # noqa: E402
from tagpup.store import db  # noqa: E402

BOM = bytes((0xEF, 0xBB, 0xBF))
KEYWORD = ("Places/Caf" + chr(0xE9)).encode("cp1252")


def _segment(marker, body):
    return bytes((0xFF, marker)) + struct.pack(">H", len(body) + 2) + body


def _tiff(make=None, maker_note=False, slack=0, moved=None):
    """A little-endian TIFF block: IFD0 (Make when given, the Exif IFD when a MakerNote is asked), the Exif IFD holding a
    MakerNote with one pointer counted from the TIFF header, then `slack` zero bytes. A note `moved` (a shift in bytes)
    points from where it was before, and the Exif IFD's OffsetSchema says by how much, as Windows' writers leave one."""
    entries, data = [], b""
    ifd0_count = (make is not None) + bool(maker_note) or 1
    at = 8 + 2 + 12 * ifd0_count + 4
    if make is not None:
        text = make.encode("ascii") + b"\0"
        entries.append(struct.pack("<HHII", 0x010F, 2, len(text), at + len(data)))
        data += text
    if maker_note:
        exif_at = at + len(data)
        exif_entries = 1 if moved is None else 2
        note_at = exif_at + 2 + 12 * exif_entries + 4
        pointer = note_at + 18 - (moved or 0)
        note = struct.pack("<H", 1) + struct.pack("<HHII", 1, 2, 8, pointer) + struct.pack("<I", 0) + b"Model 7\0"
        entries.append(struct.pack("<HHII", 0x8769, 4, 1, exif_at))
        schema = b"" if moved is None else struct.pack("<HHIi", 0xEA1D, 9, 1, moved)
        data += (struct.pack("<H", exif_entries) + struct.pack("<HHII", 0x927C, 7, len(note), note_at) + schema
                 + struct.pack("<I", 0) + note)
    if not entries:
        entries.append(struct.pack("<HHIHH", 0x0112, 3, 1, 1, 0))   # Orientation, its value in its entry
    ifd0 = struct.pack("<H", len(entries)) + b"".join(entries) + struct.pack("<I", 0)
    return b"II*\0" + struct.pack("<I", 8) + ifd0 + data + b"\0" * slack


def _moved_note(make, head, shift, bad=0, footer=False, schema=None):
    """A big-endian TIFF block whose Exif IFD holds a little-endian MakerNote (`head`, then an IFD of 8 entries whose
    8-byte values follow it and `bad` entries of no TIFF type) moved `shift` bytes on from where its pointers count, with
    a Canon footer saying where it was when asked, and an OffsetSchema saying `schema` when given."""
    text = make.encode("ascii") + b"\0"
    make_at = 8 + 2 + 12 * 2 + 4
    exif_at = make_at + len(text) + len(text) % 2
    exif_count = 1 + (schema is not None)
    note_at = exif_at + 2 + 12 * exif_count + 4 + 5000          # room before it, so its old place is in the block
    values_at = note_at + len(head) + 2 + 12 * (8 + bad) + 4
    entries = b"".join(struct.pack("<HHII", 1 + i, 2, 8, values_at + 8 * i - shift) for i in range(8))
    entries += b"".join(struct.pack("<HHII", 0x100 + i, 0x5555, 1, 0) for i in range(bad))
    note = (head + struct.pack("<H", 8 + bad) + entries + struct.pack("<I", 0) + b"Model 7\0" * 8
            + (b"II*\0" + struct.pack("<I", note_at - shift) if footer else b""))
    ifd0 = struct.pack(">HHHII", 2, 0x010F, 2, len(text), make_at) + struct.pack(">HHII", 0x8769, 4, 1, exif_at)
    exif = (struct.pack(">HHHII", exif_count, 0x927C, 7, len(note), note_at)
            + (struct.pack(">HHIi", 0xEA1D, 9, 1, schema) if schema is not None else b"") + struct.pack(">I", 0))
    block = b"MM\0*" + struct.pack(">I", 8) + ifd0 + struct.pack(">I", 0) + text + b"\0" * (len(text) % 2) + exif
    return block + b"\0" * (note_at - len(block)) + note


def _xmp(begin, toolkit, body):
    packet = (b'<?xpacket begin="' + begin + b'" id="W5M0MpCehiHzreSzNTczkc9d"?>'
              b'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="' + toolkit + b'">'
              b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">' + body +
              b"</rdf:RDF></x:xmpmeta>\n" + b" " * 200 + b'\n<?xpacket end="w"?>')
    return _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + packet)


def _resource(resource_id, data):
    return b"8BIM" + struct.pack(">H", resource_id) + b"\0\0" + struct.pack(">I", len(data)) + data + b"\0" * (len(data) & 1)


def _iptc(datasets, digest):
    """An APP13 segment holding the IPTC `datasets` [(record, number, value)] and an IPTCDigest: "right" (the MD5 of the
    IPTC block) or "wrong"."""
    block = b"".join(b"\x1c" + bytes((record, number)) + struct.pack(">H", len(value)) + value
                     for record, number, value in datasets)
    stored = hashlib.md5(block).digest() if digest == "right" else b"\0" * 16
    return _segment(0xED, b"Photoshop 3.0\0" + _resource(0x0404, block) + _resource(0x0425, stored))


SCAN = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x12\x34\x56" + b"\xff\xd9"
JFIF = _segment(0xE0, b"JFIF\0\x01\x01\0\0\x01\0\x01\0\0")
DESCRIPTION = (b'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
               b'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:xmpNote="http://ns.adobe.com/xmp/note/" '
               b'xmp:CreateDate="2024-07-04T10:00:00" xmpNote:HasExtendedXMP="0123456789ABCDEF0123456789ABCDEF">'
               b"<dc:subject><rdf:Bag><rdf:li>Family/Rowan Thackeray</rdf:li></rdf:Bag></dc:subject></rdf:Description>"
               b'<rdf:Description rdf:about="uuid:faf5bdd5-ba3d" xmlns:dc="http://purl.org/dc/elements/1.1/">'
               b"<dc:subject><rdf:Bag><rdf:li>Places/Lakeside</rdf:li></rdf:Bag></dc:subject></rdf:Description>")
RICH = (b"\xff\xd8" + JFIF
        + _segment(0xE1, b"Exif\0\0" + _tiff("Canon", maker_note=True, slack=100))
        + _segment(0xE1, b"Exif\0\0" + _tiff())
        + _xmp(b"", b"Image::ExifTool 12.40", DESCRIPTION)
        + _segment(0xE1, b"http://ns.adobe.com/xmp/extension/\0" + b"0123456789ABCDEF0123456789ABCDEF"
                   + struct.pack(">II", 20, 0) + b"<x:xmpmeta></x:xmpmeta")
        + _segment(0xE2, b"MPF\0" + b"II*\0" + struct.pack("<I", 8))
        + _iptc([(2, 25, KEYWORD)], "wrong")
        + SCAN + b"vendor trailer")
PLAIN = (b"\xff\xd8" + JFIF + _segment(0xE1, b"Exif\0\0" + _tiff("NIKON CORPORATION", maker_note=True, moved=-400))
         + _xmp(BOM, b"Adobe XMP Core 7.0-c000",
                b'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                b"<dc:subject><rdf:Bag><rdf:li>Places/Lakeside</rdf:li></rdf:Bag></dc:subject></rdf:Description>")
         + _iptc([(1, 90, b"\x1b%G"), (2, 25, b"Places/Lakeside")], "right")
         + SCAN)
CANON_MOVED = (b"\xff\xd8" + _segment(0xE1, b"Exif\0\0" + _moved_note("Canon", b"", 4218, footer=True, schema=4144))
               + SCAN)
SONY_MOVED = b"\xff\xd8" + _segment(0xE1, b"Exif\0\0" + _moved_note("SONY", b"SONY DSC \0\0\0", 4224, bad=4)) + SCAN


class ACensusOfJpegSegments(unittest.TestCase):

    def setUp(self):
        self.home = own_home.for_test(self, prefix="segment_census_")
        self.photos = os.path.join(self.home.root, "photos")
        os.makedirs(self.photos)
        self.slow = os.path.join(self.photos, "y slow share.jpg")
        files = {"a rich.jpg": RICH, "b plain.jpg": PLAIN, "c cut short.jpg": RICH[:60], "e canon.jpg": CANON_MOVED,
                 "f sony.jpg": SONY_MOVED, "y slow share.jpg": PLAIN, "z after the share went.jpg": PLAIN}
        for name, data in files.items():
            with open(os.path.join(self.photos, name), "wb") as handle:
                handle.write(data)
        db_path = self.home.library("library.db")
        library_actions.create(db_path)
        conn = db.connect(db_path)
        try:
            for name in sorted(files) + ["d gone since.jpg"]:
                photo_rows.add_read(conn, os.path.join(self.photos, name), {})
            conn.commit()
        finally:
            conn.close()
        self.library = Library(db_path)

    def _census(self):
        """The census with the read of the slow photo's share hung until the test ends: never reading the file."""
        release = threading.Event()
        self.addCleanup(shares.forget)
        self.addCleanup(release.set)
        real = segment_census._read

        def read(path):
            if os.path.normcase(path) == os.path.normcase(self.slow):
                release.wait(5)
                raise OSError("the share never answered")
            return real(path)

        with mock.patch.object(segment_census, "_read", read), mock.patch.object(shares, "READ_WAIT", 0.3), \
                mock.patch.object(shares, "AWAY_SECONDS", 60.0):
            return segment_census.census(self.library, sample=10)

    def test_every_photo_is_counted_and_nothing_named(self):
        found = self._census()
        counts, makers = found["counts"], found["makers"]
        self.assertEqual((8, 8, 8), (found["photos"], found["jpegs"], found["sampled"]))
        self.assertEqual({"read": 4, "no scan (truncated)": 1, "gone": 1, "not_answered": 2}, found["looked"])
        self.assertEqual({"ExifTool": 1, "Adobe": 1}, found["toolkits"])
        # The footer, not the OffsetSchema, says how far the Canon note moved; nothing says how far the others did.
        self.assertEqual({"tiff-relative": 1, "moved, the Canon footer says by how much": 1}, makers["Canon"])
        self.assertEqual({"moved, shift unknown": 1}, makers["Nikon"])
        self.assertEqual({"moved, shift unknown": 1}, makers["Sony"])
        self.assertEqual({"no MakerNote": 1}, makers["(no Make)"])
        expected = {"EXIF segments >1": 1, "EXIF segments 1": 3, "EXIF slack 64-511": 1, "extended XMP": 1,
                    "with MPF": 1, "after the last EOI: other bytes": 1, "after the last EOI: nothing": 3,
                    "MakerNote with entries skipped": 1, "MakerNote OffsetSchema differs from the Canon footer": 1,
                    "EXIF Orientation 1": 1, "EXIF Orientation absent": 4, "XMP size <16K": 2,
                    "XMP begin empty": 1, "XMP begin BOM": 1, "XMP dc:subject twice": 1,
                    "XMP xmp:CreateDate as an attribute": 1, "XMP two rdf:about values": 1,
                    "XMP xmpNote:HasExtendedXMP": 1, "IPTC 1:90 absent": 1, "IPTC 1:90 UTF-8": 1,
                    "IPTC values outside ASCII": 1, "IPTCDigest differs": 1, "IPTCDigest matches": 1,
                    "MakerNote": 4, "XMP not well-formed": 0}
        self.assertEqual(expected, {key: counts.get(key, 0) for key in expected})
        said = json.dumps(found, ensure_ascii=False)
        for private in ("Rowan", "Thackeray", "Lakeside", "Caf", self.photos, "slow share", "NIKON"):
            self.assertNotIn(private, said)


if __name__ == "__main__":
    unittest.main()
