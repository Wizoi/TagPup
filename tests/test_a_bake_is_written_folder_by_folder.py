"""The owner bakes a library's photos (`bake-photos --apply`; scenario F3, tagpup.services.baking_report.apply;
docs/PHASE6_DESIGN.md, section 9): each folder's photos to bake are one change in History, written through the one
writer, after one snapshot of the library; a second run writes nothing.

How it fails, each lived through: the process killed in the middle of a folder, and run again, which says it is finishing
the change the stopped run left and goes on with the photos not yet baked, or says it is left; a Ctrl+C, whose change keeps
what was written and is counted, also once the write is over; a share that stops answering in a folder's read (part-way
too: nothing of that folder is written), between its plan and its write, or in its write, which ends the run at that
folder, counts its photos not written as not answered and leaves the folders after it unread; a second run at once that
bakes a photo first, found current; a folder copied from one already baked, whose copies are current and not written
again; a PNG; and a photo turned in the app between its plan and its write, whose bake is written all the same and is
told apart from its picture by its fingerprint (#1925). From the CLI (`bake-photos --apply`): each folder said as it is
written, its change named, the totals, the snapshot and the way back last; the library brought up to date first (the dry
run never migrates); a stop after a folder, and a photo that failed (why, only with --reveal), each exit status 1.

Real ExifTool, on pictures made here, in a home of the test's own; rows as the indexer stores them (ALibraryToBake).
"""
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_bake_is_rehearsed_folder_by_folder import DETECTOR, ALibraryToBake  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402

import baked_files  # noqa: E402
import migration_names  # noqa: E402
import photo_rows  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.files import exiftool_session, field_values, images  # noqa: E402
import tagpup_cli  # noqa: E402
from tagpup.services import baking, baking_report, file_changes  # noqa: E402
from tagpup.services import photos as photo_service  # noqa: E402
from tagpup.store import db, file_journal, photo_fingerprints, snapshots  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402


