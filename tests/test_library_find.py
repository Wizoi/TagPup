"""GET /api/library/find (phase 9c): the id of the photo at a path, for the move from a folder on disk to its library view.

Flask's test client, a home of its own, photos as rows the indexer records. Fictional names only.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402


class Find(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.a = self.vl.photo("Coast", "IMG_0001.jpg", taken="2024:06:01 10:00:00")
        self.b = self.vl.photo("Coast", "IMGX0001.jpg", taken="2024:06:02 10:00:00")
        self.c = self.vl.photo("Lake", "IMG_0001.jpg", taken="2024:06:03 10:00:00")

    def find(self, path, **kwargs):
        return self.client.get("/library/api/library/find", query_string={"path": path} if path is not None else {}, **kwargs)

    def test_a_path_is_the_photo_at_it_and_no_other(self):
        for id_, folder, name in ((self.a, "Coast", "IMG_0001.jpg"), (self.b, "Coast", "IMGX0001.jpg"), (self.c, "Lake", "IMG_0001.jpg")):
            reply = self.find(os.path.join(self.vl.pictures, folder, name))
            self.assertEqual((200, id_), (reply.status_code, reply.get_json()["id"]), (folder, name))

    def test_an_underscore_is_not_a_wildcard(self):
        # IMG_0001.jpg must not find IMGX0001.jpg (LIKE would): the two are two photos.
        self.assertNotEqual(self.find(os.path.join(self.vl.pictures, "Coast", "IMG_0001.jpg")).get_json()["id"], self.b)

    def test_a_spelling_the_filesystem_treats_as_the_same_finds_the_photo(self):
        spelled = os.path.join(self.vl.pictures, "COAST", "img_0001.JPG")
        self.assertEqual(self.a, self.find(spelled).get_json()["id"])

    def test_no_photo_there_is_a_404_with_a_sentence_and_no_path_is_a_400(self):
        reply = self.find(os.path.join(self.vl.pictures, "Coast", "nothing.jpg"))
        self.assertEqual(404, reply.status_code)
        self.assertNotIn("Traceback", reply.get_json()["error"])
        self.assertEqual(400, self.find(None).status_code)
        self.assertEqual(400, self.find("   ").status_code)

    def test_this_pc_only(self):
        self.assertEqual(403, self.find(os.path.join(self.vl.pictures, "Coast", "IMG_0001.jpg"),
                                        environ_overrides={"REMOTE_ADDR": "10.0.0.7"}).status_code)


if __name__ == "__main__":
    unittest.main()
