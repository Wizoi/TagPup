"""What the pages are told of the photos found damaged (tagpup.services.damaged_photos;
docs/findings.md, #407): each app's header count, TagPup's folder notice, and the
Activity page's Needs attention and timeline.

It was silent: a damaged photo was a gap in the folder, and nothing said anything was
wrong. Counts go to both apps' headers; the paths only to what answers this PC alone --
the Activity page, and TagPup's folder list. A file replaced since has left every list.

The records are made as the indexer makes them (damaged_photos.remember), of real
damaged files (tests/damaged_photos).
"""
import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import damaged_photos  # noqa: E402
import own_home  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import damaged_photos as damaged  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.web import app as web  # noqa: E402

ELSEWHERE = {"REMOTE_ADDR": "192.168.1.20"}
PORTS = {"tagpup": 8090, "tuner": 8080}


class Case(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="damaged_routes_")
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.library = Library(self.db_path)
        self.folder = os.path.join(self.home.root, "Harbour Walk")
        self.cut = damaged_photos.truncated(os.path.join(self.folder, "cut short.jpg"))
        self.half = damaged_photos.second_half_zeros(os.path.join(self.folder, "Day 2", "copy stopped.jpg"))
        self.elsewhere = damaged_photos.truncated(os.path.join(self.home.root, "Regatta", "far.jpg"))
        self.found(self.cut, "truncated", "image file is truncated (4 bytes not processed)")
        self.found(self.half, damaged.INCOMPLETE, "the last 70000 bytes are zeros", 70000)
        self.found(self.elsewhere, "truncated", "image file is truncated")

    def found(self, path, kind, detail, zeros=0):
        stat = os.stat(path)
        damaged.remember(self.library, [(path, (stat.st_mtime, stat.st_size), kind, detail, zeros)],
                         run="index:harbour:20260928T100000-1")

    def client(self, kind="tagpup"):
        app = web.create_app(kind, ports=PORTS)
        app.testing = True
        return app.test_client()

    def get(self, url, kind="tagpup", **kwargs):
        reply = self.client(kind).get(url, **kwargs)
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        return reply.get_json()


class TheHeadersCount(Case):
    def test_both_apps_count_and_name_no_photo(self):
        for kind in ("tagpup", "tuner"):
            found = self.get("/harbour/api/damaged-photos", kind)
            self.assertEqual({"library": "harbour", "unreadable": 2, "incomplete": 1}, found, kind)

    def test_a_file_replaced_since_is_not_counted(self):
        damaged_photos.whole_jpeg(self.cut, seed=3)
        self.assertEqual(1, self.get("/harbour/api/damaged-photos")["unreadable"])


class TheFolderNotice(Case):
    def test_the_folder_lists_its_damaged_photos_at_any_depth_with_why(self):
        found = self.get("/harbour/api/folder/damaged?path=" + urllib.parse.quote(self.folder))
        shown = {os.path.basename(photo["path"]): photo for photo in found["photos"]}
        self.assertEqual({"cut short.jpg", "copy stopped.jpg"}, set(shown))
        self.assertFalse(shown["cut short.jpg"]["indexed"])
        self.assertEqual("the file ends before the picture does", shown["cut short.jpg"]["reason"])
        self.assertTrue(shown["copy stopped.jpg"]["indexed"])
        self.assertEqual(os.path.getsize(self.cut), shown["cut short.jpg"]["size"])

    def test_this_pc_only(self):
        reply = self.client().get("/harbour/api/folder/damaged?path=" + urllib.parse.quote(self.folder),
                                  environ_base=ELSEWHERE)
        self.assertEqual(403, reply.status_code)


class NothingIsWrittenToThem(Case):
    """The card note says nothing was written to a damaged photo: a page's write to one is
    refused, 409, and the file is left as it is."""

    def setUp(self):
        super().setUp()
        import photo_rows
        from tagpup.store import db
        conn = db.connect(self.db_path)
        try:   # the incomplete copy was indexed, as the indexer indexes one
            photo_rows.add_read(conn, self.half, {})
            conn.commit()
        finally:
            conn.close()
        with open(self.half, "rb") as handle:
            self.before = (handle.read(), os.stat(self.half).st_mtime_ns)

    def unchanged(self):
        with open(self.half, "rb") as handle:
            self.assertEqual(self.before, (handle.read(), os.stat(self.half).st_mtime_ns))

    def test_saving_a_title_or_tags_is_refused(self):
        reply = self.client().post("/harbour/api/photo/save-metadata",
                                   json={"path": self.half, "title": "Jetty", "tags": ["Places/Harbour"]})
        self.assertEqual(409, reply.status_code, reply.get_data(as_text=True))
        self.assertIn("nothing is written to it", reply.get_json()["error"])
        self.unchanged()

    def test_a_bulk_tag_that_names_one_is_refused(self):
        reply = self.client().post("/harbour/api/photos/bulk-tags",
                                   json={"paths": [self.half], "add_tags": ["Places/Harbour"], "remove_tags": []})
        self.assertEqual(409, reply.status_code, reply.get_data(as_text=True))
        self.unchanged()


class TheActivityPage(Case):
    def test_needs_attention_lists_every_damaged_photo_with_its_folder(self):
        found = self.get("/api/activity/attention")
        self.assertEqual((2, 1), (found["unreadable"], found["incomplete"]))
        harbour = next(each for each in found["libraries"] if each["name"] == "harbour")
        shown = {os.path.basename(photo["path"]): photo for photo in harbour["photos"]}
        self.assertEqual({"cut short.jpg", "copy stopped.jpg", "far.jpg"}, set(shown))
        self.assertEqual("http://localhost:8090/harbour/?path=" + urllib.parse.quote(self.folder),
                         shown["cut short.jpg"]["folder_url"])
        for field in ("found", "mtime", "size", "reason", "detail"):
            self.assertIn(field, shown["cut short.jpg"])

    def test_this_pc_only(self):
        self.assertEqual(403, self.client().get("/api/activity/attention", environ_base=ELSEWHERE).status_code)

    def test_the_timeline_says_what_the_indexer_found(self):
        entries = self.get("/api/activity/timeline")["entries"]
        found = [entry for entry in entries if entry["kind"] == "found"]
        self.assertEqual(1, len(found), entries)
        self.assertEqual("found 2 unreadable photos and 1 possibly incomplete copy", found[0]["what"])
        self.assertEqual("index:harbour:20260928T100000-1", found[0]["run"])
        self.assertEqual({"unreadable": 2, "incomplete": 1}, found[0]["counts"])


if __name__ == "__main__":
    unittest.main()
