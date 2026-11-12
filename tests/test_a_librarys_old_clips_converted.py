"""A library's clips in an older format converted once to H.264 and AAC in an .mp4 (F18; ONE-OFF, deleted with
scripts/convert_old_clips.py and tagpup/services/old_clip_convert.py after the owner's run, DECISIONS 2026-11-12).

What the owner lives through: TagPup stopped, the script run dry, then `--apply --limit 1`, `--limit 5`, all; a run
killed part-way (Ctrl+C twice, a closed window, a crash) and run again; a Recycle Bin that fills; then TagPup started,
whose sync of the whole library takes in the .mp4s. Each scenario runs the script as the owner does (its `main` or its
`run`) against a library of a home of its own, made by the real indexer (tagpup_cli.py index, StandIns for the models)
from clips the machine's ffmpeg generates: a .wmv of WMV 8 picture and WMA sound, with an ASF title (fictional). The clips' folder is a share as far as the test says (no Recycle Bin of its own), the Recycle Bin is stood
in for (the copy is taken away) and the mirror is a folder of the home: nothing reaches the owner's Bin. Skipped, saying
so, without ffmpeg or ExifTool.
"""
import contextlib
import gc
import hashlib
import io
import os
import struct
import sys
import time
import unittest
import uuid
import warnings
from unittest import mock

from click.testing import CliRunner

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import own_home  # noqa: E402
from test_an_index_reads_the_next_photos_ahead import StandIns, photo  # noqa: E402

import convert_old_clips as script  # noqa: E402
import tagpup_cli  # noqa: E402
from tagpup import config  # noqa: E402
from tagpup.core import paths, processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import clip_convert, recycle_bin, video_frames  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import old_clip_convert as old  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import db  # noqa: E402

TOOLS = config.locate_ffmpeg()
EXIFTOOL = own_home.installed_exiftool()

#: Each clip's title, as its ASF header holds it (fictional).
SAID = {"harbour.wmv": "Harbour at dusk", "jetty.wmv": "Jetty jumping", "launch.wmv": "Launch day"}


def older(folder, name, picture=("-c:v", "wmv2"), sound=("-c:a", "wmav2"), created=None, seconds=1):
    """A clip of `seconds` at `folder`/`name` of ffmpeg's test pattern and a tone, in an older format, with SAID's title
    when it has one, and its container's creation time `created` (ISO 8601) when given."""
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, name)
    title = SAID.get(name)
    described = ["-metadata", "title=" + title] if title else []
    described += ["-metadata", "creation_time=" + created] if created else []
    made = processes.run([TOOLS.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                          "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050", "-t", str(seconds), "-pix_fmt", "yuv420p",
                          "-map_metadata", "-1"] + list(picture) + list(sound) + described + [target],
                         capture_output=True)
    assert made.returncode == 0, made.stderr
    return target


#: Where an ASF file's header says how long it plays: its File Properties object.
FILE_PROPERTIES = uuid.UUID("8CABDCA1-A947-11CF-8EE4-00C00C205365").bytes_le


def header_short(path, seconds):
    """The ASF header of the .wmv at `path` made to say it plays `seconds` less than its packets do (negative: more), as
    the 62 photo_index .wmv of MPEG-4 v2 picture and MP3 sound say about 2.02 s less (their play duration written without
    their preroll) and the 108 of WMV 8 picture 0.47 to 3.59 s more: its File Properties object's play duration (after
    its id, size, file id, file size, date and packet count; 100 ns)."""
    with open(path, "r+b") as handle:
        data = handle.read()
        at = data.index(FILE_PROPERTIES) + 16 + 8 + 16 + 8 + 8 + 8
        play, = struct.unpack_from("<Q", data, at)
        handle.seek(at)
        handle.write(struct.pack("<Q", play - int(seconds * 1e7)))


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


