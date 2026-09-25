"""TagTuner's Remove Folder picks from the library's folders, not the disk's (#47).

It chose with the system folder dialog, which lists what is on disk, so a folder gone
from disk -- the usual reason to remove one -- could not be chosen: a deleted training
folder's rows stayed with no way to pick it. GET /api/folder/indexed lists each folder
the library holds photos directly in, as the library spells it, with every photo under
it (what removing it takes) and whether it is still on disk.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.store import db  # noqa: E402


class RemoveFolderListsTheLibrary(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tuner")
        self.client = self.app.test_client()
        self.photos = tempfile.mkdtemp(prefix="remove_folder_photos_")
        self.addCleanup(shutil.rmtree, self.photos, True)
        self.season = os.path.join(self.photos, "Season")
        self.meet = os.path.join(self.season, "Meet 1")
        self.gone = os.path.join(self.photos, "Old Meet")   # indexed, then deleted from disk
        on_disk = [os.path.join(self.season, "start.jpg"),
                   os.path.join(self.meet, "a.jpg"), os.path.join(self.meet, "b.jpg")]
        for photo in on_disk:
            os.makedirs(os.path.dirname(photo), exist_ok=True)
            with open(photo, "wb") as handle:
                handle.write(b"jpeg")
        conn = db.connect(self.home.library("library.db"))
        try:
            for photo in on_disk + [os.path.join(self.gone, "c.jpg")]:
                photo_rows.add_read(conn, photo, {"XMP:Subject": ["Activity/Cross Country"]})
            conn.commit()
        finally:
            conn.close()

    def folders(self):
        reply = self.client.get("/library/api/folder/indexed")
        self.assertEqual(200, reply.status_code, reply.data)
        return {f["path"]: f for f in reply.get_json()["folders"]}

    def test_each_folder_holding_photos_is_listed_as_the_library_spells_it(self):
        self.assertEqual(sorted(self.folders()),
                         sorted(paths.stored(f) for f in (self.season, self.meet, self.gone)))

    def test_a_folder_gone_from_disk_is_listed_and_marked(self):
        folders = self.folders()
        self.assertFalse(folders[paths.stored(self.gone)]["on_disk"])
        self.assertTrue(folders[paths.stored(self.meet)]["on_disk"])

    def test_each_counts_every_photo_under_it(self):
        folders = self.folders()
        self.assertEqual(folders[paths.stored(self.season)]["photos"], 3)
        self.assertEqual(folders[paths.stored(self.meet)]["photos"], 2)
        self.assertEqual(folders[paths.stored(self.gone)]["photos"], 1)

    def test_the_folder_gone_from_disk_can_be_removed_as_listed(self):
        gone = self.folders()[paths.stored(self.gone)]["path"]
        reply = self.client.post("/library/api/folder/remove", json={"folder_path": gone})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(reply.get_json()["photos_removed"], 1)
        self.assertNotIn(paths.stored(self.gone), self.folders())

    def test_a_library_with_no_photos_lists_none(self):
        app, _home = web_client.app_for(self, "tuner")
        reply = app.test_client().get("/library/api/folder/indexed")
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(reply.get_json(), {"folders": []})


if __name__ == "__main__":
    unittest.main()
