"""A clip in a folder the library is given is indexed as a row of kind 'video' and shown CLIP its five frames
(scenario C1; DECISIONS, "VIDEO: THE OWNERS' PLANS AND THE OWNER'S ANSWERS", item 5, and 2026-11-06, migration 38).

What the owner lives through: they add a folder of clips, or a folder where clips sit among photos, and index it with
the CLI. Each clip gets a row of kind 'video', dated by the rule a clip's dates follow (tagpup.core.dates: its XMP
date first, then Apple's CreationDate as given, else QuickTime's CreateDate, kept in UTC, as local time; a date of zeros
is none, and the bare CreateDate, QuickTime's copy, is never read as a photo's: tests/test_library_views_scenarios.py,
V1), and a vector made of its frames after the photos, in the
same turn of the graphics card: frame 0's is its vector, frames 1 to 4 kept beside it (tagpup_cli.py's _index_clips).
No face is looked for and nothing is written into its file. A clip in an older format (.wmv, .mpg, .avi, .mod) is
indexed so too, undated when it carries no date, beside an .mp4 of its name, shown its poster, and never written: a
tag, title, Date Taken or a rename's kept name is refused (images.CLIP_NOT_PATCHED: no MOV/MP4 boxes to patch;
DECISIONS 2026-11-09). The next index and the owed scan
(tagpup.store.photo_fingerprints.owed) owe it nothing, so sync never queues a clip's folder again. The photos beside
it are indexed exactly as before, and a folder with no clip is indexed as it was.

Then what happens to a clip in the library: moved to another of its folders, or renamed in its own, sync follows it
(the row, its id and its kind go with the file, by its stamp: a clip holds no DocumentID); one still being copied when
it was indexed (its size changes after) is read again by the next sync and owed its vector again; one cut short (a
copy that failed: ExifTool answers it with a Warning, "Truncated 'mdat' data", not an Error) is a row of kind 'video'
holding nothing read and no vector, listed by the index as one ffmpeg cannot read, and tried again by every index of
its folder; two libraries holding one folder of clips each record their own rows, and neither's sync touches the
other.

And how it fails: an index stopped while a clip is shown CLIP (Ctrl+C, the window closed) writes nothing of the clips
of its chunk, and the next index makes them; a clip another index holds is passed over, said, and made by the next; a
clip ffmpeg cannot read now (a share away) is recorded with no vector, not listed, and made by the next index; a clip
changed while its frames were read is owed again.

The clips of the first two classes are made here by the machine's ffmpeg (tagpup.config.locate_ffmpeg), as
tests/test_frames_of_a_video.py makes them, in a home of the test's own, and their frames read by it; the failures are
told with ffmpeg stood in for at its seam (video_frames.frames), so they run on a machine without it. ExifTool is real;
CLIP and the face models are the stand-ins of tests/test_an_index_reads_the_next_photos_ahead.py. Skipped, saying so,
on a machine without ffmpeg (the first two classes) or ExifTool.
"""
import calendar
import hashlib
import io
import os
import sys
import time
import unittest
from unittest import mock

from click.testing import CliRunner
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import corpus  # noqa: E402
import own_home  # noqa: E402
from test_an_index_reads_the_next_photos_ahead import StandIns, photo, recorded  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import config  # noqa: E402
from tagpup.core import fields, paths  # noqa: E402
from tagpup.core.result import Refused  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import field_values, images, video_frames  # noqa: E402
from tagpup.ml import clip as clip_model  # noqa: E402
from tagpup.services import bake_after_index, baking  # noqa: E402
from tagpup.services import faces as face_records  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photos as photo_service  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.services import sync, thumbnails  # noqa: E402
from tagpup.store import db, embeddings, photo_fingerprints  # noqa: E402
from tagpup.store.locks import PathLocker  # noqa: E402

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
    return corpus.clip(target, ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                                "-t", "1", "-pix_fmt", "yuv420p"] + CLIPS[name], TOOLS.ffmpeg)


