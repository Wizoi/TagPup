"""A root moved, or the library's roots changed, while a long Undo or a settle of a change cut off is under way
(tagpup.services.file_changes: undo, _finish; tagpup.services.roots.pinned; #3395).

The owner undoes a bulk change of sixty photos (three chunks), or opens a library whose sixty-photo change a crash cut
off (a tagging, or a Smart Rename of the sixty), and meanwhile the place of its root on this machine is moved by hand in machine_roots.json (the old place not kept,
as TagTuner's Roots keeps it): the run puts every file back, or settles it, where it began, and every photo's row follows
its file, spelled by the root -- never half its rows by the old place and half by the new, where a file at the old place
is under no root and its row is not found -- and the next run takes the new place. When another process changes the
library's own roots part-way, the Undo stops, is refused saying how many files it put back, and is left as a crash leaves
it: the next settle puts the others back. A root this machine does not place (machine_roots.json without it) refuses an
Undo in words, nothing written, where it raised before.

A held connection looks at the map at each use here (store.roots.RECHECK_SECONDS 0), so a move is taken at once by any
run that does not hold its map: each test fails on the code before the pin, where undo and settle held none. Sixty real
JPEGs written by the real ExifTool in a home of the test's own; their rows as the indexer records a read
(tests/roots_library.py); the move is made at the thirtieth file's step (file_changes._reached), or, for a rename, as its files are renamed
again (names.rename_all).
"""
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.files import names  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import file_changes, journal, tagging  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.store import db, file_journal  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

WINDOWS = os.name == "nt"

#: Real photos in each of the side's three folders: sixty, three chunks of batches.CHUNK.
EACH = 20


