"""Tagging fifty photos updates the index, the same as tagging one.

Saving a single photo has always written its row back. The bulk writers -- Add to all
selected, and Apply All on a folder's suggestions -- did not: they wrote the files and
updated the in-memory folder cache, so the screen was right and nothing looked wrong,
while the index quietly went on describing what those photos used to hold.

It surfaced a long way from the cause. A repair script planning from the index reported
nothing to do on a folder that had just been tagged wholesale, because as far as the
database was concerned it had not been.
"""
import json
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import db as tagpup_db
import tagpup_server

PHOTO = "D:/Library/2020/a.jpg"


class TestTheIndexHearsAboutBulkEdits(unittest.TestCase):
    def setUp(self):
        import tempfile
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.db_path)

        conn = tagpup_db.connect(self.db_path)
        conn.execute("""CREATE TABLE photos (
            path TEXT PRIMARY KEY, mtime REAL, size INTEGER, tags TEXT, people TEXT,
            captions TEXT, raw_metadata TEXT, embedding BLOB
        )""")
        conn.execute("""CREATE TABLE tag_taxonomy (
            id INTEGER PRIMARY KEY, tag TEXT, name TEXT, parent_id INTEGER, has_face INTEGER
        )""")
        conn.executemany(
            "INSERT INTO tag_taxonomy (tag, name, parent_id, has_face) VALUES (?,?,?,?)",
            [("People", "People", None, 1),
             ("People/Jane Doe", "Jane Doe", 1, 1)],
        )
        conn.execute(
            "INSERT INTO photos (path, tags, people, raw_metadata) VALUES (?,?,?,?)",
            (tagpup_server.to_db_path(PHOTO), json.dumps(["Beach"]), json.dumps([]),
             json.dumps({"XMP:Subject": ["Beach"]})),
        )
        conn.commit()
        conn.close()
        tagpup_server.invalidate_people_cache()

        def cleanup():
            tagpup_server.invalidate_people_cache()
            for suffix in ("", "-wal", "-shm"):
                target = self.db_path + suffix
                if os.path.exists(target):
                    try:
                        os.remove(target)
                    except OSError:
                        pass
        self.addCleanup(cleanup)

    def row(self, path=PHOTO):
        conn = tagpup_db.connect(self.db_path)
        try:
            cur = conn.execute(
                "SELECT tags, people, raw_metadata FROM photos WHERE path = ?",
                (tagpup_server.to_db_path(path),),
            )
            found = cur.fetchone()
        finally:
            conn.close()
        if not found:
            return None
        return {
            "tags": json.loads(found[0] or "[]"),
            "people": json.loads(found[1] or "[]"),
            "raw": json.loads(found[2] or "{}"),
        }

    def test_the_new_tags_are_recorded(self):
        tagpup_server.record_tags_in_index(
            self.db_path, PHOTO, ["Beach", "People/Jane Doe"],
            flat=["Beach", "People/Jane Doe"], hierarchical=["People/Jane Doe"],
        )
        self.assertEqual(self.row()["tags"], ["Beach", "People/Jane Doe"])

    def test_the_people_column_is_recomputed(self):
        tagpup_server.record_tags_in_index(
            self.db_path, PHOTO, ["Beach", "People/Jane Doe"],
            flat=["Beach", "People/Jane Doe"], hierarchical=["People/Jane Doe"],
        )
        self.assertIn("Jane Doe", self.row()["people"])

    def test_raw_metadata_keeps_agreeing_with_the_file(self):
        # The next read derives tags from raw_metadata, so leaving it stale would make
        # the row disagree with itself.
        tagpup_server.record_tags_in_index(
            self.db_path, PHOTO, ["Beach", "People/Jane Doe"],
            flat=["Beach", "People/Jane Doe"], hierarchical=["People/Jane Doe"],
        )
        raw = self.row()["raw"]
        self.assertEqual(raw["XMP:Subject"], ["Beach", "People/Jane Doe"])
        self.assertEqual(raw["XMP:HierarchicalSubject"], ["People/Jane Doe"])

    def test_removing_a_tag_is_recorded_too(self):
        tagpup_server.record_tags_in_index(
            self.db_path, PHOTO, [], flat=[], hierarchical=[])
        self.assertEqual(self.row()["tags"], [])

    def test_a_photo_the_database_has_never_seen_is_not_invented(self):
        # Adding it here would be indexing, which is a different job with a different
        # cost: embeddings and face detection, not a row.
        written = tagpup_server.record_tags_in_index(
            self.db_path, "D:/Library/2020/never-indexed.jpg", ["Beach"])
        self.assertFalse(written)
        self.assertIsNone(self.row("D:/Library/2020/never-indexed.jpg"))

    def test_a_broken_database_does_not_fail_the_write(self):
        # The file is already written and correct by this point; a stale row is
        # recoverable, and raising here would report a failure that did not happen.
        self.assertFalse(
            tagpup_server.record_tags_in_index("no/such.db", PHOTO, ["Beach"])
        )


class TestEveryBulkWriterTellsTheIndex(unittest.TestCase):
    """The guard. Both bulk handlers wrote files without recording the result, and
    nothing in the app showed the difference, so a third one would be just as quiet."""

    def test_each_bulk_write_records_what_it_wrote(self):
        source = os.path.join(WORKSPACE_DIR, "scripts", "tagpup_server.py")
        with open(source, encoding="utf-8") as f:
            lines = f.read().split("\n")

        # The rule is that the index hears about it, not that any one helper is used:
        # saving a single photo and remapping a renamed tag both write their own rows
        # as part of larger updates, and rewriting those to funnel through the helper
        # would be churn for its own sake.
        records = ("record_tags_in_index", "UPDATE photos", "INSERT OR REPLACE INTO photos")

        offenders = []
        for i, line in enumerate(lines):
            if "write_keyword_fields(" not in line:
                continue
            if line.lstrip().startswith(("def ", "#")):
                continue
            window = "\n".join(lines[i:i + 40])
            if not any(marker in window for marker in records):
                offenders.append("tagpup_server.py:%d" % (i + 1))

        self.assertEqual(
            offenders, [],
            "these write a photo's keywords without telling the index, so the row "
            "goes on describing what the photo used to hold: " + ", ".join(offenders)
        )

    def test_the_guard_is_looking_at_something(self):
        source = os.path.join(WORKSPACE_DIR, "scripts", "tagpup_server.py")
        with open(source, encoding="utf-8") as f:
            text = f.read()
        self.assertGreaterEqual(
            text.count("write_keyword_fields("), 5,
            "the write sites moved; the guard above is checking nothing"
        )


if __name__ == "__main__":
    unittest.main()
