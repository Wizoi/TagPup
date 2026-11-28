"""A save, a date set or a rotate made into the bytes of a JPEG's three metadata segments, or turned away to the rewrite (F1).

TagPup's own writer (docs/PLAN_OWN_METADATA.md, M4) patches a photo's header in place: `segment_edits.new_header` is
handed the journal's values of a write and answers the header with only the EXIF, XMP and IPTC bodies changed -- a
keyword or caption save in XMP alone while XMP keeps a value (the other copies reported stale), every copy when the
last keyword comes off -- or None, and the write goes to the rewrite with nothing written. The headers here are laid
out as a camera's file after an ExifTool write (a big-endian EXIF block with its values after each table, an XMP packet
with ExifTool's padding, a Photoshop block with its digest); the names are fictional.

A clip's save (M7, AClipsSave): `segment_edits.new_packet` makes the clip's new XMP packet from the one its box holds
-- Adobe's wrapped packet edited where it stands, the bare one grown, the blank one for a clip with none -- every copy
outside XMP left stale, also when the XMP value is cleared; the last keyword taken off a clip whose keywords are in
QuickTime's Subject is refused with a sentence (owner, 2026-11-05: 14 live clips).

Which copies a save, a date set or a turn writes and reports stale, that only the bodies change, and that a save made
twice changes no byte the second time, are F12's (tests/test_a_photos_fields_read_and_written.py, on every corpus item:
the photos through new_header, the clips through new_packet); this file keeps the map of every writer's fields, the
no-patch refusals, and the decisions S2 cannot see: the bare packet grown, a clip with no packet, a cleared clip value,
an XMP date's zone.
"""
import hashlib
import io
import os
import struct
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(WORKSPACE_DIR))
sys.path.insert(0, WORKSPACE_DIR)

import test_xmp_packet_edits_what_it_owns as shapes  # noqa: E402
from tagpup.core import dates, fields  # noqa: E402
from tagpup.files import (containers, exif_block, identity, iptc_block, segment_edits, xmp_packet)

PERSON = "People/Ida Corwell"


# --- a header as a camera's file after an ExifTool write ------------------------------------------------------------

def _ifd(order, entries, at, next_ifd=0):
    """An IFD at `at`: its table, then the values that do not fit an entry (data follows the table, as in every file
    the census read)."""
    entries = sorted(entries)
    values_at = at + 2 + 12 * len(entries) + 4
    table, extra = struct.pack(order + "H", len(entries)), b""
    for tag, kind, count, data in entries:
        if len(data) <= 4:
            table += struct.pack(order + "HHI", tag, kind, count) + data.ljust(4, b"\0")
        else:
            table += struct.pack(order + "HHII", tag, kind, count, values_at + len(extra))
            extra += data + b"\0" * (len(data) % 2)
    return table + struct.pack(order + "I", next_ifd) + extra


def _ascii(tag, text):
    data = text.encode("utf-8") + b"\0"
    return (tag, 2, len(data), data)


def _ucs2(tag, text):
    data = text.encode("utf-16-le") + b"\0\0"
    return (tag, 1, len(data), data)


def exif_body(order=">", caption="A day at the lake", keywords=PERSON + ";Beach", orientation=1, slack=64):
    ifd0 = [_ascii(0x010F, "Canon"), _ascii(0x0110, "Canon EOS 80D")]
    if caption:
        ifd0 += [_ascii(0x010E, caption), _ucs2(0x9C9C, caption)]
    if keywords:
        ifd0.append(_ucs2(0x9C9E, keywords))
    if orientation is not None:
        ifd0.append((0x0112, 3, 1, struct.pack(order + "H", orientation)))
    exif = [_ascii(0x9003, "2019:07:14 10:22:31"), _ascii(0x9004, "2019:07:14 10:22:31"),
            _ascii(0x9290, "00"), _ascii(0x9291, "00"), _ascii(0x9292, "00")]
    head = (b"MM\0*" if order == ">" else b"II*\0") + struct.pack(order + "I", 8)
    first = _ifd(order, ifd0 + [(0x8769, 4, 1, struct.pack(order + "I", 0))], 8)
    first = _ifd(order, ifd0 + [(0x8769, 4, 1, struct.pack(order + "I", 8 + len(first)))], 8)
    return containers.EXIF_HEAD + head + first + _ifd(order, exif, 8 + len(first)) + bytes(slack)


def description(prefix, uri, *lines):
    return (b" <rdf:Description rdf:about=''\n  xmlns:%s='%s'>\n" % (prefix, uri)
            + b"".join(b"  " + line + b"\n" for line in lines) + b" </rdf:Description>\n")


