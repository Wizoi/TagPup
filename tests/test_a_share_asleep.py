"""A share that does not answer is not "missing" (F7; findings #566, #568, #570, #721).

Windows reports a sleeping server, a share name that is not there and an unplugged drive letter with the same "not found"
it uses for a deleted file, at once; a share that merely hangs holds the thread that asks. A photo called missing is left
out of a bulk write and made read-only in the page, a folder view that waits on the share holds a request's thread, so the
owner's NAS asleep must be told as "away" (nothing is called missing, nothing is removed) in every place that looks:
the folder view (membership), a page's look at a folder or a photo it names, a page of cards, a bulk write, a sync.
Every look goes through shares.bounded. Stand-ins for
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

from click.testing import CliRunner
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import baked_files  # noqa: E402
import doctor  # noqa: E402
import own_home  # noqa: E402
import page_writes  # noqa: E402
import photo_rows  # noqa: E402
import share_waits  # noqa: E402
import web_client  # noqa: E402
from test_file_journal import EXIFTOOL, requires_exiftool, write_outside  # noqa: E402
from stand_in_models import Hold, NewFaces, RefusableFaces  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import CHANGED_ON_DISK_SENTENCE, SHARE_AWAY, Result  # noqa: E402
from tagpup.files import recycle_bin, shares  # noqa: E402
from tagpup.ml import clip  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import activity, bake_after_index, inspect, library_view, settings, sync, tagging  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import photos as photo_service  # noqa: E402
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
        self.asleep = asleep
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
                        mock.patch.object(shares, "SHARE_WAIT", 0.05)):
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
        doctor says the same: it called all of them missing with a bare os.path.exists (#1388).

        The doctor's look for a file that fails to open, run first so that the share falls asleep under it: it waits
        READ_WAIT for the first photo, then takes the share as away and counts the others not looked at at once -- it
        read each file with no deadline, a share asleep held it for as long as Windows waited on every photo, and the
        "not found" it then said skipped the photo uncounted (#2753)."""
        with mock.patch.object(shares, "READ_WAIT", 0.05):
            started = time.monotonic()
            opens = inspect.files_that_fail_to_open(self.vl.library)
            took = time.monotonic() - started
        self.assertEqual((0, 3, []), (opens["count"], opens["unreached"], opens["files"]))
        self.assertLess(took, 1.0, "the share was waited for on more than one photo")
        found = inspect.missing_files(self.vl.library)
        self.assertEqual((0, 0, 0, 3), (found["rows"], found["folders"], found["folders_gone"], found["not_looked_at"]))
        said = []
        doctor.report(self.vl.path, out=said.append)
        self.assertIn("rows whose file is not on disk: 0, in 0 folder(s); 0 folder(s) are gone entirely; 3 row(s) not "
                      "looked at (their share did not answer, or the file could not be read)", said)
        self.assertIn("looking at the end and the header of each photo's file (not the clips') for one that fails to open: "
                      "over a minute for every thousand on a network share", said)
        opened = [line for line in said if line.startswith("photos whose file fails to open")]
        self.assertTrue(opened[0].endswith("; 3 could not be looked at"), opened)


    def test_a_look_for_a_file_that_fails_to_open_ending_late_in_not_found_leaves_the_share_away(self):
        """A server gone while the doctor's look waited answers "not found", late: the look's end asks whether the share
        answers (shares.bounded, as of every look), and the share stays away. It was taken as back
        by the late answer, and the next photo was waited on again."""
        with mock.patch.object(shares, "READ_WAIT", 0.05):
            opens = inspect.files_that_fail_to_open(self.vl.library)
        self.assertEqual(3, opens["unreached"])
        root, real_exists = os.path.normcase(os.path.splitdrive(self.vl.pictures)[0] + os.sep), os.path.exists
        with mock.patch.object(os.path, "exists",
                               lambda path: os.path.normcase(str(path)) != root and real_exists(path)):
            self.asleep.set()   # the hung look ends: "not found", and the share's root is not there
            for look in [thread for thread in threading.enumerate() if thread.name == "ShareLook"]:
                look.join(5)
        self.assertIsNotNone(shares.away_since(shares.share_of(self.vl.pictures)),
                             "the late not-found took the share as back")


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


