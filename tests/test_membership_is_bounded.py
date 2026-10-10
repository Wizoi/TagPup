"""The membership walk and the card stats are bounded (findings #568, #570; phase 9c).

A library view of a folder asks what the disk holds of it (GET /api/folder/membership), which walks the folder: it must not
hold a thread on a share that has gone away, must not walk one folder twice at once, and answers "could not check" when it
cannot. A batch of cards stats each file and stops when its time is spent. Stand-ins only; no share is touched. Fictional names.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.services import damaged_photos, library_view  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402


class OneWalkAtATime(unittest.TestCase):
    def setUp(self):
        self.vl = ViewLibrary(self)
        self.folder = os.path.join(self.vl.pictures, "Coast")
        os.makedirs(self.folder, exist_ok=True)
        self.release = threading.Event()
        self.started = threading.Event()
        self.walks = []

        def walk(library, folder, roots=(), ignored=()):
            self.walks.append(folder)
            self.started.set()
            self.release.wait(5)
            return {"folder": folder, "photos": 3, "photos_not_held": 3}
        patcher = mock.patch.object(library_actions, "membership", walk)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.release.set)

    def ask(self, answers, deadline=5):
        answers.append(library_actions.membership_checked(self.vl.library, self.folder, deadline=deadline))

    def test_a_second_request_for_a_folder_under_walk_is_answered_by_the_first(self):
        answers = []
        first = threading.Thread(target=self.ask, args=(answers,))
        second = threading.Thread(target=self.ask, args=(answers,))
        first.start()
        self.assertTrue(self.started.wait(5))
        second.start()
        time.sleep(0.2)
        self.release.set()
        first.join(5)
        second.join(5)
        self.assertEqual(1, len(self.walks), "one walk of one folder")
        self.assertEqual(2, len(answers))
        self.assertEqual(answers[0], answers[1])

    def test_a_walk_that_outlives_the_deadline_is_could_not_check_and_the_next_asker_is_answered_by_it(self):
        slow = []
        self.ask(slow, deadline=0.1)
        self.assertEqual((True, "it took too long"), (slow[0]["could_not_check"], slow[0]["why"]))
        self.assertNotIn("photos_not_held", slow[0])
        again = []
        waiting = threading.Thread(target=self.ask, args=(again,))
        waiting.start()
        time.sleep(0.1)
        self.release.set()
        waiting.join(5)
        self.assertEqual(1, len(self.walks), "the walk still under way answered it; no second walk was started")
        self.assertEqual(3, again[0]["photos_not_held"])

    def test_once_a_walk_is_done_the_next_request_walks_again(self):
        self.release.set()
        library_actions.membership_checked(self.vl.library, self.folder)
        library_actions.membership_checked(self.vl.library, self.folder)
        self.assertEqual(2, len(self.walks))

    def test_a_walk_that_raised_raises_for_the_asker_and_leaves_nothing_behind(self):
        with mock.patch.object(library_actions, "membership", side_effect=RuntimeError("the library is busy")):
            with self.assertRaises(RuntimeError):
                library_actions.membership_checked(self.vl.library, self.folder)
        self.release.set()
        self.assertEqual({"photos": 3, "photos_not_held": 3, "folder": self.folder},
                         library_actions.membership_checked(self.vl.library, self.folder))

    def test_a_share_that_is_away_is_could_not_check_and_no_walk_is_started(self):
        with mock.patch.object(library_actions.shares, "on_a_network_drive", return_value=True), \
                mock.patch.object(library_actions.shares, "bounded", return_value=("away", None)):
            found = library_actions.membership_checked(self.vl.library, self.folder)
        self.assertEqual((True, "the network share is away"), (found["could_not_check"], found["why"]))
        self.assertEqual([], self.walks)

    def test_a_share_that_says_the_folder_is_not_there_is_could_not_check_too(self):
        with mock.patch.object(library_actions.shares, "on_a_network_drive", return_value=True), \
                mock.patch.object(library_actions.shares, "bounded", return_value=("ok", False)):
            found = library_actions.membership_checked(self.vl.library, self.folder)
        self.assertTrue(found["could_not_check"])
        self.assertEqual([], self.walks)


class TheRoute(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()

    def test_a_folder_that_is_not_there_is_still_a_400_and_one_that_cannot_be_checked_is_a_200_with_why(self):
        missing = os.path.join(self.home.root, "nowhere")
        self.assertEqual(400, self.client.get("/library/api/folder/membership", query_string={"path": missing}).status_code)
        there = os.path.join(self.home.root, "there")
        os.makedirs(there)
        gone = {"folder": there, "could_not_check": True, "why": "the network share is away"}
        with mock.patch.object(library_actions, "membership_checked", return_value=gone):
            reply = self.client.get("/library/api/folder/membership", query_string={"path": there})
        self.assertEqual((200, gone), (reply.status_code, reply.get_json()))

    def test_a_network_folder_is_not_looked_at_by_the_route_itself(self):
        # isdir of a share that has stopped answering would hold the request's thread: the route leaves it to the bounded walk.
        with mock.patch.object(library_actions, "on_a_network_drive", return_value=True), \
                mock.patch("tagpup.web.tagpup_routes.os.path.isdir", side_effect=AssertionError("isdir on a share")), \
                mock.patch.object(library_actions, "membership_checked",
                                  return_value={"folder": "x", "could_not_check": True, "why": "the network share is away"}):
            reply = self.client.get("/library/api/folder/membership", query_string={"path": "//server/share/photos"})
        self.assertEqual(200, reply.status_code, reply.data)


class TheCardStatsHaveABudget(unittest.TestCase):
    def setUp(self):
        from tagpup.files import recycle_bin
        recycle_bin._drive_kinds.clear()
        self.addCleanup(recycle_bin._drive_kinds.clear)

    def test_a_drives_type_is_asked_of_windows_once_for_a_batch_not_once_for_each_card(self):
        # Asked 200 times it cost 0.6 ms each: 130 ms for a batch of 200 cards, seven times the stats themselves.
        from tagpup.files import shares
        with mock.patch("tagpup.files.recycle_bin._drive_type", return_value=3) as kind, \
                mock.patch("tagpup.files.recycle_bin._mount_point", return_value="C:\\"):
            for n in range(200):
                shares.on_a_network_drive("C:\\Photos\\p%d.jpg" % n)
        self.assertEqual(1, kind.call_count)

    def test_a_share_that_answers_every_stat_slowly_ends_the_batch_within_the_budget_and_leaves_the_rest_unmarked(self):
        vl = ViewLibrary(self)
        ids = [vl.photo("Coast", "p%d.jpg" % n, taken="2024:06:01 10:00:00") for n in range(6)]
        asked = []
        # The budget's clock is the test's: each stat takes 0.6 s of it and nothing else does. Slept for real, the work
        # around the stats on a busy machine pushed the third past the budget (#721).
        clock = [1000.0]

        def slow(path):
            asked.append(path)
            clock[0] += 0.6
            return None   # gone
        with mock.patch.object(damaged_photos, "stamp_of", slow), \
                mock.patch.object(library_view, "time", mock.Mock(monotonic=lambda: clock[0])):
            cards = library_view.cards(vl.library, ids, check_disk=True)
        self.assertEqual(3, len(asked), "stats at 0, 0.6 and 1.2 s; the one due at 1.8 s is past the budget")
        self.assertEqual(6, len(cards), "every card is answered")
        self.assertEqual(["missing"] * 3, [card.get("stale") for card in cards[:3]])
        self.assertTrue(all("stale" not in card for card in cards[3:]), "the rest unmarked, quietly")

    def test_a_mapped_network_drive_is_a_share_for_the_bounded_stat(self):
        from tagpup.files import shares
        with mock.patch("tagpup.files.recycle_bin._drive_type", return_value=4), \
                mock.patch("tagpup.files.recycle_bin._mount_point", return_value="Z:\\"):
            self.assertTrue(shares.on_a_network_drive("Z:\\Photos\\a.jpg"))
        with mock.patch("tagpup.files.recycle_bin._drive_type", return_value=3), \
                mock.patch("tagpup.files.recycle_bin._mount_point", return_value="C:\\"):
            self.assertFalse(shares.on_a_network_drive("C:\\Photos\\a.jpg"))
        self.assertTrue(shares.on_a_network_drive("\\\\server\\share\\a.jpg"))
        self.assertTrue(damaged_photos._on_a_share("\\\\server\\share\\a.jpg"))

    def test_a_stat_of_a_mapped_drive_goes_through_the_bounded_look(self):
        calls = []

        def bounded(location, call, seconds, away_seconds=None):
            calls.append(location)
            return "away", None
        with mock.patch("tagpup.files.recycle_bin._drive_type", return_value=4), \
                mock.patch("tagpup.files.recycle_bin._mount_point", return_value="Z:\\"), \
                mock.patch.object(damaged_photos.shares, "bounded", bounded):
            answer = damaged_photos.stamp_of("Z:\\Photos\\a.jpg")
        self.assertIs(damaged_photos.UNANSWERED, answer)
        self.assertEqual(["Z:\\Photos\\a.jpg"], calls)


if __name__ == "__main__":
    unittest.main()