def bag(name, *items):
    return b"\n  ".join([b"<%s>" % name, b" <rdf:Bag>"] + [b"  <rdf:li>%s</rdf:li>" % i.encode() for i in items]
                        + [b" </rdf:Bag>", b"</%s>" % name])


def alt(name, text):
    return b"\n  ".join([b"<%s>" % name, b" <rdf:Alt>", b"  <rdf:li xml:lang='x-default'>%s</rdf:li>" % text.encode(),
                         b" </rdf:Alt>", b"</%s>" % name])


def uri(name):
    return xmp_packet.PROPERTIES[name][0].encode()


def packet(keywords=(PERSON, "Beach"), caption="A day at the lake", orientation=None, extra=b""):
    parts = []
    if keywords or caption:
        parts.append(description(b"dc", uri("dc:subject"), *([alt(b"dc:description", caption)] if caption else [])
                                 + ([bag(b"dc:subject", *keywords)] if keywords else [])))
    if any("/" in k for k in keywords):
        parts.append(description(b"lr", uri("lr:hierarchicalSubject"),
                                 bag(b"lr:hierarchicalSubject", *[k for k in keywords if "/" in k])))
    parts.append(description(b"exif", uri("exif:DateTimeOriginal"),
                             b"<exif:DateTimeOriginal>2019-07-14T10:22:31</exif:DateTimeOriginal>"))
    if orientation is not None:
        parts.append(description(b"tiff", uri("tiff:Orientation"), b"<tiff:Orientation>%d</tiff:Orientation>" % orientation))
    parts.append(description(b"xmp", uri("xmp:CreateDate"), b"<xmp:CreateDate>2019-07-14T10:22:31</xmp:CreateDate>"))
    meta = (b"<x:xmpmeta xmlns:x='adobe:ns:meta/' x:xmptk='Image::ExifTool 12.76'>\n"
            b"<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>\n"
            + b"".join(b"\n" + part for part in parts) + extra + b"</rdf:RDF>\n</x:xmpmeta>")
    begin = b"<?xpacket begin='" + chr(0xFEFF).encode("utf-8") + b"' id='W5M0MpCehiHzreSzNTczkc9d'?>"
    return begin + b"\n" + meta + xmp_packet.padding(xmp_packet.RESERVE) + b"<?xpacket end='w'?>"


def _dataset(record, number, value):
    return bytes([0x1C, record, number]) + len(value).to_bytes(2, "big") + value


def _resource(number, data):
    return b"8BIM" + number.to_bytes(2, "big") + b"\0\0" + len(data).to_bytes(4, "big") + data + b"\0" * (len(data) % 2)


def iptc_body(keywords=(PERSON, "Beach"), caption="A day at the lake", pad=200, lead=b""):
    block = lead + _dataset(1, 90, b"\x1b%G") + _dataset(1, 0, b"\0\x04") + _dataset(2, 0, b"\0\x04")
    block += b"".join(_dataset(2, 25, k.encode()) for k in keywords)
    block += _dataset(2, 120, caption.encode()) if caption else b""
    return (b"Photoshop 3.0\0" + _resource(0x03ED, bytes(16)) + _resource(0x0404, block)
            + _resource(0x0425, hashlib.md5(block).digest())
            + (_resource(containers.PAD_RESOURCE, bytes(pad - 12)) if pad else b""))


def _segment(marker, body):
    return bytes([0xFF, marker]) + (len(body) + 2).to_bytes(2, "big") + body


def header(exif=None, xmp=None, iptc=None):
    """A JPEG's bytes up to its first scan's header's end, as in_place reads them, and their layout."""
    data = b"\xff\xd8" + _segment(0xE0, b"JFIF\0\x01\x01\0\0\x01\0\x01\0\0")
    data += _segment(0xE1, exif_body() if exif is None else exif) if exif is not False else b""
    data += _segment(0xE1, containers.XMP_HEAD + (packet() if xmp is None else xmp)) if xmp is not False else b""
    data += _segment(0xED, iptc_body() if iptc is None else iptc) if iptc is not False else b""
    data += _segment(0xDB, b"\0" + bytes(range(64)))
    data += _segment(0xC0, b"\x08\x00\x10\x00\x10\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    data += _segment(0xDA, b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00")
    layout = containers.read(io.BytesIO(data))
    assert layout.image_offset is not None
    return layout, data


def bodies(data):
    """{kind: body} of the header `data`, by its own layout, and the layout's (marker, offset, size) of each segment."""
    layout = containers.read(io.BytesIO(data))
    found = {}
    for segment in layout.segments:
        body = data[segment.offset + 4:segment.end]
        if segment.kind == "xmp":
            found["xmp"] = xmp_packet.read(body[len(containers.XMP_HEAD):])
        elif segment.kind == "exif":
            found["exif"] = exif_block.read(body)
        elif segment.kind == "iptc":
            found["iptc"] = iptc_block.read(body)
        found.setdefault("raw", {})[segment.kind] = body
    return found, [(s.marker, s.offset, s.size, s.kind) for s in layout.segments]