#: Clips in an older format, each as its program leaves one: (picture codec, container's own options).
OLDER = {"harbour.wmv": ["-c:v", "wmv2"], "launch.mpg": ["-c:v", "mpeg1video", "-r", "25"], "slipway.avi": ["-c:v", "mpeg4"]}


def older(folder, name):
    """A one-second clip of ffmpeg's test pattern at `folder`/`name`, in an older format (OLDER), with no date."""
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, name)
    return corpus.clip(target, ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                                "-t", "1", "-pix_fmt", "yuv420p", "-map_metadata", "-1"] + OLDER[name], TOOLS.ffmpeg)


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def rows(library):
    """{file name: (kind, taken, holds a vector)} as the library holds it (a clip's frames are frames_held's)."""
    conn = db.connect(db.readonly_uri(library), uri=True)
    try:
        return {os.path.basename(path): (kind, taken, bool(vectors)) for path, kind, taken, vectors in conn.execute(
            "SELECT p.path, p.kind, p.taken, (SELECT COUNT(*) FROM embeddings e WHERE e.photo_id = p.id) FROM photos p")}
    finally:
        conn.close()


def frames_held(library):
    """{file name: how many frames are kept beside its vector} of every row holding one (embedding_frames)."""
    conn = db.connect(db.readonly_uri(library), uri=True)
    try:
        return {os.path.basename(path): count for path, count in conn.execute(
            "SELECT p.path, (SELECT COUNT(*) FROM embedding_frames f WHERE f.photo_id = p.id) FROM photos p"
            " WHERE EXISTS (SELECT 1 FROM embeddings e WHERE e.photo_id = p.id)")}
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

    def test_a_folder_of_clips_is_indexed_as_videos_from_their_frames_dated_by_their_own_rule(self):
        folder = os.path.join(self.home.root, "Regatta")
        made = {name: clip(folder, name) for name in CLIPS}
        before = {name: digest(path) for name, path in made.items()}

        output = " ".join(self.index(folder).split())

        self.assertIn(" ".join((tagpup_cli.CLIPS_LINE % (0, 3, 0, 0)).split()), output)
        self.assertEqual({
            "harbour.mp4": ("video", local_of("2021:07:04 18:30:00"), True),
            "jetty.mov": ("video", "2021:07:04 11:30:00-07:00", True),
            "undated.mp4": ("video", None, True),
        }, rows(self.library))
        self.assertEqual({name: 4 for name in CLIPS}, frames_held(self.library), "a clip's frames 1 to 4 were not kept")
        self.assertEqual(before, {name: digest(path) for name, path in made.items()}, "a clip's file was written")
        self.assertEqual(3 * 5, len(self.models.shown), "CLIP was not shown each clip's five frames")
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
        self.assertEqual(("video", True), (found["harbour.mp4"][0], found["harbour.mp4"][2]))
        self.assertEqual({"sail.jpg": 0, "harbour.mp4": 4}, frames_held(self.library))
        held = recorded(self.library)
        self.assertTrue(held["sail.jpg"][0], "the photo was not given its identity")
        self.assertEqual(1, held["sail.jpg"][3], "the photo was not examined for faces")
        given, _vector, faces, detected = held["harbour.mp4"]
        self.assertEqual((False, [], None), (given, faces, detected), "the clip was given an identity or a face")
        self.assertEqual({}, self.owed(folder))

    def test_an_older_format_beside_an_mp4_of_its_name_is_indexed_as_a_video_shown_its_poster_and_never_written(self):
        folder = os.path.join(self.home.root, "Slipway")
        clip(folder, "harbour.mp4")
        made = {name: older(folder, name) for name in OLDER}
        before = {name: digest(path) for name, path in made.items()}

        self.index(folder)

        found = rows(self.library)
        self.assertEqual({"harbour.mp4": "video", **{name: "video" for name in OLDER}},
                         {name: kind for name, (kind, _taken, _vector) in found.items()})
        self.assertEqual({name: None for name in OLDER}, {name: found[name][1] for name in OLDER}, "undated stays undated")
        self.assertEqual({name: True for name in OLDER}, {name: found[name][2] for name in OLDER},
                         "its vector was not made of its frames")
        held = ids(self.library)
        for name in OLDER:
            with self.subTest(poster=name):
                self.assertEqual(thumbnails.OK, thumbnails.serve(Library(self.library), held[name][0],
                                                                 config.locate_ffmpeg).kind)
        saved = []
        with field_values.session(own_home.installed_exiftool()) as et:
            for name, values in (("harbour.wmv", {fields.PRESERVED_NAME: ["harbour.wmv"]}),
                                 ("launch.mpg", {**fields.title_fields("Launch day")}),
                                 ("slipway.avi", fields.date_taken_fields("2021:07:04 11:30:00", clip=True))):
                with self.subTest(write=name), self.assertRaises(Refused) as refused:
                    field_values.write(et, made[name], values, saving=lambda record, old: saved.append(record))
                self.assertEqual(images.CLIP_NOT_PATCHED, str(refused.exception))
        self.assertEqual(([], before), (saved, {name: digest(path) for name, path in made.items()}),
                         "nothing saved, nothing written")
        self.assertIn("All images are up to date", self.index(folder))

    def test_a_folder_opened_lists_its_clip_beside_its_photo(self):
        # The folder view lists a folder's clips (video, merge B2), and so does Smart Rename's answer, the same scan
        # (#2564), by the real reader. Suggest takes its clips too, a time shift the photos only
        # (tests/test_folder_scan_follows_the_library.py).
        folder = os.path.join(self.home.root, "Moorings")
        clip(folder, "harbour.mp4")
        photo(os.path.join(folder, "sail.jpg"), (30, 30, 200))
        found = {os.path.basename(r["path"]): r["media"]
                 for r in photo_service.scan_folder(Library(self.library), folder, own_home.installed_exiftool()).values()}
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

        # Its row is read again, and its vector, made of the file as it was, is owed again: the folder is the index's.
        counts = result.details["counts"]
        self.assertEqual((1, 1), (counts["changed"], counts["to_check"]), counts)
        self.assertEqual((photo_id, "video", os.path.getsize(path)), ids(self.library)["harbour.mp4"],
                         "the clip's row was not read again")
        self.assertEqual([(photo_fingerprints.STALE,)], [owed.reasons for owed in self.owed(self.folder).values()])
        self.index(self.folder)
        self.assertEqual({}, self.owed(self.folder))
        self.assertTrue(self.sync(apply=False).details["in_step"])

    def test_a_clip_cut_short_is_a_row_with_no_vector_listed_and_tried_again_by_every_index(self):
        whole = clip(os.path.join(self.home.root, "Elsewhere"), "harbour.mp4")
        with open(whole, "rb") as handle:
            head = handle.read(64)   # a copy that failed part way: the start of the file and nothing after
        os.makedirs(self.folder, exist_ok=True)
        with open(os.path.join(self.folder, "harbour.mp4"), "wb") as handle:
            handle.write(head)

        output = " ".join(self.index(self.folder).split())

        cut = os.path.join(self.folder, "harbour.mp4")
        self.assertIn(" ".join((tagpup_cli.CLIPS_UNREAD % 1).split()), output)
        self.assertIn("- %s: %s" % (cut, video_frames.NOT_A_VIDEO), output)
        photo_id, kind, size = ids(self.library)["harbour.mp4"]
        self.assertEqual(("video", 64), (kind, size))
        self.assertEqual(("video", None, False), rows(self.library)["harbour.mp4"], "a date or a vector of a clip cut short")
        self.assertEqual(1, len(self.models.shown), "a clip was handed to CLIP (the one photo is Day two's)")
        self.assertEqual([os.path.join(self.day_two, "sail.jpg")], [paths.stored(path) for path, _size in self.models.detected],
                         "a clip was looked at for faces")
        self.assertEqual(thumbnails.UNREADABLE, thumbnails.serve(Library(self.library), photo_id, config.locate_ffmpeg).kind,
                         "no poster of a clip cut short")
        # Owed its vector, and tried again by the next index (no mark says it was tried): listed again, the same row.
        self.assertEqual([(photo_fingerprints.NO_VECTOR,)], [owed.reasons for owed in self.owed(self.folder).values()])
        again = " ".join(self.index(self.folder).split())
        self.assertIn(" ".join((tagpup_cli.CLIPS_LINE % (0, 0, 0, 1)).split()), again)
        self.assertEqual((photo_id, "video", 64), ids(self.library)["harbour.mp4"])

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