@unittest.skipIf(TOOLS.problem, "ffmpeg is not installed on this machine (winget install Gyan.FFmpeg): %s" % TOOLS.problem)
@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class OldClips(unittest.TestCase):
    """A library whose folder Regatta holds clips in an older format, indexed, as the live libraries are: its root set
    (the home), and a photo in another folder under it (Pier): the setting of each scenario."""

    names = ("harbour.wmv",)
    #: The creation time the clips' ASF header holds (None: none).
    created = None

    def setUp(self):
        self.home = own_home.for_test(self, prefix="old_clips_")
        self.models = StandIns(self)
        self.library = self.home.library("harbour.db")
        self.assertEqual(1, library_actions.create(self.library).changed)
        self.assertTrue(library_settings.change(Library(self.library), {library_settings.ROOTS: self.home.root}).ok)
        self.folder = os.path.join(self.home.root, "Regatta")
        os.makedirs(self.folder)
        self.pier = os.path.join(self.home.root, "Pier")
        photo(os.path.join(self.pier, "sail.jpg"), (30, 30, 200))
        self.made = {name: self.make(name) for name in self.names}
        self.binned = []
        recycle_bin.forget_bin()
        self.addCleanup(recycle_bin.forget_bin)
        for patcher in (mock.patch.object(recycle_bin, "no_bin_reason", side_effect=self.reason),
                        mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=self.recycle),
                        mock.patch.object(recycle_bin, "mirror_root", return_value=os.path.join(self.home.root, "Mirror")),
                        mock.patch("tagpup.launcher.running", return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.index()

    def make(self, name):
        """The clip `name` in the folder Regatta, before the library indexes it."""
        return older(self.folder, name, created=self.created)

    def reason(self, path):
        # The clips' folder is a share, as far as the test says; this PC's mirror folder is not.
        return recycle_bin.NETWORK if paths.key(path).startswith(paths.key(self.folder)) else None

    def recycle(self, path):
        self.binned.append(os.path.basename(path))
        os.remove(path)
        return True

    def index(self):
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.library, "index", self.folder, self.pier])
        self.assertEqual(0, result.exit_code, result.output)
        return result.output

    def run_script(self, *args):
        """The script as the owner runs it: (exit code, what it printed)."""
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = script.main(["--library", self.library] + list(args))
        return code, printed.getvalue()

    def taken(self, name):
        """The Date Taken the library's row of the file `name` holds (None: undated)."""
        conn = db.connect(db.readonly_uri(self.library), uri=True)
        try:
            return {os.path.basename(path): taken for path, taken in conn.execute("SELECT path, taken FROM photos")}[name]
        finally:
            conn.close()

    def rows(self):
        """{file name: (id, its captions as the row holds them)} as the library holds them."""
        conn = db.connect(db.readonly_uri(self.library), uri=True)
        try:
            return {os.path.basename(path): (photo_id, captions)
                    for photo_id, path, captions in conn.execute("SELECT id, path, captions FROM photos")}
        finally:
            conn.close()

    def listed(self):
        return sorted(os.listdir(self.folder))

    def assert_converted(self, name):
        made = os.path.join(self.folder, os.path.splitext(name)[0] + ".mp4")
        streams = video_frames.streams(TOOLS, made)
        self.assertEqual(("h264", "aac"), (streams.video, streams.audio), "the .mp4 is not H.264 and AAC")
        self.assertFalse(os.path.exists(self.made[name]), "the original is still there")
        self.assertIn(name, self.binned, "the original did not go to the Recycle Bin")

    def temp_of(self, name):
        return clip_convert.temp_of(clip_convert.target_of(self.made[name]))


class ADryRun(OldClips):
    names = ("harbour.wmv", "jetty.wmv")

    def test_a_dry_run_counts_the_clips_and_writes_nothing(self):
        before = digest(self.library)
        files = {name: digest(path) for name, path in self.made.items()}

        code, said = self.run_script()

        self.assertEqual(0, code, said)
        self.assertIn("2 clip(s) to look at (.wmv 2); to convert 2", said)
        self.assertIn("A dry run: nothing was written", said)
        self.assertEqual(before, digest(self.library), "the library was written")
        wal = self.library + "-wal"
        self.assertEqual(0, os.path.getsize(wal) if os.path.exists(wal) else 0, "a frame was written to the library")
        self.assertEqual(files, {name: digest(path) for name, path in self.made.items()}, "a clip was written")
        self.assertEqual(sorted(self.names), self.listed(), "a file was left beside the clips")
        self.assertEqual([], self.binned)


