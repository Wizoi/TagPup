"""A clip in the grid shows its poster frame, kept in the thumbnail cache as a photo's thumbnail is (scenario C9;
tagpup.services.thumbnails, tagpup.files.thumbs.poster, tagpup.files.video_frames; owner, 2026-11-05: the middle frame,
warmed ahead by `thumbs warm`).

What the owner lives through: a folder of clips scrolled fast (one ffmpeg run a clip, two cards of one clip one run, never
more than thumbs.POSTER_RUNS at once), the same folder opened again (the poster kept, no ffmpeg), a clip renamed or
edited by another program, a machine with no ffmpeg (a placeholder, nothing kept, ffmpeg seen once it is installed), a
share asleep (one bounded look, no ffmpeg held on it), a clip cut short by a copy that failed (a placeholder, remembered),
and `thumbs warm` making the posters ahead. The clips are made here by the machine's real ffmpeg from its test pattern;
the cases that need it are skipped, saying so, on a machine without one (as tests/test_frames_of_a_video.py is). The rows
are recorded as the indexer records a read (tests/view_library.py, photo_rows.add_read). Fictional names only.
"""
import io
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402
from PIL import Image  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.core.result import Unavailable  # noqa: E402
from tagpup.files import shares, thumbs, video_frames  # noqa: E402
from tagpup.services import thumbnails  # noqa: E402

TOOLS = config.locate_ffmpeg()
NO_FFMPEG = config.Ffmpeg(None, None, config.FFMPEG_MISSING)
NEEDS_FFMPEG = unittest.skipIf(TOOLS.problem, "ffmpeg is not installed on this machine (winget install Gyan.FFmpeg): %s"
                               % TOOLS.problem)


def make_clip(path, seconds=2, size="480x270"):
    """A clip of ffmpeg's test pattern at `path`, `seconds` long (made once and kept, tests/corpus.py)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    corpus.clip(path, ["-y", "-loglevel", "error", "-f", "lavfi",
                       "-i", "testsrc2=size=%s:rate=10:duration=%g" % (size, seconds), "-pix_fmt", "yuv420p"], TOOLS.ffmpeg)


def entries_of(library):
    return sorted(os.path.basename(each[4]) for each in thumbs.entries(library.thumbs))


def size_of(content):
    return Image.open(io.BytesIO(content)).size


class Runs:
    """Every ffmpeg (not ffprobe) run started through processes.start, with where it sought to."""

    def __init__(self, testcase):
        self.seeks, real = [], processes.start

        def start(args, *more, **kwargs):
            if os.path.basename(args[0]).lower().startswith("ffmpeg"):
                self.seeks.append(float(args[args.index("-ss") + 1]) if "-ss" in args else None)
            return real(args, *more, **kwargs)

        patcher = mock.patch.object(processes, "start", start)
        patcher.start()
        testcase.addCleanup(patcher.stop)

    @property
    def count(self):
        return len(self.seeks)


class Clips(unittest.TestCase):
    def setUp(self):
        self.vl = ViewLibrary(self)
        self.library = self.vl.library
        self.path = os.path.join(self.vl.pictures, "Regatta", "harbour.mp4")
        self.make()
        self.clip = self.vl.photo("Regatta", "harbour.mp4")
        self.assertEqual([("video",)], self.vl.rows("SELECT kind FROM photos WHERE id = ?", self.clip))
        warned = mock.patch.object(thumbnails, "_warned", set())
        warned.start()
        self.addCleanup(warned.stop)

    def make(self):
        """The clip's file: a real one (overridden where ffmpeg is not needed)."""
        make_clip(self.path)

    def serve(self, photo_id=None, find=config.locate_ffmpeg):
        return thumbnails.serve(self.library, photo_id or self.clip, find)


