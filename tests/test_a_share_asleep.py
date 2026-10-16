"""A share that does not answer is not "missing" (F7; findings #566, #568, #570, #721).

Windows reports a sleeping server, a share name that is not there and an unplugged drive letter with the same "not found"
it uses for a deleted file, at once; a share that merely hangs holds the thread that asks. A photo called missing is left
out of a bulk write and made read-only in the page, a folder view that waits on the share holds a request's thread, so the
owner's NAS asleep must be told as "away" (nothing is called missing, nothing is removed) in every place that looks:
the folder view (membership), a page of cards, a bulk write, a sync. Every look goes through shares.bounded. Stand-ins for
the share only, the library is the one the indexer makes; fictional names.

A sync that cannot look at a folder -- a root on a share asleep, a folder whose listing fails or drops part-way -- counts
the rows under it as "not looked at", neither missing nor paired with a copy elsewhere, and leaves the library not in step
(owner, 2026-10-15; #1356). A sync stopped part-way writes nothing: its look is a dry read and its rows one change after.

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
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import share_waits  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.files import recycle_bin, shares  # noqa: E402
from tagpup.services import inspect, library_view, settings, sync, tagging  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402

#: When every photo of a sync's library was last written.
THEN = 1_700_000_000


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

    def test_the_rows_whose_file_is_gone_leave_out_the_photos_on_it_and_name_no_folder_gone(self):
        """The MCP tool's answer (inspect.missing_files): a photo on the share asleep is not looked at, not gone."""
        found = inspect.missing_files(self.vl.library)
        self.assertEqual((0, 0, 0, 3), (found["rows"], found["folders"], found["folders_gone"], found["not_looked_at"]))


