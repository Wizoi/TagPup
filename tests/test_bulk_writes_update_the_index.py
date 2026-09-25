"""Tagging fifty photos updates the index, the same as tagging one.

Saving a single photo has always written its row back. The bulk writers -- Add to all
selected, and Apply All on a folder's suggestions -- did not: they wrote the files and
updated the in-memory folder cache, so the screen was right and nothing looked wrong,
while the index quietly went on describing what those photos used to hold.

It surfaced a long way from the cause. A repair script planning from the index reported
nothing to do on a folder that had just been tagged wholesale, because as far as the
database was concerned it had not been.

The recorder is tagpup.store.photos.record_tags; the routes called it as
record_tags_in_index.
"""
import json
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup.store import schema  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402
from tagpup.store.photos import read_tags, record_tags  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import people_of  # noqa: E402

PHOTO = "D:/Library/2020/a.jpg"

# What the indexer writes, and so what every real row looks like: os.path.abspath,
# native separators. Rows are seeded like this directly -- never through a helper of
# the code under test, which is how the old tests came to agree with a helper that
# matched no real row.
STORED = os.path.abspath(PHOTO)


class TestTheIndexHearsAboutBulkEdits(unittest.TestCase):
    def setUp(self):
        import tempfile
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.db_path)

        # The tables as the app makes them.
        schema.ensure(self.db_path)
        conn = tagpup_db.connect(self.db_path)
        conn.executemany(
            "INSERT INTO tag_taxonomy (tag, name, parent_id, has_face) VALUES (?,?,?,?)",
            [("People", "People", None, 1),
             ("People/Jane Doe", "Jane Doe", 1, 1)],
        )
        conn.execute(
            "INSERT INTO photos (path, tags, raw_metadata) VALUES (?,?,?)",
            (STORED, json.dumps(["Beach"]), json.dumps({"XMP:Subject": ["Beach"]})),
        )
        conn.commit()
        conn.close()
        store_taxonomy.forget_people_paths()

        def cleanup():
            store_taxonomy.forget_people_paths()
            for suffix in ("", "-wal", "-shm"):
                target = self.db_path + suffix
                if os.path.exists(target):
                    try:
                        os.remove(target)
                    except OSError:
                        pass
        self.addCleanup(cleanup)

    def row(self, stored=STORED):
        """The row exactly as stored -- no spelling conversion of any kind."""
        conn = tagpup_db.connect(self.db_path)
        try:
            cur = conn.execute(
                "SELECT tags, raw_metadata FROM photos WHERE path = ?", (stored,),
            )
            found = cur.fetchone()
            listed = people_of(conn, stored)
        finally:
            conn.close()
        if not found:
            return None
        return {
            "tags": json.loads(found[0] or "[]"),
            "people": listed,
            "raw": json.loads(found[1] or "{}"),
        }

    def test_the_new_tags_are_recorded(self):
        record_tags(
            self.db_path, PHOTO, ["Beach", "People/Jane Doe"],
            flat=["Beach", "People/Jane Doe"], hierarchical=["People/Jane Doe"],
        )
        self.assertEqual(self.row()["tags"], ["Beach", "People/Jane Doe"])

    def test_the_people_column_is_recomputed(self):
        record_tags(
            self.db_path, PHOTO, ["Beach", "People/Jane Doe"],
            flat=["Beach", "People/Jane Doe"], hierarchical=["People/Jane Doe"],
        )
        self.assertIn("Jane Doe", self.row()["people"])

    def test_raw_metadata_keeps_agreeing_with_the_file(self):
        # The next read derives tags from raw_metadata, so leaving it stale would make
        # the row disagree with itself.
        record_tags(
            self.db_path, PHOTO, ["Beach", "People/Jane Doe"],
            flat=["Beach", "People/Jane Doe"], hierarchical=["People/Jane Doe"],
        )
        raw = self.row()["raw"]
        self.assertEqual(raw["XMP:Subject"], ["Beach", "People/Jane Doe"])
        self.assertEqual(raw["XMP:HierarchicalSubject"], ["People/Jane Doe"])

    def test_removing_a_tag_is_recorded_too(self):
        record_tags(self.db_path, PHOTO, [], flat=[], hierarchical=[])
        self.assertEqual(self.row()["tags"], [])

    def test_a_photo_the_database_has_never_seen_is_not_invented(self):
        # Adding it here would be indexing, which is a different job with a different
        # cost: embeddings and face detection, not a row.
        written = record_tags(self.db_path, "D:/Library/2020/never-indexed.jpg", ["Beach"])
        self.assertFalse(written)
        self.assertIsNone(self.row(os.path.abspath("D:/Library/2020/never-indexed.jpg")))

    def test_the_write_reports_that_it_changed_the_row(self):
        self.assertTrue(record_tags(self.db_path, PHOTO, ["Beach", "Cross Country"]))

    @unittest.skipUnless(os.name == "nt", "separators and case only differ on Windows")
    def test_any_spelling_of_the_photo_finds_its_native_row(self):
        # The browser and the folder cache hand this forward slashes and whatever case
        # they were given; the row holds backslashes. Every spelling is one photo.
        self.assertIn("\\", STORED)
        for spelling in (PHOTO, "d:/library/2020/A.JPG", "D:\\Library\\2020\\a.jpg"):
            with self.subTest(spelling=spelling):
                tags = ["Beach", spelling]
                self.assertTrue(record_tags(self.db_path, spelling, tags))
                self.assertEqual(self.row()["tags"], tags)
        conn = tagpup_db.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0], 1)
        finally:
            conn.close()

    @unittest.skipUnless(os.name == "nt", "separators and case only differ on Windows")
    def test_a_cold_cache_reads_the_tags_from_the_native_row(self):
        # The bulk writers start from the file now; what the index holds for a photo is
        # still read by any spelling (tagpup.store.photos.read_tags), or a rename would
        # miss the row and erase the photo's tags.
        self.assertEqual(read_tags(self.db_path, [PHOTO])[0][1], ["Beach"])
        self.assertEqual(read_tags(self.db_path, ["d:/LIBRARY/2020/a.jpg"])[0][1], ["Beach"])

    def test_a_broken_database_does_not_fail_the_write(self):
        # The file is already written and correct by this point; a stale row is
        # recoverable, and raising here would report a failure that did not happen.
        self.assertFalse(record_tags("no/such.db", PHOTO, ["Beach"]))


