"""A card says when its file is not as the library's row describes it (phase 9c; tagpup.services.library_view.cards with
`check_disk`, the page's /api/library/cards): `stale` is "changed" when the file's size and time no longer describe
the row, "missing" when the file is gone, and absent otherwise -- for a share that did not answer, a file that cannot
be read, and a row nobody stamped. One stat for each card, at most 200 for a request, and none at all for `view`.

Photos are rows as the indexer records them, over real JPEGs where the disk matters (tests/view_library.py); a test home
of its own; Flask's test client for the route. Fictional names only.
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Refused  # noqa: E402
from tagpup.services import damaged_photos, library_view  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.web import app as web  # noqa: E402
from tagpup.web.lifecycle import Lifecycle  # noqa: E402


class Disk(unittest.TestCase):
    def setUp(self):
        self.vl = ViewLibrary(self)
        self.same = self.vl.photo("Coast", "same.jpg", taken="2024:06:01 10:00:00", real=True)
        self.edited = self.vl.photo("Coast", "edited.jpg", taken="2024:06:02 10:00:00", real=True)
        self.gone = self.vl.photo("Coast", "gone.jpg", taken="2024:06:03 10:00:00", real=True)

    def marks(self, ids=None):
        found = library_view.cards(self.vl.library, ids or [self.same, self.edited, self.gone], check_disk=True)
        return {card["id"]: card.get("stale") for card in found}

    def test_a_file_as_the_row_says_has_no_mark_one_edited_is_changed_and_one_deleted_is_missing(self):
        make_jpeg(self.vl.path_of(self.edited), shade=(1, 2, 3), size=(1000, 700))   # another size, written now
        os.remove(self.vl.path_of(self.gone))
        self.assertEqual({self.same: None, self.edited: "changed", self.gone: "missing"}, self.marks())

    def test_a_file_written_again_with_the_same_size_but_another_time_is_changed(self):
        path = self.vl.path_of(self.same)
        later = time.time() + 3600
        os.utime(path, (later, later))
        self.assertEqual("changed", self.marks()[self.same])

    def test_a_card_without_the_disk_look_has_no_stale_key_at_all(self):
        os.remove(self.vl.path_of(self.gone))
        for card in library_view.cards(self.vl.library, [self.same, self.gone]):
            self.assertNotIn("stale", card)
        for card in library_view.view(self.vl.library, "all")["cards"]:
            self.assertNotIn("stale", card)

    def test_view_looks_at_no_file(self):
        with mock.patch.object(damaged_photos, "stamp_of", side_effect=AssertionError("a stat")):
            self.assertEqual(3, len(library_view.view(self.vl.library, "all")["cards"]))

    def test_a_row_nobody_stamped_is_not_called_changed_but_is_called_missing_when_its_file_is_gone(self):
        read = self.vl.unread("Coast", "never read.jpg")
        make_jpeg(self.vl.path_of(read))
        self.assertIsNone(self.marks([read])[read])
        os.remove(self.vl.path_of(read))
        self.assertEqual("missing", self.marks([read])[read])

    def test_a_share_that_did_not_answer_gives_no_mark_and_a_file_that_cannot_be_read_gives_none(self):
        os.remove(self.vl.path_of(self.gone))
        for answer in (damaged_photos.UNANSWERED, damaged_photos.CANNOT_READ):
            with mock.patch.object(damaged_photos, "stamp_of", return_value=answer):
                self.assertEqual({self.same: None, self.edited: None, self.gone: None}, self.marks())

    def test_a_share_found_away_costs_one_wait_for_the_whole_batch(self):
        # The cards' paths on a share, the share not answering: the first look times out (shares.bounded), the
        # others are answered at once as away -- no mark, and no second thread.
        calls = []

        def bounded(location, call, seconds, away_seconds=None):
            calls.append(location)
            return "away", None
        with mock.patch.object(damaged_photos, "_on_a_share", return_value=True), \
                mock.patch.object(damaged_photos.shares, "bounded", bounded):
            self.assertEqual({self.same: None, self.edited: None, self.gone: None}, self.marks())
        self.assertEqual(3, len(calls), "one bounded look for each card, each answered at once")

    def test_each_card_costs_one_stat_and_a_request_is_at_most_two_hundred(self):
        seen = []
        real = damaged_photos._stat_state
        with mock.patch.object(damaged_photos, "_stat_state", lambda path: (seen.append(path), real(path))[1]):
            self.marks()
        self.assertEqual(3, len(seen))
        self.assertEqual(200, library_view.MAX_CARDS)
        with self.assertRaises(Refused):
            library_view.read_ids(",".join(str(n) for n in range(1, 202)))


class TheRoute(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.a = self.vl.photo("Coast", "a.jpg", taken="2024:06:01 10:00:00", real=True)
        self.b = self.vl.photo("Coast", "b.jpg", taken="2024:06:02 10:00:00", real=True)

    def cards(self, *ids):
        reply = self.client.get("/library/api/library/cards", query_string={"ids": ",".join(str(i) for i in ids)})
        self.assertEqual(200, reply.status_code, reply.data)
        return {card["id"]: card for card in reply.get_json()["cards"]}

    def test_the_page_route_marks_a_changed_and_a_missing_file(self):
        make_jpeg(self.vl.path_of(self.a), shade=(9, 9, 9), size=(900, 900))
        os.remove(self.vl.path_of(self.b))
        found = self.cards(self.a, self.b)
        self.assertEqual("changed", found[self.a]["stale"])
        self.assertEqual("missing", found[self.b]["stale"])

    def test_a_good_file_has_no_stale_key_so_the_card_is_as_it_was(self):
        found = self.cards(self.a)
        self.assertNotIn("stale", found[self.a])
        self.assertEqual({"id", "name", "path", "taken", "damaged", "damage", "thumb"}, set(found[self.a]))

    def test_two_hundred_cards_are_marked_in_one_request(self):
        ids = [self.vl.photo("Many", "p%d.jpg" % n, taken="2024:07:01 10:00:00") for n in range(200)]   # rows, no files
        reply = self.client.get("/library/api/library/cards", query_string={"ids": ",".join(str(i) for i in ids)})
        self.assertEqual(200, reply.status_code)
        found = reply.get_json()["cards"]
        self.assertEqual(200, len(found))
        self.assertTrue(all(card["stale"] == "missing" for card in found))


class FakeWatcher:
    def __init__(self, found):
        self.found = found

    def start(self):
        pass

    def stop(self, timeout=30):
        return True

    def busy(self):
        return False

    def status(self):
        return self.found


class TheSyncRouteSaysWhetherItIsSyncing(unittest.TestCase):
    def test_syncing_is_false_with_no_watcher_and_true_only_for_the_library_being_synced(self):
        home = own_home.for_test(self)
        harbour = home.library("harbour.db")
        library_actions.create(harbour)
        other = home.library("regatta.db")
        library_actions.create(other)
        for tasks, harbour_syncs, regatta_syncs in (
                ([], False, False),
                ([("folder watcher", FakeWatcher({"running": True, "roots": [], "libraries": {}, "syncing": None}))], False, False),
                ([("folder watcher", FakeWatcher({"running": True, "roots": [], "libraries": {},
                                                  "syncing": {"library": "harbour", "folder": "x", "started": "y"}}))], True, False)):
            app = web.create_app("tagpup", startup=Library(harbour), lifecycle=Lifecycle(background=runtimes.Background(list(tasks))))
            app.testing = True
            client = app.test_client()
            self.assertEqual(harbour_syncs, client.get("/harbour/api/sync").get_json()["syncing"], tasks)
            self.assertEqual(regatta_syncs, client.get("/regatta/api/sync").get_json()["syncing"], tasks)


if __name__ == "__main__":
    unittest.main()