@NEEDS_FFMPEG
class APosterIsMadeOnceAndKept(Clips):
    def test_the_first_card_makes_the_middle_frame_and_the_next_finds_it_kept(self):
        runs = Runs(self)
        first = self.serve()
        self.assertEqual((thumbnails.OK, True), (first.kind, first.kept))
        self.assertEqual(thumbs.SIZE, max(size_of(first.content)), "a poster is a thumbnail's size")
        self.assertEqual(1, runs.count)
        self.assertGreater(runs.seeks[0], 0.7, "the middle frame of two seconds, not the first")
        self.assertLess(runs.seeks[0], 1.3)
        self.assertEqual(1, len(entries_of(self.library)))
        again = self.serve()
        self.assertEqual((first.content, first.etag, True), (again.content, again.etag, again.kept))
        self.assertEqual(1, runs.count, "kept by its stamp: no ffmpeg the second time")

    def test_the_grid_is_answered_the_poster_by_id_and_the_browser_may_keep_it(self):
        app, home = web_client.app_for(self, "tagpup")
        vl = ViewLibrary(self, "regatta", home=home)
        path = os.path.join(vl.pictures, "Coast", "swim.mov")
        make_clip(path, seconds=1)
        clip = vl.photo("Coast", "swim.mov")
        mtime = vl.rows("SELECT mtime FROM photos WHERE id = ?", clip)[0][0]
        reply = app.test_client().get("/regatta/api/photo-thumb", query_string={"id": clip, "v": repr(float(mtime))})
        self.assertEqual((200, "ok"), (reply.status_code, reply.headers["X-TagPup-Thumb"]))
        self.assertIn("immutable", reply.headers["Cache-Control"])
        self.assertEqual(thumbs.SIZE, max(size_of(reply.data)))

    def test_two_cards_of_one_clip_at_once_are_one_ffmpeg_run(self):
        runs, answers = Runs(self), []
        started, release = threading.Event(), threading.Event()
        real = video_frames.poster

        def slow(*args, **kwargs):
            started.set()
            release.wait(10)
            return real(*args, **kwargs)

        with mock.patch.object(video_frames, "poster", slow):
            first = threading.Thread(target=lambda: answers.append(self.serve()))
            first.start()
            self.assertTrue(started.wait(10))
            second = threading.Thread(target=lambda: answers.append(self.serve()))
            second.start()
            release.set()
            first.join(30)
            second.join(30)
        self.assertEqual(1, runs.count)
        self.assertEqual([thumbnails.OK, thumbnails.OK], [answer.kind for answer in answers])
        self.assertEqual(answers[0].content, answers[1].content)

    def test_a_folder_of_clips_scrolled_fast_runs_at_most_three_ffmpeg_at_once(self):
        ids = [self.clip]
        for n in range(7):
            make_clip(os.path.join(self.vl.pictures, "Regatta", "lap%d.mp4" % n), seconds=1)
            ids.append(self.vl.photo("Regatta", "lap%d.mp4" % n))
        guard, running, most = threading.Lock(), [0], [0]
        real = video_frames.poster

        def counted(*args, **kwargs):
            with guard:
                running[0] += 1
                most[0] = max(most[0], running[0])
            try:
                time.sleep(0.05)
                return real(*args, **kwargs)
            finally:
                with guard:
                    running[0] -= 1

        answers = []
        with mock.patch.object(video_frames, "poster", counted):
            cards = [threading.Thread(target=lambda each=each: answers.append(self.serve(each))) for each in ids]
            for card in cards:
                card.start()
            for card in cards:
                card.join(60)
        self.assertEqual([thumbnails.OK] * 8, [answer.kind for answer in answers])
        self.assertLessEqual(most[0], thumbs.POSTER_RUNS)
        self.assertEqual(8, len(entries_of(self.library)))


