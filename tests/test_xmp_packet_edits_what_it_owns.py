"""An XMP packet as ExifTool lays it out, edited in the nine properties TagPup owns and in nothing else (F1).

The owner's photos carry ExifTool's packets: one rdf:Description a namespace, a line break and 24 lines of 100 spaces
of padding before `<?xpacket end='w'?>`. TagPup's own writer (docs/PLAN_OWN_METADATA.md, M2) changes a caption or a
keyword by splicing text into that packet: every byte it does not own stays the file's, the change is paid for out of
the padding so the container keeps its length, and a packet it does not wholly understand is refused, never repaired.
The values here are fictional; the layout is ExifTool's (docs/domains/files.md, "The XMP packet editor").
"""
import os
import re
import sys
import unittest
from xml.parsers import expat

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

from tagpup.files import xmp_packet as xp  # noqa: E402

BOM = chr(0xFEFF).encode("utf-8")
CR = chr(13)
BEGIN = b"<?xpacket begin='" + BOM + b"' id='W5M0MpCehiHzreSzNTczkc9d'?>"
END = b"<?xpacket end='w'?>"
URI = {name.split(":")[0]: uri for name, (uri, _, _) in xp.PROPERTIES.items()}
URI["photoshop"] = "http://ns.adobe.com/photoshop/1.0/"
URI["Iptc4xmpCore"] = "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/"
OWNED = sorted({name.encode() for name in xp.PROPERTIES})


def description(prefix, *lines):
    return (b" <rdf:Description rdf:about=''\n  xmlns:%s='%s'>\n" % (prefix.encode(), URI[prefix].encode())
            + b"".join(b"  " + line + b"\n" for line in lines) + b" </rdf:Description>\n")


def bag(name, *items):
    return b"\n  ".join([b"<%s>" % name, b" <rdf:Bag>"] + [b"  <rdf:li>%s</rdf:li>" % i for i in items]
                        + [b" </rdf:Bag>", b"</%s>" % name])


def alt(name, text):
    li = b"<rdf:li xml:lang='x-default'/>" if text is None else b"<rdf:li xml:lang='x-default'>%s</rdf:li>" % text
    return b"\n  ".join([b"<%s>" % name, b" <rdf:Alt>", b"  " + li, b" </rdf:Alt>", b"</%s>" % name])


def packet(*descriptions, wrapper=True, pad=xp.RESERVE):
    meta = (b"<x:xmpmeta xmlns:x='adobe:ns:meta/' x:xmptk='Image::ExifTool 12.76'>\n"
            b"<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>\n"
            + b"".join(b"\n" + d for d in descriptions) + b"</rdf:RDF>\n</x:xmpmeta>")
    return BEGIN + b"\n" + meta + xp.padding(pad) + END if wrapper else meta + b"\n"


def caption_only(text=b"A day at the lake", **kw):
    return packet(description("dc", alt(b"dc:description", text)), **kw)


FULL = packet(
    description("dc", alt(b"dc:description", b"Picnic &amp; kites"), bag(b"dc:subject", b"Beach", b"Kites"),
                alt(b"dc:title", b"Shore"), b"<dc:creator>", b" <rdf:Seq>", b"  <rdf:li>Jo Brannock</rdf:li>",
                b" </rdf:Seq>", b"</dc:creator>"),
    description("exif", b"<exif:DateTimeOriginal>2019:07:14 10:22:31</exif:DateTimeOriginal>",
                b"<exif:ExifVersion>0232</exif:ExifVersion>"),
    description("lr", bag(b"lr:hierarchicalSubject", b"Places/Beach", b"People/Ida Corwell")),
    description("photoshop", b"<photoshop:DateCreated>2019-07-14T10:22:31</photoshop:DateCreated>"),
    description("tiff", b"<tiff:Orientation>1</tiff:Orientation>"),
    description("xmp", b"<xmp:CreateDate>2019:07:14 10:22:31</xmp:CreateDate>", b"<xmp:Rating>3</xmp:Rating>"),
    description("xmpMM", b"<xmpMM:DocumentID>xmp.did:0a1b2c</xmpMM:DocumentID>",
                b"<xmpMM:PreservedFileName>IMG_0001.JPG</xmpMM:PreservedFileName>"),
)
FULL_VALUES = {
    "dc:subject": ["Beach", "Kites"], "dc:description": "Picnic & kites", "dc:title": "Shore",
    "lr:hierarchicalSubject": ["Places/Beach", "People/Ida Corwell"], "exif:DateTimeOriginal": "2019:07:14 10:22:31",
    "xmp:CreateDate": "2019:07:14 10:22:31", "tiff:Orientation": "1", "xmpMM:DocumentID": "xmp.did:0a1b2c",
    "xmpMM:PreservedFileName": "IMG_0001.JPG",
}
OWNED_ELEMENT = re.compile(rb"\s*<(%s)(?:\s[^>]*)?>.*?</\1>" % b"|".join(re.escape(n) for n in OWNED), re.S)
PADDING = re.compile(rb"(</x:xmpmeta>)\s*(<\?xpacket end)")


