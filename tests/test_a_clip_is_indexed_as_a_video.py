"""A clip in a folder the library is given is indexed as a row of kind 'video' (scenario F12; DECISIONS, "VIDEO: THE
OWNERS' PLANS AND THE OWNER'S ANSWERS").

What the owner lives through: they add a folder of clips, or a folder where clips sit among photos, and index it with
the CLI. Each clip gets a row of kind 'video', dated by the rule a clip's dates follow (tagpup.core.dates: Apple's
CreationDate as given, else QuickTime's CreateDate, kept in UTC, as local time; a date of zeros is none, and the bare
CreateDate, QuickTime's copy, is never read as a photo's), with no vector, no faces and nothing written into its file.
The next index and the owed scan (tagpup.store.photo_fingerprints.owed) owe it nothing, so sync never queues a clip's
folder again. The photos beside it are indexed exactly as before.

Then what happens to a clip in the library: moved to another of its folders, or renamed in its own, sync follows it
(the row, its id and its kind go with the file, by its stamp: a clip holds no DocumentID); one still being copied when
it was indexed (its size changes after) is read again by the next sync; one cut short (a copy that failed) is a row
of kind 'video' holding nothing read (ExifTool answers such a file with a Warning, "Truncated 'mdat' data", not an
Error, so it is not a read_error: a photo cut short is read the same way), never decoded, and not read again while it
stays as it is; two
libraries holding one folder of clips each record their own rows, and neither's sync touches the other.

The clips are made here by the machine's ffmpeg (tagpup.config.locate_ffmpeg), as tests/test_frames_of_a_video.py makes
them, in a home of the test's own; ExifTool is real; CLIP and the face models are the stand-ins of
tests/test_an_index_reads_the_next_photos_ahead.py. Skipped, saying so, on a machine without ffmpeg or ExifTool.
"""
import calendar
import hashlib
import os
import sys
import time
import unittest

from click.testing import CliRunner

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402
from test_an_index_reads_the_next_photos_ahead import StandIns, photo, recorded  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import config  # noqa: E402
from tagpup.core import dates, paths, processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import images  # noqa: E402
from tagpup.services import bake_after_index, baking  # noqa: E402
from tagpup.services import faces as face_records  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photos as photo_service  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.services import sync, thumbnails  # noqa: E402
from tagpup.store import db, embeddings, photo_fingerprints  # noqa: E402

TOOLS = config.locate_ffmpeg()

#: The clips, each as a camera leaves one: an .mp4 with QuickTime's UTC CreateDate, an iPhone's .mov with Apple's
#: CreationDate (its zone kept) beside it, and an .mp4 whose camera wrote no date (QuickTime's zeros).
CLIPS = {
    "harbour.mp4": ["-metadata", "creation_time=2021-07-04T18:30:00Z"],
    "jetty.mov": ["-movflags", "use_metadata_tags",
                  "-metadata", "com.apple.quicktime.creationdate=2021-07-04T11:30:00-0700",
                  "-metadata", "creation_time=2021-07-04T18:30:00Z"],
    "undated.mp4": ["-map_metadata", "-1"],
}


def clip(folder, name):
    """A one-second clip of ffmpeg's test pattern at `folder`/`name`, with CLIPS' metadata."""
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, name)
    made = processes.run([TOOLS.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                          "-t", "1", "-pix_fmt", "yuv420p"] + CLIPS[name] + [target], capture_output=True)
    assert made.returncode == 0, made.stderr
    return target


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def rows(library):
    """{file name: (kind, taken, holds a vector)} as the library holds it."""
    conn = db.connect(db.readonly_uri(library), uri=True)
    try:
        return {os.path.basename(path): (kind, taken, bool(vectors)) for path, kind, taken, vectors in conn.execute(
            "SELECT p.path, p.kind, p.taken, (SELECT COUNT(*) FROM embeddings e WHERE e.photo_id = p.id) FROM photos p")}
    finally:
        conn.close()


