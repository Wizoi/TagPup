"""A journaled write of a photo's fields is one ExifTool command (docs/findings.md, #271, #282), and a photo whose XMP
ExifTool finds a minor fault in takes it (#2009, DECISIONS 2026-10-27).

A phone writes an Extended XMP under rdf:about="" beside the packet another program left under its own about; ExifTool
refuses to write such a photo ("[minor] Different 'rdf:about' attributes not handled", exit 1) until told an objection
it calls minor does not stop it (`-m`). A bake of such photos counted them Failed and wrote nothing; only a write that
set an identity was made again with `-m`, carrying the keyword fields in a second try (#1355). Now every write is made
with `-m`, once: the bake, the keywords and the identity in one command, the keywords and the other description's
field kept. A packet that is not XMP at all (no closing tag) is refused with `-m` as without it, and the file is left as
it was. Real ExifTool, on JPEGs whose XMP segment is put in by hand, as the photos hold it.

One command (#271): the values set and the cleared fields (`-FIELD=`) were two commands, and a crash between them left a
file holding neither what the plan read nor what it was to hold, which settling calls a conflict and never finishes; a
DocumentID went in a command of its own too (#282). The clears still happen, in the one command that sets the rest.
"""
import hashlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup.core import fields  # noqa: E402
from tagpup.files import bake, field_values, identity  # noqa: E402
from tagpup.files.exiftool_session import ExifToolFailed, ExifToolSession  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()

HEAD = ('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">')
TAIL = '</rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
KEYWORDS = (' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:lr="http://ns.adobe.com/lightroom/1.0/"',
            '<dc:subject><rdf:Bag><rdf:li>Places/Harbour</rdf:li></rdf:Bag></dc:subject>'
            '<lr:hierarchicalSubject><rdf:Bag><rdf:li>Places/Harbour</rdf:li></rdf:Bag></lr:hierarchicalSubject>')
GALLERY = "uuid:faf5bdd5-ba3d-11da-ad31-d33d75182f1b"

#: A photo-gallery packet's description, and a phone's under rdf:about="" holding its maker note.
TWO_ABOUTS = (HEAD + '<rdf:Description rdf:about="%s"%s>%s</rdf:Description>' % ((GALLERY,) + KEYWORDS)
              + '<rdf:Description rdf:about="" xmlns:GCamera="http://ns.google.com/photos/1.0/camera/">'
              '<GCamera:hdrp_makernote>QUJD</GCamera:hdrp_makernote></rdf:Description>' + TAIL)
#: Windows Photo Gallery's namespace spelled without its closing slash.
WRONG_URI = (HEAD + '<rdf:Description rdf:about="%s"%s xmlns:MicrosoftPhoto="http://ns.microsoft.com/photo/1.0">%s'
             % ((GALLERY,) + KEYWORDS)
             + '<MicrosoftPhoto:LastKeywordXMP><rdf:Bag><rdf:li>Places/Harbour</rdf:li></rdf:Bag>'
             '</MicrosoftPhoto:LastKeywordXMP></rdf:Description>' + TAIL)
#: A packet cut short: its x:xmpmeta never closed.
NO_CLOSING_TAG = HEAD + '<rdf:Description rdf:about=""%s>%s</rdf:Description></rdf:RDF><?xpacket end="w"?>' % KEYWORDS


def photo_holding(testcase, packet):
    """A JPEG whose XMP segment is `packet`, put in as bytes after its JFIF header: ExifTool would not copy a packet it
    objects to into a file."""
    from PIL import Image

    folder = os.path.join(own_home.for_test(testcase, prefix="minor_").root, "photos")
    os.makedirs(folder)
    path = os.path.join(folder, "a.jpg")
    Image.new("RGB", (16, 12), (90, 110, 130)).save(path, "JPEG")
    with open(path, "rb") as handle:
        data = handle.read()
    body = b"http://ns.adobe.com/xap/1.0/\x00" + packet.encode("utf-8")
    at = 4 + int.from_bytes(data[4:6], "big")
    with open(path, "wb") as handle:
        handle.write(data[:at] + b"\xff\xe1" + (len(body) + 2).to_bytes(2, "big") + body + data[at:])
    return path