def unowned(data):
    """`data` with each owned element (and the whitespace before it) and the padding's width taken out."""
    return PADDING.sub(rb"\1\2", OWNED_ELEMENT.sub(b"", data))


def well_formed(data):
    expat.ParserCreate("UTF-8", " ").Parse(data, True)
    return True


class AnExifToolPacketReadAndWritten(unittest.TestCase):
    def edited(self, data, changes, **kw):
        result = xp.edit(data, changes, **kw)
        self.assertIsInstance(result, xp.Edited, result)
        return result

    def test_every_owned_property_reads_back_as_written(self):
        self.assertEqual(FULL_VALUES, xp.read(FULL))
        self.assertEqual(xp.RESERVE, 2425)
        self.assertEqual({}, xp.read(packet(description("photoshop", b"<photoshop:City>Lakeside</photoshop:City>"))))

    def test_all_nine_set_at_the_packets_length_and_every_other_byte_kept(self):
        changes = {
            "dc:subject": ["Kites", "Dunes", "Beach"], "dc:description": "Kites over the dunes",
            "dc:title": "Dunes", "lr:hierarchicalSubject": ["Places/Dunes"],
            "exif:DateTimeOriginal": "2019:07:15 08:00:00", "xmp:CreateDate": "2019:07:15 08:00:00",
            "tiff:Orientation": 6, "xmpMM:DocumentID": "xmp.did:ffee", "xmpMM:PreservedFileName": "IMG_0002.JPG",
        }
        result = self.edited(FULL, changes)
        self.assertFalse(result.length_changed)
        self.assertEqual(len(FULL), len(result.packet))
        self.assertEqual({**changes, "tiff:Orientation": "6"}, xp.read(result.packet))
        self.assertEqual(unowned(FULL), unowned(result.packet))
        self.assertIn(b"<dc:creator>\n   <rdf:Seq>\n    <rdf:li>Jo Brannock</rdf:li>", result.packet)
        # and back again: the very bytes it started from
        self.assertEqual(FULL, self.edited(result.packet, FULL_VALUES).packet)

    def test_writing_what_it_holds_changes_no_byte(self):
        for data in (FULL, caption_only(), caption_only(None), caption_only(wrapper=False)):
            values = xp.read(data)
            self.assertEqual(data, self.edited(data, values).packet)
            self.assertFalse(self.edited(data, values).length_changed)

    def test_a_change_is_paid_for_out_of_the_padding(self):
        data = caption_only()
        longer = self.edited(data, {"dc:description": "A day at the lake, and then the rain"}).packet
        self.assertEqual(len(data), len(longer))
        self.assertEqual(data.index(END), longer.index(END))
        grown_by = len(b", and then the rain")
        self.assertEqual(len(PADDING.search(data).group(0)) - grown_by, len(PADDING.search(longer).group(0)))
        self.assertTrue(longer.endswith(b"\n" + END))
        self.assertEqual(data, self.edited(longer, {"dc:description": "A day at the lake"}).packet)

    def test_no_padding_refuses_unless_grow_and_grow_lays_the_reserve(self):
        data = caption_only(pad=0)
        self.assertIsInstance(xp.edit(data, {"dc:description": "A longer day at the lake"}), xp.Refusal)
        # the padding must keep its closing line break: a change that would eat it is refused too
        tight = caption_only(pad=5)
        self.assertIsInstance(xp.edit(tight, {"dc:description": "A day at the lake!!!!!"}), xp.Refusal)
        self.assertFalse(self.edited(tight, {"dc:description": "A day at the lake!!!!"}).length_changed)
        grown = self.edited(data, {"dc:description": "A longer day at the lake"}, grow=True)
        self.assertTrue(grown.length_changed)
        self.assertTrue(grown.packet.endswith(b"</x:xmpmeta>" + xp.padding(xp.RESERVE) + END))
        small = self.edited(data, {"dc:description": "A longer day at the lake"}, grow=True, reserve=101).packet
        self.assertTrue(small.endswith(b"</x:xmpmeta>" + xp.padding(101) + END))
        # a shorter value needs no padding: the bytes it frees become padding, and the length is kept
        shorter = self.edited(data, {"dc:description": "A day"})
        self.assertFalse(shorter.length_changed)
        self.assertTrue(shorter.packet.endswith(b"</x:xmpmeta>" + xp.padding(len(b" at the lake")) + END))

    def test_padding_is_exiftools_lines(self):
        self.assertEqual(b"\n" + (b" " * 100 + b"\n") * 24, xp.padding(xp.RESERVE))
        self.assertEqual(b"", xp.padding(0))
        self.assertEqual(b"\n", xp.padding(1))
        for size in (1, 2, 100, 101, 102, 202, 2425, 5000):
            self.assertEqual(size, len(xp.padding(size)))
            self.assertTrue(xp.padding(size).endswith(b"\n") and not xp.padding(size).strip())

    def test_a_property_of_an_undeclared_namespace_is_declared_on_the_first_description(self):
        data = caption_only()
        result = self.edited(data, {"tiff:Orientation": "6", "dc:subject": ["Beach"]}).packet
        self.assertIn(b"<rdf:Description rdf:about=''\n  xmlns:dc='%s'\n  xmlns:tiff='%s'>"
                      % (URI["dc"].encode(), URI["tiff"].encode()), result)
        self.assertEqual({"dc:description": "A day at the lake", "dc:subject": ["Beach"], "tiff:Orientation": "6"},
                         xp.read(result))
        self.assertEqual(1, result.count(b"<rdf:Description"))
        self.assertEqual(len(data), len(result))
        # an Iptc4xmpCore description first: its URI holds 'xmlns/', and the declaration still lands after it
        iptc = packet(description("Iptc4xmpCore", b"<Iptc4xmpCore:Location>Pier</Iptc4xmpCore:Location>"))
        result = self.edited(iptc, {"dc:title": "The pier"}).packet
        self.assertIn(b"xmlns:Iptc4xmpCore='%s'\n  xmlns:dc='%s'>" % (URI["Iptc4xmpCore"].encode(), URI["dc"].encode()),
                      result)
        self.assertEqual({"dc:title": "The pier"}, xp.read(result))
        # cleared again, the property goes and its namespace's declaration stays (as ExifTool leaves an empty one)
        cleared = self.edited(result, {"dc:title": None}).packet
        self.assertEqual({}, xp.read(cleared))
        self.assertIn(b"xmlns:dc=", cleared)

    def test_a_packet_with_no_wrapper_grows_only_when_asked_and_gets_no_padding(self):
        data = caption_only(wrapper=False)
        self.assertIsInstance(xp.edit(data, {"dc:description": "A day at the lake!"}), xp.Refusal)
        same_length = self.edited(data, {"dc:description": "A day at the loch"})
        self.assertFalse(same_length.length_changed)
        grown = self.edited(data, {"dc:description": "A day at the lake!"}, grow=True)
        self.assertTrue(grown.length_changed)
        self.assertEqual(len(data) + 1, len(grown.packet))
        self.assertNotIn(b"xpacket", grown.packet)

    def test_an_empty_caption(self):
        data = caption_only(None)
        self.assertEqual({"dc:description": ""}, xp.read(data))
        for nothing in ("", None):
            result = self.edited(data, {"dc:description": nothing})
            self.assertEqual(data, result.packet)
        written = self.edited(data, {"dc:description": "Rain"}).packet
        self.assertIn(b"<rdf:li xml:lang='x-default'>Rain</rdf:li>", written)
        self.assertEqual({"dc:description": "Rain"}, xp.read(written))
        self.assertEqual(len(data), len(written))

    def test_a_bag_keeps_the_callers_order_and_a_keyword_added_then_removed_gives_the_bytes_back(self):
        data = packet(description("dc", bag(b"dc:subject", b"Beach", b"Kites")))
        reordered = self.edited(data, {"dc:subject": ["Kites", "Beach", "Kites"]}).packet
        self.assertEqual({"dc:subject": ["Kites", "Beach"]}, xp.read(reordered))
        added = self.edited(data, {"dc:subject": ["Beach", "Kites", "Dunes"]}).packet
        self.assertIn(b"    <rdf:li>Kites</rdf:li>\n    <rdf:li>Dunes</rdf:li>\n   </rdf:Bag>", added)
        self.assertEqual(len(data), len(added))
        self.assertEqual(data, self.edited(added, {"dc:subject": ["Beach", "Kites"]}).packet)
        emptied = self.edited(data, {"dc:subject": []}).packet
        self.assertEqual({}, xp.read(emptied))
        self.assertEqual(unowned(data), unowned(emptied))
        self.assertIsInstance(xp.edit(data, {"dc:subject": "Beach"}), xp.Refusal)

    def test_text_is_escaped_and_what_xml_cannot_carry_is_refused(self):
        text = "Fish & chips <hot> " + CR + "\nby the pier"
        result = self.edited(caption_only(), {"dc:description": text}).packet
        self.assertIn(b"Fish &amp; chips &lt;hot&gt; &#xD;\nby the pier", result)
        self.assertEqual({"dc:description": text}, xp.read(result))
        for bad in (chr(0), chr(1), chr(8), chr(27), chr(0xFFFE)):
            self.assertIsInstance(xp.edit(caption_only(), {"dc:description": "a" + bad}), xp.Refusal)
            self.assertIsInstance(xp.edit(caption_only(), {"dc:subject": ["a" + bad]}), xp.Refusal)
        self.assertIsInstance(xp.edit(caption_only(), {"xmp:Rating": "3"}), xp.Refusal)

    def test_what_it_does_not_wholly_understand_is_refused(self):
        dc = URI["dc"].encode()
        lake = caption_only()

        def before_caption(line):
            return lake.replace(b"\n  <dc:description>", b"\n  " + line + b"\n  <dc:description>")

        refused = {
            "an attribute": lake.replace(b"rdf:about=''", b"rdf:about='' dc:title='Shore'"),
            "twice": packet(description("dc", alt(b"dc:description", b"One"), alt(b"dc:description", b"Two"))),
            "twice, two descriptions": packet(description("dc", alt(b"dc:description", b"One")),
                                              description("dc", alt(b"dc:description", b"Two"))),
            "two rdf:about": packet(description("dc", alt(b"dc:title", b"One")),
                                    description("tiff", b"<tiff:Orientation>1</tiff:Orientation>").replace(
                                        b"rdf:about=''", b"rdf:about='uuid:faf5bdd5'")),
            "a DTD": b"<!DOCTYPE x:xmpmeta [<!ENTITY e 'Shore'>]>" + caption_only(wrapper=False),
            "CDATA": lake.replace(b">A day at the lake<", b"><![CDATA[A day]]><"),
            "a comment": lake.replace(b">A day at the lake<", b">A day<!-- at --> the lake<"),
            # clearing the caption splices from the Description's start tag: the comment would go with it
            "a comment before a property": before_caption(b"<!-- Lakeside -->"),
            "text beside an element": before_caption(b"Lakeside"),
            "a processing instruction": before_caption(b"<?note Lakeside?>"),
            "a BOM": BOM + caption_only(wrapper=False),
            "UTF-16": caption_only(wrapper=False).decode().encode("utf-16-le"),
            "another language": lake.replace(b"x-default", b"en-GB"),
            "two languages": lake.replace(b"</rdf:Alt>", b" <rdf:li xml:lang='de'>Ein Tag</rdf:li>\n   </rdf:Alt>"),
            "a Seq for a Bag": packet(description("dc", bag(b"dc:subject", b"Beach").replace(b"Bag", b"Seq"))),
            "a Bag for text": packet(description("tiff", bag(b"tiff:Orientation", b"1"))),
            "bytes after the end": lake + b"\n",
            "a read-only packet": lake.replace(b"end='w'", b"end='r'"),
            "a cut wrapper": lake[:-len(END)],
            "not XML": lake.replace(b"</rdf:RDF>", b"</rdf:RDX>"),
            "over the limit": packet(description("dc", bag(b"dc:subject", *[b"Kite %d" % n for n in range(5000)]))),
        }
        for why, data in refused.items():
            with self.subTest(why):
                self.assertIsInstance(xp.read(data), xp.Refusal)
                self.assertIsInstance(xp.edit(data, {"dc:description": "Rain"}), xp.Refusal)
                self.assertIsInstance(xp.edit(data, {"dc:description": None}), xp.Refusal)
        self.assertIn(b"xmlns:dc='%s'" % dc, lake)
        # a self-closing Description reads (it holds nothing) but nothing can be put in it
        empty = packet(b" <rdf:Description rdf:about=''\n  xmlns:dc='%s'/>\n" % dc)
        self.assertEqual({}, xp.read(empty))
        self.assertIsInstance(xp.edit(empty, {"dc:title": "Shore"}), xp.Refusal)
        # an edit that would carry the packet past the limit, and a larger limit for a caller with a larger packet
        many = [("Kite %d" % n) for n in range(5000)]
        self.assertIsInstance(xp.edit(lake, {"dc:subject": many}, grow=True), xp.Refusal)
        big = refused["over the limit"]
        self.assertEqual(5000, len(xp.read(big, limit=len(big))["dc:subject"]))
        self.assertIsInstance(xp.edit(lake, {"dc:shiny": "x"}), xp.Refusal)