@NEEDS_FFMPEG
class AClipThatChanges(Clips):
    def test_a_renamed_clip_keeps_its_id_and_its_poster_is_made_once_under_its_new_name(self):
        self.serve()
        before = entries_of(self.library)
        new_path = os.path.join(os.path.dirname(self.path), "harbour swim.mp4")
        os.rename(self.path, new_path)
        self.assertEqual((1, []), photo_rows.move(self.library.path, {self.path: new_path}))
        runs = Runs(self)
        self.assertEqual(thumbnails.OK, self.serve().kind)
        self.assertEqual(thumbnails.OK, self.serve().kind)
        self.assertEqual(1, runs.count)
        after = entries_of(self.library)
        self.assertEqual(1, len(after), "the old name's entry is deleted as the new one is made")
        self.assertTrue(after[0].startswith("%d_" % self.clip))
        self.assertNotEqual(before[0].split("_")[1], after[0].split("_")[1], "the path hash changed")

    def test_a_clip_another_program_changed_is_made_again(self):
        first = self.serve()
        make_clip(self.path, seconds=3, size="320x320")
        os.utime(self.path, (1_700_000_000, 1_700_000_000))
        runs = Runs(self)
        again = self.serve()
        self.assertEqual((thumbnails.OK, 1), (again.kind, runs.count))
        self.assertEqual((thumbs.SIZE, thumbs.SIZE), size_of(again.content))
        self.assertNotEqual(first.etag, again.etag)
        self.assertEqual(1, len(entries_of(self.library)))

    def test_a_clip_whose_file_is_gone_is_shown_its_last_poster(self):
        self.serve()
        os.remove(self.path)
        runs = Runs(self)
        gone = self.serve()
        self.assertEqual((thumbnails.LAST_KNOWN, 0), (gone.kind, runs.count))
        self.assertEqual(thumbs.SIZE, max(size_of(gone.content)))

    def test_a_clip_cut_short_is_the_placeholder_kept_nowhere_and_not_run_again(self):
        whole = os.path.join(self.vl.home.root, "Elsewhere", "whole.mp4")
        make_clip(whole)
        with open(whole, "rb") as handle:
            head = handle.read(64)   # a copy that failed part way: the start of the file and nothing after
        with open(self.path, "wb") as handle:
            handle.write(head)
        runs = Runs(self)
        answer = self.serve()
        self.assertEqual((thumbnails.UNREADABLE, False, None), (answer.kind, answer.kept, answer.etag))
        self.assertEqual(thumbs.placeholder(), answer.content)
        self.assertEqual([], entries_of(self.library))
        started = runs.count
        self.assertEqual(thumbnails.UNREADABLE, self.serve().kind)
        self.assertEqual(started, runs.count, "remembered by its stamp: ffmpeg is not asked again")


@NEEDS_FFMPEG
class WarmingAhead(Clips):
    def test_warm_makes_the_posters_counts_them_and_a_second_run_finds_them(self):
        self.vl.photo("Regatta", "boat.jpg", real=True, size=(400, 300))
        dry = thumbnails.warm(self.library, config.locate_ffmpeg)
        self.assertEqual((2, 2, 0), (dry["photos"], dry["to_make"], dry["made"]))
        self.assertEqual([], entries_of(self.library), "a dry run writes nothing")
        made = thumbnails.warm(self.library, config.locate_ffmpeg, apply=True)
        self.assertEqual((2, 2, 1), (made["photos"], made["made"], made["posters"]))
        again = thumbnails.warm(self.library, config.locate_ffmpeg, apply=True)
        self.assertEqual((2, 0, 0), (again["present"], again["made"], again["posters"]))
        runs = Runs(self)
        self.assertTrue(self.serve().kept)
        self.assertEqual(0, runs.count, "the card finds the poster warm made")