def a_bake():
    import numpy as np

    rng = np.random.default_rng(7)
    return bake.values(rng.standard_normal(bake.PHOTO_DIM).astype(np.float32), [], (16, 12), bytes(range(32)),
                       "photo-model", "face-model")


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def writes(execute):
    return [call.args for call in execute.call_args_list if "-o" in call.args]


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class APhotoWhoseXmpExifToolObjectsTo(unittest.TestCase):
    def test_two_abouts_take_a_bake(self):
        # As the owner's bake met them: no identity in the plan, so nothing was ever made again with -m.
        photo = photo_holding(self, TWO_ABOUTS)
        plan = a_bake()
        with field_values.opened(EXIFTOOL) as et:
            et.write(photo, plan)
            held = et.read_one(photo, list(plan))
        self.assertTrue(fields.same_fields(held, plan), held)

    def test_two_abouts_take_a_bake_keywords_and_an_identity_in_one_command(self):
        photo = photo_holding(self, TWO_ABOUTS)
        plan = a_bake()
        plan.update({identity.DOCUMENT_ID_FIELD: "xmp.did:harbour-new", "IPTC:Keywords": ["Places/Harbour", "Quay"]})
        with ExifToolSession(executable=EXIFTOOL, config_file=bake.config_path()) as et:
            with mock.patch.object(et, "execute", wraps=et.execute) as execute:
                field_values.write(et, photo, plan)
            self.assertEqual(1, len(writes(execute)), "a write was %d commands" % len(writes(execute)))
            held = field_values.read_one(et, photo, list(plan))
            kept = field_values.read_one(et, photo, ["XMP:Subject", "XMP:HierarchicalSubject"])
            maker_note = et.execute("-b", "-XMP-GCamera:HDRPMakerNote", photo)
        self.assertTrue(fields.same_fields(held, plan), held)
        self.assertEqual((["Places/Harbour"], ["Places/Harbour"]), (kept["XMP:Subject"], kept["XMP:HierarchicalSubject"]))
        self.assertEqual("QUJD", maker_note, "the phone's description lost its field")

    def test_a_new_photo_with_two_abouts_is_given_an_identity_in_one_command(self):
        photo = photo_holding(self, TWO_ABOUTS)
        with ExifToolSession(executable=EXIFTOOL) as et:
            with mock.patch.object(et, "execute", wraps=et.execute) as execute:
                minted = field_values.ensure_document_id(et, photo, {})
            held = field_values.read_one(et, photo, [identity.DOCUMENT_ID_FIELD, "XMP:Subject"])
        self.assertTrue(minted and minted.startswith("xmp.did:"), minted)
        self.assertEqual(1, len(writes(execute)), "a mint was %s" % writes(execute))
        self.assertEqual(([minted], ["Places/Harbour"]), (held[identity.DOCUMENT_ID_FIELD], held["XMP:Subject"]))

    def test_a_wrong_microsoft_photo_uri_takes_a_write(self):
        photo = photo_holding(self, WRONG_URI)
        plan = {"XMP:Subject": ["Places/Harbour", "Quay"]}
        with ExifToolSession(executable=EXIFTOOL) as et:
            field_values.write(et, photo, plan)
            self.assertEqual(0, et.last_status)
            held = field_values.read_one(et, photo, list(plan))
        self.assertTrue(fields.same_fields(held, plan), held)

    def test_a_packet_with_no_closing_tag_is_refused_and_the_file_left_as_it_was(self):
        photo = photo_holding(self, NO_CLOSING_TAG)
        before = digest(photo)
        with field_values.opened(EXIFTOOL) as et:
            with self.assertRaises(ExifToolFailed):
                et.write(photo, a_bake())
        self.assertEqual(before, digest(photo))
        self.assertEqual(["a.jpg"], os.listdir(os.path.dirname(photo)), "ExifTool left a file beside the photo")


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class AMintThatFails(unittest.TestCase):
    """The indexer reads a photo with no identity, and the write fails: a lock, a timeout."""

    def test_is_not_made_again_and_leaves_the_photo(self):
        photo = photo_holding(self, TWO_ABOUTS)
        for error in (ExifToolFailed(1, "", "Error: Error opening file - a.jpg", ["-overwrite_original"]),
                      TimeoutError("ExifTool did not answer within 60s")):
            with self.subTest(error=type(error).__name__), ExifToolSession(executable=EXIFTOOL) as et:
                before = digest(photo)
                with mock.patch.object(et, "set_tags", side_effect=error) as set_tags, \
                        mock.patch.object(et, "execute", wraps=et.execute) as execute:
                    minted = field_values.ensure_document_id(et, photo, {})
            self.assertIsNone(minted, "a photo that took no identity reported one")
            self.assertEqual(1, set_tags.call_count, "a mint that failed was made again")
            self.assertEqual([], writes(execute), "a second command wrote the file")
            self.assertEqual(before, digest(photo))


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
            self.assertIn("-m", execute.call_args.args, "a clear alone is written as every write is")
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
