"""The phone's Browse rows get a cover (docs/plans/phone-redesign.md, cut 2): GET /api/library/covers answers, for each row
the page names, the first photo of the row's own source -- what a tap on the row opens -- in the views' order (by Date
Taken, the undated after the dated) and its thumbnail's URL (thumbnails.url, a clip's being its poster), in the order
asked (tagpup.services.library_view.covers).

The owner scrolls Browse: the page asks for the covers of the rows near the screen, at most 24 at a time. A row whose source
holds no photo now -- a keyword no photo carries, a folder the library no longer holds -- has no cover and keeps its
placeholder; a row named twice is answered once; a photo added afterwards is the next ask's cover, nothing stored. What
cannot mean anything (a section that is none, no rows, rows that are not a list of rows, more than 24) is 400 with a sentence.

New with the route: on the code before it every request here was a 404.

Photos are rows as the indexer records them (tests/view_library.py, tests/photo_rows.py add_read); fictional names only.
"""
import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import page_fixtures  # noqa: E402
import roots_library as rl  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.services import library_view  # noqa: E402

WREN = "People/Wren Halloway"
COVERS_REPLIES = page_fixtures.path_of("covers_replies.json")


class BrowseRowCovers(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.vl.tree("Trips/Coast", "Trips/Empty", WREN, face_root="People")
        photo = self.vl.photo
        # Made in this order so that the lowest id is not the first by Date Taken: the undated comes after the dated.
        self.jetty = photo("Coast", "jetty.jpg")
        self.pier = photo("Coast", "pier.jpg", "2024:03:01 10:00:00", tags=["Trips/Coast"])
        self.dawn = photo("Coast", "dawn.jpg", "2024:01:05 07:00:00", tags=["Trips/Coast", WREN])
        self.reeds = photo("Lake", "reeds.jpg")
        self.shore = photo("Lake", "shore.jpg")
        # A clip is dated by its QuickTime header, as the index reads one.
        self.swim = photo("Reels", "swim.mp4", fields={"QuickTime:CreateDate": "2023:08:10 15:00:00"})
        self.coast = os.path.join(self.vl.pictures, "Coast")
        self.lake = os.path.join(self.vl.pictures, "Lake")
        self.reels = os.path.join(self.vl.pictures, "Reels")

    def ask(self, section, rows, library="library"):
        reply = self.client.get("/%s/api/library/covers" % library,
                                query_string={"section": section, "rows": json.dumps(rows)})
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

    def covers(self, section, rows, library="library"):
        return {each["id"]: each["photo"] for each in self.ask(section, rows, library)["covers"]}

    def refused(self, **query):
        reply = self.client.get("/library/api/library/covers", query_string=query)
        self.assertEqual(400, reply.status_code, reply.data)
        self.assertTrue(reply.get_json()["error"], "a sentence says why")
        return reply.get_json()["error"]

    def thumb_of(self, photo_id):
        """The thumbnail URL the card of the photo carries: the one thumbnails.url makes."""
        reply = self.client.get("/library/api/library/cards", query_string={"ids": str(photo_id)})
        return reply.get_json()["cards"][0]["thumb"]

    @staticmethod
    def folder(row_id, path, recursive=False):
        return {"id": row_id, "kind": "folder", "value": path, "recursive": recursive}

    def test_each_row_gets_the_first_photo_its_tap_opens_by_date_taken_in_the_order_asked(self):
        reply = self.ask("folders", [self.folder("f:lake", self.lake), self.folder("f:coast", self.coast),
                                     self.folder("f:pictures", self.vl.pictures, recursive=True)])
        self.assertEqual("folders", reply["section"])
        self.assertEqual(["f:lake", "f:coast", "f:pictures"], [each["id"] for each in reply["covers"]])
        lake, coast, pictures = reply["covers"]
        self.assertEqual(self.dawn, coast["photo"], "the earliest Date Taken, not the lowest id (an undated photo)")
        self.assertEqual(self.reeds, lake["photo"], "a folder of undated photos: the first by id")
        self.assertEqual(self.swim, pictures["photo"], "a folder with its subfolders: the earliest of them all")
        self.assertEqual(self.thumb_of(self.dawn), coast["thumb"])
        self.assertTrue(coast["thumb"].startswith("/api/photo-thumb?id=%d" % self.dawn), coast["thumb"])
        for section, row, first in (
                ("keywords", {"id": "k:Trips", "kind": "keyword", "value": "Trips"}, self.dawn),
                ("people", {"id": "p:1", "kind": "person", "value": WREN}, self.dawn),
                ("dates", {"id": "y:2024", "kind": "year", "value": "2024"}, self.dawn),
                ("dates", {"id": "m:2024-03", "kind": "month", "value": "2024-03"}, self.pier)):
            self.assertEqual({row["id"]: first}, self.covers(section, [row]), row)

    def test_a_clips_cover_is_its_own_id_and_the_thumbnail_url_that_serves_its_poster(self):
        cover = self.ask("folders", [self.folder("f:reels", self.reels)])["covers"][0]
        self.assertEqual(self.swim, cover["photo"])
        self.assertEqual(self.thumb_of(self.swim), cover["thumb"], "the card's thumbnail: the route draws a clip's poster")
        self.assertEqual("video", self.vl.rows("SELECT kind FROM photos WHERE id = ?", self.swim)[0][0])

    def test_a_row_whose_source_holds_no_photo_now_has_no_cover_and_the_others_still_have_theirs(self):
        rows = [{"id": "k:Trips/Empty", "kind": "keyword", "value": "Trips/Empty"},
                {"id": "k:Gone/Away", "kind": "keyword", "value": "Gone/Away"},
                {"id": "p:9", "kind": "person", "value": "People/Ivo Marchetti"},
                {"id": "m:1999-02", "kind": "month", "value": "1999-02"},
                self.folder("f:gone", os.path.join(self.vl.pictures, "Gone")),
                {"id": "k:Trips/Coast", "kind": "keyword", "value": "Trips/Coast"}]
        reply = self.ask("keywords", rows)
        self.assertEqual([None] * 5 + [self.dawn], [each["photo"] for each in reply["covers"]])
        self.assertEqual([None] * 5, [each["thumb"] for each in reply["covers"][:5]])

    def test_a_union_of_folders_is_covered_by_the_first_photo_of_them_all(self):
        union = {"id": "sel", "kind": "any_of",
                 "value": [{"kind": "folder", "value": self.lake}, {"kind": "folder", "value": self.coast}]}
        self.assertEqual({"sel": self.dawn}, self.covers("folders", [union]))
        undated = dict(union, value=[{"kind": "folder", "value": self.lake}, {"kind": "folder", "value": self.lake}])
        self.assertEqual({"sel": self.reeds}, self.covers("folders", [undated]), "one folder named twice is that folder")

    def test_a_row_named_twice_is_answered_once_in_its_first_place(self):
        reply = self.ask("folders", [self.folder("f:coast", self.coast), self.folder("f:lake", self.lake),
                                     self.folder("f:coast", self.coast)])
        self.assertEqual(["f:coast", "f:lake"], [each["id"] for each in reply["covers"]])

    def test_a_photo_added_afterwards_is_the_next_asks_cover_nothing_stored(self):
        row = [self.folder("f:coast", self.coast)]
        self.assertEqual({"f:coast": self.dawn}, self.covers("folders", row))
        earlier = self.vl.photo("Coast", "frost.jpg", "2023:12:24 08:00:00")
        self.assertEqual({"f:coast": earlier}, self.covers("folders", row))

    def test_each_library_answers_from_its_own_photos(self):
        other = ViewLibrary(self, "harbour", home=self.home)
        theirs = other.photo("Coast", "dawn.jpg", "2020:01:01 00:00:00", at=os.path.join(self.coast, "dawn.jpg"))
        row = [self.folder("f:coast", self.coast)]
        self.assertEqual({"f:coast": theirs}, self.covers("folders", row, library="harbour"))
        self.assertEqual({"f:coast": self.dawn}, self.covers("folders", row))

    def test_twenty_four_rows_are_answered_and_twenty_five_refused(self):
        rows = [self.folder("f:%d" % n, self.coast) for n in range(library_view.MAX_COVERS)]
        self.assertEqual(24, len(self.ask("folders", rows)["covers"]))
        said = self.refused(section="folders", rows=json.dumps(rows + [self.folder("f:more", self.lake)]))
        self.assertIn("24", said)

    def test_a_request_that_cannot_mean_anything_is_400_with_a_sentence(self):
        rows = json.dumps([self.folder("f:coast", self.coast)])
        self.refused(rows=rows)
        self.refused(section="albums", rows=rows)
        self.refused(section="folders")
        for text in ("", "not json", "{}", "[]", '"f:coast"', "[1]", '[{"kind": "folder"}]', '[{"id": "", "kind": "all"}]',
                     '[{"id": 7, "kind": "all"}]', '[{"id": "x", "kind": "album", "value": "a"}]',
                     '[{"id": "x", "kind": "month", "value": "June"}]', '[{"id": "x", "kind": "keyword"}]'):
            self.refused(section="folders", rows=text)


class TheCoversThePageReads(unittest.TestCase):
    """The covers of Browse rows as the route sends them (GET /api/library/covers), written for the phone's page tests
    (tests/frontend/harness.mjs COVERS_REPLIES, coveredAs): a row whose source holds a photo (`found`) and one whose holds
    none (`none`); the tests answer every cover from these two, never one spelled by hand."""

    def test_the_covers_reply_the_page_reads_is_written_for_its_test(self):
        # Two photos made as the indexer records a read of each, the second the found row's, so the fixture's id is not
        # the first a library gives; `Empty` holds none.
        app, home = web_client.app_for(self, "tagpup")
        vl = ViewLibrary(self, "library", home=home)
        vl.photo("Lake", "reeds.jpg")
        dawn = vl.photo("Coast", "dawn.jpg", "2024:01:05 07:00:00")
        rows = [BrowseRowCovers.folder("found", os.path.join(vl.pictures, "Coast")),
                BrowseRowCovers.folder("none", os.path.join(vl.pictures, "Empty"))]
        reply = app.test_client().get("/library/api/library/covers",
                                      query_string={"section": "folders", "rows": json.dumps(rows)})
        body = reply.get_json()
        self.assertEqual((200, [dawn, None]), (reply.status_code, [each["photo"] for each in body["covers"]]), body)
        text = json.dumps({"folders": {"status": reply.status_code, "reply": body}}, sort_keys=True, indent=1) + "\n"
        self.assertNotIn(json.dumps(home.root)[1:-1], text, "a path of the test's home left in")
        page_fixtures.hold_to(self, COVERS_REPLIES, text, "tests.test_library_covers", "the phone's Browse covers")


@unittest.skipUnless(os.name == "nt", "the spellings are Windows paths")
class ARootedLibrarysCovers(unittest.TestCase):
    """A library with roots: a row names its folder as the page has it, the place the owner knows; the service resolves it
    as it resolves a view's source (the ingress does not read `rows`), and a root this machine does not place is the roots'
    sentence, never a Browse of rows with no covers."""

    def setUp(self):
        self.home = own_home.for_test(self)
        self.side = rl.Side(self.home, "harbour", real=2, bulk=1, outside=2)
        self.assertIsNone(self.side.adopt().refused)
        self.library = self.side.library
        self.folder = os.path.join(self.side.pictures, rl.FOLDERS[0])

    def cover(self, folder):
        rows = [{"id": "f:one", "kind": "folder", "value": folder}]
        return library_view.covers(self.library, "folders", json.dumps(rows))["covers"][0]["photo"]

    def test_a_folder_row_in_the_previous_places_spelling_has_the_same_cover(self):
        first = self.cover(self.folder)
        self.assertEqual(library_view.ids(self.library, "folder", self.folder, False, cap=1)["ids"], [first])
        copy = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.side.pictures, copy, copy_function=shutil.copy2)
        config.set_location(rl.NAME, copy)
        self.assertEqual(first, self.cover(self.folder))

    def test_a_root_this_machine_does_not_place_is_the_roots_sentence(self):
        os.remove(config.machine_roots_path())
        with self.assertRaises(paths.RootsError) as caught:
            self.cover(self.folder)
        self.assertIn("machine_roots.json", str(caught.exception))

if __name__ == "__main__":
    unittest.main()
