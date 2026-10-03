"""One bulk write names at most 5,000 photos (findings #535).

Select all of 68,000 and a bulk tag posted five megabytes of paths and ran under the global file-changes lock for
hours, with no progress, no cancel and no undo of its own. The server refuses a request over the cap before it
reads or writes anything; a job with progress for more is the editing stage's (docs/ARCHITECTURE.md, phase 9d).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402

from tagpup.web import tagpup_routes  # noqa: E402


class TheCap(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.folder = self.home.root

    def post(self, route, body):
        return self.client.post("/library/api" + route, json=body)

    def names(self, count):
        return [os.path.join(self.folder, "IMG_%05d.jpg" % n) for n in range(count)]

    def test_bulk_tags_over_the_cap_is_a_400_with_a_sentence_and_nothing_is_read(self):
        reply = self.post("/photos/bulk-tags", {"paths": self.names(tagpup_routes.BULK_LIMIT + 1), "add_tags": ["Trips/Coast"]})
        self.assertEqual(400, reply.status_code)
        self.assertIn("Narrow the selection: bulk edits over 5000 photos arrive with the editing stage", reply.get_json()["error"])

    def test_smart_rename_over_the_cap_is_refused_the_same_way(self):
        reply = self.post("/folder/rename-photos", {"folder_path": self.folder, "grouping": "Regatta",
                                                   "photo_paths": self.names(tagpup_routes.BULK_LIMIT + 1)})
        self.assertEqual(400, reply.status_code)
        self.assertIn("Narrow the selection", reply.get_json()["error"])

    def test_at_the_cap_the_request_is_not_refused_for_its_size(self):
        reply = self.post("/photos/bulk-tags", {"paths": self.names(tagpup_routes.BULK_LIMIT), "add_tags": []})
        self.assertNotIn("Narrow the selection", str(reply.get_json()))

    def test_the_cap_is_five_thousand(self):
        self.assertEqual(5000, tagpup_routes.BULK_LIMIT)


if __name__ == "__main__":
    unittest.main()
