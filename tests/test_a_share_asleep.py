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
"Not looked at" is for a SHARE only: a local drive letter that is not connected is a root gone and its rows missing, as
before #1356 (owner, 2026-10-15: no USB drives; #1387).

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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import doctor  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import share_waits  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import recycle_bin, shares  # noqa: E402
from tagpup.services import activity, inspect, library_view, settings, sync, tagging  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

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

    def test_a_tag_write_to_its_photos_reads_none_of_them_and_says_the_share_is_away(self):
        """#1492: the one place that leaves a photo of a share asleep out of a write is file_changes.write_fields, so a
        TagTuner face write (face_people.write_tags, which lost its own 60 s deadline), a bulk tag write and a library
        view's edit each get the error at once, not ExifTool's 300 s with the lock held."""
        from tagpup.services import face_people
        session = mock.MagicMock()
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", session):
            done = face_people.write_tags(self.vl.library, {path: (["Trips/Coast"], ()) for path in self.photo_paths},
                                          face_people.Writer("exiftool"), "person added for a named face")
        self.assertEqual(0, session.return_value.__enter__.return_value.get_tags.call_count, "a photo of the share was read")
        self.assertEqual(0, done.changed)
        self.assertIn("network share that did not answer", str(done.errors[0][1]))
        self.assertEqual(3, self.vl.rows("SELECT COUNT(*) FROM photos")[0][0])

    def test_the_rows_whose_file_is_gone_leave_out_the_photos_on_it_and_name_no_folder_gone(self):
        """The MCP tool's answer (inspect.missing_files): a photo on the share asleep is not looked at, not gone. The
        doctor says the same: it called all of them missing with a bare os.path.exists (#1388)."""
        found = inspect.missing_files(self.vl.library)
        self.assertEqual((0, 0, 0, 3), (found["rows"], found["folders"], found["folders_gone"], found["not_looked_at"]))
        said = []
        doctor.report(self.vl.path, out=said.append)
        self.assertIn("rows whose file is not on disk: 0, in 0 folder(s); 0 folder(s) are gone entirely; 3 row(s) not "
                      "looked at (their share did not answer, or the file could not be read)", said)


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

    def a_drive_not_connected(self):
        """A local drive letter this machine has nothing on: where the photos of a drive unplugged are spelled."""
        for letter in "QRSTUVWXYZ":
            drive = letter + ":" + os.sep
            if not recycle_bin.on_a_network_drive(drive) and not os.path.exists(drive):
                return drive
        self.skipTest("every drive letter from Q to Z is in use")

    def test_a_root_on_a_local_drive_not_connected_is_gone_and_its_rows_missing_and_one_on_a_share_asleep_is_not(self):
        """#1387: a local drive letter that did not answer was taken for a share asleep -- its root "not looked at" and the
        library never in step -- where it was, and is again, a root gone with its rows missing (reported, never removed)."""
        unplugged = os.path.join(self.a_drive_not_connected(), "Pictures")
        share = os.path.join(self.home.root, "NAS", "Pictures")
        on_share = [self.photo(os.path.join(share, "2019 Regatta"), "IMG_%04d.jpg" % n) for n in range(3)]
        # The rows of the drive as the indexer recorded them while it was connected: a read of a file of that stamp.
        made = [self.photo(os.path.join(self.home.root, "Was on the drive"), "DSC_%04d.jpg" % n) for n in range(2)]
        on_drive = [os.path.join(unplugged, "2018 Harbour", os.path.basename(path)) for path in made]
        library = self.library("regatta.db", on_share)
        conn = db.connect(library.path)
        try:
            for path, read_from in zip(on_drive, made):
                store_photos.record_indexed(conn, path, dict(photo_rows.as_read(read_from, {}), path=path))
            conn.commit()
        finally:
            conn.close()
        self.asleep(share)
        with mock.patch.object(shares, "SHARE_WAIT", 0.05):
            result = sync.sync(library, apply=True, roots=[unplugged, share])
            found = inspect.missing_files(library)
            roots = activity.roots_there([unplugged, share])
        counts = result.details["counts"]
        self.assertTrue(result.ok, result.message())
        self.assertEqual({"roots_gone": 1, "missing": 2, "folders_gone": 1, "not_looked_at": 3, "folders_not_looked_at": 1},
                         {what: counts[what] for what in ("roots_gone", "missing", "folders_gone", "not_looked_at",
                                                          "folders_not_looked_at")})
        self.assertEqual([unplugged], result.details["reveal"]["roots_gone"])
        self.assertFalse(result.details["in_step"], "a share asleep leaves the library not in step")
        self.assertEqual((2, 1, 1, 3), (found["rows"], found["folders"], found["folders_gone"], found["not_looked_at"]))
        self.assertEqual([(False, True), (False, False)], [(root["there"], root["answering"]) for root in roots],
                         "the drive is missing, the share not answering")
        self.assertEqual(sorted(on_share + on_drive), self.paths_of(library), "nothing was removed")

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

    def test_a_root_that_answers_and_will_not_list_is_not_counted_walked(self):
        """#1389: a top folder that answered and then failed to list was counted among the folders walked."""
        pictures = os.path.join(self.home.root, "Pictures")
        taken = [self.photo(os.path.join(pictures, "2020 Harbour"), "IMG_0001.jpg")]
        library = self.library("harbour.db", taken)
        real = os.scandir

        def scandir(path="."):
            if os.path.normcase(str(path)) == os.path.normcase(pictures):
                raise PermissionError(5, "Access is denied")
            return real(path)
        with mock.patch.object(os, "scandir", scandir):
            result = sync.sync(library, apply=True, roots=[pictures])
        self.assert_not_looked_at(result, 1, 1)
        self.assertEqual(0, result.details["counts"]["folders_walked"])

    def test_a_folder_to_review_on_a_share_asleep_is_refused_at_once_and_not_called_gone(self):
        """#1392: include() asked a bare os.path.isdir, which held the request while the share hung and then said the
        folder was not on disk."""
        share = os.path.join(self.home.root, "NAS", "Pictures")
        folder = os.path.join(share, "2019 Regatta")
        self.photo(folder, "IMG_0001.jpg")
        library = self.library("regatta.db", [])
        self.asleep(share)
        queued = []
        started = time.monotonic()
        with mock.patch.object(shares, "SHARE_WAIT", 0.05):
            result = sync.include(library, folder, [share], queued.append)
        self.assertLess(time.monotonic() - started, 1.0, "the request waited on the share")
        self.assertIn("not answering", result.refused or "")
        self.assertEqual([], queued)

    def test_a_share_whose_listing_failed_for_a_thousand_folders_sorts_its_rows_within_a_second(self):
        """#1442: each row the walk did not see was compared with every folder not looked at (two paths.key each), so
        1,282 unlisted folders of the 2,564-folder library cost about 125 s; its parents are asked of the folders' keys."""
        share = os.path.join(self.home.root, "NAS", "Pictures")
        folders = [os.path.join(share, "%04d Regatta" % n, "Day %d" % (n % 7)) for n in range(1000)]
        rows = {}
        for n in range(3000):
            path = os.path.join(folders[n % 1000] if n % 2 else os.path.join(share, "Elsewhere", "%04d" % n),
                                "IMG_%04d.jpg" % n)
            rows[paths.key(path)] = (n + 1, path, THEN, 100)
        started = time.perf_counter()
        missing, unseen = sync._unseen(rows, {}, folders)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual((1500, 1500), (len(missing), len(unseen)))
        unc = os.sep * 2 + "nas" + os.sep + "photos"
        for folder, under, answer in ((unc + os.sep + "2019", unc, True), (unc + "2" + os.sep + "2019", unc, False),
                                      (unc, unc + os.sep, True), (os.path.join("C:" + os.sep, "a"), "C:" + os.sep, True),
                                      (os.path.join("C:" + os.sep, "ab"), os.path.join("C:" + os.sep, "a"), False)):
            self.assertEqual(answer, sync._under_any(folder, {paths.key(under): under}), (folder, under))

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
    def test_a_file_on_a_share_is_gone_only_when_the_share_answers_and_on_a_local_drive_when_it_is_not_there(self):
        """Lifted from test_unreachable_is_not_missing (#566); break: _stat_state not asking _share_answers. A mapped
        network drive is a share; a local drive letter that is not connected is a drive gone (#1387)."""
        for path, network, drive_answers, gone in (("Q:\\Pictures\\a.jpg", True, False, False),
                                                   ("Q:\\Pictures\\a.jpg", True, True, True),
                                                   ("Q:\\Pictures\\a.jpg", False, False, True),
                                                   ("pictures/a.jpg", False, False, True)):
            with mock.patch("tagpup.files.shares.os.stat", side_effect=FileNotFoundError(2, "not found")), \
                    mock.patch("tagpup.files.shares.os.path.exists", return_value=drive_answers), \
                    mock.patch.object(shares, "on_a_network_drive", return_value=network):
                shares.forget()   # the share found away by the case before is not the one asked now
                present, left_out = tagging.leave_out_missing([path])
            self.assertEqual((not gone, gone), (present == [path], left_out != []), (path, network, drive_answers))