class TestEveryBulkWriterTellsTheIndex(unittest.TestCase):
    """The guard. Both bulk handlers wrote files without recording the result, and
    nothing in the app showed the difference, so a third one would be just as quiet."""

    #: A keyword write, wherever it is made: the files layer's one writer.
    WRITES = ("write_keywords(",)

    #: The rule is that the index hears about it, not that any one helper is used:
    #: saving a single photo writes its own row as part of a larger update, and
    #: rewriting that to funnel through the helper would be churn for its own sake.
    RECORDS = ("record_tags(", "record_saved(", "UPDATE photos", "INSERT OR REPLACE INTO photos")

    #: Where keywords are written: the routes (which must not), and the services, the
    #: CLI's `write` among them.
    SOURCES = (os.path.join("tagpup", "web", "tagpup_routes.py"),)

    def write_sites(self):
        """(where, the source from there on) of every keyword write in the routes, the
        services and the writer."""
        services = os.path.join(WORKSPACE_DIR, "tagpup", "services")
        sources = list(self.SOURCES) + [
            os.path.join("tagpup", "services", name) for name in sorted(os.listdir(services))
            if name.endswith(".py")]
        for relative in sources:
            with open(os.path.join(WORKSPACE_DIR, relative), encoding="utf-8") as f:
                lines = f.read().split("\n")
            for i, line in enumerate(lines):
                if not any(call in line for call in self.WRITES):
                    continue
                if line.lstrip().startswith(("def ", "#", "from ", "import ")):
                    continue   # a definition, a comment or an import
                yield "%s:%d" % (relative, i + 1), "\n".join(lines[i:i + 40])

    def test_each_bulk_write_records_what_it_wrote(self):
        offenders = [where for where, window in self.write_sites()
                     if not any(marker in window for marker in self.RECORDS)]
        self.assertEqual(
            offenders, [],
            "these write a photo's keywords without telling the index, so the row "
            "goes on describing what the photo used to hold: " + ", ".join(offenders)
        )

    def test_the_guard_is_looking_at_something(self):
        # The number falls as copies of the write become one service; what matters is
        # that the writes are among what it reads.
        sites = [where for where, _ in self.write_sites()]
        self.assertTrue(any("tagging.py" in where for where in sites),
                        "the write sites moved; the guard above is checking nothing")

    def test_the_routes_write_no_keywords_themselves(self):
        # Every keyword write they serve is a service's (tagpup.services.tagging).
        self.assertEqual([where for where, _ in self.write_sites() if "tagpup_routes.py" in where], [])


if __name__ == "__main__":
    unittest.main()
