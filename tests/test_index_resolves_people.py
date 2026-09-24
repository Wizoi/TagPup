"""The index works out a photo's people from its own taxonomy, whoever read the file.

Who a keyword names depends on the library: `Crew/Rowan Thackeray` is a person only
where `Crew` is a face root, and a bare `Imogen Vale` only where the taxonomy lists
her. The metadata reader resolves that when it is given the database, and nothing
made its callers give it one. `tagpup_cli index` did not -- nor the folder indexers in
either server -- so re-indexing rebuilt a photo's people from the three default roots
and dropped everyone else: 2,594 names over 2,291 rows of one library, nearly all of
them under `Pets`, a face root that only the taxonomy knows about.

So the write resolves them, against the connection it is writing through.
"""
import os
import sys
import tempfile
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from index import PhotoIndex
from metadata import extract_people

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import people_of  # noqa: E402


class TheIndexResolvesPeople(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "crew.db")
        self.index = PhotoIndex(self.db_path)
        self.index.load()
        self.index.conn.executemany(
            "INSERT INTO tag_taxonomy (id, tag, name, parent_id, has_face) VALUES (?, ?, ?, ?, ?)",
            [(1, "Crew", "Crew", None, 1),
             (2, "Crew/Rowan Thackeray", "Rowan Thackeray", 1, 1),
             (3, "People", "People", None, 1),
             (4, "People/Imogen Vale", "Imogen Vale", 3, 1)])
        self.index.conn.commit()

    def tearDown(self):
        self.index.close()
        self.tmp.cleanup()

    def stored_people(self, path):
        return people_of(self.index.conn, path)

    def test_a_reader_without_the_database_does_not_cost_the_row_its_people(self):
        tags = ["Crew/Rowan Thackeray", "Imogen Vale", "Activity/Rowing"]
        raw = {"XMP:HierarchicalSubject": tags}
        path = os.path.join(self.tmp.name, "boathouse.jpg")
        meta = {"path": path, "tags": tags, "captions": [], "raw_metadata": raw,
                "mtime": 1.0, "size": 1,
                # What the reader gives when it is not told which library: neither.
                "people": extract_people(raw, tags)}
        self.assertEqual([], meta["people"], "fixture: the reader alone finds nobody")

        self.index.build_or_update([[0.1] * 512], [meta], reload=False)

        self.assertEqual({"Rowan Thackeray", "Imogen Vale"}, set(self.stored_people(path)))

    def test_people_the_reader_did_find_are_kept(self):
        path = os.path.join(self.tmp.name, "slipway.jpg")
        raw = {"XMP:PersonInImage": ["Tamsin Oakes"]}
        meta = {"path": path, "tags": [], "captions": [], "raw_metadata": raw,
                "mtime": 1.0, "size": 1, "people": ["Tamsin Oakes"]}
        self.index.build_or_update([[0.1] * 512], [meta], reload=False)
        self.assertEqual(["Tamsin Oakes"], self.stored_people(path))


class TheCliReadsWithItsLibrary(unittest.TestCase):
    """`suggest` compares suggestions with who is already in the photo."""

    def test_every_cli_metadata_read_names_the_database(self):
        import ast
        with open(os.path.join(WORKSPACE_DIR, "tagpup_cli.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        missing = [node.lineno for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "batch_read"
                   and not any(k.arg == "db_path" for k in node.keywords)]
        self.assertEqual([], missing, "batch_read without db_path at tagpup_cli.py lines %s" % missing)


if __name__ == "__main__":
    unittest.main()
