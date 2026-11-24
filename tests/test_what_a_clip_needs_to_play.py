"""What a clip needs before a browser plays it (C8; tagpup.files.convertible, video_frames.streams,
/api/clip/convert-plan; DECISIONS 2026-11-09).

What the owner lives through: a clip opened in the photo panel does not play -- an older format (.wmv, .mpg, .avi, .mod)
no browser opens, or an MPEG-4 Part 2 picture Chrome plays the sound of and shows nothing, with no error -- and the panel
asks the server what it needs and says it ("... Convert to play"). The table is the owner's (2026-11-09, measured in
Chrome 156): H.264 with AAC or PCM plays, in a .mov as in an .mp4, and is left alone; HEVC plays (the owner has its
extension); an older format is re-encoded whatever it holds. How it fails: the share stops answering between the look at
the clip and ffprobe's read (a sentence, nothing started), ffprobe hangs on a share gone silent (killed at the deadline,
a 503 with a sentence, never a hang), a file ffprobe cannot read as a video (400, the file's), an id that is a photo.

The clips are made here by the machine's ffmpeg (tagpup.config.locate_ffmpeg) in a home of the test's own, recorded as
the indexer records a read (ViewLibrary); skipped, saying so, on a machine without ffmpeg. Flask's test client, no port.
Fictional names only.
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.files import convertible, images, shares, video_frames  # noqa: E402
from tagpup.files.convertible import NOTHING, REENCODE  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402

TOOLS = config.locate_ffmpeg()
NEEDS_FFMPEG = unittest.skipIf(TOOLS.problem, "ffmpeg is not installed on this machine (winget install Gyan.FFmpeg): %s"
                               % TOOLS.problem)

#: The clips, each as its camera or program left one: (picture codec, sound codec) as ffmpeg encodes them.
CLIPS = {
    "harbour.mp4": ("libx264", "aac"),          # a phone's: plays
    "jetty.mov": ("libx264", "pcm_s16le"),      # a camera's .mov with PCM sound: plays (measured)
    "regatta.mov": ("mpeg4", "pcm_mulaw"),      # sound and no picture in Chrome, no error (measured)
    "regatta.wmv": ("wmv2", "wmav2"),           # an older format no browser opens
}


def make(folder, name):
    picture, sound = CLIPS[name]
    target = os.path.join(folder, name)
    return corpus.clip(target, ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=10:duration=1",
                                "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", picture, "-c:a", sound,
                                "-pix_fmt", "yuv420p"], TOOLS.ffmpeg)


class ThePlanOfEachClip(unittest.TestCase):
    def test_the_owners_table(self):
        for extension, picture, sound, action in (
                (".mp4", "h264", "aac", NOTHING),
                (".mov", "h264", "pcm_s16le", NOTHING),        # Chrome-verified
                (".MOV", "h264", "pcm_mulaw", NOTHING),        # Chrome-verified
                (".mov", "h264", "pcm_alaw", NOTHING),
                (".m4v", "hevc", "aac", NOTHING),              # the owner has the HEVC extension
                (".mp4", "vp9", "opus", NOTHING),
                (".mp4", "h264", None, NOTHING),               # no sound
                (".mov", "mpeg4", "pcm_mulaw", REENCODE),      # sound, no picture, no error in Chrome
                (".mp4", "mpeg4", "aac", REENCODE),
                (".mp4", "h264", "ac3", REENCODE),             # a sound that does not play: re-encoded too
                (".wmv", "wmv3", "wmav2", REENCODE),
                (".avi", "h264", "mp3", REENCODE),            # an older format is re-encoded whatever it holds
                (".mpg", "mpeg2video", "mp2", REENCODE),
                (".mpeg", "mpeg1video", "mp2", REENCODE),
                (".MOD", "mpeg2video", "ac3", REENCODE)):
            with self.subTest(extension=extension, picture=picture, sound=sound):
                plan = convertible.plan_for(extension, picture, sound)
                self.assertEqual(action, plan.action)
                self.assertTrue(plan.reason.endswith("."), plan.reason)
                if action != NOTHING:
                    self.assertIn("Convert to play", plan.reason)

    def test_every_older_format_the_index_keeps_as_a_video_is_one_no_browser_opens(self):
        older = {".wmv", ".mpg", ".mpeg", ".avi", ".mod"}
        self.assertEqual(older, set(images.VIDEO_TYPES) - convertible.OPENED)
        for extension in images.VIDEO_TYPES:
            with self.subTest(extension):
                self.assertEqual("video", images.kind_of("clip" + extension.upper()))
                self.assertTrue(images.content_type("clip" + extension).startswith("video/"))


@NEEDS_FFMPEG
class WhatAClipHolds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp(prefix="tagpup-streams-")
        cls.clips = {name: make(cls.folder, name) for name in CLIPS}
        cls.notes = os.path.join(cls.folder, "notes.mp4")
        with open(cls.notes, "w", encoding="utf-8") as handle:
            handle.write("A packing list, not a video.\n" * 50)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_each_clips_picture_and_sound_by_ffprobes_names(self):
        self.assertEqual({"harbour.mp4": ("h264", "aac", None), "jetty.mov": ("h264", "pcm_s16le", None),
                          "regatta.mov": ("mpeg4", "pcm_mulaw", None), "regatta.wmv": ("wmv2", "wmav2", None)},
                         {name: video_frames.streams(TOOLS, path)[:3] for name, path in self.clips.items()})

    def test_a_file_that_is_no_video_is_the_files_and_a_share_asleep_starts_nothing(self):
        self.assertEqual((None, None, video_frames.NOT_A_VIDEO, video_frames.THE_FILE, None),
                         video_frames.streams(TOOLS, self.notes))
        with mock.patch.object(shares, "stamp_of", return_value=shares.UNANSWERED), \
                mock.patch.object(processes, "start", side_effect=AssertionError("ffprobe started on a share asleep")):
            self.assertEqual((None, None, video_frames.SHARE_AWAY, video_frames.THE_MOMENT, None),
                             video_frames.streams(TOOLS, self.clips["harbour.mp4"]))


@NEEDS_FFMPEG
class AskingWhatAClipNeeds(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        folder = os.path.join(self.vl.pictures, "Clips")
        os.makedirs(folder, exist_ok=True)
        self.ids = {}
        for name in CLIPS:
            make(folder, name)
            self.ids[name] = self.vl.photo("Clips", name)
        self.photo = self.vl.photo("Clips", "pier.jpg")

    def ask(self, photo_id):
        return self.client.get("/library/api/clip/convert-plan", query_string={"id": photo_id})

    def test_each_clip_is_answered_with_what_it_needs_and_its_sentence(self):
        answers = {name: self.ask(photo_id) for name, photo_id in self.ids.items()}
        self.assertEqual({name: 200 for name in CLIPS}, {name: reply.status_code for name, reply in answers.items()})
        self.assertEqual({"harbour.mp4": NOTHING, "jetty.mov": NOTHING, "regatta.mov": REENCODE, "regatta.wmv": REENCODE},
                         {name: reply.get_json()["action"] for name, reply in answers.items()})
        self.assertIn("mpeg4", answers["regatta.mov"].get_json()["sentence"])
        self.assertIn(".wmv", answers["regatta.wmv"].get_json()["sentence"])
        for reply in answers.values():
            self.assertNotIn("regatta", reply.get_json()["sentence"], "a sentence names no file")

    def test_a_photo_or_no_photo_is_not_found_and_a_missing_id_is_refused(self):
        self.assertEqual(404, self.ask(self.photo).status_code)
        self.assertEqual(404, self.ask(99999).status_code)
        self.assertEqual(400, self.client.get("/library/api/clip/convert-plan").status_code)

    def test_a_file_ffprobe_cannot_read_as_a_video_is_refused_with_its_sentence(self):
        with open(self.vl.path_of(self.ids["harbour.mp4"]), "wb") as handle:
            handle.write(b"not a video at all " * 64)
        reply = self.ask(self.ids["harbour.mp4"])
        self.assertEqual((400, video_frames.NOT_A_VIDEO), (reply.status_code, reply.get_json()["error"]))

    def test_a_share_that_stops_answering_before_ffprobe_reads_is_a_503_and_nothing_started(self):
        real = shares.stamp_of
        looks = []

        def stamp_of(path, *more, **kwargs):
            looks.append(path)
            return real(path, *more, **kwargs) if len(looks) == 1 else shares.UNANSWERED

        with mock.patch.object(shares, "stamp_of", stamp_of), \
                mock.patch.object(processes, "start", side_effect=AssertionError("ffprobe started on a share away")):
            reply = self.ask(self.ids["regatta.wmv"])
        self.assertEqual((503, video_frames.SHARE_AWAY), (reply.status_code, reply.get_json()["error"]))
        self.assertIn("Retry-After", reply.headers)

    def test_an_ffprobe_that_never_answers_is_killed_at_the_deadline_and_said(self):
        started, real = [], processes.start

        def hung(args, *more, **kwargs):
            child = real([sys.executable, "-c", "import time; time.sleep(60)"], *more, **kwargs)
            started.append(child.pid)
            return child

        began = time.monotonic()
        with mock.patch.object(photo_actions, "PLAN_SECONDS", 1), mock.patch.object(processes, "start", hung):
            reply = self.ask(self.ids["regatta.mov"])
        self.assertLess(time.monotonic() - began, 30, "the page is answered, not left waiting on a share gone silent")
        self.assertEqual((503, video_frames.TOO_LONG % 1), (reply.status_code, reply.get_json()["error"]))
        self.assertEqual(1, len(started))
        self.assertFalse(processes.is_alive(started[0]), "the hung ffprobe was killed with its tree")


if __name__ == "__main__":
    unittest.main()
