"""The one-time move of a library's clips' Windows Tags into their XMP (scripts/move_windows_tags.py,
tagpup.services.windows_tags; DECISIONS 2026-11-09), as the owner runs it: a dry run first, then --apply on 5 clips, 50,
then all, stopped and run again, a clip open in a player, a share that does not answer, the app left running.

Four real one-second clips made by the machine's ffmpeg, each given an Xtra as Windows writes one
(tests/test_a_clip_patched_in_place.with_windows_tags), one of them saved with an XMP tag of its own first through the
journal's keyword writer, one holding a Windows tag the tag tree does not hold; every row recorded by the indexer's own
read, in a home of its own (tests/test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip). Skipped, saying so,
without ffmpeg or ExifTool. Fictional tags only. The script did not exist before this file: each test fails on the
trunk before it for want of it.
"""
import os
import shutil
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
import move_windows_tags  # noqa: E402
import test_a_clip_patched_in_place as patched  # noqa: E402
from test_a_clip_tagged_through_the_journal import EXIFTOOL, ALibraryHoldingAClip  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.services import file_changes, tagging, windows_tags  # noqa: E402

#: A tag the tree does not hold: its clip is left as it is and named by --show.
UNKNOWN = "Trips/Lighthouse"


class ALibrarysWindowsTagsMovedOnce(ALibraryHoldingAClip):

    def setUp(self):
        super().setUp()
        self.vl.tree("People/Wren Ashcombe", face_root="People")
        running = mock.patch.object(move_windows_tags.launcher, "running", return_value=[])
        running.start()
        self.addCleanup(running.stop)
        self.made = os.path.join(self.home.root, "made.mp4")
        shutil.copyfile(self.clip_path, self.made)
        self.jetty = patched.with_windows_tags(self.clip_path, patched.WINDOWS_TAGS)
        self.slipway = self.copy("slipway.mp4", ("Trips/Harbour", "Trips/Coast"))
        self.quay = self.copy("quay.mp4", ("Trips/Coast", UNKNOWN))
        self.dock = self.copy("dock.mp4", ("Trips/Pier",))
        # The slipway holds an XMP tag of its own, saved through the journal's keyword writer before the move.
        saved = file_changes.write_fields(self.vl.library, windows_tags.OPERATION, EXIFTOOL, [self.slipway],
                                          list(tagging.KEYWORD_READ),
                                          tagging.keywords_planner(self.vl.library, lambda _p, _h: (["Trips/Coast"], ())))
        self.assertEqual(1, saved.changed)
        self.saved = saved.details["change"]
        self.ids = dict(zip(("jetty", "slipway", "quay", "dock"),
                            self.index(self.jetty, self.slipway, self.quay, self.dock)))

    def copy(self, name, texts):
        """A copy of the ffmpeg clip, before any Xtra was added, given an Xtra holding `texts`."""
        path = os.path.join(self.folder, name)
        shutil.copyfile(self.made, path)
        return patched.with_windows_tags(path, texts)

    def run_script(self, *args):
        """(exit code, what it said) of the script run on the library, as the owner runs it."""
        said = []
        argv = ["--library", self.vl.path, *args]
        with mock.patch.object(move_windows_tags, "say", said.append):
            code = move_windows_tags.main(argv)
        return code, "\n".join(said)

    def held(self, path):
        """(its XMP:Subject, its Windows Tags) as ExifTool reads them now."""
        return patched.windows_tags_read(path)

    def changes(self):
        """The changes made since the slipway's own save."""
        return self.vl.rows("SELECT id, operation, status FROM changes WHERE id > ? ORDER BY id", self.saved)

    def assert_moved(self, path, tags):
        self.assertEqual((list(tags), None), self.held(path), "its tags in its XMP, its Windows Tags gone")

    def assert_not_moved(self, path, windows):
        self.assertEqual(list(windows), self.held(path)[1], "its Windows Tags where they were")