class OneClipEndToEnd(OldClips):
    def test_a_clip_is_converted_its_original_binned_and_the_next_sync_and_index_take_in_the_mp4(self):
        old_id = self.rows()["harbour.wmv"][0]

        code, said = self.run_script("--apply")

        self.assertEqual(0, code, said)
        self.assertIn("Wrote 1 clip(s)", said)
        self.assertEqual(["harbour.mp4"], self.listed(), "a temp or a marker was left, or the original kept")
        self.assert_converted("harbour.wmv")
        self.assertIn("harbour.wmv", self.rows(), "the script wrote the library")

        # The steady state: the sync of the whole library, as the owner runs it (or TagPup's catch-up as it starts):
        # not a move (another name, another size, no identity), so the old row removed and the folder indexed.
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.library, "sync", "--apply"])
        self.assertEqual(0, result.exit_code, result.output)
        said = " ".join(result.output.split())
        self.assertIn("1 of the missing row(s) were removed", said)
        self.assertNotIn("harbour.wmv", self.rows(), "the sync kept the old row: %s" % said)
        # The sync indexes the folder it found the .mp4 in as a child process (tagpup_cli.py index), which here has
        # no StandIns for the models: the same index, run in this process.
        self.assertIn("Indexing 1 folder(s) with new files", said)
        self.index()
        rows = self.rows()
        photo_id, captions = rows["harbour.mp4"]
        self.assertNotEqual(old_id, photo_id)
        self.assertEqual('["Harbour at dusk"]', captions, "the title was not read")
        self.assertTrue(sync.sync(Library(self.library), apply=False, exiftool_path=EXIFTOOL).details["in_step"])

    def test_a_conversion_leaves_no_handle_of_ffmpeg_open(self):
        # 629 clips in one run: two pipes left open a conversion were about 1,258 handles of this process.
        with warnings.catch_warnings(record=True) as warned:
            warnings.simplefilter("always", ResourceWarning)
            code, said = self.run_script("--apply")
            gc.collect()
        self.assertEqual(0, code, said)
        self.assertEqual([], [str(each.message) for each in warned if issubclass(each.category, ResourceWarning)])

    def test_a_running_app_refuses_the_run_and_nothing_is_touched(self):
        with mock.patch("tagpup.launcher.running", return_value=[object()]):
            code, said = self.run_script("--apply")
        self.assertEqual(1, code)
        self.assertIn("TagPup is running", said)
        self.assertEqual(["harbour.wmv"], self.listed())
        self.assertEqual([], self.binned)


class AnUndatedClip(OldClips):
    """A .wmv whose ASF header holds the time the file was made (387 of photo_index's 503 .wmv hold one, counted
    2026-11-12; weeks to months after the capture): an older clip stays undated (DECISIONS 2026-11-09, item 6), and so
    does its .mp4."""
    created = "2004-06-15T10:20:30Z"

    def test_the_mp4_of_a_wmv_holding_a_creation_time_is_indexed_undated(self):
        self.assertIsNone(self.taken("harbour.wmv"), "the .wmv itself was dated")

        code, said = self.run_script("--apply")

        self.assertEqual(0, code, said)
        self.assertEqual(["harbour.mp4"], self.listed())
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.library, "sync", "--apply"])
        self.assertEqual(0, result.exit_code, result.output)
        self.index()
        self.assertIsNone(self.taken("harbour.mp4"), "the .wmv's ASF creation time was carried as a Date Taken")


class AHeaderShortOfItsPackets(OldClips):
    """A .wmv whose ASF header says it is 2.02 s shorter than its packets (62 photo_index .wmv): its copy holds every
    packet, so it is as long as the packets, and is verified by them, not refused against the header every run."""
    #: How much less than its packets (4 s) the header says.
    short = 2.024

    def make(self, name):
        made = older(self.folder, name, seconds=4)
        header_short(made, self.short)
        return made

    def test_it_is_converted_and_verified_by_its_packets(self):
        header = video_frames.streams(TOOLS, self.made["harbour.wmv"]).duration
        self.assertGreater(abs(header - 4), 1.5, "the header says the packets' length")

        code, said = self.run_script("--apply")

        self.assertEqual(0, code, said)
        self.assertIn("Wrote 1 clip(s)", said)
        self.assertEqual(["harbour.mp4"], self.listed())
        self.assertGreater(video_frames.streams(TOOLS, os.path.join(self.folder, "harbour.mp4")).duration, 3.5)