class ASyncThatCannotLookAtAFolder(unittest.TestCase):
    """Sync on a share asleep, or a folder that will not list: nothing is called missing, nothing follows a copy, and the
    library is not in step (#1356). Two libraries holding the folder on the share: the second is answered at once."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="share_sync_")
        shares.forget()
        self.addCleanup(shares.forget)
        patcher = mock.patch.object(sync, "FOLDER_WAIT", 0.05, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def photo(self, folder, name):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"jpeg " + name.encode())
        os.utime(path, (THEN, THEN))
        return path

    def library(self, name, photo_paths):
        """A library holding `photo_paths`, each a row as the indexer records a read of it."""
        db_path = self.home.library(name)
        library_actions.create(db_path)
        conn = db.connect(db_path)
        try:
            for path in photo_paths:
                photo_rows.add_read(conn, path, {})
            conn.commit()
        finally:
            conn.close()
        return Library(db_path)

    def paths_of(self, library):
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            return sorted(path for (path,) in conn.execute("SELECT path FROM photos"))
        finally:
            conn.close()

    def assert_not_looked_at(self, result, rows, folders):
        self.assertTrue(result.ok, result.message())
        counts = result.details["counts"]
        self.assertEqual({"missing": 0, "moved": 0, "roots_gone": 0, "folders_gone": 0, "not_looked_at": rows,
                          "folders_not_looked_at": folders},
                         {what: counts.get(what) for what in ("missing", "moved", "roots_gone", "folders_gone",
                                                              "not_looked_at", "folders_not_looked_at")})
        self.assertFalse(result.details["in_step"])

    def asleep(self, share):
        """From now on every look at `share` hangs, and it is a network share."""
        asleep, under = threading.Event(), os.path.normcase(share)
        self.addCleanup(asleep.set)

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
                        mock.patch.object(shares, "on_a_network_drive",
                                          lambda path: os.path.normcase(str(path)).startswith(under))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_root_on_a_share_asleep_is_not_looked_at_and_a_second_library_on_it_is_answered_at_once(self):
        share = os.path.join(self.home.root, "NAS", "Pictures")
        taken = [self.photo(os.path.join(share, "2019 Regatta"), "IMG_%04d.jpg" % n) for n in range(3)]
        regatta, crew = self.library("regatta.db", taken), self.library("crew.db", taken)
        self.asleep(share)
        first = sync.sync(regatta, apply=True, roots=[share])
        self.assert_not_looked_at(first, 3, 1)
        self.assertEqual(0, first.details["counts"]["folders_walked"])
        self.assertIsNone(sync.last(regatta)["last_in_step"], "a sync that could not look recorded the library in step")
        waits, counting = share_waits.counted()
        with counting:
            second = sync.sync(crew, apply=True, roots=[share])
        self.assert_not_looked_at(second, 3, 1)
        self.assertEqual([], waits, "the share taken as away is answered at once for the second library")
        self.assertEqual((sorted(taken), sorted(taken)), (self.paths_of(regatta), self.paths_of(crew)))

    def test_a_folder_synced_alone_takes_no_row_on_a_share_asleep_for_a_file_moved_in(self):
        share = os.path.join(self.home.root, "NAS", "Pictures")
        taken = [self.photo(os.path.join(share, "2019 Regatta"), "IMG_0001.jpg")]
        library = self.library("regatta.db", taken)
        pictures = os.path.join(self.home.root, "Pictures")
        desk = os.path.join(pictures, "Desk")
        self.photo(desk, "IMG_0001.jpg")
        self.asleep(share)
        result = sync.sync(library, folder=desk, apply=True, roots=[share, pictures])
        self.assertTrue(result.ok, result.message())
        self.assertEqual(0, result.details["counts"]["moved"], "a row on the share asleep followed a copy")
        self.assertEqual(taken, self.paths_of(library))

    def test_a_folder_that_will_not_list_keeps_its_rows_where_they_are_though_a_copy_of_one_is_elsewhere(self):
        pictures = os.path.join(self.home.root, "Pictures")
        harbour = os.path.join(pictures, "2020 Harbour")
        taken = [self.photo(harbour, "IMG_0001.jpg"), self.photo(harbour, "IMG_0002.jpg")]
        # An unindexed copy, the same name, size and time: the stamp pairing would take the row's file to have moved.
        self.photo(os.path.join(pictures, "Copies"), "IMG_0001.jpg")
        library = self.library("harbour.db", taken)
        real = os.scandir

        def scandir(path="."):
            if os.path.normcase(str(path)) == os.path.normcase(harbour):
                raise OSError(64, "The specified network name is no longer available")
            return real(path)
        with mock.patch.object(os, "scandir", scandir):
            result = sync.sync(library, apply=True, roots=[pictures])
        self.assert_not_looked_at(result, 2, 1)
        self.assertEqual([harbour], result.details["reveal"]["not_looked_at"])
        self.assertEqual(sorted(taken), self.paths_of(library), "a row followed a copy because its folder did not list")

    def test_a_listing_that_drops_part_way_leaves_the_photos_it_did_not_reach_not_looked_at(self):
        pictures = os.path.join(self.home.root, "Pictures")
        harbour = os.path.join(pictures, "2020 Harbour")
        taken = [self.photo(harbour, "IMG_%04d.jpg" % n) for n in range(1, 4)]
        library = self.library("harbour.db", taken)
        real = os.scandir

        class Dropping:
            def __init__(self, listing):
                self.listing = listing

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.listing.close()
                return False

            def __iter__(self):
                yield next(iter(self.listing))
                raise OSError(64, "The specified network name is no longer available")

        def scandir(path="."):
            listing = real(path)
            return Dropping(listing) if os.path.normcase(str(path)) == os.path.normcase(harbour) else listing
        with mock.patch.object(os, "scandir", scandir):
            result = sync.sync(library, apply=True, roots=[pictures])
        self.assert_not_looked_at(result, 2, 1)
        self.assertEqual(sorted(taken), self.paths_of(library))


class TheSyncCardOfARootOnAShareAsleep(unittest.TestCase):
    def test_the_root_is_not_answering_never_missing_and_the_request_waits_only_the_shares_moment(self):
        app, home = web_client.app_for(self, "tagpup")
        share = os.path.join(home.root, "NAS", "Pictures")
        os.makedirs(share)
        settings.change(Library(home.library("library.db")), {"library.roots": share})
        asleep, under = threading.Event(), os.path.normcase(share)
        self.addCleanup(asleep.set)
        shares.forget()
        self.addCleanup(shares.forget)

        def hang(real):
            def look(path, *args, **kwargs):
                if os.path.normcase(str(path)).startswith(under):
                    asleep.wait(1.5)
                    return False
                return real(path, *args, **kwargs)
            return look
        waits, counting = share_waits.counted()
        with mock.patch.object(os.path, "isdir", hang(os.path.isdir)), \
                mock.patch.object(os.path, "exists", hang(os.path.exists)), \
                mock.patch.object(shares, "on_a_network_drive", lambda path: os.path.normcase(str(path)).startswith(under)), \
                mock.patch.object(shares, "SHARE_WAIT", 0.05), counting:
            reply = app.test_client().get("/api/activity/sync")
        self.assertEqual(200, reply.status_code)
        (library,) = reply.get_json()["libraries"]
        self.assertEqual([{"path": share, "there": False, "answering": False}],
                         [{k: root[k] for k in ("path", "there", "answering") if k in root} for root in library["roots"]])
        self.assertEqual([0.05], waits, "the root was asked through shares.bounded, once")


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