def keywords(flat):
    """The journal's values of a keyword write of `flat`, as tagging's planner hands them to the writer."""
    return {field: fields.field_values(value) for field, value in fields.keyword_fields(*fields.expand_tag_fields(flat)).items()}


def caption(text):
    return {field: fields.field_values(value) for field, value in fields.caption_fields(text).items()}


def title(text):
    return {field: fields.field_values(value) for field, value in fields.title_fields(text).items()}


class TheMapCoversEveryWriter(unittest.TestCase):

    def test_every_field_a_writer_names_has_its_place_and_the_rest_go_to_the_rewrite(self):
        written = (list(keyword_fields_of()) + list(caption("x")) + list(title("x"))
                   + list(fields.date_taken_fields("2019-06-15T10:30:00"))
                   + list(dates.SHIFTED_FIELDS) + [fields.ORIENTATION, fields.PRESERVED_NAME, identity.DOCUMENT_ID_FIELD])
        self.assertEqual([], [field for field in written if field not in fields.EDITED_AS])
        owned = {"xmp": xmp_packet.PROPERTIES, "exif": exif_block.FIELDS, "iptc": iptc_block.DATASETS}
        for field, targets in fields.EDITED_AS.items():
            for kind, name in targets:
                self.assertIn(name, owned[kind], field)
        for field in (fields.REGION_INFO, "EXIF:SubSecTimeOriginal"):
            self.assertNotIn(field, fields.EDITED_AS)

    def test_a_write_naming_a_field_not_mapped_is_no_patch(self):
        layout, data = header()
        for field in ("XMP-tagpup:FaceVectors", fields.REGION_INFO, "XMP:Rating"):
            with self.subTest(field), self.assertLogs("tagpup.files.segment_edits", "INFO") as said:
                self.assertIsNone(segment_edits.new_header(layout, data, {**keywords([PERSON]), field: ["x"]}))
            self.assertIn("not a field the own writer writes", said.output[0])


def keyword_fields_of():
    return fields.keyword_fields([], [])


class AKeywordOrCaptionSave(unittest.TestCase):

    def test_the_last_keyword_taken_off_a_copy_that_cannot_take_it_is_no_patch(self):
        # An IPTC block whose data does not start with a dataset (6 of photo_index's 1,467 read) is refused: with the
        # keyword group not XMP-only, the whole write goes to the rewrite; XMP-only, the block is not touched.
        layout, data = header(iptc=iptc_body(lead=b"\0\0junk"))
        self.assertIsInstance(iptc_block.read(data[layout.iptc.offset + 4:layout.iptc.end]), containers.Refusal)
        self.assertIsNone(segment_edits.new_header(layout, data, keywords([])))
        self.assertIsNotNone(segment_edits.new_header(layout, data, keywords([PERSON, "Shore"])))

    def test_the_last_keyword_taken_off_a_copy_it_does_not_fit_is_no_patch(self):
        # A block with no dummy resource (none was laid by a patch yet) holding one short keyword: taking it off frees
        # fewer bytes than a dummy needs, so the block cannot keep its length and the write goes to the rewrite.
        layout, data = header(iptc=iptc_body(keywords=("Kite",), pad=None),
                              xmp=packet(keywords=("Kite",)), exif=exif_body(keywords="Kite"))
        self.assertTrue(iptc_block.edit(data[layout.iptc.offset + 4:layout.iptc.end], {"IPTC:Keywords": []}).length_changed)
        self.assertIsNone(segment_edits.new_header(layout, data, keywords([])))

    def test_a_text_field_given_two_values_is_no_patch(self):
        layout, data = header()
        self.assertIsNone(segment_edits.new_header(layout, data, {**caption("One"), "XMP:Description": ["One", "Two"]}))

    def test_an_edit_that_changes_the_header_length_is_caught(self):
        layout, data = header()
        longer = mock.Mock(side_effect=lambda body, changes: containers.Edited(body + b" ", False))
        with mock.patch.object(xmp_packet, "edit", longer), self.assertRaises(AssertionError):
            segment_edits.new_header(layout, data, keywords([PERSON, "Shore"]))