class APacketDamagedAnywhere(unittest.TestCase):
    """A packet cut short or with one byte changed is refused or edited whole; nothing else ever comes back."""

    CHANGES = {"dc:description": "Rain at the pier", "dc:subject": ["Beach", "Pier"], "tiff:Orientation": None}

    def check(self, data):
        self.assertIsInstance(xp.read(data), (dict, xp.Refusal))
        result = xp.edit(data, self.CHANGES)
        if isinstance(result, xp.Refusal):
            return 0
        self.assertTrue(well_formed(result.packet))
        self.assertEqual(len(data), len(result.packet))
        held = xp.read(result.packet)
        self.assertEqual(("Rain at the pier", ["Beach", "Pier"]), (held["dc:description"], held["dc:subject"]))
        self.assertNotIn("tiff:Orientation", held)
        self.assertEqual(unowned(data), unowned(result.packet))
        return 1

    def test_cut_short_at_every_seventh_byte(self):
        edited = sum(self.check(FULL[:end]) for end in range(0, len(FULL), 7))
        self.assertEqual(0, edited)

    def test_one_byte_changed_anywhere(self):
        edited = 0
        for at in range(len(FULL)):
            flip = (0x01, 0x20, 0x80)[at % 3]
            with self.subTest(at=at, flip=flip):
                edited += self.check(FULL[:at] + bytes([FULL[at] ^ flip]) + FULL[at + 1:])
        self.assertGreater(edited, 0)


if __name__ == "__main__":
    unittest.main()
