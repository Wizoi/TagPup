"""A video's frames and its poster, through the machine's ffmpeg (tagpup.files.video_frames, tagpup.config.locate_ffmpeg;
DECISIONS 2026-11-02, "VIDEO IS MEDIA TAGPUP MANAGES" 9 and 10).

Each clip is made here, in a folder of the test's own, by the real ffmpeg from its test pattern: a plain one, one under a
second, one carrying a rotation matrix, one with non-square pixels, a raw stream with no duration, one cut short after
its index and one before it, a file that is not a video. The whole class is skipped, saying so, on a machine with no
ffmpeg. What the owner would meet: five pictures, upright, at most 1024 on a side, a share asleep or ffmpeg missing or
hung said in a sentence and no exception, two calls at once each answered whole. Every child is started through
tagpup.core.processes (tests/test_rules_have_one_owner.py fails a module that calls subprocess itself).
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from PIL import Image, ImageChops, ImageStat

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tagpup import config  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.files import shares, video_frames  # noqa: E402

TOOLS = config.locate_ffmpeg()


def size_of(picture):
    return Image.open(io.BytesIO(picture)).size


def apart(one, other):
    """The mean difference of two pictures' pixels, 0 to 255."""
    return sum(ImageStat.Stat(ImageChops.difference(one.convert("L"), other.convert("L"))).mean)


class Started:
    """Every child started through processes.start: its arguments and pid."""

    def __init__(self):
        self.children = []
        self.real = processes.start

    def start(self, args, *more, **kwargs):
        child = self.real(args, *more, **kwargs)
        self.children.append((list(args), child.pid))
        return child

    def seeks(self):
        """Where each frame's run seeks to, None for one that reads the first frame unsought."""
        return [float(args[args.index("-ss") + 1]) if "-ss" in args else None
                for args, _pid in self.children if os.path.basename(args[0]).startswith("ffmpeg")]