class AHeaderLongerThanItsPackets(AHeaderShortOfItsPackets):
    """A .wmv whose ASF header says it is 2.4 s longer than its packets (as the 108 photo_index .wmv of WMV 8 picture say
    0.47 to 3.59 s more): verified by its packets too."""
    short = -2.4


class APictureNamedAsAClip(OldClips):
    """A JPEG file named .avi, which the library holds as a clip (27 photo_index rows): left as it is, said apart from the
    clips, and no reason for the run to end as if something were left."""
    names = ("harbour.wmv", "regatta.avi")

    def make(self, name):
        if not name.endswith(".avi"):
            return super().make(name)
        return photo(os.path.join(self.folder, name), (200, 120, 30))

    def test_it_is_counted_apart_and_left_as_it_is(self):
        self.assertEqual(sorted(self.names), sorted(name for name in self.rows() if name in self.names),
                         "the .avi was not indexed as a clip's row")
        picture = digest(self.made["regatta.avi"])

        code, said = self.run_script()
        self.assertEqual(0, code, said)
        self.assertIn("to convert 1", said)
        self.assertIn("A picture with a video extension: left as it is (1)", said)

        code, said = self.run_script("--apply")

        self.assertEqual(0, code, said)
        self.assertIn("Wrote 1 clip(s)", said)
        self.assertIn("0 not converted", said)
        self.assertIn("A picture with a video extension: left as it is (1)", said)
        self.assertEqual(["harbour.mp4", "regatta.avi"], self.listed())
        self.assertEqual(picture, digest(self.made["regatta.avi"]))


class KilledPartWay(OldClips):
    def test_killed_after_the_marker_the_next_run_deletes_the_original_and_renames_the_copy(self):
        with mock.patch.object(recycle_bin, "delete_file", side_effect=KeyboardInterrupt):
            code, said = self.run_script("--apply")
        self.assertEqual(130, code, said)
        temp = self.temp_of("harbour.wmv")
        self.assertEqual(sorted(["harbour.wmv", os.path.basename(temp), os.path.basename(old.marker_of(temp))]),
                         self.listed())

        code, said = self.run_script("--apply")

        self.assertEqual(0, code, said)
        self.assertIn("Wrote 1 clip(s)", said)
        self.assertEqual(["harbour.mp4"], self.listed())
        self.assert_converted("harbour.wmv")

    def test_killed_after_the_delete_the_next_run_renames_the_copy(self):
        real = recycle_bin.delete_file

        def deleted_then_killed(path, for_good=False):
            real(path, for_good)
            raise KeyboardInterrupt

        with mock.patch.object(recycle_bin, "delete_file", side_effect=deleted_then_killed):
            code, said = self.run_script("--apply")
        self.assertEqual(130, code, said)
        temp = self.temp_of("harbour.wmv")
        self.assertEqual(sorted([os.path.basename(temp), os.path.basename(old.marker_of(temp))]), self.listed())

        code, said = self.run_script("--apply")

        self.assertEqual(0, code, said)
        self.assertIn("Wrote 1 clip(s)", said)
        self.assertEqual(["harbour.mp4"], self.listed())
        self.assert_converted("harbour.wmv")

    def test_a_stale_temp_is_removed_and_the_clip_converted_a_fresh_one_refuses(self):
        temp = self.temp_of("harbour.wmv")
        with open(temp, "wb") as handle:
            handle.write(b"\x00" * 64)

        code, said = self.run_script("--apply")
        self.assertIn(old.UNDER_WAY, said)
        self.assertEqual(sorted(["harbour.wmv", os.path.basename(temp)]), self.listed(), "a fresh temp was touched")
        self.assertNotEqual(0, code, "a clip left unconverted ended the run as if all were done: %s" % said)

        aged = time.time() - old.STALE - 60
        os.utime(temp, (aged, aged))
        code, said = self.run_script("--apply")

        self.assertEqual(0, code, said)
        self.assertIn("Wrote 1 clip(s)", said)
        self.assertEqual(["harbour.mp4"], self.listed())
        self.assert_converted("harbour.wmv")


