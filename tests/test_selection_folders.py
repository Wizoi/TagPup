"""The folders a selection is in, in the tally's answer (POST /api/library/selection/tally; tagpup.services.selection;
tagpup.store.selection_folders; #675, the owner's first review of the library views): what the panel offers to open in Organize.

Photos are rows as the indexer records them (tests/view_library.py), so photo_folder is what the writes keep. Fictional names only.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.services import library_view, selection  # noqa: E402
from tagpup.store import db  # noqa: E402


class Folders(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.vl.tree("Trips/Coast")
        self.a = self.vl.photo("2024 Coast", "a.jpg", taken="2024:06:01 10:00:00", tags=["Trips/Coast"])
        self.b = self.vl.photo("2024 Coast", "b.jpg", taken="2024:06:02 10:00:00")
        self.c = self.vl.photo("Bake & Share #2 (Rowan's)", "c.jpg", taken="2024:07:02 10:00:00", tags=["Trips/Coast"])
        self.d = self.vl.photo("Misc", "d.jpg")

    def folders(self, selection_body):
        reply = self.client.post("/library/api/library/selection/tally", json={"selection": selection_body})
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()["folders"]

    def folder(self, name):
        return paths.stored(os.path.join(self.vl.pictures, name))

    def test_a_list_of_ids_names_its_folders_by_name_with_their_photos_and_paths(self):
        found = self.folders({"ids": [self.a, self.b, self.c]})
        self.assertEqual(2, found["count"])
        self.assertEqual([{"path": self.folder("2024 Coast"), "name": "2024 Coast", "photos": 2},
                          {"path": self.folder("Bake & Share #2 (Rowan's)"), "name": "Bake & Share #2 (Rowan's)", "photos": 1}], found["listed"])

    def test_a_source_less_the_excluded_counts_the_folders_of_what_is_left(self):
        found = self.folders({"source": {"kind": "keyword", "value": "Trips"}})
        self.assertEqual(["2024 Coast", "Bake & Share #2 (Rowan's)"], [each["name"] for each in found["listed"]])
        found = self.folders({"source": {"kind": "keyword", "value": "Trips"}, "excluded": [self.c]})
        self.assertEqual([("2024 Coast", 1)], [(each["name"], each["photos"]) for each in found["listed"]])
        found = self.folders({"source": {"kind": "all"}, "excluded": [self.a, self.b, self.c, self.d]})
        self.assertEqual({"count": 0, "listed": []}, found)

    def test_more_than_ten_folders_are_counted_and_not_named(self):
        ids = [self.vl.photo("Event %02d" % n, "p.jpg") for n in range(11)]
        found = self.folders({"ids": ids})
        self.assertEqual({"count": 11, "listed": []}, found)
        found = self.folders({"ids": ids[:10]})
        self.assertEqual(10, found["count"])
        self.assertEqual(["Event %02d" % n for n in range(10)], [each["name"] for each in found["listed"]])
        with mock.patch.object(selection, "MAX_FOLDERS_LISTED", 3):
            self.assertEqual({"count": 4, "listed": []}, self.folders({"ids": [self.a, self.c, self.d, ids[0]]}))

    def test_the_whole_library_is_one_grouped_read_and_names_no_photo(self):
        found = self.folders({"source": {"kind": "all"}})
        self.assertEqual(3, found["count"])
        self.assertNotIn("a.jpg", str(found))

    def test_a_list_of_ids_seeks_photo_folder_by_key_and_never_scans_it(self):
        seen = []
        real = library_view.opened

        def opened(library):
            conn = real(library)
            conn.set_trace_callback(seen.append)
            return conn
        with mock.patch.object(library_view, "opened", opened):
            self.folders({"ids": [self.a, self.c]})
        grouped = [text for text in seen if "FROM photo_folder" in text]
        self.assertEqual(1, len(grouped), seen)
        conn = db.connect(db.readonly_uri(self.vl.path), uri=True)
        try:
            conn.execute("CREATE TEMP TABLE sel (id INTEGER PRIMARY KEY)")
            plan = [row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + grouped[0])]
        finally:
            conn.close()
        self.assertFalse([line for line in plan if line.startswith("SCAN photo_folder") or line.startswith("SCAN p ")], plan)
        self.assertTrue([line for line in plan if "photo_folder USING INTEGER PRIMARY KEY" in line], plan)


if __name__ == "__main__":
    unittest.main()