@unittest.skipIf(TOOLS.problem, "ffmpeg is not installed on this machine (winget install Gyan.FFmpeg): %s" % TOOLS.problem)
class FramesOfAVideo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp(prefix="tagpup-video-")
        cls.clip = cls.make("clip.mp4", "testsrc2=size=64x32:rate=10:duration=3")
        cls.short = cls.make("short.mp4", "testsrc2=size=64x32:rate=10:duration=0.5")
        cls.wide = cls.make("wide.mp4", "testsrc2=size=2000x1000:rate=5:duration=1")
        cls.anamorphic = cls.make("anamorphic.mp4", "testsrc2=size=64x48:rate=10:duration=1", "-vf", "setsar=2")
        cls.rotated = cls.path("rotated.mp4")
        cls.ffmpeg("-display_rotation:v:0", "90", "-i", cls.clip, "-c", "copy", cls.rotated)
        cls.raw = cls.make("raw.h264", "testsrc2=size=64x32:rate=10:duration=2", "-f", "h264")
        # Large enough frames, a key frame every five, that half the file holds frames whole after its index.
        whole = cls.make("faststart.mp4", "testsrc2=size=320x240:rate=10:duration=4", "-g", "5", "-movflags", "+faststart")
        cls.cut_after_index = cls.cut(whole, "cut_after_index.mp4")
        cls.cut_before_index = cls.cut(cls.clip, "cut_before_index.mp4")   # ffmpeg writes the index (moov) last
        cls.not_a_video = cls.path("notes.mp4")
        with open(cls.not_a_video, "w", encoding="utf-8") as handle:
            handle.write("A shopping list, not a video.\n" * 50)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    @classmethod
    def path(cls, name):
        return os.path.join(cls.folder, name)

    @classmethod
    def ffmpeg(cls, *args):
        done = processes.run([TOOLS.ffmpeg, "-v", "error", "-y"] + list(args), capture_output=True, timeout=60)
        assert done.returncode == 0, done.stderr.decode("utf-8", "replace")

    @classmethod
    def make(cls, name, pattern, *more):
        target = cls.path(name)
        cls.ffmpeg("-f", "lavfi", "-i", pattern, "-c:v", "libx264", "-pix_fmt", "yuv420p", *more, target)
        return target

    @classmethod
    def cut(cls, source, name):
        with open(source, "rb") as handle:
            data = handle.read()
        target = cls.path(name)
        with open(target, "wb") as handle:
            handle.write(data[:len(data) // 2])
        return target

    def frames(self, path, count=5, **kwargs):
        started = Started()
        with mock.patch.object(processes, "start", started.start):
            found = video_frames.frames(TOOLS, path, count, **kwargs)
        return found, started

    def test_five_frames_equally_spaced_each_its_own_picture(self):
        found, started = self.frames(self.clip)
        self.assertIsNone(found.problem)
        self.assertEqual([(64, 32)] * 5, [size_of(p) for p in found.pictures])
        self.assertEqual(5, len(set(found.pictures)), "each frame from its own moment")
        self.assertEqual([0.29, 0.87, 1.45, 2.03, 2.61], started.seeks(),
                         "the middle of each fifth of the 2.9 s from the first frame to the start of the last")
        self.assertEqual(6, len(started.children), "one look at the duration, one run a frame, each through processes")

    def test_the_poster_is_one_frame_from_the_middle(self):
        found, started = self.frames(self.clip, 1)
        self.assertEqual(1, len(found.pictures))
        self.assertEqual([1.45], started.seeks())
        self.assertEqual(found.pictures, video_frames.poster(TOOLS, self.clip).pictures)

    def test_a_clip_under_a_second_still_gives_five(self):
        found, _started = self.frames(self.short)
        self.assertIsNone(found.problem)
        self.assertEqual(5, len(found.pictures))

    def test_a_stream_with_no_duration_gives_its_first_frame_unsought(self):
        found, started = self.frames(self.raw)
        self.assertIsNone(found.problem)
        self.assertEqual(1, len(found.pictures))
        self.assertEqual([None], started.seeks(), "ffmpeg cannot seek in a raw stream, even to 0")

    def test_a_rotated_video_comes_out_upright(self):
        plain = Image.open(io.BytesIO(self.frames(self.clip, 1)[0].pictures[0]))
        found, _started = self.frames(self.rotated, 1)
        turned = Image.open(io.BytesIO(found.pictures[0]))
        self.assertEqual((32, 64), turned.size)
        # The matrix says a quarter turn counterclockwise: the picture shown is the stored one turned that way.
        self.assertLess(apart(turned, plain.rotate(90, expand=True)), apart(turned, plain.rotate(-90, expand=True)) / 3)

    def test_the_longer_side_is_bounded_and_pixels_made_square(self):
        self.assertEqual((1024, 512), size_of(self.frames(self.wide, 1)[0].pictures[0]))
        self.assertEqual((512, 256), size_of(self.frames(self.wide, 1, max_side=512)[0].pictures[0]))
        self.assertEqual((128, 48), size_of(self.frames(self.anamorphic, 1)[0].pictures[0]))

    def test_a_file_cut_short_after_its_index_gives_the_frames_before_the_cut(self):
        found, _started = self.frames(self.cut_after_index)
        self.assertIsNone(found.problem)
        self.assertTrue(1 <= len(found.pictures) < 5, len(found.pictures))

    def test_what_ffmpeg_cannot_read_is_said_and_starts_no_frame(self):
        for path in (self.cut_before_index, self.not_a_video):
            with self.subTest(os.path.basename(path)):
                found, started = self.frames(path)
                self.assertEqual(((), video_frames.NOT_A_VIDEO), found)
                self.assertEqual(1, len(started.children), "the look at the duration only")

    def test_a_file_not_there_or_on_a_share_asleep_starts_nothing(self):
        found, started = self.frames(self.path("gone.mp4"))
        self.assertEqual(((), video_frames.NOT_THERE), (found.pictures, found.problem))
        self.assertEqual([], started.children)
        for stamp, said in ((shares.UNANSWERED, video_frames.SHARE_AWAY), (shares.CANNOT_READ, video_frames.CANNOT_READ)):
            with self.subTest(said), mock.patch.object(shares, "stamp_of", return_value=stamp):
                found, started = self.frames(self.clip)
                self.assertEqual(((), said), (found.pictures, found.problem))
                self.assertEqual([], started.children)

    def test_ffmpeg_past_its_deadline_is_killed_and_said(self):
        found, started = self.frames(self.clip, seconds=0.05)
        self.assertEqual(((), video_frames.TOO_LONG % 0.05), (found.pictures, found.problem))
        self.assertIn("0.05 s", found.problem)
        self.assertTrue(started.children, "a child was started, so there was something to kill")
        for _args, pid in started.children:
            self.assertFalse(processes.is_alive(pid))

    def test_two_calls_at_once_are_each_answered_whole(self):
        answers = []

        def ask():
            answers.append(video_frames.frames(TOOLS, self.clip, 5))
        threads = [threading.Thread(target=ask) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(4, len(answers))
        self.assertEqual({None}, {answer.problem for answer in answers})
        self.assertEqual(1, len({answer.pictures for answer in answers}), "the same five pictures each time")


class AHungProgram(unittest.TestCase):
    def test_a_program_that_never_answers_is_killed_with_its_tree_at_the_deadline(self):
        started = Started()
        began = time.monotonic()
        with mock.patch.object(processes, "start", started.start):
            done, problem = video_frames._run([sys.executable, "-c", "import time; time.sleep(60)"],
                                              time.monotonic() + 1, 1)
        self.assertIsNone(done)
        self.assertEqual(video_frames.TOO_LONG % 1, problem)
        self.assertLess(time.monotonic() - began, 30)
        self.assertFalse(processes.is_alive(started.children[0][1]))


class AChildThatCannotBeWaitedFor(unittest.TestCase):
    """_run promises a sentence, never an exception, and no ffmpeg left running whichever way the wait or the kill fails."""

    class Child:
        pid = 4242

        def __init__(self, fails):
            self.fails, self.killed, self.returncode = fails, False, None

        def communicate(self, timeout=None):
            if self.fails and not self.killed:
                raise self.fails
            return b"", b""

        def poll(self):
            return -9 if self.killed else None

        def kill(self):
            self.killed = True

    def run_with(self, child, kill_tree):
        with mock.patch.object(processes, "start", return_value=child), \
                mock.patch.object(processes, "kill_tree", kill_tree):
            return video_frames._run(["ffmpeg"], time.monotonic() + 5, 5)

    def test_a_wait_that_fails_otherwise_kills_the_child_and_says_so(self):
        child = self.Child(ValueError("pipe closed"))
        ended = []
        done, problem = self.run_with(child, ended.append)
        self.assertIsNone(done)
        self.assertTrue(problem and problem.endswith("."), problem)
        self.assertEqual([child.pid], ended)

    def test_a_taskkill_that_hangs_does_not_escape_and_the_child_is_still_killed(self):
        def hangs(_pid):
            raise subprocess.TimeoutExpired("the kill", 30)
        for fails in (subprocess.TimeoutExpired("ffmpeg", 5), ValueError("pipe closed")):
            with self.subTest(type(fails).__name__):
                child = self.Child(fails)
                done, problem = self.run_with(child, hangs)
                self.assertIsNone(done)
                self.assertTrue(problem)
                self.assertTrue(child.killed)


class LocatingFfmpeg(unittest.TestCase):
    """Where the machine's ffmpeg is: PATH, else winget's folders; found once and kept while it is there, looked for
    again while it is not. Fake programs in a folder of the test's own, PATH and LOCALAPPDATA pointed at it."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="tagpup-locate-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        empty = os.path.join(self.folder, "empty")
        os.makedirs(empty)
        environment = mock.patch.dict(os.environ, {"PATH": empty, "LOCALAPPDATA": self.folder})
        environment.start()
        self.addCleanup(environment.stop)
        config._FFMPEG.clear()
        self.addCleanup(config._FFMPEG.clear)
        self.packages = os.path.join(self.folder, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg_Microsoft.Winget.Source_x")

    def install(self, version):
        folder = os.path.join(self.packages, "ffmpeg-%s-full_build" % version, "bin")
        os.makedirs(folder)
        for name in ("ffmpeg", "ffprobe"):
            open(os.path.join(folder, name + (".exe" if os.name == "nt" else "")), "wb").close()
        return folder

    def test_none_then_installed_then_upgraded(self):
        self.assertEqual(config.Ffmpeg(None, None, config.FFMPEG_MISSING), config.locate_ffmpeg())
        old = self.install("8.1")
        found = config.locate_ffmpeg()
        self.assertIsNone(found.problem, "an install is found without a restart")
        self.assertEqual(old, os.path.dirname(found.ffmpeg))
        self.assertEqual(old, os.path.dirname(found.ffprobe))
        new = self.install("10.0.1")
        self.assertEqual(old, os.path.dirname(config.locate_ffmpeg().ffmpeg), "kept while it is there")
        shutil.rmtree(os.path.dirname(old))
        self.assertEqual(new, os.path.dirname(config.locate_ffmpeg().ffmpeg), "an upgrade moved it: found again")

    def test_the_newest_of_several_versions(self):
        self.install("9.0.2")
        newest = self.install("10.0.1")
        self.install("9.10")
        self.assertEqual(newest, os.path.dirname(config.locate_ffmpeg().ffmpeg))

    def test_ffmpeg_missing_is_a_sentence_and_starts_nothing(self):
        started = Started()
        with mock.patch.object(processes, "start", started.start):
            found = video_frames.frames(config.locate_ffmpeg(), __file__, 5)
        self.assertEqual(((), config.FFMPEG_MISSING), (found.pictures, found.problem))
        self.assertEqual([], started.children)


if __name__ == "__main__":
    unittest.main()