class TheRecycleBinFull(OldClips):
    names = ("harbour.wmv", "jetty.wmv")

    def test_a_full_recycle_bin_stops_the_run_keeping_the_original_and_its_verified_copy(self):
        recycle_bin.forget_bin()
        with mock.patch.dict(os.environ, {"TAGPUP_RECYCLE_BIN": "10,10,0"}):
            code, said = self.run_script("--apply")
        recycle_bin.forget_bin()

        self.assertEqual(1, code, said)
        self.assertIn("Empty the Recycle Bin", said)
        temp = self.temp_of("harbour.wmv")
        self.assertEqual(sorted(["harbour.wmv", "jetty.wmv", os.path.basename(temp),
                                 os.path.basename(old.marker_of(temp))]), self.listed())
        self.assertEqual([], self.binned)

        code, said = self.run_script("--apply")   # the Bin emptied
        self.assertEqual(0, code, said)
        self.assertIn("Wrote 2 clip(s)", said)
        self.assertEqual(["harbour.mp4", "jetty.mp4"], self.listed())


class TheNameTaken(OldClips):
    names = ("harbour.wmv", "jetty.wmv")

    def test_an_mp4_of_its_name_as_a_file_or_as_a_row_leaves_the_clip_be(self):
        # jetty.mp4 is a row (indexed, then its file gone); harbour.mp4 a file the library has not read.
        older(self.folder, "jetty.mp4", picture=("-c:v", "libx264"), sound=("-c:a", "aac"))
        self.index()
        os.remove(os.path.join(self.folder, "jetty.mp4"))
        older(self.folder, "harbour.mp4", picture=("-c:v", "libx264"), sound=("-c:a", "aac"))
        files = {name: digest(os.path.join(self.folder, name)) for name in self.listed()}

        code, said = self.run_script("--apply")

        self.assertIn("name taken 2", said)
        self.assertIn("Wrote 0 clip(s)", said)
        self.assertEqual(files, {name: digest(os.path.join(self.folder, name)) for name in self.listed()})
        self.assertEqual([], self.binned)


class ALimit(OldClips):
    names = ("harbour.wmv", "jetty.wmv", "launch.wmv")

    def test_limit_1_then_5_converts_the_first_by_id_then_the_rest(self):
        first = min((item for item in self.rows().items() if item[0].endswith(".wmv")), key=lambda item: item[1][0])[0]

        code, said = self.run_script("--apply", "--limit", "1")
        self.assertEqual(0, code, said)
        self.assertIn("Wrote 1 clip(s)", said)
        self.assertEqual([first], self.binned)

        code, said = self.run_script("--apply", "--limit", "5")
        self.assertEqual(0, code, said)
        self.assertIn("Wrote 2 clip(s)", said)
        self.assertEqual(["harbour.mp4", "jetty.mp4", "launch.mp4"], self.listed())


class MPEG4Part2(OldClips):
    """An .mp4 whose picture is MPEG-4 Part 2 (no browser plays it): replaced by an .mp4 of its own name."""
    names = ()

    def setUp(self):
        super().setUp()
        self.made = {"harbour.mp4": older(self.folder, "harbour.mp4", picture=("-c:v", "mpeg4"), sound=("-c:a", "aac"))}
        self.index()

    def test_it_is_replaced_by_an_mp4_of_its_own_name_and_a_rerun_after_the_delete_finishes_it(self):
        code, said = self.run_script("--apply")
        self.assertIn("Wrote 0 clip(s)", said, "without --also-mpeg4-part-2 it is not looked at")

        real = recycle_bin.delete_file

        def deleted_then_killed(path, for_good=False):
            real(path, for_good)
            raise KeyboardInterrupt

        with mock.patch.object(recycle_bin, "delete_file", side_effect=deleted_then_killed):
            code, said = self.run_script("--apply", "--also-mpeg4-part-2")
        self.assertEqual(130, code, said)

        code, said = self.run_script("--apply", "--also-mpeg4-part-2")

        self.assertEqual(0, code, said)
        self.assertIn("Wrote 1 clip(s)", said)
        self.assertEqual(["harbour.mp4"], self.listed())
        streams = video_frames.streams(TOOLS, self.made["harbour.mp4"])
        self.assertEqual(("h264", "aac"), (streams.video, streams.audio))


if __name__ == "__main__":
    unittest.main()
