"""A photo gets an identity that outlives its path.

A path is a bad name for a photo. Rename the file, move the folder, reorganise a
shoot, and the index describes something that no longer exists while the photo looks
unindexed. The index is the valuable half: it holds the embedding and the faces,
including every name assigned by hand.

`XMP-xmpMM:DocumentID` is the XMP standard's per-document identifier, and most photos
already carry one -- 1,075 of 1,129 sampled from this library, all distinct. So the
rule is read it, and mint one only where it is missing: nothing already answered gets
rewritten, and what is written uses Adobe's own `xmp.did:` form so other tools
recognise it.
"""
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from identity import (
    DOCUMENT_ID_FIELD,
    ensure_document_id,
    mint_document_id,
    read_document_id,
)


class FakeExifTool:
    """Records what would be written, and can be told to fail."""

    def __init__(self, fail=False):
        self.writes = []
        self.fail = fail

    def set_tags(self, paths, tags=None, params=None):
        if self.fail:
            raise RuntimeError("exiftool said no")
        self.writes.append((list(paths), dict(tags or {})))


class TestReadingAnIdentity(unittest.TestCase):
    def test_it_finds_the_field_however_exiftool_spelled_it(self):
        for key in ("XMP:DocumentID", "XMP-xmpMM:DocumentID", "DocumentID"):
            self.assertEqual(read_document_id({key: "xmp.did:abc"}), "xmp.did:abc", key)

    def test_a_list_value_is_unwrapped(self):
        self.assertEqual(read_document_id({"XMP:DocumentID": ["xmp.did:abc"]}), "xmp.did:abc")

    def test_a_photo_without_one_reports_none(self):
        self.assertIsNone(read_document_id({"XMP:Subject": ["Cross Country"]}))

    def test_an_empty_value_is_not_an_identity(self):
        self.assertIsNone(read_document_id({"XMP:DocumentID": "   "}))

    def test_nothing_at_all_is_not_a_crash(self):
        self.assertIsNone(read_document_id(None))
        self.assertIsNone(read_document_id({}))


class TestMinting(unittest.TestCase):
    def test_it_uses_the_form_other_tools_recognise(self):
        # Adobe's own prefix, so a reader sees an XMP document id rather than
        # something TagPup invented.
        self.assertTrue(mint_document_id().startswith("xmp.did:"))

    def test_every_one_is_different(self):
        minted = {mint_document_id() for _ in range(200)}
        self.assertEqual(len(minted), 200)


class TestEnsuring(unittest.TestCase):
    def test_a_photo_that_has_one_is_not_written_to(self):
        # The common case by a wide margin, and the reason this is cheap: 1,075 of
        # 1,129 photos in this library already have an identity.
        et = FakeExifTool()
        found = ensure_document_id(et, "D:/a.jpg", {"XMP:DocumentID": "xmp.did:already"})
        self.assertEqual(found, "xmp.did:already")
        self.assertEqual(et.writes, [], "a photo that already had an identity was rewritten")

    def test_a_photo_without_one_is_given_one(self):
        et = FakeExifTool()
        minted = ensure_document_id(et, "D:/a.jpg", {"XMP:Subject": ["Cross Country"]})
        self.assertTrue(minted.startswith("xmp.did:"))
        self.assertEqual(len(et.writes), 1)
        paths, tags = et.writes[0]
        self.assertEqual(paths, ["D:/a.jpg"])
        self.assertEqual(tags[DOCUMENT_ID_FIELD], minted)

    def test_a_write_that_fails_does_not_stop_the_index(self):
        # An identity is an improvement on knowing the path. A photo that cannot take
        # one -- read-only, an odd format, a locked file -- still indexes perfectly.
        et = FakeExifTool(fail=True)
        self.assertIsNone(ensure_document_id(et, "D:/a.jpg", {}))

    def test_no_metadata_at_all_still_mints(self):
        et = FakeExifTool()
        self.assertTrue(ensure_document_id(et, "D:/a.jpg", None).startswith("xmp.did:"))