class TheMove(ALibrarysWindowsTagsMovedOnce):

    def test_a_dry_run_reads_every_clip_counts_and_writes_nothing_nor_settles_a_change_left_half_done(self):
        # An --apply killed after its plan: its change is left planned, for the next open of the library to finish.
        surveyed = windows_tags.survey(self.vl.library, EXIFTOOL)
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("plan committed")):
            with self.assertRaises(Crash):
                windows_tags.move(self.vl.library, EXIFTOOL, surveyed)
        clips = {path: self.read_bytes(path) for path in (self.jetty, self.slipway, self.quay, self.dock)}
        journal = self.vl.rows("SELECT id, state FROM change_files WHERE change_id > ? ORDER BY id", self.saved)
        self.assertEqual(3, len(journal), "the killed run's change, planned")
        db_bytes = self.read_bytes(self.vl.path)

        code, said = self.run_script()
        self.assertEqual(0, code, said)
        self.assertIn("clips 4, to move 3, tags to add 5, no Windows Tags 0, a tag not in the tree 1, unreadable 0, "
                      "away 0.", said)
        self.assertIn("A dry run: nothing was written. --apply moves the Windows Tags of 3 clip(s)", said)
        self.assertEqual(clips, {path: self.read_bytes(path) for path in clips}, "no clip written")
        self.assertEqual(journal, self.vl.rows("SELECT id, state FROM change_files WHERE change_id > ? ORDER BY id",
                                               self.saved),
                         "the change left half done is left as it is: a dry run settles nothing")
        self.assertEqual(db_bytes, self.read_bytes(self.vl.path), "the library's file as it was")
        self.assertNotIn(self.folder, said, "a path was said")
        self.assertNotIn("Trips/", said, "a tag was said")

        # --apply opens the library as the apps do: the change left half done is finished first, then nothing is left.
        code, said = self.run_script("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("to move 0", said)
        self.assert_moved(self.jetty, patched.WINDOWS_TAGS)

    def test_apply_moves_each_clips_tags_beside_those_it_holds_leaves_an_unknown_tag_and_a_second_run_writes_nothing(self):
        code, said = self.run_script("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Snapshot of the library:", said)
        self.assertIn("Moved: wrote 3 clip file(s), 5 tag(s) added. Left alone: 0 with no Windows Tags any more, 0 with "
                      "a tag not in the tree, 0 unreadable, 0 away", said)
        self.assert_moved(self.jetty, patched.WINDOWS_TAGS)
        self.assert_moved(self.slipway, ("Trips/Coast", "Trips/Harbour"))     # its own first: nothing it held is lost
        self.assert_moved(self.dock, ("Trips/Pier",))
        self.assertEqual(([], ["Trips/Coast", UNKNOWN]), self.held(self.quay), "a tag not in the tree: left as it is")
        self.assertEqual(sorted(["Trips/Coast", "Trips/Harbour"]), sorted(self.tags_of(self.ids["slipway"])),
                         "the row reads the tags")
        [(change, operation, status)] = self.changes()
        self.assertEqual(("bulk tags", "applied"), (operation, status))
        self.assertIn("Recorded as change %d in History" % change, said)

        before = {path: self.read_bytes(path) for path in (self.jetty, self.slipway, self.quay, self.dock)}
        code, said = self.run_script("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("to move 0", said)
        self.assertIn("Nothing was recorded: no file was written.", said)
        self.assertEqual(before, {path: self.read_bytes(path) for path in before})
        self.assertEqual(1, len(self.changes()))

    def test_show_names_the_clip_whose_tag_is_not_in_the_tree_and_its_text(self):
        code, said = self.run_script("--show")
        self.assertEqual(0, code, said)
        self.assertIn("not in the tree: clip %d: %s" % (self.ids["quay"], UNKNOWN), said)

    def test_limit_moves_the_first_clips_by_id_and_the_next_run_the_rest(self):
        code, said = self.run_script("--apply", "--limit", "1")
        self.assertEqual(0, code, said)
        self.assertIn("Moved: wrote 1 clip file(s), 3 tag(s) added.", said)
        self.assert_moved(self.jetty, patched.WINDOWS_TAGS)
        self.assert_not_moved(self.dock, ("Trips/Pier",))
        code, said = self.run_script()
        self.assertIn("to move 2", said)
        code, said = self.run_script("--apply")
        self.assertIn("Moved: wrote 2 clip file(s), 2 tag(s) added.", said)
        self.assertEqual(2, len(self.changes()))

    def test_ctrl_c_part_way_keeps_what_was_written_and_a_second_run_finishes(self):
        pressed = []

        def reached(step):
            if step == "file written" and not pressed:
                pressed.append(step)
                raise KeyboardInterrupt
        with mock.patch.object(file_changes, "_reached", side_effect=reached):
            code, said = self.run_script("--apply")
        self.assertEqual(130, code, said)
        self.assertIn("stopped: 1 file(s) written", said)
        self.assert_moved(self.jetty, patched.WINDOWS_TAGS)
        self.assert_not_moved(self.dock, ("Trips/Pier",))
        code, said = self.run_script("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Moved: wrote 2 clip file(s), 2 tag(s) added.", said)
        self.assert_moved(self.dock, ("Trips/Pier",))
        self.assertEqual(["applied", "applied"], [status for _id, _op, status in self.changes()])

    def test_a_clip_open_in_a_player_is_refused_by_its_writer_and_the_others_are_written(self):
        with open(self.slipway, "rb"):
            code, said = self.run_script("--apply")
        self.assertEqual(1, code, said)
        self.assertIn("Moved: wrote 2 clip file(s), 4 tag(s) added.", said)
        self.assertIn("Refused by the writer (open in another program): 1.", said)
        self.assertIn("1 clip(s) were not reached: run this again", said)
        self.assert_not_moved(self.slipway, ("Trips/Harbour", "Trips/Coast"))
        code, said = self.run_script("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Moved: wrote 1 clip file(s), 1 tag(s) added.", said)
        self.assert_moved(self.slipway, ("Trips/Coast", "Trips/Harbour"))

    def test_a_clip_on_a_share_that_does_not_answer_is_away_and_the_next_run_moves_it(self):
        real = file_changes.file_states

        def states(photo_paths):
            return [(path, file_changes.AWAY if paths.key(path) == paths.key(self.dock) else state)
                    for path, state in real(photo_paths)]
        with mock.patch.object(file_changes, "file_states", side_effect=states), \
                mock.patch.object(shares, "wait_back", return_value=False):
            code, said = self.run_script("--apply")
        self.assertEqual(1, code, said)
        self.assertIn("away 1.", said)
        self.assertIn("Moved: wrote 2 clip file(s)", said)
        self.assertIn("1 clip(s) were not reached: run this again", said)
        self.assert_not_moved(self.dock, ("Trips/Pier",))
        code, said = self.run_script("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Moved: wrote 1 clip file(s), 1 tag(s) added.", said)
        self.assert_moved(self.dock, ("Trips/Pier",))

    def test_apply_is_refused_while_the_app_runs_and_nothing_is_read_or_written(self):
        before = {path: self.read_bytes(path) for path in (self.jetty, self.dock)}
        with mock.patch.object(move_windows_tags.launcher, "running", return_value=[{"pid": 4242}]), \
                mock.patch.object(windows_tags, "survey", side_effect=AssertionError("read")):
            code, said = self.run_script("--apply")
        self.assertEqual(1, code, said)
        self.assertIn("Not run: TagPup is running (1 app server(s)).", said)
        self.assertEqual(before, {path: self.read_bytes(path) for path in before})
        self.assertEqual([], self.changes())


if __name__ == "__main__":
    unittest.main()