class AClipsSave(unittest.TestCase):
    """The packet of a clip's XMP box made new by a keyword or title save; the clip's file is in_place.patch_clip's."""

    def made(self, old, values):
        found = segment_edits.new_packet(old, values)
        self.assertIsInstance(found, tuple, found)
        return found

    def test_the_bare_packet_is_grown_and_its_own_properties_kept(self):
        new, stale = self.made(shapes.BARE, keywords([PERSON, "Pier"]))
        self.assertGreater(len(new), len(shapes.BARE))
        held = xmp_packet.read(new)
        self.assertEqual(([PERSON, "Pier"], [PERSON], "2017-08-19T09:41:27+01:00"),
                         (held["dc:subject"], held["lr:hierarchicalSubject"], held["exif:DateTimeOriginal"]))
        self.assertEqual(("IPTC:Keywords", "EXIF:XPKeywords"), stale)

    def test_a_clip_with_no_packet_is_given_the_blank_one_and_none_when_the_save_says_nothing(self):
        new, _ = self.made(None, keywords(["Pier"]))
        self.assertEqual((len(xmp_packet.blank()), ["Pier"]), (len(new), xmp_packet.read(new)["dc:subject"]))
        self.assertEqual((None, ("IPTC:Keywords", "EXIF:XPKeywords", "IPTC:ObjectName")),
                         self.made(None, {**keywords([]), **title("")}))

    def test_a_cleared_value_leaves_every_copy_outside_xmp_stale(self):
        # A JPEG's last keyword comes off every copy (#2433); a clip's EXIF and IPTC copies are not TagPup's to write.
        self.assertEqual((), segment_edits.stale_of(keywords([])))
        new, stale = self.made(self.made(None, keywords(["Pier"]))[0], keywords([]))
        self.assertEqual((("IPTC:Keywords", "EXIF:XPKeywords"), {}), (stale, xmp_packet.read(new)))

    def test_a_packet_the_editor_refuses_is_no_patch_and_a_field_not_a_clips_is_refused(self):
        with self.assertLogs("tagpup.files.segment_edits", "INFO") as said:
            self.assertIsNone(segment_edits.new_packet(b"<x:xmpmeta>", keywords(["Pier"])))
        self.assertIn("not well-formed", said.output[0])
        clips_dates = set(fields.date_taken_fields("2019-06-15T10:30:00", clip=True))
        photos_dates = set(fields.date_taken_fields("2019-06-15T10:30:00")) - clips_dates
        for field in (fields.ORIENTATION, *sorted(photos_dates), *caption("")):
            with self.subTest(field), self.assertRaises(ValueError):
                segment_edits.new_packet(None, {**keywords(["Pier"]), field: ["1"]})
        self.assertEqual(set(keywords([])) | set(title("")) | {fields.CLIP_TITLE, fields.PRESERVED_NAME} | clips_dates
                         | {fields.CLIP_CATEGORY}, set(fields.CLIP_FIELDS),
                         "its name it had kept too, before its first rename (A5), its XMP dates, a photo's EXIF ones "
                         "refused (A5 (D) REVISED), and its Windows Tags, freed by their move into XMP (2026-11-09)")


class ADateOrARotation(unittest.TestCase):

    def test_an_xmp_date_keeps_its_own_zone_and_fraction_and_is_given_none(self):
        layout, data = header()
        for value, spelt in (("2021:03:02 08:05:09+02:00", "2021-03-02T08:05:09+02:00"),
                             ("2021:03:02 08:05:09.12", "2021-03-02T08:05:09.12"),
                             ("2021:03:02 08:05:09.12-05:00", "2021-03-02T08:05:09.12-05:00"),
                             ("2021:03:02 08:05:09", "2021-03-02T08:05:09")):
            new, _stale = segment_edits.new_header(layout, data, {"XMP:DateTimeOriginal": [value]})
            self.assertEqual(spelt, bodies(new)[0]["xmp"]["exif:DateTimeOriginal"])
        self.assertIsNone(segment_edits.new_header(layout, data, {"XMP:DateTimeOriginal": ["2021-03-02"]}))

    def test_an_exif_date_with_a_zone_goes_to_the_rewrite(self):
        layout, data = header()
        self.assertIsNone(segment_edits.new_header(layout, data, {"EXIF:DateTimeOriginal": ["2021:03:02 08:05:09+02:00"]}))

    def test_a_rotation_beside_a_packet_it_cannot_read_is_no_patch(self):
        # Whether the packet holds a tiff:Orientation to follow the EXIF one cannot be told: the rewrite, not an
        # EXIF patch that might leave XMP saying the old turn.
        layout, data = header(xmp=packet(orientation=1, extra=b"<!-- a note -->\n"))
        self.assertIsNone(segment_edits.new_header(layout, data, {fields.ORIENTATION: ["6"]}))

    def test_a_rotation_of_a_photo_with_no_orientation_tag_is_no_patch(self):
        layout, data = header(exif=exif_body(orientation=None))
        self.assertIsNone(segment_edits.new_header(layout, data, {fields.ORIENTATION: ["6"]}))


if __name__ == "__main__":
    unittest.main()