class TestTheExtractorCarriesIt(unittest.TestCase):
    def test_a_structured_record_reports_the_identity(self):
        from metadata import MetadataExtractor

        extractor = MetadataExtractor(mint_identities=False)
        record = extractor._structure(
            "D:/a.jpg", {"XMP:DocumentID": "xmp.did:abc", "XMP:Subject": ["Beach"]}, None)
        self.assertEqual(record["document_id"], "xmp.did:abc")

    def test_minting_is_asked_for_not_assumed(self):
        # Reading a folder to show it used to write an identity into every photo that
        # had none, and nothing told the index -- whose rows then looked out of date.
        # The indexer, which records what it read, asks for minting; nothing else does.
        from metadata import MetadataExtractor

        self.assertFalse(MetadataExtractor().mint_identities,
                         "reading a photo writes to it")
        self.assertTrue(MetadataExtractor(mint_identities=True).mint_identities)

    def test_the_indexer_mints_and_nothing_else_does(self):
        """Indexing records what it read, so it may write an identity; a scan may not.

        Without minting at index time renames still strand rows, so the indexer has to
        ask for it. Every other reader -- the folder scans in both servers, suggest,
        inspect -- must not.
        """
        import ast

        def minting_calls(path):
            with open(os.path.join(WORKSPACE_DIR, path), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            found = []
            for func in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
                for call in (n for n in ast.walk(func) if isinstance(n, ast.Call)):
                    name = getattr(call.func, "id", getattr(call.func, "attr", None))
                    if name == "MetadataExtractor" and any(
                            k.arg == "mint_identities" and getattr(k.value, "value", None) is True
                            for k in call.keywords):
                        found.append(func.name)
            return found

        self.assertEqual(["index"], minting_calls("tagpup_cli.py"))
        for server in ("scripts/tagpup_server.py", "scripts/tuner_server.py"):
            self.assertEqual([], minting_calls(server), server)

    def test_a_minted_identity_refreshes_the_change_stamps(self):
        """Writing the identity changes the file, so mtime and size must be re-read.

        Otherwise the next pass sees a photo modified since it was indexed and
        re-indexes it -- for a write this pass made. That is an infinite loop of
        expensive work, and it would look like the indexer never finishing.
        """
        import tempfile
        from metadata import MetadataExtractor

        fd, path = tempfile.mkstemp(suffix=".jpg")
        os.write(fd, b"not really a jpeg")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

        extractor = MetadataExtractor()
        record = {"path": path, "mtime": 0.0, "size": 0, "raw_metadata": {}}

        class GrowingExifTool(FakeExifTool):
            def set_tags(self, paths, tags=None, params=None):
                super().set_tags(paths, tags=tags, params=params)
                with open(path, "ab") as f:
                    f.write(b"  ")   # the write an identity would cause

        extractor._give_identities(GrowingExifTool(), [record])

        self.assertTrue(record["document_id"].startswith("xmp.did:"))
        self.assertEqual(record["size"], os.path.getsize(path),
                         "the size recorded predates the identity write")
        self.assertGreater(record["mtime"], 0.0)

    def test_a_photo_that_already_has_one_is_left_alone_in_a_batch(self):
        from metadata import MetadataExtractor

        et = FakeExifTool()
        records = [
            {"path": "D:/a.jpg", "document_id": "xmp.did:already", "raw_metadata": {}},
            {"path": "D:/b.jpg", "document_id": None, "raw_metadata": {}},
        ]
        MetadataExtractor()._give_identities(et, records)
        self.assertEqual([p for p, _ in et.writes], [["D:/b.jpg"]])


class TestAPhotoExifToolObjectsTo(unittest.TestCase):
    """Some photos carry two XMP blocks with different `rdf:about` attributes, which
    ExifTool refuses to write to until told the error is minor. Six such photos in this
    library would otherwise never get an identity.

    `-m` makes it proceed, and rewrites the XMP as it does. Photo files are not
    recoverable, so the keyword fields are read before and after and put back if the
    write cost them anything.
    """

    class Stubborn:
        """Refuses the ordinary write; records the forced one."""

        def __init__(self, before, after=None):
            self.before = before
            self.after = after if after is not None else before
            self.forced = []
            self.restored = []
            self.reads = 0

        def set_tags(self, paths, tags=None, params=None):
            if params and "-m" in params:
                self.restored.append(dict(tags or {}))
                return
            raise RuntimeError("Different 'rdf:about' attributes not handled")

        def execute(self, *args):
            self.forced.append(args)

        def get_tags(self, paths, tags=None):
            self.reads += 1
            return [dict(self.before if self.reads == 1 else self.after)]

    EMPTY = {"XMP:Subject": [], "IPTC:Keywords": [], "XMP:HierarchicalSubject": []}
    KEYWORDS = {"XMP:Subject": ["People/Hazel Brookmire"], "IPTC:Keywords": [],
                "XMP:HierarchicalSubject": []}

    def test_a_refused_write_is_retried_with_minor_errors_ignored(self):
        et = self.Stubborn(self.EMPTY)
        minted = ensure_document_id(et, "D:/a.jpg", {})
        self.assertTrue(minted.startswith("xmp.did:"))
        self.assertEqual(len(et.forced), 1, "the write was not retried")
        self.assertIn("-m", et.forced[0])

    def test_keywords_that_survive_the_forced_write_are_left_alone(self):
        et = self.Stubborn(self.KEYWORDS)
        ensure_document_id(et, "D:/a.jpg", {})
        self.assertEqual(et.restored, [], "keywords were rewritten for no reason")

    def test_keywords_lost_to_the_forced_write_are_put_back(self):
        et = self.Stubborn(self.KEYWORDS, after=self.EMPTY)
        ensure_document_id(et, "D:/a.jpg", {})
        self.assertEqual(len(et.restored), 1, "the photo lost its keywords silently")
        self.assertEqual(et.restored[0]["XMP:Subject"], ["People/Hazel Brookmire"])

    def test_a_photo_that_refuses_both_writes_is_left_without_one(self):
        class Hopeless(self.Stubborn):
            def execute(self, *args):
                raise RuntimeError("still no")

        self.assertIsNone(ensure_document_id(Hopeless(self.EMPTY), "D:/a.jpg", {}))


if __name__ == "__main__":
    unittest.main()