class AShareThatFallsAsleepMidLook(unittest.TestCase):
    """The owner's NAS falls asleep while a look at it is under way: the look says "not found", in time or late, the words
    Windows uses for a file deleted. Whoever made the look -- a tag write's open of its photo, the faces page's look at a
    photo's header, a converted clip's unfinished copy removed -- is told the share did not answer, never that the file is
    gone, and the share stays away, so the next photo on it is answered at once. A file deleted while the share answers is
    still gone. The ask is shares.bounded's own (#2781). Break: bounded not asking _share_answers of a "not found" (each
    caller's own ask, as before #2781, missed by these three) fails the three asleep tests."""

    def setUp(self):
        shares.forget()
        self.addCleanup(shares.forget)
        self.vl = ViewLibrary(self)
        self.share = os.path.normcase(self.vl.pictures)
        self.root = os.path.normcase(os.path.splitdrive(self.vl.pictures)[0] + os.sep)
        self.server_up = True
        real_exists = os.path.exists
        for patcher in (mock.patch.object(shares, "on_a_network_drive",
                                          lambda path: os.path.normcase(str(path)).startswith(self.share)),
                        mock.patch.object(os.path, "exists", lambda path: (self.server_up or os.path.normcase(str(path))
                                                                           != self.root) and real_exists(path))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def away(self):
        return shares.away_since(shares.share_of(self.vl.pictures)) is not None

    def falls_asleep(self, *args, **kwargs):
        """A look at the share as it falls asleep: "not found" (WinError 53), and its root not there from then on."""
        self.server_up = False
        raise FileNotFoundError(2, "The network path was not found")

    @requires_exiftool
    def test_a_tag_rename_as_the_share_falls_asleep_says_the_share_did_not_answer_and_opens_no_other_photo(self):
        """The patch's open of the first photo says "not found": the write stops there, saying to check the share, the
        second photo is not opened, and both files hold what they held. Was: the open's FileNotFoundError taken as the
        photo's own failure, the share asked by no one, and the second photo opened in turn."""
        from tagpup.files import in_place
        vl = self.vl
        photo_paths = [os.path.join(vl.pictures, "Coast", "p%d.jpg" % n) for n in range(2)]
        os.makedirs(os.path.dirname(photo_paths[0]))
        for path in photo_paths:
            Image.new("RGB", (16, 12), (90, 110, 130)).save(path, "JPEG")
            values = {"XMP:Subject": ["Trips/Harbour"], "IPTC:Keywords": ["Trips/Harbour"],
                      "EXIF:XPKeywords": "Trips/Harbour"}
            write_outside(path, values)
            vl.photo("Coast", os.path.basename(path), fields=values)
        held = [read_bytes(path) for path in photo_paths]
        with mock.patch.object(in_place, "_alone", side_effect=self.falls_asleep) as opened:
            done = tagging.replace_tag(vl.library, photo_paths, "Trips/Harbour", "Trips/Coast", EXIFTOOL)
        self.assertEqual(0, done.changed)
        self.assertEqual(1, opened.call_count, "a photo on the share asleep was opened")
        said = " ".join(str(why) for _, why in done.errors)
        self.assertIn("check that the share or drive is there", said)
        self.assertNotIn("not found", said)
        self.assertTrue(self.away(), "the share was taken as answering")
        self.assertEqual(held, [read_bytes(path) for path in photo_paths])

    def test_a_photo_deleted_while_its_share_answers_is_still_gone(self):
        """The same "not found" while the share's root is there: the file is gone, and the share is not away."""
        from tagpup.files import clip_convert
        temp = os.path.join(self.vl.pictures, "clip.mp4" + clip_convert.SUFFIX)
        self.assertIsNone(clip_convert.removed(temp), "a temp that is not there is removed")
        with mock.patch.object(photo_service.images, "shown_shape",
                               side_effect=FileNotFoundError(2, "The system cannot find the file specified")):
            self.assertEqual({"size": None, "turn": 1}, photo_service.box_shape(os.path.join(self.vl.pictures, "a.jpg")))
        self.assertFalse(self.away(), "a file deleted took its share as away")

    def test_a_converted_clips_unfinished_copy_on_a_share_asleep_is_not_said_removed(self):
        """Was: the remove's "not found" swallowed, and the copy on the share asleep said removed."""
        from tagpup.files import clip_convert
        temp = os.path.join(self.vl.pictures, "clip.mp4" + clip_convert.SUFFIX)
        with mock.patch.object(clip_convert.os, "remove", side_effect=self.falls_asleep):
            self.assertEqual(clip_convert.TEMP_LEFT % "the share did not answer", clip_convert.removed(temp))
        self.assertTrue(self.away())

    def test_two_pages_drawing_face_boxes_as_the_share_falls_asleep_leave_it_away_and_the_next_waits_for_nothing(self):
        """Two requests at once each look at a photo's header; the share hangs, then says "not found", late. Both draw no
        box; the late answers leave the share away, so a third photo's box is answered without a wait. Was: the late
        "not found" took the share as back, and the next photo waited out SHAPE_WAIT again."""
        asleep = threading.Event()
        self.addCleanup(asleep.set)

        def header(path):
            asleep.wait(5)
            self.falls_asleep()
        photo_paths = [os.path.join(self.vl.pictures, "Coast", "p%d.jpg" % n) for n in range(3)]
        drawn = {}
        with mock.patch.object(photo_service.images, "shown_shape", header), \
                mock.patch.object(photo_service, "SHAPE_WAIT", 0.05):
            pages = [threading.Thread(target=lambda path=path: drawn.update({path: photo_service.box_shape(path)}))
                     for path in photo_paths[:2]]
            for page in pages:
                page.start()
            for page in pages:
                page.join(5)
            asleep.set()   # the hung looks end: "not found", and the share's root is not there
            for look in [thread for thread in threading.enumerate() if thread.name == "ShareLook"]:
                look.join(5)
            self.assertEqual([{"size": None, "turn": 1}] * 2, [drawn[path] for path in photo_paths[:2]])
            self.assertTrue(self.away(), "the late not-found took the share as back")
            waits, counting = share_waits.counted()
            with counting:
                self.assertEqual({"size": None, "turn": 1}, photo_service.box_shape(photo_paths[2]))
            self.assertEqual([], waits, "the third photo waited on the share")


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

    def test_a_root_on_a_share_asleep_is_not_looked_at_and_the_library_is_not_recorded_in_step(self):
        """The second library on the same share is answered at once: TwoLibrariesSyncedOnAShareAsleep."""
        share = os.path.join(self.home.root, "NAS", "Pictures")
        taken = [self.photo(os.path.join(share, "2019 Regatta"), "IMG_%04d.jpg" % n) for n in range(3)]
        regatta = self.library("regatta.db", taken)
        self.asleep(share)
        first = sync.sync(regatta, apply=True, roots=[share])
        self.assert_not_looked_at(first, 3, 1)
        self.assertEqual(0, first.details["counts"]["folders_walked"])
        self.assertIsNone(sync.last(regatta)["last_in_step"], "a sync that could not look recorded the library in step")
        self.assertEqual(sorted(taken), self.paths_of(regatta))

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
                photo_rows.added(conn, path)
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

    def test_a_listing_that_drops_part_way_leaves_the_whole_folder_not_looked_at(self):
        """A folder's listing is one answer (shares.listing): one that failed part-way is not an answer, and none of the
        folder's photos is looked at -- the one listed before the drop neither."""
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
        self.assert_not_looked_at(result, 3, 1)
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
        (root,) = library["roots"]
        self.assertRegex(root["away_since"] or "", r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$",
                         "the card says since when the share has not answered")
        self.assertEqual([0.05], waits, "the root was asked through shares.bounded, once")


class ADriveThatSaysNotFound(unittest.TestCase):
    def test_a_file_on_a_share_is_gone_only_when_the_share_answers_and_on_a_local_drive_when_it_is_not_there(self):
        """Lifted from test_unreachable_is_not_missing (#566); break: bounded not asking _share_answers. A mapped
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
    Counted 2026-10-17: 1,309 photos of one live library sit on a UNC root, the shape stood in for here. Break:
    stamp_of answering CANNOT_READ."""

    SERVER = "\\\\NAS\\PICTURES"
    PHOTOS = ["\\\\Nas\\Pictures\\p%d.jpg" % n for n in range(4)]

    def setUp(self):
        shares.forget()
        self.addCleanup(shares.forget)
        self.reads = []
        self.server_up = False
        self.file_there = False
        real_stat, real_exists, real_isdir = os.stat, os.path.exists, os.path.isdir

        def on_the_server(path):
            return str(path).upper().startswith(self.SERVER)

        def stat(path, *args, **kwargs):
            if not on_the_server(path):
                return real_stat(path, *args, **kwargs)
            self.reads.append(path)
            if self.server_up and self.file_there:
                return os.stat_result((0, 0, 0, 1, 0, 0, 7, 0, 5, 0))
            raise FileNotFoundError(53, "The network path was not found")

        def isdir(path):
            if not on_the_server(path):
                return real_isdir(path)
            self.reads.append(path)
            return self.server_up
        # Only the server's paths are stood in for: the test's own library, in a home of its own, is on the disk.
        for patcher in (mock.patch.object(os, "stat", stat), mock.patch.object(os.path, "isdir", isdir),
                        mock.patch.object(os.path, "exists",
                                          lambda path: self.server_up if on_the_server(path) else real_exists(path)),
                        mock.patch.object(shares, "on_a_network_drive", on_the_server)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_walk_over_a_share_asleep_reports_it_away_once_and_reads_none_of_the_other_photos(self):
        walk = shares.Walk()
        found = [walk.stamp_of(path) for path in self.PHOTOS]
        self.assertEqual([shares.UNANSWERED] * 4, found)
        self.assertEqual(([shares.share_of(self.PHOTOS[0])], 1, 4), (walk.away, len(self.reads), walk.skipped))
        self.assertIs(shares.UNANSWERED, shares.stamp_of(self.PHOTOS[0]), "a look outside the walk is answered at once")
        self.assertEqual(1, len(self.reads))

    def test_a_server_that_answers_and_a_file_really_missing_is_still_gone_not_away(self):
        self.server_up = True
        walk = shares.Walk()
        self.assertEqual([None] * 4, [walk.stamp_of(path) for path in self.PHOTOS])
        self.assertEqual(([], 0, 4), (walk.away, walk.skipped, len(self.reads)))

    def test_a_file_found_away_answers_a_folder_of_the_server_at_once_and_the_reverse_for_thirty_seconds(self):
        """One place learns that a server saying not found at once is away (_share_answers), so a look at a file (a page of
        cards, a write) and a look at a folder (a sync, each folder it walks) share it: the second is answered without
        asking the server, until AWAY_SECONDS from when it was FOUND away -- the looks answered meanwhile come from the
        memo (bounded) and never reach the mark, so they do not slide the window. Break: the share marked in stamp_of alone (a folder look asks again), or bounded clearing what its
        own look learned."""
        folder, clock = "\\\\Nas\\Pictures\\2019", [1000.0]
        with mock.patch.object(shares, "time", mock.Mock(monotonic=lambda: clock[0])):
            self.assertIs(shares.UNANSWERED, shares.stamp_of(self.PHOTOS[0]))
            self.assertEqual(["unanswered", 1], [shares.folder_state(folder), len(self.reads)], "the folder asked again")
            clock[0] += 20
            self.assertEqual([shares.UNANSWERED, 1], [shares.stamp_of(self.PHOTOS[1]), len(self.reads)])
            clock[0] += 11   # 31 s after it was found away, 11 after the last look answered
            self.assertEqual(["unanswered", 2], [shares.folder_state(folder), len(self.reads)], "the window slid")
            shares.forget()   # what Retry does
            del self.reads[:]
            self.assertEqual("unanswered", shares.folder_state(folder))
            self.assertEqual([shares.UNANSWERED, 1], [shares.stamp_of(self.PHOTOS[2]), len(self.reads)],
                             "a file of the server found away by a folder look was asked")

    def test_a_tag_write_to_its_photos_stats_once_reads_none_and_touches_no_row_or_journal(self):
        """The journaled write (file_changes.write_fields; here a TagTuner face write, face_people.write_tags, the path
        a bulk tag, rename, rotate and delete share): each photo of the server that said not found at once is left out
        with AWAY_WHY after ONE stat for the whole selection -- no ExifTool read, no row touched, no change journaled.
        Break: stamp_of answering CANNOT_READ for it (the trunk before #1543: each photo stat'ed and read)."""
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

    def test_a_tag_tree_rewrite_of_photos_on_a_share_found_away_answers_at_once_without_the_away_wait(self):
        """The tag tree's rewrites are uncapped but not patient (owner, 2026-11-08, #2630): a photo on a share found away is
        its error at once, as before the caption sweep's wait existed; only the sweep looks again after AWAY_SECONDS.
        Break: write_fields passing `patient=uncapped`."""
        from tagpup.services import file_changes
        vl = ViewLibrary(self)
        for path in self.PHOTOS:
            vl.photo("", "", taken="2024:06:01 10:00:00", tags=["Trips/Harbour"], at=path)
        shares.stamp_of(self.PHOTOS[0])   # found away
        session = mock.MagicMock()
        started = time.monotonic()
        with mock.patch.object(shares, "AWAY_SECONDS", 6.0), \
                mock.patch.object(shares, "wait_back") as wait_back, \
                mock.patch("tagpup.files.exiftool_session.ExifToolSession", session):
            done = tagging.replace_tag(vl.library, self.PHOTOS, "Trips/Harbour", "Trips/Coast", "exiftool")
        self.assertLess(time.monotonic() - started, 3.0, "waited for the share's window")
        wait_back.assert_not_called()
        self.assertEqual(0, done.changed)
        self.assertEqual([file_changes.AWAY_WHY] * 4, [str(why) for _, why in done.errors])

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
                mock.patch.object(shares, "bounded", return_value=("error", FileNotFoundError(2, "not found"))):
            found = library_actions.membership_checked(self.vl.library, self.folder)
        self.assertTrue(found["could_not_check"])
        self.assertEqual([], self.walks)


class TheFolderViewsRoute(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()

    def test_a_folder_not_there_is_a_400_and_one_that_cannot_be_checked_a_200(self):
        url = "/library/api/folder/membership"
        self.assertEqual(400, self.client.get(url, query_string={"path": os.path.join(self.home.root, "nowhere")}).status_code)
        there = os.path.join(self.home.root, "there")
        os.makedirs(there)
        gone = {"folder": there, "could_not_check": True, "why": "the network share is away"}
        with mock.patch.object(library_actions, "membership_checked", return_value=gone):
            reply = self.client.get(url, query_string={"path": there})
        self.assertEqual((200, gone), (reply.status_code, reply.get_json()))


#: How long a share that has stopped answering holds a look before Windows gives up on it, in the test: far beyond the
#: moment a page waits for it.
WINDOWS_WAIT = 3.0


class APageAskingOfAShareThatHangs(unittest.TestCase):
    """The owner picks a folder on the NAS, or opens one, while it has stopped answering: every look a page's request
    makes at it -- the folder's check, the folder box's listing, the folder picker's subfolders, a photo's check before a
    rotate -- is made on a thread that is waited for a moment (tagpup.services.photos, the pages' door to
    tagpup.files.shares), so the request answers within that moment, not after Windows's wait.
    Break: any route looking itself (os.path.isdir, os.listdir, os.path.exists on the request's thread, as before the
    looks moved) holds the request for WINDOWS_WAIT and makes a look off a ShareLook thread."""

    def setUp(self):
        shares.forget()
        self.addCleanup(shares.forget)
        self.released = threading.Event()
        self.addCleanup(self.released.set)
        self.looked_from = []
        self.share = None
        for patcher in (mock.patch.object(shares, "SHARE_WAIT", 0.2), mock.patch.object(shares, "LISTING_WAIT", 0.2),
                        mock.patch.object(shares, "on_a_network_drive", self.on_the_share)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def on_the_share(self, path):
        return self.share is not None and os.path.normcase(str(path)).startswith(os.path.normcase(self.share))

    def the_share_hangs(self):
        """Every look under the share holds its thread for WINDOWS_WAIT, then fails as Windows says the network name is
        no longer available; a look anywhere else is the disk's."""
        def hanging(real, failed):
            def look(path, *args, **kwargs):
                if isinstance(path, str) and self.on_the_share(path):
                    self.looked_from.append(threading.current_thread().name)
                    self.released.wait(WINDOWS_WAIT)
                    if failed is OSError:
                        raise OSError(64, "The specified network name is no longer available")
                    return failed
                return real(path, *args, **kwargs)
            return look
        for name, failed in (("stat", OSError), ("scandir", OSError), ("listdir", OSError)):
            patcher = mock.patch.object(os, name, hanging(getattr(os, name), failed))
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("isdir", "isfile", "exists"):
            patcher = mock.patch.object(os.path, name, hanging(getattr(os.path, name), False))
            patcher.start()
            self.addCleanup(patcher.stop)

    def app(self, kind):
        app, home = web_client.app_for(self, kind)
        self.share = os.path.join(home.root, "NAS", "Pictures")
        self.folder = os.path.join(self.share, "2021 Coast")
        os.makedirs(os.path.join(self.folder, "Dunes"))
        self.photo = os.path.join(self.folder, "IMG_0001.jpg")
        Image.new("RGB", (8, 8)).save(self.photo, "JPEG")
        return app.test_client()

    def answered(self, ask):
        """(the reply, seconds it took) of `ask()`, with the share hanging."""
        began = time.monotonic()
        reply = ask()
        return reply, time.monotonic() - began

    def assert_within_the_moment(self, took):
        self.assertLess(took, WINDOWS_WAIT / 2, "the request waited on the share as long as Windows does")
        self.assertEqual([], [name for name in self.looked_from if name != "ShareLook"],
                         "a look at the share was made on the request's own thread")

    def test_opening_typing_and_picking_a_folder_on_it_answer_within_the_moment(self):
        client = self.app("tagpup")
        self.the_share_hangs()
        for ask, status in ((lambda: client.get("/library/api/folder/scan", query_string={"path": self.folder}), 503),
                            (lambda: client.get("/library/api/folder/membership", query_string={"path": self.folder}),
                             200),
                            (lambda: client.get("/library/api/autocomplete-folder",
                                                query_string={"path": os.path.join(self.folder, "Du")}), 200),
                            # The photo on a share that did not answer is not called gone (#3068).
                            (lambda: client.post("/library/api/photo/rotate",
                                                 json={"path": self.photo, "direction": "left"}), 503)):
            shares.forget()   # each request meets the share hanging, not one already taken as away
            reply, took = self.answered(ask)
            self.assertEqual(status, reply.status_code, reply.data)
            self.assert_within_the_moment(took)
        self.assertTrue(self.looked_from, "the share was looked at")
        self.assertEqual(True, client.get("/library/api/folder/membership",
                                          query_string={"path": self.folder}).get_json()["could_not_check"])

    def test_the_folder_pickers_subfolders_on_it_answer_within_the_moment(self):
        client = self.app("tuner")
        self.the_share_hangs()
        reply, took = self.answered(lambda: client.get("/library/api/folder/subfolders",
                                                       query_string={"path": self.share}))
        self.assertEqual(503, reply.status_code, reply.data)
        self.assert_within_the_moment(took)
        self.assertTrue(self.looked_from, "the share was looked at")


class AFolderOnAShareThatDoesNotAnswerIsRefusedAsAway(unittest.TestCase):
    """#2129: the owner opens, adds, Suggests in or renames in a folder on the NAS while it has stopped answering
    (the library's one network root; #2101, #2109), or lists it in TagTuner's folder picker: every route that refuses the
    folder says the SHARE is not answering, by its server and share and never the folder's path (which names people),
    503 (try again later), in one sentence from one place (tagpup.files.shares.folder_refusal); a folder that is really
    not there is still 400 "not a valid directory". A Time Shift names its photos, not the folder (POST /api/photos/write
    op shift): a photo on the share asleep is left out, saying so, and the others written
    (tests/test_a_write_through_the_one_entry_point.py). Break: a route refusing an away share 400 "not a valid directory",
    "Invalid folder path" or "Not a folder" (as each did), or its own sentence."""

    SERVER = "\\\\NAS\\Pictures"
    FOLDER = SERVER + "\\2021 Coast"
    AWAY = "The network share \\\\NAS\\Pictures is not answering just now. Try again when it is back."

    #: (app, method, route, the request's body or query for `folder`): every route that refuses a folder.
    SITES = (
        ("tagpup", "get", "/api/folder/scan", lambda folder: {"path": folder}),
        ("tagpup", "post", "/api/folder/add", lambda folder: {"folder_path": folder}),
        ("tagpup", "post", "/api/folder/suggest-start", lambda folder: {"folder_path": folder}),
        ("tagpup", "post", "/api/folder/auto-apply", lambda folder: {"folder_path": folder}),
        ("tagpup", "post", "/api/folder/rename-photos",
         lambda folder: {"folder_path": folder, "photo_paths": [folder + "\\IMG_0001.jpg"], "grouping": "Coast"}),
        # The Smart Rename of one photo, from the box beside its name.
        ("tagpup", "post", "/api/photo/smart-rename", lambda folder: {"path": folder + "\\IMG_0001.jpg", "grouping": "Coast"}),
        ("tagpup", "post", "/api/sync/review/include", lambda folder: {"folder": folder}),
        ("tuner", "get", "/api/folder/subfolders", lambda folder: {"path": folder}),
        ("tuner", "post", "/api/folder/index-start", lambda folder: {"folder_paths": [folder]}),
    )

    def setUp(self):
        shares.forget()
        self.addCleanup(shares.forget)
        real_isdir, real_exists = os.path.isdir, os.path.exists

        def on_the_server(path):
            return str(path).upper().startswith(self.SERVER.upper())
        # The server asleep says "not found" at once (WinError 53): the share is away, not the folder gone.
        for patcher in (mock.patch.object(os.path, "isdir", lambda p: False if on_the_server(p) else real_isdir(p)),
                        mock.patch.object(os.path, "exists", lambda p: False if on_the_server(p) else real_exists(p))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.clients = {}

    def client(self, kind):
        if kind not in self.clients:
            app, self.home = web_client.app_for(self, kind, runtime=Runtime())   # its index queue, as the server's
            self.clients[kind] = app.test_client()
        return self.clients[kind]

    def ask(self, kind, method, route, made, folder):
        shares.forget()   # each request meets the share asleep, not one already taken as away
        client = self.client(kind)
        if method == "get":
            return client.get("/library" + route, query_string=made(folder))
        return client.post("/library" + route, json=made(folder))

    def test_every_route_says_the_share_is_not_answering_503_and_a_folder_not_there_is_400(self):
        for kind, method, route, made in self.SITES:
            with self.subTest(route=route):
                reply = self.ask(kind, method, route, made, self.FOLDER)
                self.assertEqual((503, self.AWAY), (reply.status_code, reply.get_json()["error"]), route)
                self.assertNotIn("2021 Coast", reply.get_json()["error"], "the folder's path is named")
                nowhere = os.path.join(self.home.root, "nowhere")
                reply = self.ask(kind, method, route, made, nowhere)
                self.assertEqual(400, reply.status_code, route)
                expected = ("No valid folder path: %s" if route == "/api/folder/index-start"
                            else "Path is not a valid directory: %s") % nowhere
                self.assertEqual(expected, reply.get_json()["error"], route)

    def test_an_index_from_the_cli_says_the_share_is_not_answering_and_a_folder_not_there_is_not_one(self):
        """The CLI's index (the owner's, and the queue's after a share dropped): click's exists=True looked unbounded and
        said the folder does not exist."""
        home = own_home.for_test(self)
        db_path = home.library("library.db")
        library_actions.create(db_path)
        nowhere = os.path.join(home.root, "nowhere")
        for folder, said in ((self.FOLDER, self.AWAY), (nowhere, "Path is not a valid directory: %s" % nowhere)):
            shares.forget()
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", db_path, "index", folder])
            self.assertEqual(2, result.exit_code, result.output)
            self.assertIn(said, " ".join(result.output.split()), folder)

    def test_a_folder_typed_in_an_old_places_spelling_on_a_share_asleep_says_the_share_is_away(self):
        """#2145: the first place is on a share that does not answer; the old place's sentence ("does not exist at its
        current place") said the folder was not there. A first place that is really gone still says the old one's.
        Break: `not os.path.isdir(found)` in _resolve_directories, unbounded and with no word of the share."""
        old_place = "an old place sentence"
        nowhere = os.path.join(own_home.for_test(self).root, "nowhere")
        for first, said in ((self.FOLDER, self.AWAY), (nowhere, old_place)):
            shares.forget()
            printed = []
            with mock.patch.object(tagpup_cli.library_roots, "canonicaliser", return_value=lambda folder: first), \
                    mock.patch.object(tagpup_cli.library_roots, "old_place_sentence", return_value=old_place), \
                    mock.patch.object(tagpup_cli.console, "print", lambda text, **kwargs: printed.append(text)):
                with self.assertRaises(SystemExit) as stopped:
                    tagpup_cli._resolve_directories(None, {"directories": ("C:\\Old\\Coast",)})
            self.assertEqual(2, stopped.exception.code, "the code _folders_now's usage error exits with")
            self.assertEqual([said], printed, first)

    def test_the_cli_does_not_stat_a_folder_before_the_bounded_look(self):
        """Break: click.Path(file_okay=False) on DIRECTORIES: it stats the path itself, unbounded, before folder_refusal."""
        home = own_home.for_test(self)
        db_path = home.library("library.db")
        library_actions.create(db_path)
        real_stat, statted = os.stat, []

        def stat(path, *args, **kwargs):
            if str(path).upper().startswith(self.SERVER.upper()):
                statted.append(path)
            return real_stat(path, *args, **kwargs)
        with mock.patch.object(os, "stat", stat):
            CliRunner().invoke(tagpup_cli.cli, ["--db", db_path, "index", self.FOLDER])
        self.assertEqual([], statted)

    def test_a_share_that_stops_answering_between_the_look_and_the_queue_is_still_refused_as_away(self):
        """The review's include hands up the queue's Result whole: the queue's refusal keeps its kind, SHARE_AWAY (a 503),
        where a refusal re-made from its sentence was a 400. Break: include copying `refused` into a Result of its own."""
        home = own_home.for_test(self)
        library = Library(home.library("library.db"))
        library_actions.create(library.path)
        folder = os.path.join(home.root, "Pictures", "2021 Coast")
        os.makedirs(folder)

        def queue(folders):
            away = Result(attempted=len(folders))
            away.refuse(self.AWAY, SHARE_AWAY)
            return away
        result = sync.include(library, folder, [os.path.join(home.root, "Pictures")], queue)
        self.assertEqual(self.AWAY, result.refused)
        self.assertEqual(SHARE_AWAY, result.refused_kind)


class APhotoOnAShareThatDoesNotAnswerIsRefusedAsAway(unittest.TestCase):
    """#3068: the owner adds a tag to a photo, turns it, deletes it or shows it in Explorer while its share sleeps: every
    route that acts on one photo says the SHARE is not answering, 503, in the sentence a folder on it is refused with
    (shares.SHARE_AWAY_SENTENCE), and writes nothing: the file, its row and the journal are as they were. A photo whose
    file is really gone is still 400 "Invalid file path"; a save, which names the photo's row, is told its photo changed
    on disk since it was opened (409, as one renamed since). Break: the routes' one check reading shares.known_stamp's None
    for a share that did not answer as no file (as _a_file did): the share asleep is 400 "Invalid file path"."""

    #: (route, the request's body for `path` and its row's id): every route that acts on one photo
    #: (responses.file_refused); a save as the panel makes it from the record the folder gave before the share slept.
    #: The answer to a photo whose file is gone, by route, when it is not 400 "Invalid file path".
    GONE = {"/api/photos/write": (409, CHANGED_ON_DISK_SENTENCE)}
    SITES = (
        ("/api/photos/write", lambda path, photo_id: page_writes.set_from(
            {"path": path, "id": photo_id, "title": "", "tags": ["Trips/Lakes"], "mtime": 1.0, "size": 1},
            tags=["Trips/Lakes", "Trips/Coast"])),
        ("/api/photo/rotate", lambda path, photo_id: {"path": path, "direction": "right"}),
        ("/api/photo/delete", lambda path, photo_id: {"path": path}),
        ("/api/photo/open-explorer", lambda path, photo_id: {"path": path}),
        ("/api/photo/open", lambda path, photo_id: {"path": path}),
    )

    def setUp(self):
        app, home = web_client.app_for(self, "tagpup")
        self.client = app.test_client()
        self.library_path = home.library("library.db")
        self.asleep_folder = os.path.join(home.root, "Pictures", "Coast")
        self.photo = os.path.join(self.asleep_folder, "IMG_0001.jpg")
        os.makedirs(self.asleep_folder)
        Image.new("RGB", (64, 48), (90, 110, 130)).save(self.photo, "JPEG")
        self.gone = os.path.join(home.root, "Pictures", "Lakes", "IMG_0002.jpg")
        conn = db.connect(self.library_path)
        try:
            for path in (self.photo, self.gone):
                photo_rows.add_read(conn, path, {"XMP:Subject": ["Trips/Lakes"]})
            conn.commit()
            self.ids = [row[0] for row in conn.execute("SELECT id FROM photos ORDER BY id")]   # the photo's, the gone one's
        finally:
            conn.close()
        asleep, under = threading.Event(), os.path.normcase(self.asleep_folder)
        self.addCleanup(asleep.set)
        shares.forget()
        self.addCleanup(shares.forget)
        real_stat = os.stat

        def on_it(path):
            return os.path.normcase(str(path)).startswith(under)

        def stat(path, *args, **kwargs):
            if on_it(path):
                asleep.wait(1.5)
                raise FileNotFoundError(2, "not found")
            return real_stat(path, *args, **kwargs)
        for patcher in (mock.patch.object(os, "stat", stat),
                        mock.patch.object(shares, "on_a_network_drive", on_it),
                        mock.patch.object(shares, "SHARE_WAIT", 0.05)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def held(self):
        """What the library holds: its photos' rows and its journal."""
        conn = db.connect(self.library_path)
        try:
            return (conn.execute("SELECT * FROM photos ORDER BY id").fetchall(),
                    conn.execute("SELECT * FROM changes ORDER BY id").fetchall())
        finally:
            conn.close()

    def test_every_route_says_the_share_is_not_answering_503_writes_nothing_and_a_file_gone_is_400(self):
        with open(self.photo, "rb") as handle:
            before = handle.read()
        held = self.held()
        away = shares.SHARE_AWAY_SENTENCE % os.path.splitdrive(self.photo)[0]
        for route, made in self.SITES:
            with self.subTest(route=route):
                reply = self.client.post("/library" + route, json=made(self.photo, self.ids[0]))
                self.assertEqual((503, away), (reply.status_code, reply.get_json()["error"]), route)
                self.assertNotIn("IMG_0001", reply.get_json()["error"], "the photo's path is named")
                reply = self.client.post("/library" + route, json=made(self.gone, self.ids[1]))
                self.assertEqual(self.GONE.get(route, (400, "Invalid file path")),
                                 (reply.status_code, reply.get_json()["error"]), route)
        with open(self.photo, "rb") as handle:
            self.assertEqual(before, handle.read(), "the photo was written")
        self.assertEqual(held, self.held(), "a row or the journal changed")

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


@unittest.skipIf(own_home.installed_exiftool() is None, "ExifTool not installed")
class ABakeOnAShareSlowToWake(unittest.TestCase):
    """Scenario 10 (docs/PLAN_KEEP_IN_STEP.md): the index's bake runs over a folder on a share asleep. Its first answer
    after idle is slower than the share's moment (1.3 s where SHARE_WAIT is 1.0, measured 2026-10-27), and the bake then
    counted every photo of the library "could not be reached" in seconds and wrote nothing. A bake's walk waits the away
    out and asks again once (shares.Walk, patient): a share that wakes is baked; one that stays away has its photos
    counted not answered -- never unreachable -- after one bounded wait, its files untouched. Times shrunk. The library
    is indexed by the CLI over a real ExifTool and the stand-in models, its bake held back until the share sleeps."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="bake_share_asleep_")
        self.db_path = self.home.library("harbour.db")
        self.assertEqual(1, library_actions.create(self.db_path).changed)
        self.library = Library(self.db_path)
        self.folder = paths.stored(os.path.join(self.home.root, "Harbour"))
        os.makedirs(self.folder)
        self.photos = []
        for name, colour in (("a.jpg", (200, 40, 40)), ("b.jpg", (40, 200, 40))):
            self.photos.append(os.path.join(self.folder, name))
            Image.new("RGB", (640, 480), colour).save(self.photos[-1], "JPEG")
        detector = RefusableFaces({"a.jpg": [([300, 200, 400, 330], 0.99, 1)]}, vector=baked_files.face_vector)
        self.detector = face_service.detector_of(NewFaces())
        vector = baked_files.unit(1152, 99).tolist()
        with mock.patch.object(clip.ClipModel, "_init_model"), \
                mock.patch.object(clip.ClipModel, "picture_input", return_value="input"), \
                mock.patch.object(clip.ClipModel, "embed_input", return_value=vector), \
                mock.patch.object(Runtime, "gpu_turn", lambda runtime, what, *args, **kwargs: Hold()), \
                mock.patch.object(runtimes, "on_the_card", return_value=True), \
                mock.patch.object(Runtime, "faces", lambda runtime, library, settings=None: detector), \
                mock.patch.object(tagpup_cli, "_bake_after_index"):
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", self.folder])
        self.assertEqual(0, result.exit_code, result.output)
        self.stamps = {path: os.stat(path).st_mtime_ns for path in self.photos}
        shares.forget()
        self.addCleanup(shares.forget)
        self.asleep = threading.Event()
        self.addCleanup(self.asleep.set)
        under = os.path.normcase(self.folder)
        for patcher in (mock.patch.object(shares, "on_a_network_drive",
                                          lambda path: os.path.normcase(str(path)).startswith(under)),
                        mock.patch.object(shares, "SHARE_WAIT", 0.1), mock.patch.object(shares, "AWAY_SECONDS", 0.4),
                        mock.patch.object(shares, "STOP_EVERY", 0.05)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def share(self, slow):
        """os.stat of a file in the folder takes `slow(n)` seconds at its n-th look (0 first), cut short by the test's end."""
        real, under, looks = os.stat, os.path.normcase(self.folder), []

        def stat(path, *args, **kwargs):
            if os.path.normcase(str(path)).startswith(under):
                looks.append(path)
                self.asleep.wait(slow(len(looks) - 1))
            return real(path, *args, **kwargs)
        return mock.patch.object(os, "stat", stat)

    def bake(self):
        started = time.monotonic()
        baked = bake_after_index.bake_folders(self.library, own_home.installed_exiftool(), self.detector, [self.folder], {})
        return baked, time.monotonic() - started

    def test_a_share_whose_first_answer_is_slower_than_its_moment_is_waited_for_and_its_photos_baked(self):
        with self.share(lambda n: 0.3 if n == 0 else 0):
            baked, _took = self.bake()
        self.assertEqual((2, 0, 0), (baked.written, baked.not_answered, sum(baked.cannot.values())), baked.said())
        self.assertFalse(baked.stalled)

    def test_a_share_that_answers_late_is_asked_again_when_its_look_ends_not_when_the_away_window_does(self):
        # The first answer takes 0.3 s against a moment of 0.1 s and an away window of 6 s: the bake asks again at 0.3 s.
        with mock.patch.object(shares, "AWAY_SECONDS", 6.0), self.share(lambda n: 0.3 if n == 0 else 0):
            baked, took = self.bake()
        self.assertEqual((2, 0, 0), (baked.written, baked.not_answered, sum(baked.cannot.values())), baked.said())
        self.assertLess(took, 3.0, "the look's own length, not the 6 s window the share was taken as away for")

    def test_a_share_that_stays_away_is_counted_not_answered_after_one_bounded_wait_and_its_files_are_untouched(self):
        with self.share(lambda n: 5.0):
            baked, took = self.bake()
        self.assertEqual((0, 2, 0), (baked.written, baked.not_answered, sum(baked.cannot.values())), baked.said())
        self.assertIn("2 not answered (a share away)", baked.said())
        self.assertNotIn("unreadable", baked.said())
        self.assertLess(took, 3.0, "one wait of the away and one moment, not one for each photo")
        self.asleep.set()
        self.assertEqual(self.stamps, {path: os.stat(path).st_mtime_ns for path in self.photos})


class AShareThatHangsAgain(unittest.TestCase):
    """A share that hung, was let alone for its away window, and hangs again is away again from the new look: the pages
    that ask of it next are answered at once, not each held for a full moment on the thread that still hangs."""

    def test_after_its_window_ran_out_one_more_wait_then_every_ask_is_answered_at_once(self):
        photo = "//server/share/a.jpg"
        release = threading.Event()
        self.addCleanup(release.set)
        self.addCleanup(shares.forget)
        shares.forget()
        real = os.stat

        def stat(path, *args, **kwargs):
            if str(path) == photo:
                release.wait(30)
            return real(path, *args, **kwargs)
        with mock.patch.object(shares, "SHARE_WAIT", 0.2), mock.patch.object(shares, "AWAY_SECONDS", 0.5), \
                mock.patch.object(os, "stat", stat):
            self.assertIs(shares.UNANSWERED, shares.stamp_of(photo))
            time.sleep(0.55)   # the first mark has expired; the look still hangs
            self.assertIs(shares.UNANSWERED, shares.stamp_of(photo))
            for _ in range(3):
                started = time.monotonic()
                self.assertIs(shares.UNANSWERED, shares.stamp_of(photo))
                self.assertLess(time.monotonic() - started, 0.1, "answered from the new mark, not joined for a moment")


if __name__ == "__main__":
    unittest.main()
