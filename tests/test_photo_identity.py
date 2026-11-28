"""A photo gets an identity that outlives its path.

A path is a bad name for a photo. Rename the file, move the folder, reorganise a
shoot, and the index describes something that no longer exists while the photo looks
unindexed. The index is the valuable half: it holds the embedding and the faces,
including every name assigned by hand.

`XMP-xmpMM:DocumentID` is the XMP standard's per-document identifier, and most photos
already carry one -- 1,075 of 1,129 sampled from this library, all distinct. So the
rule is read it, and mint one only where it is missing: nothing already answered gets
rewritten, and what is written uses Adobe's own `xmp.did:` form so other tools
recognise it. The one mint is a field of the index's bake (tagpup.services.bake_after_index;
its scenarios: tests/test_the_index_writes_the_bake.py).
"""
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.core.fields import read_document_id  # noqa: E402
from tagpup.files.identity import mint_document_id  # noqa: E402


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


class TestTheExtractorCarriesIt(unittest.TestCase):
    def test_a_structured_record_reports_the_identity(self):
        from tagpup.files import metadata

        record = metadata.structure(
            "D:/a.jpg", {"XMP:DocumentID": "xmp.did:abc", "XMP:Subject": ["Beach"]}, None, (1.0, 1))
        self.assertEqual(record["document_id"], "xmp.did:abc")

    def test_the_index_s_bake_mints_and_nothing_else_does(self):
        """Indexing records what it read, so its bake may write an identity, in the transaction that records it in
        the photo's row; a scan may not. Reading a folder to show it once wrote an identity into every photo that had
        none, and nothing told the index -- whose rows then looked out of date. The one mint is the index's bake
        (bake_after_index, DECISIONS 2026-11-26): no other module, program or script calls mint_document_id."""
        import ast

        def mints(path):
            with open(os.path.join(WORKSPACE_DIR, path), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            return any(getattr(call.func, "id", getattr(call.func, "attr", None)) == "mint_document_id"
                       for call in ast.walk(tree) if isinstance(call, ast.Call))

        sources = ["tagpup_cli.py"]
        for folder in ("tagpup", "scripts"):
            for top, _dirs, names in os.walk(os.path.join(WORKSPACE_DIR, folder)):
                sources += [os.path.relpath(os.path.join(top, name), WORKSPACE_DIR).replace(os.sep, "/")
                            for name in sorted(names) if name.endswith(".py")]
        self.assertEqual(["tagpup/services/bake_after_index.py"], [path for path in sources if mints(path)])


if __name__ == "__main__":
    unittest.main()