def ids(library):
    """{file name: (id, kind, size)} as the library holds it."""
    conn = db.connect(db.readonly_uri(library), uri=True)
    try:
        return {os.path.basename(path): (photo_id, kind, size)
                for photo_id, path, kind, size in conn.execute("SELECT id, path, kind, size FROM photos")}
    finally:
        conn.close()


def local_of(utc):
    """A UTC 'YYYY:MM:DD HH:MM:SS' as local time, worked out here by the time module, not by the code under test."""
    return time.strftime("%Y:%m:%d %H:%M:%S", time.localtime(calendar.timegm(time.strptime(utc, "%Y:%m:%d %H:%M:%S"))))


@unittest.skipIf(TOOLS.problem, "ffmpeg is not installed on this machine (winget install Gyan.FFmpeg): %s" % TOOLS.problem)
@unittest.skipIf(own_home.installed_exiftool() is None, "ExifTool not installed")
class AClipIsIndexedAsAVideo(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="clip_index_")
        self.models = StandIns(self)
        self.library = self.home.library("harbour.db")
        self.assertEqual(1, library_actions.create(self.library).changed)

    def index(self, folder):
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.library, "index", folder])
        self.assertEqual(0, result.exit_code, result.output)
        return result.output

    def owed(self, folder):
        """What the owed scan (sync's and the indexer's answer) says the index owes the files under `folder`, faces
        asked by the library's detector."""
        settings = library_settings.of(Library(self.library))
        conn = db.connect(db.readonly_uri(self.library), uri=True)
        try:
            return photo_fingerprints.owed(conn, embeddings.model_key(**settings.embedder),
                                           face_records.detector_named(settings.faces), images.stamps_under(folder))
        finally:
            conn.close()

    def test_a_folder_of_clips_is_indexed_as_videos_dated_by_their_own_rule(self):
        folder = os.path.join(self.home.root, "Regatta")
        made = {name: clip(folder, name) for name in CLIPS}
        before = {name: digest(path) for name, path in made.items()}

        output = self.index(folder)

        self.assertIn("Recorded 3 video(s).", " ".join(output.split()))
        self.assertEqual({
            "harbour.mp4": ("video", local_of("2021:07:04 18:30:00"), False),
            "jetty.mov": ("video", "2021:07:04 11:30:00-07:00", False),
            "undated.mp4": ("video", None, False),
        }, rows(self.library))
        self.assertEqual(before, {name: digest(path) for name, path in made.items()}, "a clip's file was written")
        self.assertEqual([], self.models.shown, "a clip was handed to CLIP")
        self.assertEqual([], self.models.detected, "a clip was looked at for faces")

        # The next index reads nothing, and the owed scan sync asks owes the clips nothing: their folder is never
        # queued again.
        self.assertIn("All images are up to date", self.index(folder))
        self.assertEqual({}, self.owed(folder))

    def test_photos_beside_a_clip_are_indexed_as_before(self):
        folder = os.path.join(self.home.root, "Jetty")
        clip(folder, "harbour.mp4")
        photo(os.path.join(folder, "sail.jpg"), (30, 30, 200))

        output = " ".join(self.index(folder).split())

        # The bake after the index plans the folder's photos: a clip there is not counted a photo it cannot bake.
        self.assertIn(bake_after_index.SAID, output)
        self.assertNotIn(baking.NO_VECTOR, output, "the bake counted a clip as a photo with no vector")
        found = rows(self.library)
        self.assertEqual(("photo", True), (found["sail.jpg"][0], found["sail.jpg"][2]))
        self.assertEqual(("video", False), (found["harbour.mp4"][0], found["harbour.mp4"][2]))
        held = recorded(self.library)
        self.assertTrue(held["sail.jpg"][0], "the photo was not given its identity")
        self.assertEqual(1, held["sail.jpg"][3], "the photo was not examined for faces")
        self.assertEqual((False, None, [], None), held["harbour.mp4"], "the clip was given an identity, a vector or a face")
        self.assertEqual({}, self.owed(folder))

    def test_a_folder_opened_and_read_again_after_a_rename_lists_its_clip_beside_its_photo(self):
        # The folder view lists a folder's clips (video, merge B2), and so does its read after Smart Rename, which the
        # page shows as the folder (#2564), each by the real reader. Suggest and a time shift take the photos only
        # (tests/test_folder_scan_follows_the_library.py).
        folder = os.path.join(self.home.root, "Moorings")
        clip(folder, "harbour.mp4")
        photo(os.path.join(folder, "sail.jpg"), (30, 30, 200))
        exiftool = own_home.installed_exiftool()
        for read in (photo_service.scan_folder, photo_service.read_folder):
            with self.subTest(read=read.__name__):
                found = {os.path.basename(r["path"]): r["media"]
                         for r in read(Library(self.library), folder, exiftool).values()}
                self.assertEqual({"harbour.mp4": "video", "sail.jpg": "photo"}, found)


