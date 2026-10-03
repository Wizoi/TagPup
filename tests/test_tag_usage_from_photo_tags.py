"""The tag tree's counts come from `photo_tags`, not from every photo's tags JSON (findings #534).

`/api/taxonomy/tree` parsed the tags of all 68,000 photos in Python (350 ms on a one-level tree), which held the
process at page start and made the first library request wait behind it. The counts are now one pass of the
derived table, and are the same counts, node for node, as the JSON gave: a photo counts once toward a node
however many of its tags lie under it. A library with no derived tables is counted from the JSON as ever.
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import vocabulary  # noqa: E402
from tagpup.services import tags as tags_service  # noqa: E402
from tagpup.store import db, photos  # noqa: E402


def counted_from_the_json(db_path):
    """What tag_usage always answered: every photo's tags, each level above them, once per photo."""
    counts = {}
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        for (text,) in conn.execute("SELECT tags FROM photos WHERE tags IS NOT NULL"):
            for level in {level for tag in json.loads(text) for level in vocabulary.lineage(tag)}:
                counts[level] = counts.get(level, 0) + 1
    finally:
        conn.close()
    return counts


class TheCounts(unittest.TestCase):
    def setUp(self):
        self.vl = ViewLibrary(self)
        self.vl.tree("Trips/Coast/Harbour", "Trips/Coast/Cliffs", "Trips/Lakes", "Activity/Sailing")
        tags = ["Trips/Coast/Harbour", "Trips/Coast/Cliffs", "Trips/Lakes", "Activity/Sailing"]
        for number in range(240):
            chosen = {tags[number % 4], tags[(number * 3) % 4]}      # two tags under Trips/Coast on some photos
            self.vl.photo("Event %d" % (number % 6), "IMG_%04d.jpg" % number, taken="2020:01:%02d 10:00:00" % (number % 28 + 1),
                          tags=sorted(chosen))
        self.vl.photo("Event 0", "unknown.jpg", tags=["Not/In/The/Tree"])
        self.vl.photo("Event 0", "none.jpg")

    def test_they_are_the_json_counts_node_for_node(self):
        usage = photos.tag_usage(self.vl.path)
        wanted = {tag: count for tag, count in counted_from_the_json(self.vl.path).items() if count}
        for node in tags_service.tree(self.vl.library):
            self.assertEqual(wanted.get(node["tag"], 0), node["usage_count"], node["tag"])
        self.assertEqual(wanted["Trips/Coast"], usage["Trips/Coast"])
        self.assertLess(usage["Trips/Coast"], 240, "a photo with two tags under it counts once")

    def test_a_keyword_with_no_node_counts_toward_nothing_and_a_case_folded_one_toward_its_node(self):
        """The meaning, pinned (findings #553): counts follow the views' derivation (photo_tags), which ties a photo's
        keyword to a node, else the node it is without case, and makes no node: so a keyword with no node credits no
        level above it (the old lineage count credited 'Not'), and 'trips/lakes' credits the node 'Trips/Lakes'."""
        before = photos.tag_usage(self.vl.path)
        self.vl.photo("Event 0", "folded.jpg", tags=["trips/lakes"])
        self.vl.photo("Event 0", "orphan.jpg", tags=["Orphans/Nowhere"])
        after = photos.tag_usage(self.vl.path)
        self.assertEqual(before["Trips/Lakes"] + 1, after["Trips/Lakes"])
        self.assertEqual(before["Trips"] + 1, after["Trips"])
        self.assertNotIn("Orphans", after)
        self.assertNotIn("Orphans/Nowhere", after)
        self.assertNotIn("Not", after)
        self.assertIn("Not", counted_from_the_json(self.vl.path), "the JSON count credited the levels of a keyword with no node")

    def test_no_photos_tags_are_read(self):
        seen = []
        real = db.connect

        def traced(*args, **kwargs):
            conn = real(*args, **kwargs)
            conn.set_trace_callback(seen.append)
            return conn
        from unittest import mock
        with mock.patch.object(db, "connect", traced):
            photos.tag_usage(self.vl.path)
        self.assertTrue(seen)
        for sql in seen:
            self.assertNotRegex(sql, r"(?i)select\s+tags\s+from\s+photos", sql)

    def test_a_library_without_the_derived_tables_is_counted_from_the_json(self):
        conn = db.connect(self.vl.path)
        try:
            conn.execute("DROP TABLE photo_tags")
            conn.commit()
        except Exception:
            self.skipTest("the derived table cannot be dropped here")
        finally:
            conn.close()
        usage = photos.tag_usage(self.vl.path)
        self.assertEqual(counted_from_the_json(self.vl.path)["Trips/Coast"], usage["Trips/Coast"])

    def test_it_is_fast_enough_not_to_hold_a_page_start(self):
        started = time.perf_counter()
        photos.tag_usage(self.vl.path)
        self.assertLess(time.perf_counter() - started, 1.0, "generous: 70 ms on 68,466 photos")


if __name__ == "__main__":
    unittest.main()