def frame(colour):
    """A frame as ffmpeg hands one back (video_frames.Frames.pictures): JPEG bytes."""
    out = io.BytesIO()
    Image.new("RGB", (32, 24), colour).save(out, "JPEG", quality=95)
    return out.getvalue()


#: A clip's five frames, each its own colour: what the stand-in CLIP's vectors tell apart.
FIVE = tuple(frame((40 * n, 90, 200 - 40 * n)) for n in range(5))


@unittest.skipIf(own_home.installed_exiftool() is None, "ExifTool not installed")
class AClipsVectorWhenItFails(unittest.TestCase):
    """How an index of clips fails, ffmpeg stood in for at its seam (video_frames.frames): each clip is a file of an
    .mp4's first bytes, which ExifTool reads as a clip; `self.answers` says what ffmpeg gives of each, by name."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="clip_vectors_")
        self.models = StandIns(self)
        self.library = self.home.library("slipway.db")
        self.assertEqual(1, library_actions.create(self.library).changed)
        self.folder = os.path.join(self.home.root, "Slipway")
        os.makedirs(self.folder)
        self.answers, self.read = {}, []

        def frames(tools, path, count, *args, **kwargs):
            self.read.append(os.path.basename(path))
            answer = self.answers.get(os.path.basename(path), video_frames.Frames(FIVE[:count], None))
            return answer(path) if callable(answer) else answer
        for patcher in (mock.patch.object(video_frames, "frames", frames),
                        mock.patch.object(config, "ffmpeg_problem", return_value=None),
                        mock.patch.object(config, "locate_ffmpeg", return_value=config.Ffmpeg("ffmpeg", "ffprobe", None))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def clip_file(self, name):
        path = os.path.join(self.folder, name)
        with open(path, "wb") as handle:
            handle.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048)
        return path

    def index(self, code=0):
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.library, "index", self.folder])
        self.assertEqual(code, result.exit_code, result.output)
        return " ".join(result.output.split())

    owed = AClipIsIndexedAsAVideo.owed

    def assert_made(self, *names):
        found = rows(self.library)
        self.assertEqual({name: True for name in names}, {name: found[name][2] for name in names})
        self.assertEqual({name: 4 for name in names}, {name: n for name, n in frames_held(self.library).items()
                                                       if name in names})

    def test_an_index_stopped_while_a_clip_is_shown_clip_writes_nothing_of_it_and_the_next_one_makes_it(self):
        photo(os.path.join(self.folder, "sail.jpg"), (30, 30, 200))
        self.clip_file("harbour.mp4")
        self.clip_file("jetty.mp4")

        def stopped(model, image_inputs):
            raise KeyboardInterrupt()
        with mock.patch.object(clip_model.ClipModel, "embed_inputs", stopped):
            self.index(code=1)

        # The photos were recorded before the clips; nothing of the clips' chunk was, not even a row.
        self.assertEqual({"sail.jpg": ("photo", True)}, {name: (kind, held) for name, (kind, _taken, held)
                                                        in rows(self.library).items()})
        self.assertEqual([], os.listdir(Library(self.library).locks), "a clip's lock was left held")
        self.index()
        self.assert_made("harbour.mp4", "jetty.mp4")
        self.assertEqual({}, self.owed(self.folder))

    def test_a_clip_another_index_holds_is_passed_over_said_and_made_by_the_next_index(self):
        held = self.clip_file("harbour.mp4")
        self.clip_file("jetty.mp4")
        other = PathLocker(lock_dir=Library(self.library).locks)   # another index of the folder, alive
        self.assertTrue(other.acquire(held))

        output = self.index()

        self.assertIn("1 photo(s) were skipped because another process holds their lock", output)
        self.assertNotIn("harbour.mp4", rows(self.library), "a clip another index holds was recorded")
        self.assertNotIn("harbour.mp4", self.read, "a clip another index holds was read")
        self.assert_made("jetty.mp4")
        other.release(held)
        self.index()
        self.assert_made("harbour.mp4", "jetty.mp4")

    def test_a_clip_ffmpeg_cannot_read_now_is_recorded_unlisted_and_made_by_the_next_index(self):
        self.clip_file("harbour.mp4")
        self.clip_file("jetty.mp4")
        self.answers["harbour.mp4"] = video_frames.Frames((), video_frames.SHARE_AWAY, video_frames.THE_MOMENT)
        self.answers["jetty.mp4"] = video_frames.Frames((), video_frames.NO_FRAME, video_frames.THE_FILE)

        output = self.index()

        self.assertIn(" ".join((tagpup_cli.CLIPS_LINE % (0, 0, 1, 1)).split()), output)
        self.assertIn(" ".join((tagpup_cli.CLIPS_UNREAD % 1).split()), output)
        self.assertNotIn(video_frames.SHARE_AWAY, output, "a clip ffmpeg could not read for the moment was listed")
        self.assertEqual({"harbour.mp4": ("video", False), "jetty.mp4": ("video", False)},
                         {name: (kind, held) for name, (kind, _taken, held) in rows(self.library).items()})
        self.assertEqual({(photo_fingerprints.NO_VECTOR,)}, {owed.reasons for owed in self.owed(self.folder).values()})

        self.answers.clear()   # the share is back; the file is one ffmpeg reads (replaced from a backup)
        self.assertIn(" ".join((tagpup_cli.CLIPS_LINE % (0, 2, 0, 0)).split()), self.index())
        self.assert_made("harbour.mp4", "jetty.mp4")
        self.assertEqual({}, self.owed(self.folder))

    def test_a_clip_changed_as_its_frames_were_read_is_owed_again_and_made_of_the_file_as_it_is(self):
        path = self.clip_file("harbour.mp4")

        def changed_as_read(clip_path):
            with open(clip_path, "ab") as handle:   # the owner trims it in another program meanwhile
                handle.write(b"\x00" * 512)
            later = os.stat(clip_path).st_mtime + 10
            os.utime(clip_path, (later, later))
            return video_frames.Frames(FIVE, None)
        self.answers["harbour.mp4"] = changed_as_read
        self.index()

        # Its vector is stamped with the file as it was before the read: not the file's now.
        self.assertEqual([(photo_fingerprints.STALE,)], [owed.reasons for owed in self.owed(self.folder).values()])
        del self.answers["harbour.mp4"]
        self.read.clear()
        self.index()
        self.assertEqual(["harbour.mp4"], self.read)
        self.assertEqual({}, self.owed(self.folder))
        self.assertEqual(os.path.getsize(path), ids(self.library)["harbour.mp4"][2])

    def test_a_folder_with_no_clip_is_indexed_as_before(self):
        for n in range(3):
            photo(os.path.join(self.folder, "sail %d.jpg" % n), (30 + 60 * n, 30, 200))

        output = self.index()

        self.assertNotIn("video", output)
        self.assertEqual([], self.read, "ffmpeg was asked about a folder of photos")
        self.assertEqual({"sail %d.jpg" % n: ("photo", True) for n in range(3)},
                         {name: (kind, held) for name, (kind, _taken, held) in rows(self.library).items()})


if __name__ == "__main__":
    unittest.main()
