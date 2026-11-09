"""Scenarios V1 and V7 under the Photos / Videos filter: the owner looks at a view's videos alone (or its photos alone),
selects all of them less a few, and edits in bulk.

Under `media=video` a view lists, counts and tallies its clips alone, and says how many photos the source holds of either kind
(`of`) beside its own total; a folder of photos alone is empty there, `of` its photos. Select all under the filter is the same
clips for the panel's tally and folders and for a bulk edit's resolve: never a photo the page did not show. Both -- no `media`,
or "" -- is the view as it was: no condition on the kind in any statement that reads its list. A `media` that names neither
kind is a sentence, never the whole library.

On the old code every test here fails: `media` was not read (the list held the photos, the reply had no `of`), and
source_of / ids took no `media`.

Photos are rows as the indexer records them (tests/view_library.py); fictional names only.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core.result import Refused  # noqa: E402
from tagpup.services import library_view, selection  # noqa: E402
from tagpup.store import library_view as store  # noqa: E402

WREN = "People/Wren Halloway"
JUNE = {"QuickTime:CreateDate": "2024:06:15 12:00:00"}


class AViewOfVideosAlone(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.vl.tree("Trips/Coast", WREN, face_root="People")
        photo = self.vl.photo
        self.dawn = photo("Coast", "dawn.jpg", "2024:06:14 10:00:00", tags=["Trips/Coast"])
        self.dusk = photo("Coast", "dusk.jpg", "2024:06:16 10:00:00", tags=[WREN])
        self.pier = photo("Lake", "pier.jpg")
        self.swim = photo("Coast", "Harbour Swim.mov", tags=["Trips/Coast", WREN], fields=JUNE)
        self.ferry = photo(os.path.join("Coast", "Day 2"), "Ferry.mp4", fields=JUNE)
        self.reel = photo("Reels", "Reel.mov", fields=JUNE)
        self.clips = [self.swim, self.ferry, self.reel]
        self.photos = [self.dawn, self.dusk, self.pier]
        self.coast = os.path.join(self.vl.pictures, "Coast")
        self.library = self.vl.library

    def ids(self, **query):
        reply = self.client.get("/library/api/library/ids", query_string=query)
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

    def tally(self, source, excluded=()):
        reply = self.client.post("/library/api/library/selection/tally",
                                 json={"selection": {"source": source, "excluded": list(excluded)}})
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

    def test_every_way_of_looking_lists_and_counts_its_clips_alone_with_the_whole_beside(self):
        member = lambda kind, value, recursive=False: {"kind": kind, "value": value, "recursive": recursive}  # noqa: E731
        union = '[{"kind": "folder", "value": %s, "recursive": true}, {"kind": "folder", "value": %s}]' % (
            json.dumps(self.coast), json.dumps(os.path.join(self.vl.pictures, "Reels")))
        sources = (
            (dict(kind="all"), set(self.clips), 6),
            (dict(kind="folder", folder=self.coast, recursive="1"), {self.swim, self.ferry}, 4),
            (dict(kind="folder", folder=self.coast), {self.swim}, 3),
            (dict(kind="keyword", value="Trips"), {self.swim}, 2),
            (dict(kind="person", value=WREN), {self.swim}, 2),
            (dict(kind="month", value="2024-06"), set(self.clips), 5),
            (dict(kind="any_of", value=union), set(self.clips), 5),
            (dict(kind="search", value='{"words": "swim"}'), {self.swim}, 1),
        )
        for query, clips, whole in sources:
            for order in ("taken", "name-desc", "caption"):
                with self.subTest(query=query, order=order):
                    found = self.ids(order=order, media="video", **query)
                    self.assertEqual(clips, set(found["ids"]), "the clips alone")
                    self.assertEqual((len(clips), sorted(clips), whole, "video", True),
                                     (found["total"], found["videos"], found["of"], found["media"], found["complete"]))
                    photos = self.ids(order=order, media="photo", **query)
                    self.assertEqual(([], whole - len(clips), whole), (photos["videos"], photos["total"], photos["of"]))
                    self.assertFalse(set(photos["ids"]) & set(self.clips), "and the photos alone")
                    both = self.ids(order=order, **query)
                    self.assertEqual((whole, whole, None), (both["total"], both["of"], both["media"]))
        # A union too long for an address is sent in a body, its filter with it.
        reply = self.client.post("/library/api/library/ids", json={
            "kind": "any_of", "media": "video", "value": [member("folder", self.coast, True), member("keyword", "Trips")]})
        self.assertEqual(({self.swim, self.ferry}, 4), (set(reply.get_json()["ids"]), reply.get_json()["of"]))
        # A cut list: the head of the clips, its count and its clips the whole source's.
        with mock.patch.object(library_view, "MAX_IDS", 1):
            cut = self.ids(kind="all", media="video")
        self.assertEqual(([self.clips[0]], 3, False, sorted(self.clips), 6),
                         (cut["ids"], cut["total"], cut["complete"], cut["videos"], cut["of"]))

    def test_a_folder_of_photos_alone_is_empty_under_videos_and_says_how_many_photos_it_holds(self):
        lake = os.path.join(self.vl.pictures, "Lake")
        for recursive in ("", "1"):
            with self.subTest(recursive=recursive):
                found = self.ids(kind="folder", folder=lake, recursive=recursive, media="video")
                self.assertEqual(([], 0, [], 1), (found["ids"], found["total"], found["videos"], found["of"]))
        nowhere = self.ids(kind="folder", folder=os.path.join(self.vl.pictures, "Nothing here"), media="video")
        self.assertEqual(([], 0, 0), (nowhere["ids"], nowhere["total"], nowhere["of"]))

    def test_select_all_under_videos_less_one_is_the_same_clips_for_the_tally_the_folders_and_a_bulk_edit(self):
        both = self.tally({"kind": "all"})
        self.assertEqual((6, [{"tag": "Trips/Coast", "count": 2}], 2), (both["total"], both["tags"], both["people"][0]["count"]))
        clips = self.tally({"kind": "all", "media": "video"})
        self.assertEqual((3, [{"tag": "Trips/Coast", "count": 1}], 1, 3),
                         (clips["total"], clips["tags"], clips["people"][0]["count"], clips["folders"]["count"]))
        less = self.tally({"kind": "all", "media": "video"}, excluded=[self.ferry, self.dawn])
        self.assertEqual((2, 2), (less["total"], less["folders"]["count"]), "the excluded clip out; a photo excluded is no matter")
        named = {"source": {"kind": "all", "media": "video"}, "excluded": [self.ferry]}
        resolved = selection.resolve(self.library, selection.read(self.library, named))
        listed = [each for each in self.ids(kind="all", media="video")["ids"] if each != self.ferry]
        self.assertEqual((listed, 3, 1), (resolved.ids, resolved.requested, resolved.excluded),
                         "a bulk edit writes the clips the panel counted, in the view's order, and no photo")
        person = selection.read(self.library, {"source": {"kind": "person", "value": WREN, "media": "photo"}})
        self.assertEqual([self.dusk], selection.resolve(self.library, person).ids)

    def test_both_reads_the_list_as_before_with_no_condition_on_the_kind(self):
        sources = (store.Source(store.ALL), store.Source(store.FOLDER, self.coast, True), store.Source(store.KEYWORD, "Trips"),
                   store.Source(store.MONTH, "2024-06"))
        for source in sources:
            for order in (store.TAKEN, store.NAME, store.CAPTION):
                with self.subTest(kind=source.kind, order=order):
                    conn = library_view.opened(self.library)
                    try:
                        statements, _elapsed = store.id_plans(conn, source._replace(only=None), 1000, order)
                        named, _elapsed = store.id_plans(conn, library_view.source_of(
                            self.library, source.kind, source.value, source.recursive, ""), 1000, order)
                    finally:
                        conn.close()
                    # The photos' statements: the first read of a folder also reads the roots, which the connection keeps.
                    statements = [sql for sql, _plan in statements if "FROM roots" not in sql]
                    self.assertEqual([], [sql for sql in statements if "kind" in sql])
                    self.assertEqual(statements, [sql for sql, _plan in named if "FROM roots" not in sql], "'' is both")
        self.assertEqual(self.ids(kind="all"), self.ids(kind="all", media=""))

    def test_a_media_that_names_neither_kind_is_a_sentence(self):
        for media in ("videos", "Video", "clip", "both"):
            with self.subTest(media=media):
                reply = self.client.get("/library/api/library/ids", query_string={"kind": "all", "media": media})
                self.assertEqual(400, reply.status_code)
                self.assertIn("media must be one of photo, video", reply.get_json()["error"])
                reply = self.client.post("/library/api/library/selection/tally",
                                         json={"selection": {"source": {"kind": "all", "media": media}}})
                self.assertEqual(400, reply.status_code)
        for media in (5, ["video"], True):
            with self.subTest(media=media), self.assertRaises(Refused):
                library_view.source_of(self.library, "all", media=media)


if __name__ == "__main__":
    unittest.main()