@unittest.skipIf(TOOLS.problem, "ffmpeg is not installed on this machine (winget install Gyan.FFmpeg): %s" % TOOLS.problem)
@unittest.skipIf(own_home.installed_exiftool() is None, "ExifTool not installed")
class AClipInTheLibrary(unittest.TestCase):
    """What happens to a clip after it is indexed: sync follows it, reads it again, or leaves it be."""

    index, owed = AClipIsIndexedAsAVideo.index, AClipIsIndexedAsAVideo.owed

    def setUp(self):
        AClipIsIndexedAsAVideo.setUp(self)
        self.folder = os.path.join(self.home.root, "Regatta")
        self.day_two = os.path.join(self.folder, "Day two")
        os.makedirs(self.day_two)
        photo(os.path.join(self.day_two, "sail.jpg"), (30, 30, 200))

    def sync(self, library=None, apply=True):
        return sync.sync(Library(library or self.library), apply=apply, exiftool_path=own_home.installed_exiftool())

    def test_a_clip_moved_to_another_folder_or_renamed_in_its_own_is_followed_by_sync(self):
        moved, renamed = clip(self.folder, "harbour.mp4"), clip(self.folder, "jetty.mov")
        self.index(self.folder)
        before = ids(self.library)

        os.replace(moved, os.path.join(self.day_two, "harbour.mp4"))   # a move keeps the modified time
        os.replace(renamed, os.path.join(self.folder, "jetty at dusk.mov"))
        result = self.sync()

        counts = result.details["counts"]
        self.assertEqual((2, 0, 0, 0), (counts["moved"], counts["missing"], counts["to_remove"], counts["to_check"]),
                         counts)
        after = ids(self.library)
        self.assertEqual(before["harbour.mp4"], after["harbour.mp4"], "the moved clip's row did not follow it")
        self.assertEqual(before["jetty.mov"], after["jetty at dusk.mov"], "the renamed clip's row did not follow it")
        self.assertNotIn("jetty.mov", after)
        self.assertTrue(self.sync(apply=False).details["in_step"], "the library is not in step after the sync")
        self.assertEqual({}, self.owed(self.folder))

    def test_a_clip_still_being_copied_when_it_was_indexed_is_read_again_by_the_next_sync(self):
        path = clip(self.folder, "harbour.mp4")
        self.index(self.folder)
        photo_id = ids(self.library)["harbour.mp4"][0]
        with open(path, "ab") as handle:   # the copy goes on: the file grows after its row was made
            handle.write(b"\x00" * 4096)
        later = os.stat(path).st_mtime + 10
        os.utime(path, (later, later))

        result = self.sync()

        counts = result.details["counts"]
        self.assertEqual((1, 0), (counts["changed"], counts["to_check"]), counts)
        self.assertEqual((photo_id, "video", os.path.getsize(path)), ids(self.library)["harbour.mp4"],
                         "the clip's row was not read again")
        self.assertTrue(self.sync(apply=False).details["in_step"])

    def test_a_clip_cut_short_is_a_row_that_is_never_decoded_and_not_read_again(self):
        whole = clip(os.path.join(self.home.root, "Elsewhere"), "harbour.mp4")
        with open(whole, "rb") as handle:
            head = handle.read(64)   # a copy that failed part way: the start of the file and nothing after
        os.makedirs(self.folder, exist_ok=True)
        with open(os.path.join(self.folder, "harbour.mp4"), "wb") as handle:
            handle.write(head)

        self.index(self.folder)

        photo_id, kind, size = ids(self.library)["harbour.mp4"]
        self.assertEqual(("video", 64), (kind, size))
        self.assertEqual(("video", None, False), rows(self.library)["harbour.mp4"], "a date or a vector of a clip cut short")
        self.assertEqual(1, len(self.models.shown), "a clip was handed to CLIP (the one photo is Day two's)")
        self.assertEqual([os.path.join(self.day_two, "sail.jpg")], [paths.stored(path) for path, _size in self.models.detected],
                         "a clip was looked at for faces")
        self.assertEqual(thumbnails.UNREADABLE, thumbnails.serve(Library(self.library), photo_id, config.locate_ffmpeg).kind,
                         "no poster of a clip cut short")
        self.assertIn("All images are up to date", self.index(self.folder))
        self.assertEqual({}, self.owed(self.folder))

    def test_two_libraries_holding_one_folder_of_clips_each_keep_their_own_rows(self):
        clip(self.folder, "harbour.mp4")
        other = self.home.library("jetty.db")
        self.assertEqual(1, library_actions.create(other).changed)
        self.index(self.folder)
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", other, "index", self.folder])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual(("video", "video"), (rows(self.library)["harbour.mp4"][0], rows(other)["harbour.mp4"][0]))

        os.replace(os.path.join(self.folder, "harbour.mp4"), os.path.join(self.day_two, "harbour.mp4"))
        before = ids(other)
        self.assertEqual(1, self.sync().details["counts"]["moved"])
        self.assertEqual(before, ids(other), "one library's sync changed the other's rows")
        self.assertEqual(1, self.sync(other).details["counts"]["moved"])
        self.assertEqual(ids(self.library)["harbour.mp4"][1:], ids(other)["harbour.mp4"][1:])