@NEEDS_FFMPEG
class AClipPastTheCardsDeadline(Clips):
    """A deadline is the moment's (contending ffmpeg runs, a slow share), not the file's: no verdict is kept."""

    def watch(self):
        real, asked = video_frames.poster, []

        def poster(tools, path, max_side=video_frames.MAX_SIDE, seconds=video_frames.DEFAULT_SECONDS):
            asked.append(seconds)
            return real(tools, path, max_side=max_side, seconds=seconds)

        patcher = mock.patch.object(video_frames, "poster", poster)
        patcher.start()
        self.addCleanup(patcher.stop)
        return asked

    def test_a_poster_past_the_cards_deadline_is_try_again_remembers_nothing_and_warm_makes_it_with_more_time(self):
        asked = self.watch()
        with mock.patch.object(thumbs, "POSTER_SECONDS", 0.001):
            for _ in range(2):
                with self.assertRaises(Unavailable):
                    self.serve()
        self.assertEqual([0.001, 0.001], asked, "the second ask runs ffmpeg again: nothing was remembered")
        self.assertEqual([], entries_of(self.library))
        made = thumbnails.warm(self.library, config.locate_ffmpeg, apply=True)
        self.assertEqual((1, 1, 0, 0), (made["made"], made["posters"], made["damaged"], made["too_long"]))
        self.assertEqual(thumbs.WARM_POSTER_SECONDS, asked[-1], "warm waits longer than a card does")
        self.assertGreater(thumbs.WARM_POSTER_SECONDS, thumbs.POSTER_SECONDS)

    def test_warm_counts_a_clip_past_even_its_deadline_apart_from_the_damaged_and_tries_it_next_run(self):
        asked = self.watch()
        with mock.patch.object(thumbs, "WARM_POSTER_SECONDS", 0.001):
            late = thumbnails.warm(self.library, config.locate_ffmpeg, apply=True)
        self.assertEqual((0, 0, 1), (late["made"], late["damaged"], late["too_long"]))
        self.assertEqual((0, 0), (late["failed"], late["posters"]))
        self.assertEqual([], entries_of(self.library))
        again = thumbnails.warm(self.library, config.locate_ffmpeg, apply=True)
        self.assertEqual((1, 0), (again["made"], again["too_long"]))
        self.assertEqual(2, len(asked), "retried: nothing was remembered")


class NoFfmpegOrNoAnswer(Clips):
    """No ffmpeg run is needed, or allowed, here: the clip's bytes are only what a .mp4 starts with."""

    def make(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "wb") as handle:
            handle.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096)

    def test_a_machine_without_ffmpeg_is_the_placeholder_kept_nowhere_said_once_and_seen_once_installed(self):
        with self.assertLogs(thumbnails.logger, "WARNING") as said:
            for _ in range(3):
                answer = self.serve(find=lambda: NO_FFMPEG)
                self.assertEqual((thumbnails.UNREADABLE, False, None), (answer.kind, answer.kept, answer.etag))
        self.assertEqual(1, len(said.output), "said once in the process")
        self.assertIn("winget install", said.output[0])
        self.assertEqual([], entries_of(self.library))
        installed = config.Ffmpeg("ffmpeg.exe", "ffprobe.exe", None)
        with mock.patch.object(video_frames, "poster", return_value=video_frames.Frames((b"poster",), None)) as poster:
            answer = self.serve(find=lambda: installed)
        self.assertEqual((thumbnails.OK, True, b"poster"), (answer.kind, answer.kept, answer.content))
        self.assertEqual(1, poster.call_count, "not remembered as the clip's: ffmpeg is asked once it is there")

    def test_a_clip_on_a_share_asleep_is_one_bounded_look_and_no_ffmpeg(self):
        shares.forget()
        self.addCleanup(shares.forget)
        release = threading.Event()
        self.addCleanup(release.set)
        away = [self.vl.photo("", "", at="\\\\idziserver-test\\Pictures\\2024\\lap%02d.mp4" % n) for n in range(12)]
        real = os.stat

        def stat(path, *args, **kwargs):
            if str(path).startswith("\\\\"):
                release.wait(10)
                raise OSError(53, "The network path was not found")
            return real(path, *args, **kwargs)

        runs = Runs(self)
        started = time.monotonic()
        with mock.patch.object(shares.os, "stat", stat), mock.patch.object(shares, "SHARE_WAIT", 0.3):
            for clip in away:
                with self.assertRaises(Unavailable) as caught:
                    self.serve(clip)
                self.assertIn("video", str(caught.exception))
                self.assertIn("away", str(caught.exception))
        self.assertLess(time.monotonic() - started, 2.0, "12 waits of 0.3 s would be 3.6 s")
        self.assertEqual(1, sum(1 for each in threading.enumerate() if each.name == "ShareLook"), "one hung look")
        self.assertEqual(0, runs.count, "no ffmpeg is started on a share asleep")


if __name__ == "__main__":
    unittest.main()
