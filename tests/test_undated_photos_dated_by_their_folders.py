"""The one-off that dates each undated photo and clip in a folder named for a day by that day
(scripts/date_undated_by_folder.py; DECISIONS 2026-11-15; docs/plans/date-undated-by-folder.md), as the owner runs it
with TagPup closed: a dry run, --apply, a dry run again that lists none, and History's undo (scenario F3; deleted with
the script once its dry run says 0 left).

A folder `2018-06-23 Picnic` holds an undated photo, an undated clip (its QuickTime dates zeros), a photo with a date of
its own, and a photo whose file name holds another day (left for the owner); a folder `2018-06 Beach` an undated photo.
The undated photo and clip are dated 2018:06:23 00:00:00 in their files and rows; the others are never written. Each
undo of the photos' change and the clips' takes the date off its file and its row. An app running refuses --apply.

A library of its own (web_client), rows recorded by the indexer's own read, real ffmpeg clips and the real ExifTool
(tests/test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip). The script did not exist before this file.
Fictional names only.
"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
import date_undated_by_folder  # noqa: E402
import test_a_clip_dated_by_its_name as by_name  # noqa: E402
import test_a_clip_dated_through_the_journal as dated  # noqa: E402
import test_a_clip_tagged_through_the_journal as tagged  # noqa: E402

from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402

EXIFTOOL = tagged.EXIFTOOL
TOOLS = tagged.TOOLS
DAY = "2018:06:23 00:00:00"


@unittest.skipIf(EXIFTOOL is None or TOOLS.problem, "ExifTool or ffmpeg is not installed on this machine")
class UndatedPhotosDatedByTheirFolders(tagged.ALibraryHoldingAClip):

    taken = dated.AClipDatedThroughTheJournal.taken
    read = dated.AClipDatedThroughTheJournal.read

    def setUp(self):
        super().setUp()
        picnic = os.path.join(self.vl.pictures, "2018-06-23 Picnic")
        beach = os.path.join(self.vl.pictures, "2018-06 Beach")
        os.makedirs(picnic)
        os.makedirs(beach)
        self.lawn_path, self.kite_path, self.blanket_path, self.renamed_path, self.shore_path = (
            os.path.join(picnic, "lawn.jpg"), by_name.zeroed_clip(os.path.join(picnic, "kite.mp4")),
            os.path.join(picnic, "blanket.jpg"), os.path.join(picnic, "2017-01-02 basket.jpg"),
            os.path.join(beach, "shore.jpg"))
        for path in (self.lawn_path, self.blanket_path, self.renamed_path, self.shore_path):
            tagged.make_jpeg(path, size=(32, 32))
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([self.blanket_path], tags={"EXIF:DateTimeOriginal": dated.PHOTO_TAKEN},
                        params=["-overwrite_original"])
        self.lawn, self.kite, self.blanket, self.renamed, self.shore = self.index(
            self.lawn_path, self.kite_path, self.blanket_path, self.renamed_path, self.shore_path)
        self.untouched = {path: self.read_bytes(path) for path in (self.blanket_path, self.renamed_path, self.shore_path)}
        # No app is running for the test (launcher.running reads the user's own servers folder).
        running = mock.patch.object(date_undated_by_folder.launcher, "running", return_value=[])
        self.running = running.start()
        self.addCleanup(running.stop)
        exiftool = mock.patch.object(date_undated_by_folder.runtime, "exiftool", return_value=EXIFTOOL)
        exiftool.start()
        self.addCleanup(exiftool.stop)

    def run_it(self, *apply):
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            code = date_undated_by_folder.main(["--library", self.vl.library.path, *apply])
        return code, said.getvalue()

    def changes(self):
        return [change for change, in self.vl.rows("SELECT id FROM changes WHERE operation = ? ORDER BY id",
                                                   date_undated_by_folder.OPERATION)]

    def file_date(self, path):
        names = ["XMP:DateTimeOriginal"] if path == self.kite_path else ["EXIF:DateTimeOriginal", "XMP:DateTimeOriginal"]
        return sorted({str(value) for value in self.read(path, names).values() if value is not None})

    def listing(self, photos, clips):
        return "%d photo(s) to date by their folder%s; %d clip(s) to date by their folder%s; left for you to date, " \
               "their file name holding another date: 1: ids %d." % (
                   len(photos), (": ids " + ", ".join(map(str, photos))) if photos else "",
                   len(clips), (": ids " + ", ".join(map(str, clips))) if clips else "", self.renamed)

    def test_the_photo_and_clip_in_a_dated_folder_are_dated_by_it_and_history_undoes_each(self):
        code, said = self.run_it()
        self.assertEqual(0, code, said)
        self.assertIn(self.listing([self.lawn], [self.kite]), said, "the dry run: counts and ids from the index")
        self.assertEqual([], self.changes())
        self.assertEqual(([], []), (self.file_date(self.lawn_path), self.file_date(self.kite_path)), "and wrote nothing")
        clip_before = self.read_bytes(self.kite_path)

        code, said = self.run_it("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("photos: wrote 1.", said)
        self.assertIn("clips: wrote 1.", said)
        self.assertIn("Left to date by their folder: 0.", said)
        self.assertNotIn(self.lawn_path, said, "ids, never a path")
        self.assertEqual(([DAY], [DAY]), (self.file_date(self.lawn_path), self.file_date(self.kite_path)))
        self.assertEqual((DAY, DAY), (self.taken(self.lawn), self.taken(self.kite)), "each row follows its file")
        self.assertEqual(self.untouched, {path: self.read_bytes(path) for path in self.untouched},
                         "a photo dated already, one named for another day and one in a year-month folder: untouched")
        self.assertEqual((None, None), (self.taken(self.renamed), self.taken(self.shore)))

        code, said = self.run_it()
        self.assertIn(self.listing([], []), said, "a second run lists none")

        photos_change, clips_change = self.changes()
        for change in (clips_change, photos_change):
            dated.AClipDatedThroughTheJournal.undo(self, change)
        self.assertEqual(([], []), (self.file_date(self.lawn_path), self.file_date(self.kite_path)),
                         "History's undo takes each date back off its file")
        self.assertEqual(clip_before, self.read_bytes(self.kite_path)[:len(clip_before)], "the clip's own bytes as they were")
        self.assertEqual((None, None), (self.taken(self.lawn), self.taken(self.kite)), "and off its row")
        code, said = self.run_it()
        self.assertIn(self.listing([self.lawn], [self.kite]), said, "listed again")

    def test_apply_is_refused_while_an_app_runs(self):
        self.running.return_value = [{"pid": 4242}]
        before = self.read_bytes(self.lawn_path)
        with mock.patch.object(date_undated_by_folder.snapshots, "before_a_long_write") as snapshot:
            code, said = self.run_it("--apply")
        self.assertEqual((1, date_undated_by_folder.APP_RUNNING % 1), (code, said.strip()))
        snapshot.assert_not_called()
        self.assertEqual(before, self.read_bytes(self.lawn_path))
        self.assertEqual([], self.changes())


if __name__ == "__main__":
    unittest.main()
