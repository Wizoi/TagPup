"""Renaming or deleting a tag says how many photos it rewrote, and rewrites them right.

Both handlers rewrote every photo carrying the tag and answered "success" whatever
update_photo_metadata_tags managed. A photo ExifTool could not write kept the old
keyword while the tree said it was gone -- and a deleted tag was removed from the tree
even though photos still carried it.

Rename changed the tree first and cleared the people cache last, so the rewrite in
between resolved names against the tree as it had been: a photo carrying the person's
bare name as well had the old path written straight back into it. And it found the
tag's descendants with LIKE, which reads `_` as any character, so renaming `Club_A`
also renamed everything under `ClubXA`.
"""
import json
import os
import unittest

from tests.handler_harness import Library
from tests.test_taxonomy_lifecycle import EXIFTOOL, requires_exiftool

import tagpup_server
from exiftool_session import ExifToolSession

TREE = [
    (1, "People", "People", None, 1),
    (2, "People/Rowan Thackeray", "Rowan Thackeray", 1, 1),
    (3, "Activity", "Activity", None, 0),
    (4, "Activity/Rowing", "Rowing", 3, 0),
    (5, "Club_A", "Club_A", None, 0),
    (6, "Club_A/Juniors", "Juniors", 5, 0),
    (7, "ClubXA", "ClubXA", None, 0),
    (8, "ClubXA/Seniors", "Seniors", 7, 0),
]


@requires_exiftool
class TaxonomyEditsReportWhatTheyWrote(unittest.TestCase):
    def setUp(self):
        self.lib = Library(self)
        for row in TREE:
            self.lib.execute("INSERT INTO tag_taxonomy (id, tag, name, parent_id, has_face)"
                             " VALUES (?, ?, ?, ?, ?)", row)

    def add_photo(self, name, tags, writable=True):
        path = os.path.join(self.lib.photos, name)
        if writable:
            from PIL import Image

            Image.new("RGB", (8, 8)).save(path)
            with ExifToolSession(executable=EXIFTOOL) as et:
                et.set_tags([path], tags={"XMP:HierarchicalSubject": [t for t in tags if "/" in t],
                                          "XMP:Subject": [t.split("/")[-1] for t in tags]},
                            params=["-overwrite_original"])
        else:
            with open(path, "wb") as handle:
                handle.write(b"not a picture")
        self.lib.execute("INSERT INTO photos (path, mtime, size, tags, people, captions, raw_metadata)"
                         " VALUES (?, 1.0, 1, ?, '[]', '[]', '{}')", (path, json.dumps(tags)))
        return path

    def hierarchical(self, path):
        with ExifToolSession(executable=EXIFTOOL) as et:
            found = et.get_tags([path], tags=["XMP:HierarchicalSubject"])[0]
        value = found.get("XMP:HierarchicalSubject", [])
        return value if isinstance(value, list) else [value]

    def tree(self):
        return {r[0] for r in self.lib.rows("SELECT tag FROM tag_taxonomy")}

    def call(self, method, body):
        handler = self.lib.handler(EXIFTOOL)
        return handler.call(method, body)

    def test_a_person_renamed_is_not_written_back_under_the_old_path(self):
        photo = self.add_photo("regatta.jpg", ["People/Rowan Thackeray", "Rowan Thackeray"])
        # A running server has the people cache warm.
        tagpup_server.people_paths_for(self.lib.db_path)

        self.call("handle_post_taxonomy_rename", {"tag_id": 2, "new_name": "Rowan Thackeray-Vale"})

        written = self.hierarchical(photo)
        self.assertIn("People/Rowan Thackeray-Vale", written)
        self.assertNotIn("People/Rowan Thackeray", written)

    def test_a_rename_reports_the_photos_it_could_not_rewrite(self):
        self.add_photo("boathouse.jpg", ["Activity/Rowing"])
        self.add_photo("broken.jpg", ["Activity/Rowing"], writable=False)

        status, reply = self.call("handle_post_taxonomy_rename", {"tag_id": 4, "new_name": "Sculling"})

        self.assertEqual(200, status, reply)
        self.assertEqual(2, reply["photos_affected"])
        self.assertEqual(1, reply["photos_rewritten"])
        self.assertIn("warning", reply)

    def test_a_delete_that_cannot_rewrite_every_photo_keeps_the_tag(self):
        good = self.add_photo("boathouse.jpg", ["Activity/Rowing"])
        self.add_photo("broken.jpg", ["Activity/Rowing"], writable=False)

        status, reply = self.call("handle_post_taxonomy_delete_confirm",
                                  {"tag_id": 4, "action": "remove"})

        self.assertFalse(reply.get("success"), reply)
        self.assertEqual(1, reply["photos_rewritten"])
        self.assertIn("Activity/Rowing", self.tree(), "deleted while a photo still carries it")
        self.assertNotIn("Activity/Rowing", self.hierarchical(good))

    def test_a_delete_that_rewrites_every_photo_removes_the_tag(self):
        good = self.add_photo("boathouse.jpg", ["Activity/Rowing"])

        status, reply = self.call("handle_post_taxonomy_delete_confirm",
                                  {"tag_id": 4, "action": "remove"})

        self.assertTrue(reply.get("success"), reply)
        self.assertEqual(1, reply["photos_rewritten"])
        self.assertNotIn("Activity/Rowing", self.tree())
        self.assertNotIn("Activity/Rowing", self.hierarchical(good))

    def test_renaming_club_a_leaves_clubxa_alone(self):
        self.call("handle_post_taxonomy_rename", {"tag_id": 5, "new_name": "Club_B"})

        tree = self.tree()
        self.assertIn("Club_B/Juniors", tree)
        self.assertIn("ClubXA/Seniors", tree)


if __name__ == "__main__":
    unittest.main()
