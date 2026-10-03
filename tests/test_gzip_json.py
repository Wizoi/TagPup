"""A long JSON reply is gzipped for a client that accepts it (findings #572; phase 9c): the navigator's folders and a view's order
are long lists of repeating text. Flask's test client; nothing else is compressed.
"""
import gzip
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.web import app as web  # noqa: E402


class Gzipping(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        for n in range(40):
            self.vl.photo("Coast", "p%d.jpg" % n, taken="2024:06:01 10:00:00")

    def ids(self, **headers):
        return self.client.get("/library/api/library/ids?kind=all", headers=headers)

    def test_a_long_reply_is_gzipped_for_a_client_that_accepts_it_and_is_the_same_json(self):
        with mock.patch.object(web, "GZIP_FROM", 50):
            plain = self.ids()
            packed = self.ids(**{"Accept-Encoding": "gzip, deflate"})
        self.assertNotIn("Content-Encoding", plain.headers)
        self.assertEqual("gzip", packed.headers["Content-Encoding"])
        self.assertIn("Accept-Encoding", packed.headers["Vary"])
        self.assertEqual(len(packed.data), int(packed.headers["Content-Length"]))
        self.assertEqual(json.loads(gzip.decompress(packed.data)), plain.get_json())
        self.assertIn("no-store", packed.headers["Cache-Control"], "still never kept by the browser")
        self.assertEqual("application/json", packed.mimetype)

    def test_a_short_reply_is_left_alone(self):
        reply = self.ids(**{"Accept-Encoding": "gzip"})
        self.assertNotIn("Content-Encoding", reply.headers)
        self.assertLess(len(reply.data), web.GZIP_FROM)

    def test_a_client_that_does_not_accept_gzip_gets_it_plain(self):
        with mock.patch.object(web, "GZIP_FROM", 50):
            self.assertNotIn("Content-Encoding", self.ids(**{"Accept-Encoding": "identity"}).headers)

    def test_only_a_successful_json_reply_is_compressed(self):
        with mock.patch.object(web, "GZIP_FROM", 5):
            bad = self.client.get("/library/api/library/ids?kind=nope", headers={"Accept-Encoding": "gzip"})
            photo = self.client.get("/library/api/photo-thumb?id=1", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(400, bad.status_code)
        self.assertNotIn("Content-Encoding", bad.headers)
        self.assertEqual(404, photo.status_code)
        self.assertNotIn("Content-Encoding", photo.headers)

    def test_the_threshold_is_a_hundred_kilobytes(self):
        self.assertEqual(100_000, web.GZIP_FROM)


if __name__ == "__main__":
    unittest.main()