class ABakeIsWrittenFolderByFolder(ALibraryToBake):

    def make_read(self, name, tags=("Beach",)):
        """A JPEG as the base makes it; a PNG; or a file copied here already, its row as the indexer leaves it."""
        path = os.path.join(self.folder, name)
        if not name.endswith(".png") and not os.path.exists(path):
            return super().make_read(name, tags)
        if not os.path.exists(path):
            from PIL import Image
            Image.new("RGB", (16, 12), (90, 110, 130)).save(path, "PNG")
        conn = db.connect(self.library.path)
        try:
            photo_rows.add_read(conn, path, {"XMP:Subject": list(tags)} if name.endswith(".jpg") else {})
            conn.commit()
        finally:
            conn.close()
        return path

    def apply(self, **kwargs):
        said = []
        run = baking_report.apply(self.library, baked_files.EXIFTOOL, DETECTOR, told=said.append, **kwargs)
        return run, said

    def plan_of(self, path):
        return baked_files.plan(self.library.path, path, DETECTOR)

    def changes(self):
        return [(operation, summary) for operation, summary in
                self.rows("SELECT operation, summary FROM changes WHERE operation = ? ORDER BY id", (baking.OPERATION,))]

    def dailies(self):
        return len(snapshots.listed(self.library.path, snapshots.DAILY))

    def fingerprint_row(self, path):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return photo_fingerprints.get(conn, store_roots.photo_ids_at(conn, [path])[0])
        finally:
            conn.close()

    def three_to_bake(self, folder):
        os.makedirs(os.path.join(self.folder, folder), exist_ok=True)
        return [self.photo(os.path.join(folder, name), faces=1) for name in ("x.jpg", "y.jpg", "z.jpg")]

    def test_each_folder_is_one_change_after_one_snapshot_and_a_second_run_writes_nothing(self):
        self.a_mix()
        run, said = self.apply()
        self.assertEqual((None, None, False, None), (run.refused, run.stalled, run.stopped, run.interrupted))
        first, second = run.folders
        self.assertEqual((6, 1, 1, 1, 4), (first.photos, first.to_bake, first.written, first.current, sum(first.cannot.values())))
        self.assertEqual((1, 1, 1), (second.photos, second.to_bake, second.written))
        self.assertEqual(([], []), (run.total.conflicts, run.total.failed))
        self.assertEqual(2, len(self.changes()), "one change a folder that had a photo to bake")
        self.assertEqual(1, self.dailies(), "one snapshot, before the first write")
        self.assertEqual(1, sum("Took a snapshot" in line for line in said), said)
        for path in (os.path.join(self.folder, "a.jpg"), os.path.join(self.folder, "Second", "many.jpg")):
            self.assertIsInstance(self.plan_of(path), baking.Current, "the file holds what its library says, decode and all")
            held, row = baked_files.held(path), self.fingerprint_row(path)
            self.assertEqual((held.fingerprint_key, held.fingerprint), (row.key, bytes(row.hash)),
                             "the write leaves the fingerprint row of what it wrote")

        again, said = self.apply()
        self.assertEqual((0, 3), (again.total.written, again.total.current))
        self.assertEqual(2, len(self.changes()), "nothing written, nothing recorded")
        self.assertEqual(1, self.dailies())
        self.assertEqual([], said, "no change to finish, no snapshot for a run that writes nothing")

    def test_a_run_killed_in_a_folder_is_finished_by_the_next_which_says_so(self):
        baked = self.three_to_bake("Trip")
        later = self.three_to_bake("Uphill")
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written", nth=2)):
            with self.assertRaises(Crash):
                self.apply()
        change = self.last_change()
        self.assertEqual([change], [each.id for each in file_journal.unfinished(self.library.path)])

        run, said = self.apply()
        self.assertIn("Finishing change %d (%s), left unfinished by a run that stopped." % (change, baking.OPERATION), said)
        self.assertIn("Change %d is finished." % change, said)
        trip, uphill = run.folders
        self.assertEqual((0, 3), (trip.written, trip.current), "settled forward: nothing left to write, nothing decoded")
        self.assertEqual(3, uphill.written)
        self.assertEqual([], file_journal.unfinished(self.library.path))
        for path in baked + later:
            self.assertIsInstance(self.plan_of(path), baking.Current)
        self.assertEqual(1, self.dailies(), "today's snapshot stands for the second run")
        self.assertTrue(any("Today's snapshot" in line for line in said), said)

    def test_a_ctrl_c_keeps_what_was_written_and_counts_it(self):
        self.three_to_bake("Trip")
        self.three_to_bake("Uphill")

        seen = []

        def interrupt(step):
            if step == "file written":
                seen.append(step)
                if len(seen) == 2:
                    raise KeyboardInterrupt
        with mock.patch.object(file_changes, "_reached", side_effect=interrupt):
            run, _said = self.apply()
        change = self.last_change()
        self.assertEqual(change, run.interrupted["change"])
        self.assertEqual([2], [folder.written for folder in run.folders], "the folder under way, as the journal holds it")
        self.assertEqual(2, run.interrupted["written"])
        self.assertEqual([], file_journal.unfinished(self.library.path), "applied, undoable, nothing left to settle")
        self.assertEqual(1, len(self.changes()), "the folder after it is not reached")

    def test_a_ctrl_c_once_the_folders_write_is_over_counts_what_the_journal_holds(self):
        self.three_to_bake("Trip")
        self.three_to_bake("Uphill")
        real_write, real_files_of, armed = file_changes.write_fields, file_journal.files_of, []

        def write(*args, **kwargs):
            result = real_write(*args, **kwargs)
            armed.append(True)
            return result

        def files_of(*args, **kwargs):
            if armed:
                armed.clear()
                raise KeyboardInterrupt      # while the run reads the journal for the folder's counts
            return real_files_of(*args, **kwargs)
        with mock.patch.object(file_changes, "write_fields", side_effect=write), \
                mock.patch.object(file_journal, "files_of", side_effect=files_of):
            run, _said = self.apply()
        change = self.last_change()
        self.assertEqual((change, 3), (run.interrupted["change"], run.interrupted["written"]))
        self.assertEqual([(change, 3)], [(folder.change, folder.written) for folder in run.folders])
        self.assertEqual(1, len(self.changes()), "the folder after it is not reached")

    def test_a_ctrl_c_while_a_folder_is_said_keeps_its_change_and_counts_it_once(self):
        self.three_to_bake("Trip")
        self.three_to_bake("Uphill")

        def heard(baked, number, folders):
            raise KeyboardInterrupt      # the second Ctrl+C, while the CLI says the folder's lines (#1944)
        run, _said = self.apply(heard=heard)
        change = self.last_change()
        self.assertEqual((change, 3), (run.interrupted["change"], run.interrupted["written"]))
        self.assertEqual([(change, 3)], [(folder.change, folder.written) for folder in run.folders])
        self.assertEqual(3, run.total.written, "counted once")
        self.assertEqual(1, len(self.changes()), "the folder after it is not reached")

    def ctrl_c_in_the_second_folders_read(self):
        """A context: the second Ctrl+C while the Walk reads and plans the folder after a folder written."""
        real, asked = baking_report.Walk._folder, []

        def folder(walk, group):
            asked.append(group)
            if len(asked) == 2:
                raise KeyboardInterrupt
            return real(walk, group)
        return mock.patch.object(baking_report.Walk, "_folder", folder)

    def test_a_ctrl_c_while_the_next_folder_is_read_keeps_the_folder_before_and_counts_it(self):
        self.three_to_bake("Trip")
        self.three_to_bake("Uphill")
        with self.ctrl_c_in_the_second_folders_read():
            run, _said = self.apply()
        change = self.last_change()
        self.assertEqual({"change": None, "written": None, "left": None}, run.interrupted,
                         "stopped before that folder's write: nothing of it under way")
        self.assertEqual([(change, 3)], [(folder.change, folder.written) for folder in run.folders])
        self.assertEqual(3, run.total.written, "the folder before, counted once")

    def test_from_the_cli_a_ctrl_c_while_the_next_folder_is_read_says_the_totals_and_the_way_back(self):
        self.three_to_bake("Trip")
        self.three_to_bake("Uphill")
        with self.ctrl_c_in_the_second_folders_read():
            stopped = self.cli("--apply")
        out = stopped.output
        self.assertEqual(1, stopped.exit_code, out)
        self.assertIsNone(stopped.exception if not isinstance(stopped.exception, SystemExit) else None, out)
        self.assertIn("Written: 3 file(s) in 1 change(s) (%d)." % self.last_change(), out)
        self.assertIn("Stopped at once, before that folder was written. Run it again to go on.", out)
        self.assertEqual(tagpup_cli.BAKE_WAY_BACK, out.splitlines()[-1])

    def test_a_run_that_cannot_finish_a_stopped_runs_change_says_it_is_left(self):
        self.three_to_bake("Trip")
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written", nth=2)):
            with self.assertRaises(Crash):
                self.apply()
        change, said = self.last_change(), []
        with mock.patch.object(file_changes, "settle"):     # its finishing raised, and was logged
            baking_report._settle(self.library, baked_files.EXIFTOOL, said.append)
        self.assertEqual(["Finishing change %d (%s), left unfinished by a run that stopped." % (change, baking.OPERATION),
                          "Change %d is left unfinished; the log says why, and the next run tries again." % change], said)

    def test_a_share_that_stops_answering_in_a_folders_read_ends_the_run_there(self):
        self.three_to_bake("Trip")
        self.three_to_bake("Uphill")
        self.three_to_bake("Valley")
        real, asked = field_values.read, []

        def read(et, photo_paths, *args, **kwargs):
            asked.append(os.path.basename(os.path.dirname(photo_paths[0])))
            if asked[-1] == "Uphill":
                return {paths.key(one): field_values.NotAnswered("ExifTool did not answer in 300 s") for one in photo_paths}
            return real(et, photo_paths, *args, **kwargs)
        with mock.patch.object(baking_report.field_values, "read", side_effect=read):
            run, _said = self.apply()
        self.assertEqual(os.path.join(self.folder, "Uphill"), run.stalled)
        self.assertNotIn("Valley", asked, "the folder after it is not read")
        self.assertEqual([(3, 0), (0, 3)], [(folder.written, folder.not_answered) for folder in run.folders])
        self.assertEqual(1, len(self.changes()))

    def test_a_share_that_stops_answering_part_way_through_a_folders_read_writes_nothing_of_it(self):
        self.three_to_bake("Trip")
        self.three_to_bake("Uphill")
        real = field_values.read

        def read(et, photo_paths, *args, **kwargs):
            if os.path.basename(os.path.dirname(photo_paths[0])) != "Uphill":
                return real(et, photo_paths, *args, **kwargs)
            found = real(et, photo_paths[:1], *args, **kwargs)     # its first batch answered, then the share went
            found.update({paths.key(one): field_values.NotAnswered("ExifTool did not answer in 300 s")
                          for one in photo_paths[1:]})
            return found
        with mock.patch.object(baking_report.field_values, "read", side_effect=read):
            run, _said = self.apply()
        self.assertEqual(os.path.join(self.folder, "Uphill"), run.stalled)
        trip, uphill = run.folders
        self.assertEqual((0, 0, 0, None, 3), (uphill.photos, uphill.to_bake, uphill.written, uphill.change,
                                              uphill.not_answered),
                         "the photo answered and not written is not answered for, as a write stall's (#1943)")
        self.assertEqual({}, dict(uphill.formats))
        self.assertEqual(1, len(self.changes()), "nothing of the folder whose read stalled is written")

    def share_stops_before_the_write(self, stopping):
        """Trip and Uphill to bake, the share stopping (`stopping`, a context) once Trip is planned: the write's own read of
        Trip is not answered."""
        self.three_to_bake("Trip")
        self.three_to_bake("Uphill")
        with stopping:
            run, _said = self.apply()
        self.assertEqual(os.path.join(self.folder, "Trip"), run.stalled, "the folder the share stopped in, not the next")
        self.assertEqual([("Trip", 0, 3)], [(os.path.basename(folder.folder), folder.written, folder.not_answered)
                                            for folder in run.folders], "Uphill is not reached")
        self.assertEqual(([], 0, None), (run.total.failed, run.total.to_bake, run.refused),
                         "not answered for, not failed: nothing is known of the files")
        self.assertEqual([], self.changes(), "nothing written, nothing recorded")

    def test_a_share_that_stops_answering_between_a_folders_plan_and_its_write_ends_the_run_there(self):
        real, trips = exiftool_session.ExifToolSession.get_tags, []

        def get_tags(session, files, *args, **kwargs):
            if os.path.basename(os.path.dirname(files[0])) == "Trip":
                trips.append(files)
                if len(trips) == 2:      # the write's own read: the session times out and stays dead
                    raise exiftool_session.ExifToolTimeout("ExifTool did not answer in 300 s")
            return real(session, files, *args, **kwargs)
        self.share_stops_before_the_write(mock.patch.object(exiftool_session.ExifToolSession, "get_tags", get_tags))
        self.assertEqual(2, len(trips))

    def test_a_share_found_away_at_a_folders_write_ends_the_run_there(self):
        self.share_stops_before_the_write(mock.patch.object(
            file_changes, "file_states", side_effect=lambda photo_paths: [(path, file_changes.AWAY) for path in photo_paths]))

    def test_a_photo_another_run_bakes_first_is_current(self):
        self.photo("harbour.jpg", faces=1)
        real = file_changes.write_fields

        def another_run_first(*args, **kwargs):
            real(*args, **kwargs)        # a second CLI window's bake of the folder, just before this one's
            return real(*args, **kwargs)
        with mock.patch.object(file_changes, "write_fields", side_effect=another_run_first):
            run, _said = self.apply()
        self.assertEqual((0, 0, 1), (run.total.to_bake, run.total.written, run.total.current))
        self.assertEqual({}, dict(run.total.formats), "no format line of nothing to bake")

    def test_a_share_that_stops_answering_in_a_folders_write_ends_the_run_there(self):
        self.three_to_bake("Trip")
        uphill = self.three_to_bake("Uphill")
        self.three_to_bake("Valley")
        real = field_values.read_one

        def read_one(et, path, *args, **kwargs):
            if paths.key(path) == paths.key(uphill[1]):
                raise field_values.NotAnswered("ExifTool did not answer in 300 s")
            return real(et, path, *args, **kwargs)
        with mock.patch.object(file_changes.field_values, "read_one", side_effect=read_one):
            run, _said = self.apply()
        self.assertEqual(os.path.join(self.folder, "Uphill"), run.stalled)
        self.assertIn("ExifTool stopped answering after 1 of 3 photos", run.refused)
        self.assertEqual([(3, 0), (1, 2)], [(folder.written, folder.not_answered) for folder in run.folders],
                         "Valley is not reached; the two of Uphill not written were not answered for")
        self.assertEqual([], run.total.failed)
        self.assertEqual(2, len(self.changes()))
        self.assertEqual([], file_journal.unfinished(self.library.path), "the photo not answered for was not under way")

    def test_a_photo_that_failed_before_the_share_stopped_keeps_its_sentence(self):
        trip = self.three_to_bake("Trip")
        real_write, real_read = field_values.write, field_values.read_one

        def write(et, path, *args, **kwargs):
            if paths.key(path) == paths.key(trip[0]):
                raise field_values.Unreadable("Error: the file is read-only")    # ExifTool's own answer
            return real_write(et, path, *args, **kwargs)

        def read_one(et, path, *args, **kwargs):
            if paths.key(path) == paths.key(trip[1]):
                raise field_values.NotAnswered("ExifTool did not answer in 300 s")
            return real_read(et, path, *args, **kwargs)
        with mock.patch.object(file_changes.field_values, "write", side_effect=write), \
                mock.patch.object(file_changes.field_values, "read_one", side_effect=read_one):
            run, _said = self.apply()
        self.assertEqual(os.path.join(self.folder, "Trip"), run.stalled)
        (folder,) = run.folders
        ids = {paths.key(path): photo_id for photo_id, path in self.rows("SELECT id, path FROM photos")}
        self.assertEqual([ids[paths.key(trip[0])]], [photo_id for photo_id, _why in folder.failed],
                         "answered with its own error: failed, its sentence kept (#1942)")
        self.assertIn("read-only", folder.failed[0][1])
        self.assertEqual((0, 2, 1, 1), (folder.written, folder.not_answered, folder.to_bake, folder.photos))

    def test_from_the_cli_each_folder_is_said_as_it_is_written_and_the_way_back_last(self):
        self.a_mix()
        with migration_names.ahead():
            dry = self.cli("--verbose")
            self.assertEqual(0, dry.exit_code, dry.output)
            self.assertNotIn("Brought the library up to date", dry.output, "the dry run never migrates")
            applied = self.cli("--apply", "--verbose")
            again = self.cli("--apply", "--verbose")
        out = applied.output
        self.assertEqual(1, applied.exit_code, "a photo whose file could not be read is not a success: " + out)
        self.assertIn("Brought the library up to date", out)
        first, second = [change for change in self.rows(
            "SELECT id FROM changes WHERE operation = ? ORDER BY id", (baking.OPERATION,))]
        self.assertIn("Folder 1 of 2:\n  6 photo(s): 1 to bake, 1 already current", out)
        self.assertIn("  Written: 1 file(s), change %d.\nFolder 2 of 2:" % first[0], out)
        total = out[out.index("All 2 folder(s):"):]
        self.assertIn("  Written: 2 file(s) in 2 change(s) (%d, %d)." % (first[0], second[0]), total)
        self.assertIn("  Snapshot before the first write: taken (", total)
        self.assertEqual(tagpup_cli.BAKE_WAY_BACK, out.splitlines()[-1], "the way back last (#1926)")
        self.assertNotIn(self.folder, out, "counts only without --reveal")

        self.assertEqual(1, again.exit_code, again.output)
        self.assertIn("Written: 0 file(s) in 0 change(s).", again.output)
        self.assertNotIn("undo <change>", again.output, "no way back of nothing written")

    def test_from_the_cli_a_run_is_quiet_by_default_and_says_each_reason_with_what_to_do(self):
        """Owner, 2026-10-27: a folder with nothing to say is not said; one that wrote or refused gets one line; --verbose
        restores each folder's block; the closing summary says each reason on its own line with what to do about it."""
        self.a_mix()
        dry = self.cli()
        self.assertEqual(0, dry.exit_code, dry.output)
        self.assertIn("Folder 1 of 2: 1 to bake, 4 cannot be baked\n", dry.output)
        self.assertIn("Folder 2 of 2: 1 to bake\n", dry.output)
        applied = self.cli("--apply")
        first, second = [change[0] for change in self.rows(
            "SELECT id FROM changes WHERE operation = ? ORDER BY id", (baking.OPERATION,))]
        out = applied.output
        self.assertIn("Folder 1 of 2: 1 baked, change %d, 4 cannot be baked\n" % first, out)
        self.assertIn("Folder 2 of 2: 1 baked, change %d\n" % second, out)
        for block in ("Folder 1 of 2:\n", "photo(s): 1 to bake", "Written: 1 file(s), change"):
            self.assertNotIn(block, out.split("All 2 folder(s)")[0], "no per-folder block without --verbose")
        total = out[out.index("All 2 folder(s):"):]
        for said in ("1 never examined by the face model: run face-pass on its folder first",
                     "1 with a face holding no vector under the face model: run face-pass on its folder",
                     "1 with no photo vector of its picture as it is now under the photo model: index its folder (sync queues it)",
                     "1 whose file could not be reached or read: the file or the share did not answer: run again when it does"):
            self.assertIn("\n  Cannot be baked: %s.\n" % said, total)
        again = self.cli("--apply")
        self.assertIn("Folder 1 of 2: 4 cannot be baked\n", again.output)
        self.assertNotIn("Folder 2 of 2", again.output, "a folder with nothing to bake or refuse is not said")
        self.assertIn("1 whose file could not be reached or read", again.output.split("All ")[1])
        verbose = self.cli("--apply", "--verbose")
        self.assertIn("Folder 1 of 2:\n  6 photo(s): 0 to bake, 2 already current", verbose.output)
        self.assertIn("  Written: 0 file(s).\nFolder 2 of 2:", verbose.output)

    def test_from_the_cli_a_run_that_could_not_reach_a_file_exits_1(self):
        """kr-track's run (2026-10-26): every file unreadable, "Cannot be baked: 1634 whose file could not be read", and
        exit 0, which reads as success. An unreachable file is not."""
        broken = self.photo("broken.jpg", faces=1)
        stat = os.stat(broken)
        with open(broken, "wb") as handle:
            handle.write(b"x" * stat.st_size)
        os.utime(broken, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        run = self.cli("--apply")
        self.assertIn("1 whose file could not be reached or read", run.output)
        self.assertEqual(1, run.exit_code, run.output)
        self.assertEqual(0, len(self.changes()))

    def test_from_the_cli_a_stop_after_a_folder_keeps_it_and_exits_1(self):
        self.three_to_bake("Trip")
        self.three_to_bake("Uphill")
        real = baking_report.apply

        def first_ctrl_c(*args, heard, stop, **kwargs):
            def said(baked, number, folders):
                heard(baked, number, folders)
                stop.set()                   # Ctrl+C while the first folder is written: it stops after it
            return real(*args, heard=said, stop=stop, **kwargs)
        with mock.patch.object(baking_report, "apply", side_effect=first_ctrl_c):
            stopped = self.cli("--apply")
        self.assertEqual(1, stopped.exit_code, stopped.output)
        self.assertIn("Stopped as asked: what was written stays written and undoable", stopped.output)
        self.assertNotIn("Folder 2 of 2", stopped.output)
        self.assertEqual(1, len(self.changes()))

    def test_from_the_cli_a_photo_that_failed_is_named_by_id_and_why_only_with_reveal(self):
        trip = self.three_to_bake("Trip")
        real = field_values.write
        photo_id = dict((paths.key(path), found) for found, path in self.rows("SELECT id, path FROM photos"))[
            paths.key(trip[0])]

        def write(et, path, *args, **kwargs):
            if paths.key(path) == paths.key(trip[0]):
                raise field_values.Unreadable("Error: the file is read-only")
            return real(et, path, *args, **kwargs)
        with mock.patch.object(file_changes.field_values, "write", side_effect=write):
            failed = self.cli("--apply")
        self.assertEqual(1, failed.exit_code, failed.output)
        self.assertIn("Failed: 1 photo(s) not written (photo ids %d); --reveal says why." % photo_id, failed.output)
        self.assertNotIn("read-only", failed.output)
        self.assertIn("Folder 1 of 1: 2 baked, change", failed.output)
        self.assertIn("1 failed", failed.output)
        with mock.patch.object(file_changes.field_values, "write", side_effect=write):
            revealed = self.cli("--apply", "--reveal")
        self.assertIn("    photo %d: Error: the file is read-only" % photo_id, revealed.output)

    def test_a_folder_copied_from_a_baked_one_is_current_and_not_written_again(self):
        self.three_to_bake("Trip")
        self.apply()
        shutil.copytree(os.path.join(self.folder, "Trip"), os.path.join(self.folder, "Copy"))
        copies = [self.photo(os.path.join("Copy", name), faces=1) for name in ("x.jpg", "y.jpg", "z.jpg")]
        run, _said = self.apply()
        self.assertEqual([(0, 3), (0, 3)], [(folder.written, folder.current) for folder in run.folders])
        self.assertEqual(1, len(self.changes()))
        self.assertIsNone(self.fingerprint_row(copies[0]),
                          "a copy found current is not decoded, so its fingerprint row waits for its index")

    def test_a_png_is_baked(self):
        path = self.photo("harbour.png", faces=1)
        run, _said = self.apply()
        self.assertEqual((1, [], []), (run.total.written, run.total.conflicts, run.total.failed))
        self.assertEqual({".png": 1}, dict(run.total.formats))
        self.assertIsInstance(self.plan_of(path), baking.Current)

    def test_a_photo_turned_between_its_plan_and_its_write_is_baked_and_told_apart_by_its_fingerprint(self):
        turned = self.photo("turned.jpg", faces=1)
        before = images.fingerprint(images.opened(turned, upright=True))
        real = file_changes.write_fields

        def turn_first(library, operation, *args, **kwargs):
            if operation == baking.OPERATION:
                # The owner clicks Rotate in the app while the bake has planned the folder: the turn's own write first.
                turn = photo_service.rotate(library, turned, "left", baked_files.EXIFTOOL)
                self.assertEqual((None, []), (turn.refused, turn.errors))
            return real(library, operation, *args, **kwargs)
        with mock.patch.object(file_changes, "write_fields", side_effect=turn_first):
            run, _said = self.apply()
        self.assertEqual((1, []), (run.total.written, run.total.conflicts),
                         "the turn wrote no bake field: no conflict (#1925, no branch)")
        held = baked_files.held(turned)
        now = images.fingerprint(images.opened(turned, upright=True))
        self.assertNotEqual(now, before)
        self.assertEqual(before, held.fingerprint, "the bake describes the picture as planned...")
        self.assertNotEqual(now, held.fingerprint, "...which its decode now tells apart: the read side rejects it")
        self.assertEqual(before, bytes(self.fingerprint_row(turned).hash),
                         "the fingerprint row of the old decode, until the photo's next index (pending finding)")


if __name__ == "__main__":
    unittest.main()
