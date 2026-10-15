"""A journaled write of a photo's fields is one ExifTool command (docs/findings.md, #271, #282), and one that sets a photo's
identity is retried telling ExifTool an error is minor only when ExifTool refused it for a minor error, and the retry is the one command
(review of pass/journal, item 6).

Some photos carry two XMP blocks with different rdf:about attributes, which ExifTool
refuses to write until told the error is minor (`-m`). The retry was made on any
failure -- a timeout, a locked file -- and the keyword fields it might cost were put
back afterwards in a second command, outside the journal's record of the file. Now only
ExifTool's "[minor]" refusal is retried, and the keyword fields the write does not set
go in the same -m command with the values they hold. Real ExifTool on a JPEG made with
two XMP blocks here; a stand-in for the refusal that is not minor.

One command (#271): the values set and the cleared fields (`-FIELD=`) were two commands, and a crash between them left a file
holding neither what the plan read nor what it was to hold, which settling calls a conflict and never finishes; a DocumentID
went in a command of its own too (#282). Real ExifTool, on a JPEG made here: the clears still happen, in the one command that
sets the rest. (Folded in from test_journaled_write_is_one_command.)
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup.core import fields  # noqa: E402
from tagpup.files import field_values, identity  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()

TWO_BLOCKS = ('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
              '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
              '<rdf:Description rdf:about="one" xmlns:dc="http://purl.org/dc/elements/1.1/"'
              ' xmlns:lr="http://ns.adobe.com/lightroom/1.0/">'
              '<dc:subject><rdf:Bag><rdf:li>Places/Harbour</rdf:li></rdf:Bag></dc:subject>'
              '<lr:hierarchicalSubject><rdf:Bag><rdf:li>Places/Harbour</rdf:li></rdf:Bag></lr:hierarchicalSubject>'
              '</rdf:Description>'
              '<rdf:Description rdf:about="two" xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/">'
              '<xmpMM:DocumentID>xmp.did:harbour-old</xmpMM:DocumentID></rdf:Description>'
              '</rdf:RDF></x:xmpmeta><?xpacket end="w"?>')


class Refusal(Exception):
    def __init__(self, stderr):
        super().__init__("execute returned a non-zero exit status: 1")
        self.stderr = stderr


class ARefusalThatIsNotMinor(unittest.TestCase):
    def test_is_not_retried(self):
        et = mock.MagicMock()

        def set_tags(paths, tags=None, params=None):
            if "-m" not in (params or []):
                raise Refusal("Error: Error opening file - D:/a.jpg")
        et.set_tags.side_effect = set_tags
        with self.assertRaises(Refusal):
            field_values.write(et, "D:/a.jpg", {identity.DOCUMENT_ID_FIELD: "xmp.did:x", "XMP:Subject": ["Beach"]})
        self.assertEqual(1, et.set_tags.call_count, "a refusal that is not minor was retried with -m")

    def test_a_minor_one_is_known_by_exiftools_word(self):
        self.assertTrue(identity.is_minor_refusal(Refusal("Error: [minor] Different 'rdf:about' attributes"
                                                          " not handled - D:/a.jpg\r\n")))
        self.assertFalse(identity.is_minor_refusal(TimeoutError("ExifTool did not answer within 60s")))


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class APhotoWithTwoXmpBlocks(unittest.TestCase):
    def setUp(self):
        from PIL import Image

        home = own_home.for_test(self, prefix="minor_")
        self.photo = os.path.join(home.root, "a.jpg")
        Image.new("RGB", (16, 12), (90, 110, 130)).save(self.photo, "JPEG")
        xmp = os.path.join(home.root, "two.xmp")
        with open(xmp, "w", encoding="utf-8") as handle:
            handle.write(TWO_BLOCKS)
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.execute("-overwrite_original", "-xmp<=" + xmp, self.photo)

    def test_a_mixed_plan_is_the_refused_command_and_one_forced_one(self):
        plan = {identity.DOCUMENT_ID_FIELD: "xmp.did:harbour-new", "IPTC:Keywords": ["Places/Harbour", "Quay"],
                "XMP:Description": "Quay at dusk"}
        kept = ["XMP:Subject", "XMP:HierarchicalSubject"]
        with ExifToolSession(executable=EXIFTOOL) as et:
            with mock.patch.object(et, "set_tags", wraps=et.set_tags) as set_tags, \
                    mock.patch.object(et, "execute", wraps=et.execute) as execute:
                field_values.write(et, self.photo, plan)
            writes = [call for call in set_tags.call_args_list]
            held = field_values.read_one(et, self.photo, list(plan) + kept)
        self.assertEqual(2, len(writes), "the refused write, and one forced: %s" % writes)
        self.assertIn("-m", writes[1].kwargs["params"])
        # The keyword fields it does not set went in the forced command as they were.
        self.assertEqual(["Places/Harbour"], writes[1].kwargs["tags"]["XMP:Subject"])
        # No command writes the file but those two: nothing put back afterwards.
        self.assertEqual(2, len([c for c in execute.call_args_list if "-overwrite_original" in c.args]))
        self.assertTrue(fields.same_fields(held, plan), held)
        self.assertEqual((["Places/Harbour"], ["Places/Harbour"]),
                         (held["XMP:Subject"], held["XMP:HierarchicalSubject"]))


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class AKeywordWrite(unittest.TestCase):
    def setUp(self):
        from PIL import Image

        home = own_home.for_test(self, prefix="one_command_")
        self.photo = os.path.join(home.root, "a.jpg")
        Image.new("RGB", (16, 12), (90, 110, 130)).save(self.photo, "JPEG")
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([self.photo], tags={"XMP:Subject": ["Places/Harbour"], "IPTC:Keywords": ["Places/Harbour"],
                                            "XMP:HierarchicalSubject": ["Places/Harbour"],
                                            "EXIF:XPKeywords": "Places/Harbour"},
                        params=["-overwrite_original"])

    def test_sets_and_clears_in_one_command(self):
        # Every tag taken off: each keyword field cleared, and one caption set with them.
        values = dict(fields.keyword_fields([], []))
        values["XMP:Description"] = "Regatta"
        with ExifToolSession(executable=EXIFTOOL) as et:
            with mock.patch.object(et, "execute", wraps=et.execute) as execute:
                field_values.write(et, self.photo, values)
            self.assertEqual(1, execute.call_count, "a write was %d ExifTool commands" % execute.call_count)
            held = field_values.read_one(et, self.photo, list(values))
        self.assertTrue(fields.same_fields(held, values), held)
        self.assertEqual([], held["XMP:HierarchicalSubject"])
        self.assertEqual(["Regatta"], held["XMP:Description"])

    def test_only_clears_is_one_command_too(self):
        values = {"XMP:HierarchicalSubject": [], "EXIF:XPKeywords": ""}
        with ExifToolSession(executable=EXIFTOOL) as et:
            with mock.patch.object(et, "execute", wraps=et.execute) as execute:
                field_values.write(et, self.photo, values)
            self.assertEqual(1, execute.call_count)
            held = field_values.read_one(et, self.photo, ["XMP:HierarchicalSubject", "EXIF:XPKeywords", "XMP:Subject"])
        self.assertEqual(([], [], ["Places/Harbour"]),
                         (held["XMP:HierarchicalSubject"], held["EXIF:XPKeywords"], held["XMP:Subject"]))

    def test_an_identity_goes_in_the_same_command(self):
        values = {identity.DOCUMENT_ID_FIELD: "xmp.did:harbour-0001", "XMP:Subject": ["Places/Quay"],
                  "XMP:HierarchicalSubject": []}
        with ExifToolSession(executable=EXIFTOOL) as et:
            with mock.patch.object(et, "execute", wraps=et.execute) as execute:
                field_values.write(et, self.photo, values)
            self.assertEqual(1, execute.call_count, "a write was %d ExifTool commands" % execute.call_count)
            held = field_values.read_one(et, self.photo, list(values))
        self.assertTrue(fields.same_fields(held, values), held)

    def test_an_identity_taken_away_goes_in_the_same_command(self):
        with ExifToolSession(executable=EXIFTOOL) as et:
            field_values.write(et, self.photo, {identity.DOCUMENT_ID_FIELD: "xmp.did:harbour-0002"})
            values = {identity.DOCUMENT_ID_FIELD: "", "XMP:Subject": ["Places/Quay"]}
            with mock.patch.object(et, "execute", wraps=et.execute) as execute:
                field_values.write(et, self.photo, values)
            self.assertEqual(1, execute.call_count)
            held = field_values.read_one(et, self.photo, list(values))
        self.assertEqual(([], ["Places/Quay"]), (held[identity.DOCUMENT_ID_FIELD], held["XMP:Subject"]))


if __name__ == "__main__":
    unittest.main()