class AServerThatSaysNotFoundAtOnce(unittest.TestCase):
    """A server asleep answers WinError 53 at once, not by hanging (#1543): the share is away, not its files unreadable.
    Counted 2026-10-17: 1,309 photos of one live library sit on a UNC root. Break: _stat_state answering CANNOT_READ."""

    SERVER = "Q:\\"
    PHOTOS = ["Q:\\Pictures\\p%d.jpg" % n for n in range(4)]

    def setUp(self):
        shares.forget()
        self.addCleanup(shares.forget)
        self.reads = []
        self.server_up = False
        self.file_there = False
        real_stat, real_exists = os.stat, os.path.exists

        def on_the_server(path):
            return str(path).upper().startswith(self.SERVER)

        def stat(path, *args, **kwargs):
            if not on_the_server(path):
                return real_stat(path, *args, **kwargs)
            self.reads.append(path)
            if self.server_up and self.file_there:
                return os.stat_result((0, 0, 0, 1, 0, 0, 7, 0, 5, 0))
            raise FileNotFoundError(53, "The network path was not found")
        # Only the server's paths are stood in for: the test's own library, in a home of its own, is on the disk.
        for patcher in (mock.patch.object(os, "stat", stat),
                        mock.patch.object(os.path, "exists",
                                          lambda path: self.server_up if on_the_server(path) else real_exists(path)),
                        mock.patch.object(shares, "on_a_network_drive", on_the_server)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_walk_over_a_share_asleep_reports_it_away_once_and_reads_none_of_the_other_photos(self):
        walk = shares.Walk()
        found = [walk.stamp_of(path) for path in self.PHOTOS]
        self.assertEqual([shares.UNANSWERED] * 4, found)
        self.assertEqual((1, 1, 4), (len(walk.away), len(self.reads), walk.skipped))
        self.assertIs(shares.UNANSWERED, shares.stamp_of(self.PHOTOS[0]), "a look outside the walk is answered at once")
        self.assertEqual(1, len(self.reads))

    def test_a_server_that_answers_and_a_file_really_missing_is_still_gone_not_away(self):
        self.server_up = True
        walk = shares.Walk()
        self.assertEqual([None] * 4, [walk.stamp_of(path) for path in self.PHOTOS])
        self.assertEqual(([], 0, 4), (walk.away, walk.skipped, len(self.reads)))

    def test_a_tag_write_to_its_photos_stats_once_reads_none_and_touches_no_row_or_journal(self):
        """The journaled write (file_changes.write_fields; here a TagTuner face write, face_people.write_tags, the path
        a bulk tag, rename, rotate and delete share): each photo of the server that said not found at once is left out
        with AWAY_WHY after ONE stat for the whole selection -- no ExifTool read, no row touched, no change journaled.
        Break: _stat_state answering CANNOT_READ for it (the trunk before #1543: each photo stat'ed and read)."""
        from tagpup.services import face_people, file_changes
        vl = ViewLibrary(self)
        for path in self.PHOTOS:
            vl.photo("", "", taken="2024:06:01 10:00:00", tags=["Trips/Harbour"], at=path)
        before = vl.rows("SELECT * FROM photos ORDER BY id")
        journal = vl.rows("SELECT id, status FROM changes ORDER BY id")   # the new library's settings stamp
        del self.reads[:]   # the seeding's own stamps
        session = mock.MagicMock()
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", session):
            done = face_people.write_tags(vl.library, {path: (["Trips/Coast"], ()) for path in self.PHOTOS},
                                          face_people.Writer("exiftool"), "person added for a named face",
                                          stop_at_first_error=False)
        self.assertEqual(1, len(self.reads), "the server was asked more than once")
        self.assertEqual(0, session.return_value.__enter__.return_value.get_tags.call_count, "a photo was read")
        self.assertEqual(0, done.changed)
        self.assertEqual([file_changes.AWAY_WHY] * 4, [str(why) for _, why in done.errors])
        self.assertEqual(before, vl.rows("SELECT * FROM photos ORDER BY id"), "a row was touched")
        self.assertEqual(journal, vl.rows("SELECT id, status FROM changes ORDER BY id"), "a change was journaled")

    def test_a_share_that_comes_back_is_asked_again_by_the_next_walk(self):
        self.assertIs(shares.UNANSWERED, shares.Walk().stamp_of(self.PHOTOS[0]))
        self.server_up = self.file_there = True
        shares.forget()   # what Retry does, or AWAY_SECONDS passing
        again = shares.Walk()
        self.assertEqual([(5, 7)] * 2, [tuple(again.stamp_of(path)) for path in self.PHOTOS[:2]])
        self.assertEqual(([], 0), (again.away, again.skipped))


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
