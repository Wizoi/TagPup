"""Playing a video in the browser (C8): /api/video-file answers the ranges a <video> asks for, a piece at a time
(tagpup.services.photos.video, video_bytes; tagpup.files.shares.read_span; tagpup.web.responses.ranged), and the cards
and the record say which photos are videos (`media`).

What the owner lives through: a video played, sought back and forth quickly, closed half way, renamed by TagPup while it
plays, on a share that stops answering, and in two libraries whose ids collide. Flask's test client in a home of its own,
no port; the bounded reads on a "share" make their own threads (tagpup.files.shares.bounded), which the test does not.

No writer stores a video's row yet (M4a): a row is made as the indexer records a read (tests/photo_rows.py, through
ViewLibrary), then its kind set to 'video', as migration 37 made the column for. Fictional names only.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.files import in_place, lock_owners, shares  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402

#: The bytes of the fixture's video: no two neighbouring pieces alike, so a piece from the wrong place is seen.
CONTENT = bytes((n * 7 + n // 256) % 256 for n in range(1000))
SPAN, READ = 300, 100


class Videos(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.video = self.make_video(self.vl, "surf.mp4", CONTENT)
        self.path = self.vl.path_of(self.video)
        self.photo = self.vl.photo("Coast", "pier.jpg")
        for name, value in (("VIDEO_SPAN", SPAN), ("VIDEO_READ", READ)):
            patcher = mock.patch.object(photo_actions, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def make_video(vl, name, content):
        path = os.path.join(vl.pictures, "Clips", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content)
        video_id = vl.photo("Clips", name)
        # No writer stores 'video' until M4a: the kind is set as the indexer will set it.
        vl.conn.execute("UPDATE photos SET kind = 'video' WHERE id = ?", (video_id,))
        vl.conn.commit()
        return video_id

    def ask(self, video_id=None, asked=None, library="library", **kwargs):
        headers = {"Range": asked} if asked else {}
        return self.client.get("/%s/api/video-file" % library,
                               query_string={"id": self.video if video_id is None else video_id}, headers=headers,
                               **kwargs)


class Playing(Videos):
    def test_a_range_is_answered_with_its_bytes(self):
        reply = self.ask(asked="bytes=100-249")
        self.assertEqual(206, reply.status_code)
        self.assertEqual(CONTENT[100:250], reply.data)
        self.assertEqual(("bytes 100-249/1000", "bytes", "150", "video/mp4"),
                         (reply.headers["Content-Range"], reply.headers["Accept-Ranges"],
                          reply.headers["Content-Length"], reply.mimetype))
        self.assertEqual("max-age=86400", reply.headers["Cache-Control"])
        self.assertEqual(int(os.stat(self.path).st_mtime), reply.last_modified.timestamp())

    def test_an_open_ended_range_is_answered_a_span_at_a_time(self):
        reply = self.ask(asked="bytes=500-")
        self.assertEqual((206, "bytes 500-799/1000"), (reply.status_code, reply.headers["Content-Range"]))
        self.assertEqual(CONTENT[500:800], reply.data)
        rest = self.ask(asked="bytes=800-")
        self.assertEqual(("bytes 800-999/1000", CONTENT[800:]), (rest.headers["Content-Range"], rest.data))

    def test_no_range_is_the_first_span_never_the_whole_file(self):
        reply = self.ask()
        self.assertEqual((206, CONTENT[:SPAN], "bytes 0-299/1000", str(SPAN)),
                         (reply.status_code, reply.data, reply.headers["Content-Range"], reply.headers["Content-Length"]))
        self.assertIsNotNone(reply.last_modified)

    def test_a_range_that_cannot_be_answered_is_416_with_the_size(self):
        for asked in ("bytes=1000-", "bytes=0-1,5-6", "bytes=-2000"):
            with self.subTest(asked=asked):
                reply = self.ask(asked=asked)
                self.assertEqual((416, "bytes */1000"), (reply.status_code, reply.headers["Content-Range"]))

    def test_a_file_of_no_bytes_has_no_range_asked_or_not(self):
        empty = self.make_video(self.vl, "blank.mov", b"")
        for asked in (None, "bytes=0-"):
            with self.subTest(asked=asked):
                reply = self.ask(empty, asked=asked)
                self.assertEqual((416, "bytes */0"), (reply.status_code, reply.headers["Content-Range"]))


class WhatIsNotAVideo(Videos):
    def test_a_photo_an_unknown_id_and_a_file_gone_are_404_with_a_sentence(self):
        for video_id in (self.photo, 99999, -3, 2 ** 70):
            with self.subTest(video_id=video_id):
                reply = self.ask(video_id)
                self.assertEqual(404, reply.status_code)
                self.assertIn("no video", reply.get_json()["error"])
        os.remove(self.path)
        gone = self.ask()
        self.assertEqual(404, gone.status_code)
        self.assertIn("is not there", gone.get_json()["error"])

    def test_an_id_that_is_not_a_number_is_400(self):
        for wanted in ("surf", ""):
            with self.subTest(wanted=wanted):
                self.assertEqual(400, self.ask(wanted).status_code)


class WhileItPlays(Videos):
    def stream(self, asked):
        """(the reply, its pieces as they come): the body not read until asked, as a browser reads it."""
        reply = self.ask(asked=asked, buffered=False)
        self.addCleanup(reply.close)
        return reply, iter(reply.response)

    def test_closed_half_way_the_file_is_not_held(self):
        reply, pieces = self.stream("bytes=0-")
        self.assertEqual(CONTENT[:READ], next(pieces))
        reply.close()
        os.replace(self.path, self.path + ".moved")   # WinError 32 if a handle were held
        self.assertTrue(os.path.exists(self.path + ".moved"))

    def test_rapid_seeking_then_a_rename_ends_every_stream_short(self):
        streams = []
        for n in range(20):
            start = n * 37
            reply, pieces = self.stream("bytes=%d-" % start)
            self.assertEqual(206, reply.status_code)
            self.assertEqual(CONTENT[start:start + READ], next(pieces), "the first piece of the range from %d" % start)
            streams.append((start, pieces))
        os.replace(self.path, os.path.join(os.path.dirname(self.path), "surf renamed.mp4"))   # TagPup's own rename
        for start, pieces in streams:
            self.assertEqual(b"", b"".join(pieces), "the stream from %d ends at its first piece" % start)

    def test_a_file_rewritten_while_it_plays_sends_none_of_the_new_bytes(self):
        reply, pieces = self.stream("bytes=0-")   # the test client reads the first piece as it answers
        with open(self.path, "wb") as handle:
            handle.write(CONTENT[::-1] + b"longer")
        self.assertEqual(CONTENT[:READ], b"".join(pieces), "the piece read before, and nothing of the new file")

    def test_a_rename_while_a_piece_is_read_renames_and_the_piece_is_answered(self):
        """TagPup's own rename or replace of a clip lands while a piece of it is read (a clip's rename and delete, A5):
        the read shares delete, so the rename is not refused (WinError 32) for the length of the read; a save of it then
        still finds it open elsewhere (in_place._alone shares nothing)."""
        renamed = os.path.join(os.path.dirname(self.path), "surf renamed.mp4")
        real_fstat, seen = os.fstat, {}

        def renamed_mid_read(descriptor):
            if not seen:
                with self.assertRaises(OSError) as alone:
                    in_place._alone(self.path)
                seen["save"] = getattr(alone.exception, "winerror", None)
                os.replace(self.path, renamed)
                seen["renamed"] = True
            return real_fstat(descriptor)

        with mock.patch.object(os, "fstat", side_effect=renamed_mid_read):
            reply = self.ask(asked="bytes=0-%d" % (READ - 1))
        self.assertEqual({"save": 32, "renamed": True}, seen)
        self.assertEqual((206, CONTENT[:READ]), (reply.status_code, reply.data))
        self.assertTrue(os.path.exists(renamed))


class AShareThatStopsAnswering(Videos):
    def setUp(self):
        super().setUp()
        shares.forget()
        self.addCleanup(shares.forget)
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        self.fails = OSError(None, "The specified network name is no longer available", None, 64)
        opened = []
        real = lock_owners.opened   # the one open of a piece's read (shares._read_span)

        def blocking(path, *args, **kwargs):
            opened.append(path)
            if len(opened) > 1:   # the share stops answering after the first piece
                self.release.wait(10)
                raise self.fails
            return real(path, *args, **kwargs)

        for patcher in (mock.patch.object(shares, "on_a_network_drive", lambda path: True),
                        mock.patch.object(lock_owners, "opened", blocking),
                        mock.patch.object(shares, "READ_WAIT", 0.3)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_stream_ends_within_the_wait_and_the_next_ask_is_503_at_once(self):
        reply = self.ask(asked="bytes=0-", buffered=False)
        self.addCleanup(reply.close)
        pieces = iter(reply.response)
        self.assertEqual(CONTENT[:READ], next(pieces))
        started = time.monotonic()
        self.assertEqual(b"", b"".join(pieces))
        self.assertLess(time.monotonic() - started, 2.0, "READ_WAIT is 0.3 s")
        started = time.monotonic()
        again = self.ask(asked="bytes=%d-" % READ)
        self.assertLess(time.monotonic() - started, 0.25, "a share taken as away is answered at once")
        self.assertEqual((503, "2"), (again.status_code, again.headers["Retry-After"]))
        self.assertIn("away", again.get_json()["error"])

    def test_a_server_that_says_not_found_after_the_wait_is_still_away(self):
        """The open waited past READ_WAIT (the share taken as away), then the server said "not found" (WinError 53):
        the share is not back, as a stamp or a listing that heard the same would say."""
        self.fails = FileNotFoundError(None, "The network path was not found", None, 53)
        reply = self.ask(asked="bytes=0-", buffered=False)
        self.addCleanup(reply.close)
        pieces = iter(reply.response)
        self.assertEqual(CONTENT[:READ], next(pieces))
        self.assertEqual(b"", b"".join(pieces), "the wait ran out")
        server = os.path.splitdrive(self.path)[0] + os.sep
        real = os.path.exists
        with mock.patch("os.path.exists", lambda path: path != server and real(path)):   # the server is gone
            self.release.set()
            for look in [thread for thread in threading.enumerate() if thread.name == "ShareLook"]:
                look.join(5)
        again = self.ask(asked="bytes=%d-" % READ)
        self.assertEqual(503, again.status_code, "the look that ended in 'not found' did not take the share as back")


class TwoLibraries(Videos):
    def test_an_id_that_is_a_video_in_one_library_and_a_photo_in_another(self):
        other = ViewLibrary(self, "other", home=self.home)
        ids = [other.photo("Garden", "shed%d.jpg" % n) for n in range(self.video)]
        self.assertEqual(self.video, ids[-1], "the same id in both libraries")
        self.assertEqual(404, self.ask(library="other").status_code)
        self.assertEqual((206, CONTENT[:SPAN]), (self.ask(asked="bytes=0-").status_code, self.ask(asked="bytes=0-").data))


class TheCardsSayWhichAreVideos(Videos):
    def test_a_card_and_the_record_carry_media(self):
        cards = self.client.get("/library/api/library/cards",
                                query_string={"ids": "%d,%d" % (self.video, self.photo)}).get_json()["cards"]
        self.assertEqual(["video", "photo"], [card["media"] for card in cards])
        for photo_id, media in ((self.video, "video"), (self.photo, "photo")):
            record = self.client.get("/library/api/library/photo", query_string={"id": photo_id}).get_json()["photo"]
            self.assertEqual(media, record["media"])


if __name__ == "__main__":
    unittest.main()