@unittest.skipUnless(WINDOWS and rl.EXIFTOOL, "Windows paths and the real ExifTool")
class ALongRun(unittest.TestCase):
    """Sixty photos under one root, tagged Harbour as one change."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_moved_mid_run_")
        self.side = rl.Side(self.home, "photo_index", real=EACH, bulk=0, outside=0)
        self.assertTrue(self.side.adopt().ok)
        self.library = self.side.library
        self.photos = self.side.real
        self.assertEqual(60, len(self.photos))
        self.copy = os.path.join(self.home.root, "Copy", "Pictures")
        # Each test is a fresh process: no settle run yet, none put off.
        for kept in (file_changes._settled, file_changes._put_off, file_changes._unsettled):
            saved = kept.copy()
            kept.clear()
            self.addCleanup(kept.update, saved)
        recheck = mock.patch.object(store_roots, "RECHECK_SECONDS", 0)
        recheck.start()
        self.addCleanup(recheck.stop)

    def copy_the_photos(self):
        """The owner's copy of the photos at another place, which the root is moved to part-way."""
        shutil.copytree(self.side.pictures, self.copy, copy_function=shutil.copy2)
        return {path: self.bytes_of(path) for path in self.copies()}

    def copies(self):
        return [os.path.join(self.copy, os.path.relpath(path, self.side.pictures)) for path in self.photos]

    @staticmethod
    def bytes_of(path):
        with open(path, "rb") as handle:
            return handle.read()

    def holding_harbour(self, photo_paths):
        """How many of `photo_paths` hold Harbour, read in one ExifTool session."""
        with ExifToolSession(executable=rl.EXIFTOOL) as et:
            found = et.get_tags(photo_paths, tags=["XMP:Subject"])
        return sum(1 for each in found if "Harbour" in (each.get("XMP:Subject") or []))

    def at_the_thirtieth_file(self, do):
        """A stand-in for file_changes._reached that calls `do()` once the thirtieth file of the run is recorded."""
        recorded = []

        def reached(name):
            if name == "file recorded":
                recorded.append(name)
                if len(recorded) == 30:
                    do()
        return reached

    def move_the_map(self):
        """The owner's hand edit of machine_roots.json: the root at the copy's place, the old place gone."""
        with open(config.machine_roots_path(), "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "roots": {rl.NAME: [self.copy]}}, handle)

    def rows_holding_harbour(self):
        """(how many photo rows say Harbour, how many rows are not spelled by the root)."""
        rows = self.side.rows("SELECT path, tags FROM photos")
        return (sum(1 for _path, tags in rows if "Harbour" in json.loads(tags or "[]")),
                sum(1 for path, _tags in rows if not path.startswith("@")))

    def place_seen_by_a_new_connection(self):
        conn = db.connect(self.side.db_path)
        try:
            return store_roots.roots_for(conn).locations["pictures"][0]
        finally:
            conn.close()

    def tag_them_all(self):
        result = tagging.change_tags(self.library, self.photos, ["Harbour"], [], rl.EXIFTOOL)
        self.assertEqual(60, result.changed, result.errors)
        return result.details["change"]

    def undo(self, change):
        return journal.undo(self.library, change, apply=True, exiftool_path=rl.EXIFTOOL)


class AnUndo(ALongRun):
    def test_a_root_moved_mid_undo_is_not_taken_by_it_and_the_next_run_takes_it(self):
        change = self.tag_them_all()
        copied = self.copy_the_photos()
        with mock.patch.object(file_changes, "_reached", side_effect=self.at_the_thirtieth_file(self.move_the_map)):
            result = self.undo(change)
        self.assertIsNone(result.refused, result.refused)
        self.assertEqual(60, result.changed, result.errors)
        self.assertEqual(0, self.holding_harbour(self.photos), "a photo was left holding what the change wrote")
        self.assertEqual((0, 0), self.rows_holding_harbour(), "a row did not follow its file, by the root")
        self.assertEqual(copied, {path: self.bytes_of(path) for path in self.copies()},
                         "the undo wrote the copies at the place the root was moved to part-way")
        self.assertEqual("undone", file_journal.change(self.library.path, change).status)
        self.assertEqual(self.copy, self.place_seen_by_a_new_connection(), "the next run does not take the new place")

    def test_the_roots_changed_by_another_process_stop_it_saying_so_and_the_next_settle_puts_the_rest_back(self):
        change = self.tag_them_all()

        def another_process_changes_the_roots():
            conn = db.connect(self.side.db_path)
            try:
                conn.execute("UPDATE roots SET address = ? WHERE name = ?", (rl.ADDRESS + "\\Moved", rl.NAME))
                conn.commit()
            finally:
                conn.close()

        with mock.patch.object(file_changes, "_reached",
                               side_effect=self.at_the_thirtieth_file(another_process_changes_the_roots)):
            result = self.undo(change)
        self.assertTrue(result.refused and result.refused.startswith(roots_service.STOPPED), result.refused)
        states = [row.state for row in file_journal.files_of(self.library.path, change)]
        self.assertGreaterEqual(result.changed, 30)
        self.assertLess(result.changed, 60)
        self.assertIn("Before it stopped at least %d file(s) were put back" % result.changed, result.refused)
        self.assertEqual(result.changed, states.count("undone"))
        # The file under way when the stop came is put back and left `writing`, as a crash leaves it.
        self.assertLessEqual(states.count("writing"), 1)
        self.assertEqual(60 - result.changed - states.count("writing"), self.holding_harbour(self.photos))
        # Left as a crash leaves an undo: released, still being undone, for the next settle.
        self.assertIsNone(file_journal.change(self.library.path, change).owner)

        self.assertEqual(1, file_changes.settle(self.library, rl.EXIFTOOL))
        self.assertEqual(0, self.holding_harbour(self.photos))
        self.assertEqual("undone", file_journal.change(self.library.path, change).status)

    def test_a_root_this_machine_does_not_place_refuses_it_in_words_and_writes_nothing(self):
        change = self.tag_them_all()
        os.remove(config.machine_roots_path())
        result = self.undo(change)
        self.assertIn("machine_roots.json", result.refused or "")
        self.assertEqual(0, result.changed)
        self.assertEqual(60, self.holding_harbour(self.photos))
        self.assertEqual("applied", file_journal.change(self.library.path, change).status)


class ASettle(ALongRun):
    def cut_off(self, run=None, step="file written", nth=3):
        """The sixty-photo change killed at `step`'s `nth` time (by default as the third file is written, before its row:
        two done, one written, fifty-seven never reached); its process gone. Its id."""
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at(step, nth)):
            with self.assertRaises(Crash):
                if run:
                    run()
                else:
                    tagging.change_tags(self.library, self.photos, ["Harbour"], [], rl.EXIFTOOL)
        change = max(each.id for each in file_journal.unfinished(self.library.path))
        conn = db.connect(self.side.db_path)
        try:
            conn.execute("UPDATE changes SET owner = NULL WHERE id = ?", (change,))
            conn.commit()
        finally:
            conn.close()
        return change

    def test_a_root_moved_mid_settle_is_not_taken_by_it_and_the_next_run_takes_it(self):
        change = self.cut_off()
        copied = self.copy_the_photos()
        with mock.patch.object(file_changes, "_reached", side_effect=self.at_the_thirtieth_file(self.move_the_map)):
            self.assertEqual(1, file_changes.settle(self.library, rl.EXIFTOOL))
        self.assertEqual(60, self.holding_harbour(self.photos), "a photo of the change was left unwritten")
        self.assertEqual((60, 0), self.rows_holding_harbour(), "a row did not follow its file, by the root")
        self.assertEqual(copied, {path: self.bytes_of(path) for path in self.copies()},
                         "the settle wrote the copies at the place the root was moved to part-way")
        self.assertEqual("applied", file_journal.change(self.library.path, change).status)
        self.assertEqual(self.copy, self.place_seen_by_a_new_connection(), "the next run does not take the new place")

    def test_a_root_moved_mid_settle_of_a_rename_is_not_taken_by_it_and_the_next_run_takes_it(self):
        """A Smart Rename of the sixty killed once its files are marked being renamed, before any is: the settle renames
        them again and moves their rows, and the map moved as it renames them is not taken by it, so every row follows its
        file by the root (by the moved map the old place is under no root, and no row would be found to move)."""
        renamed = {path: os.path.join(os.path.dirname(path), "Renamed_" + os.path.basename(path)) for path in self.photos}
        change = self.cut_off(lambda: file_changes.rename(self.library, "smart rename", renamed, {}),
                              step="file writing", nth=1)
        self.copy_the_photos()
        rename_all = names.rename_all

        def moved_then_renamed(*args, **kwargs):
            self.move_the_map()
            return rename_all(*args, **kwargs)
        with mock.patch.object(names, "rename_all", side_effect=moved_then_renamed):
            self.assertEqual(1, file_changes.settle(self.library, rl.EXIFTOOL))
        self.assertEqual(60, sum(1 for new in renamed.values() if os.path.exists(new)), "a photo was not renamed again")
        rows = [path for (path,) in self.side.rows("SELECT path FROM photos")]
        self.assertEqual(60, sum(1 for path in rows if path.startswith("@") and "/Renamed_" in path),
                         "a row did not follow its renamed file, by the root")
        self.assertEqual("applied", file_journal.change(self.library.path, change).status)
        self.assertEqual(self.copy, self.place_seen_by_a_new_connection(), "the next run does not take the new place")


if __name__ == "__main__":
    unittest.main()