class AClipsDateRule(unittest.TestCase):
    """The rule beside the ones a real clip shows above: what a clip holding zeros falls back to, and a photo untouched."""

    def test_a_clip_with_no_date_of_its_own_falls_back_to_the_photo_fields_under_their_group(self):
        zeros = {"QuickTime:CreateDate": "0000:00:00 00:00:00", "CreateDate": "0000:00:00 00:00:00"}
        self.assertIsNone(dates.date_taken(zeros), "QuickTime's bare copy of zeros was read as a photo's CreateDate")
        self.assertIsNone(dates.year_taken(zeros))
        tagged = dict(zeros, **{"XMP:CreateDate": "2019:05:04 10:00:00"})
        self.assertEqual("2019:05:04 10:00:00", dates.date_taken(tagged))
        self.assertEqual(2019, dates.year_taken(tagged))

    def test_a_clips_utc_date_is_never_read_as_given(self):
        clip_record = {"QuickTime:CreateDate": "2021:12:31 23:30:00", "CreateDate": "2021:12:31 23:30:00"}
        self.assertEqual(local_of("2021:12:31 23:30:00"), dates.date_taken(clip_record))
        self.assertEqual(int(local_of("2021:12:31 23:30:00")[:4]), dates.year_taken(clip_record))

    def test_a_photos_dates_are_read_as_before(self):
        self.assertEqual("2020:01:02 03:04:05", dates.date_taken({"CreateDate": "2020:01:02 03:04:05"}))
        self.assertEqual("0000:00:00 00:00:00", dates.date_taken({"EXIF:DateTimeOriginal": "0000:00:00 00:00:00"}))


if __name__ == "__main__":
    unittest.main()
