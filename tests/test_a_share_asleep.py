"""A share that does not answer is not "missing" (F7; findings #566, #568, #570, #721).

Windows reports a sleeping server, a share name that is not there and an unplugged drive letter with the same "not found"
it uses for a deleted file, at once; a share that merely hangs holds the thread that asks. A photo called missing is left
out of a bulk write and made read-only in the page, a folder view that waits on the share holds a request's thread, so the
owner's NAS asleep must be told as "away" (nothing is called missing, nothing is removed) in every place that looks:
the folder view (membership), a page of cards, a bulk write. Every look goes through shares.bounded. Stand-ins for the
share only, the library is the one the indexer makes; fictional names.

Lifted: test_unreachable_is_not_missing (#566; its gzip q=0 test went to test_gzip_json) and test_membership_is_bounded
(#568 #570 #721). Mutation proof: shares.bounded calling `call()` through with no deadline fails
ASleepingShare (both tests); each other lifted test fails on the break its docstring names.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import share_waits  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.files import recycle_bin, shares  # noqa: E402
from tagpup.services import library_view, tagging  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402


class ASleepingShare(unittest.TestCase):
    """Everything under the library's Pictures is a share whose every look hangs (until the test is done)."""

    def setUp(self):
        self.vl = ViewLibrary(self)
        self.folder = os.path.join(self.vl.pictures, "Coast")
        self.ids = [self.vl.photo("Coast", "p%d.jpg" % n, taken="2024:06:01 10:00:00") for n in range(3)]
        self.photo_paths = [self.vl.path_of(photo_id) for photo_id in self.ids]
        asleep, under = threading.Event(), os.path.normcase(self.vl.pictures)
        self.addCleanup(asleep.set)
        shares.forget()
        self.addCleanup(shares.forget)

        def hang(real, when_asleep):
            def look(path, *args, **kwargs):
                if os.path.normcase(str(path)).startswith(under):
                    asleep.wait(1.5)
                    return when_asleep()
                return real(path, *args, **kwargs)
            return look

        def not_found():
            raise FileNotFoundError(2, "not found")
        for patcher in (mock.patch.object(os, "stat", hang(os.stat, not_found)),
                        mock.patch.object(os.path, "isdir", hang(os.path.isdir, lambda: False)),
                        mock.patch.object(shares, "on_a_network_drive", lambda path: True),
                        mock.patch.object(shares, "SHARE_WAIT", 0.05),
                        mock.patch.object(library_actions, "MEMBERSHIP_SHARE_WAIT", 0.05)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_folder_view_says_the_share_is_away_and_the_next_look_waits_for_nothing(self):
        found = library_actions.membership_checked(self.vl.library, self.folder)
        self.assertEqual((True, "the network share is away"), (found["could_not_check"], found["why"]))
        waits, counting = share_waits.counted()
        with counting:
            self.assertEqual(found, library_actions.membership_checked(self.vl.library, self.folder))
        self.assertEqual([], waits, "a share taken as away is answered at once")
        self.assertEqual(3, self.vl.rows("SELECT COUNT(*) FROM photos")[0][0], "nothing was removed")

    def test_a_page_of_cards_and_a_bulk_write_do_not_call_its_photos_missing(self):
        cards = library_view.cards(self.vl.library, self.ids, check_disk=True)
        self.assertEqual([None] * 3, [card.get("stale") for card in cards])
        self.assertEqual((self.photo_paths, []), tagging.leave_out_missing(self.photo_paths))
        self.assertEqual(3, self.vl.rows("SELECT COUNT(*) FROM photos")[0][0])


class ADriveThatSaysNotFound(unittest.TestCase):
    def test_a_file_is_gone_only_when_the_place_it_was_on_answers(self):
        """Lifted from test_unreachable_is_not_missing (#566); break: _stat_state not asking _root_answers."""
        for path, drive_answers, gone in (("Q:\\Pictures\\a.jpg", False, False), ("Q:\\Pictures\\a.jpg", True, True),
                                          ("pictures/a.jpg", False, True)):
            with mock.patch("tagpup.files.shares.os.stat", side_effect=FileNotFoundError(2, "not found")), \
                    mock.patch("tagpup.files.shares.os.path.exists", return_value=drive_answers):
                present, left_out = tagging.leave_out_missing([path])
            self.assertEqual((not gone, gone), (present == [path], left_out != []), (path, drive_answers))


class OneWalkAtATime(unittest.TestCase):
    """The membership walk of a folder (#568): never twice at once, "could not check" when it takes too long."""

    def setUp(self):
        self.vl = ViewLibrary(self)
        self.folder = os.path.join(self.vl.pictures, "Coast")
        os.makedirs(self.folder, exist_ok=True)
        self.release, self.started, self.walks = threading.Event(), threading.Event(), []

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

    def test_a_second_request_is_answered_by_the_walk_under_way_and_one_after_it_walks_again(self):
        """Break: no `_walks` entry (two walks), or the entry never removed (no third walk)."""
        answers = []
        first, second = (threading.Thread(target=self.ask, args=(answers,)) for _ in range(2))
        first.start()
        self.assertTrue(self.started.wait(5))
        second.start()
        time.sleep(0.2)
        self.release.set()
        first.join(5)
        second.join(5)
        self.assertEqual(1, len(self.walks))
        self.assertEqual(answers[0], answers[1])
        self.ask([])
        self.assertEqual(2, len(self.walks))

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
        self.assertEqual(1, len(self.walks), "the walk still under way answered it")
        self.assertEqual(3, again[0]["photos_not_held"])

    def test_a_walk_that_raised_raises_for_the_asker_and_leaves_nothing_behind(self):
        with mock.patch.object(library_actions, "membership", side_effect=RuntimeError("the library is busy")):
            with self.assertRaises(RuntimeError):
                library_actions.membership_checked(self.vl.library, self.folder)
        self.release.set()
        self.assertEqual(3, library_actions.membership_checked(self.vl.library, self.folder)["photos"])

    def test_a_share_that_answers_the_folder_is_not_there_is_could_not_check_and_no_walk_is_started(self):
        with mock.patch.object(shares, "on_a_network_drive", return_value=True), \
                mock.patch.object(shares, "bounded", return_value=("ok", False)):
            found = library_actions.membership_checked(self.vl.library, self.folder)
        self.assertTrue(found["could_not_check"])
        self.assertEqual([], self.walks)


class TheFolderViewsRoute(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()

    def test_a_folder_not_there_is_a_400_one_that_cannot_be_checked_a_200_and_a_share_is_not_looked_at_by_the_route(self):
        url = "/library/api/folder/membership"
        self.assertEqual(400, self.client.get(url, query_string={"path": os.path.join(self.home.root, "nowhere")}).status_code)
        there = os.path.join(self.home.root, "there")
        os.makedirs(there)
        gone = {"folder": there, "could_not_check": True, "why": "the network share is away"}
        with mock.patch.object(library_actions, "membership_checked", return_value=gone):
            reply = self.client.get(url, query_string={"path": there})
        self.assertEqual((200, gone), (reply.status_code, reply.get_json()))
        # isdir of a share that has stopped answering would hold the request's thread: the bounded walk looks, not the route.
        with mock.patch.object(library_actions, "on_a_network_drive", return_value=True), \
                mock.patch("tagpup.web.tagpup_routes.os.path.isdir", side_effect=AssertionError("isdir on a share")), \
                mock.patch.object(library_actions, "membership_checked", return_value=gone):
            self.assertEqual(200, self.client.get(url, query_string={"path": "//server/share/photos"}).status_code)


class TheCardStatsHaveABudget(unittest.TestCase):
    def setUp(self):
        recycle_bin._drive_kinds.clear()
        self.addCleanup(recycle_bin._drive_kinds.clear)

    def test_a_drives_type_is_asked_of_windows_once_for_a_batch_and_a_mapped_drive_is_a_share(self):
        """Break: _drive_kinds not kept (200 asks, 130 ms for 200 cards), or a remote drive type not taken as a share."""
        with mock.patch("tagpup.files.recycle_bin._drive_type", return_value=3) as kind, \
                mock.patch("tagpup.files.recycle_bin._mount_point", return_value="C:\\"):
            for n in range(200):
                self.assertFalse(shares.on_a_network_drive("C:\\Photos\\p%d.jpg" % n))
        self.assertEqual(1, kind.call_count)
        with mock.patch("tagpup.files.recycle_bin._drive_type", return_value=4), \
                mock.patch("tagpup.files.recycle_bin._mount_point", return_value="Z:\\"):
            self.assertTrue(shares.on_a_network_drive("Z:\\Photos\\a.jpg"))
        self.assertTrue(shares.on_a_network_drive("\\\\server\\share\\a.jpg"))

    def test_a_share_that_answers_every_stat_slowly_ends_the_batch_within_the_budget_and_leaves_the_rest_unmarked(self):
        vl = ViewLibrary(self)
        ids = [vl.photo("Coast", "p%d.jpg" % n, taken="2024:06:01 10:00:00") for n in range(6)]
        asked, clock = [], [1000.0]
        # The budget's clock is the test's: each stat takes 0.6 s of it and nothing else does (#721).

        def slow(path):
            asked.append(path)
            clock[0] += 0.6
            return None   # gone
        with mock.patch.object(shares, "stamp_of", slow), \
                mock.patch.object(library_view, "time", mock.Mock(monotonic=lambda: clock[0])):
            cards = library_view.cards(vl.library, ids, check_disk=True)
        self.assertEqual(3, len(asked), "stats at 0, 0.6 and 1.2 s; the one due at 1.8 s is past the budget")
        self.assertEqual(["missing"] * 3, [card.get("stale") for card in cards[:3]])
        self.assertTrue(all("stale" not in card for card in cards[3:]), "the rest unmarked, quietly")


if __name__ == "__main__":
    unittest.main()
