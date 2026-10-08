"""The stage 3 review's findings #466-#474 (docs/findings.md), each with the case that found it.

#466 a stale copy (every file there, other times) is a poor result; the dry run says what sync does with
differing files. #467 the refusal and the dialog say what a move can and cannot see. #468 a write stopped
half-way says what it wrote. #469 a row the index never read is "never read", not "differs". #470 a library
behind the schema says so in words. #472 only the current and previous place are kept. #473 an unplaced
root is a check result, not an exception. #474 one owner of "a share is away".
"""
import os
import shutil
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
import roots_library as rl  # noqa: E402
import share_waits  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core.result import Conflict  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.services import damaged_photos, file_changes, inspect, roots_location, roots_verify, tagging  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import job_runs as store_job_runs  # noqa: E402
from tagpup.web import app as web  # noqa: E402

WINDOWS = os.name == "nt"


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class Base(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_review_")
        self.side = rl.Side(self.home, "photo_index", real=6, bulk=0, outside=0)
        self.assertIsNone(self.side.adopt().refused)
        self.library = self.side.library
        self.machine = roots_service.Machine(config.machine_roots, config.add_machine_root, config.machine_roots_path,
                                             config.set_location, config.change_back)
        self.copy = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.side.pictures, self.copy, copy_function=shutil.copy2)

    def verify(self, location, **kwargs):
        return roots_verify.verify(self.library, "pictures", location, self.machine, **kwargs)

    def move(self, location, **kwargs):
        return roots_location.change_location(self.library, "pictures", location, self.machine, **kwargs)

    def places(self):
        return list(config.machine_roots()["pictures"])


class AStaleCopy(Base):
    def make_stale(self):
        for folder, _dirs, files in os.walk(self.copy):
            for name in files:
                with open(os.path.join(folder, name), "ab") as handle:
                    handle.write(b"\x00" * 5)       # other size: an older or edited copy

    def test_a_copy_that_differs_in_most_rows_is_poor_and_says_what_sync_does(self):          # #466
        self.make_stale()
        found = self.verify(self.copy)
        self.assertEqual((0, 18, 0), (found["matches"], found["differs"], found["missing"]))
        self.assertTrue(found["poor"])
        said = " ".join(found["poor_why"])
        self.assertIn("18 of the 18 rows", said)
        self.assertIn("re-reads those rows from the files there", said)
        self.assertIn("tag newer in a row than in its file is replaced", found["summary"])
        self.assertIn("18 differ", found["summary"])

    def test_it_is_refused_unless_overridden_and_the_map_is_untouched(self):                  # #466
        self.make_stale()
        before = open(config.machine_roots_path(), "rb").read()
        refused = self.move(self.copy, apply=True)
        self.assertIn("older or edited copy", refused.refused)
        self.assertIn("Say override", refused.refused)
        self.assertEqual(before, open(config.machine_roots_path(), "rb").read())
        self.assertIsNone(self.move(self.copy, apply=True, override=True).refused)

    def test_a_few_differing_files_are_not_poor(self):                                        # #466
        with open(os.path.join(self.copy, rl.FOLDERS[0], "IMG_1001.jpg"), "ab") as handle:
            handle.write(b"\x00")
        found = self.verify(self.copy)
        self.assertEqual(1, found["differs"])
        self.assertFalse(found["poor"], found["poor_why"])
        self.assertIn("Sync re-reads", found["summary"], "the dry run says what sync does with the one that differs")


class ARowTheIndexNeverRead(Base):
    def test_it_is_counted_apart_from_differs(self):                                          # #469
        extra = os.path.join(self.side.pictures, "Suggest saw this.jpg")
        rl.make_jpeg(extra)
        shutil.copy2(extra, os.path.join(self.copy, "Suggest saw this.jpg"))
        conn = db.connect(self.side.db_path)
        try:
            photo_rows.add_unread(conn, extra)
            conn.commit()
        finally:
            conn.close()
        found = self.verify(self.copy)
        self.assertEqual((18, 0, 1, 0), (found["matches"], found["differs"], found["unread"], found["missing"]))
        self.assertIn("1 never read by the index (sync reads them)", found["summary"])
        self.assertIn("0 differ", found["summary"])
        self.assertFalse(found["poor"])


class WhatTheOwnerIsToldBeforeAMove(Base):
    def test_the_refusal_names_what_tagtuner_cannot_see(self):                                # #467
        result = self.move(self.copy, apply=True, busy=lambda: ["an index run is running or queued"])
        self.assertIn("an index run is running or queued", result.refused)
        self.assertIn("TagTuner sees only its own runs", result.refused)
        self.assertIn("stop Suggest in TagPup", result.refused)
        self.assertIn("a sync", result.refused)

    def test_the_architecture_does_not_claim_more(self):                                      # #467
        text = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs",
                                 "ARCHITECTURE.md"), encoding="utf-8").read()
        self.assertNotIn("refused **while anything of the library is running or queued**", text)
        self.assertIn("TagTuner's own runs", text)


class AWriteStoppedHalfWay(Base):
    def test_it_says_what_was_written_and_recorded_before_it_stopped(self):                   # #468
        real = file_changes._carry
        calls = []

        def stop_after_the_first(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise roots_service.RootsChanged("changed")
            return real(*args, **kwargs)

        with mock.patch.object(file_changes, "_carry", stop_after_the_first):
            result = tagging.change_tags(self.library, self.side.real[:3], ["Harbour"], [], rl.EXIFTOOL)
        self.assertIn(roots_service.STOPPED, result.refused)
        self.assertIn("Before it stopped 1 file(s) were written", result.refused)
        self.assertEqual(1, result.changed)
        self.assertIsNotNone(result.details["change"])
        self.assertEqual(1, len(result.details["written"]))
        from tagpup.files.exiftool_session import ExifToolSession
        with ExifToolSession(executable=rl.EXIFTOOL) as et:
            tagged = [p for p in self.side.real[:3] if "Harbour" in et.get_tags([p], tags=["XMP:Subject"])[0].get(
                "XMP:Subject", [])]
        self.assertEqual(1, len(tagged), "the file written is the one the Result says")


class ALibraryBehindTheSchema(Base):
    def behind(self):
        return mock.patch.object(store_job_runs, "claim", return_value=store_job_runs.Claim(why="behind"))

    def test_verify_says_so_in_words(self):                                                   # #470
        with self.behind(), self.assertRaises(Conflict) as why:
            roots_location.begin_verify(self.library, "pictures")
        self.assertIn("open it with TagPup or the CLI once so that it updates, then try again", str(why.exception))
        self.assertNotIn("under way", str(why.exception))

    def test_a_move_says_so_in_words(self):                                                   # #470
        with self.behind():
            result = self.move(self.copy, apply=True)
        self.assertIn("open it with TagPup or the CLI once", result.refused)
        self.assertNotIn("under way", result.refused)
        self.assertNotIn("in a moment", result.refused)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class OnlyTheCurrentAndThePreviousPlace(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_keep_")
        self.places = []
        for name in "ABCD":
            self.places.append(os.path.join(self.home.root, name))
            os.makedirs(self.places[-1])
        config.add_machine_root("pictures", self.places[0])

    def test_a_move_drops_anything_older(self):                                               # #472
        config.set_location("pictures", self.places[1])
        config.set_location("pictures", self.places[2])
        self.assertEqual([self.places[2], self.places[1]], list(config.machine_roots()["pictures"]))
        config.set_location("pictures", self.places[3])
        self.assertEqual([self.places[3], self.places[2]], list(config.machine_roots()["pictures"]))

    def test_going_to_the_previous_place_by_name_keeps_two(self):                             # #472
        config.set_location("pictures", self.places[1])
        config.set_location("pictures", self.places[0])
        self.assertEqual([self.places[0], self.places[1]], list(config.machine_roots()["pictures"]))

    def test_change_back_swaps_and_keeps_two(self):                                           # #472
        config.set_location("pictures", self.places[1])
        config.change_back("pictures")
        self.assertEqual([self.places[0], self.places[1]], list(config.machine_roots()["pictures"]))

    def test_a_place_older_than_that_is_no_longer_recognised(self):                           # #472
        from tagpup.core import paths
        config.set_location("pictures", self.places[1])
        config.set_location("pictures", self.places[2])
        roots = config.roots_of({"pictures": ""})
        self.assertIsNone(roots.locate(os.path.join(self.places[0], "x.jpg")))
        self.assertIsNotNone(roots.locate(os.path.join(self.places[1], "x.jpg")))
        self.assertEqual("@pictures/x.jpg", paths.to_row(os.path.join(self.places[2], "x.jpg"), roots))


class AnUnplacedRootIsACheckResult(Base):
    def lose_the_map(self):
        os.remove(config.machine_roots_path())

    def test_all_checks_answers_with_the_sentence(self):                                      # #473
        self.lose_the_map()
        found = inspect.all_checks(self.library)
        self.assertEqual(1, found["broken"])
        self.assertEqual(["roots_placed"], [c["check"] for c in found["checks"]])
        self.assertIn("machine_roots.json", found["checks"][0]["message"])

    def test_one_check_and_the_summary_answer_with_it_too(self):                              # #473
        self.lose_the_map()
        self.assertIn("machine_roots.json", inspect.check(self.library, "rooted_rows_convert")["message"])
        self.assertIn("machine_roots.json", inspect.check(self.library, "roots_placed")["message"])
        self.assertIn("machine_roots.json", inspect.summary(self.library)["roots_problem"])

    def test_a_placed_library_is_as_it_was(self):                                             # #473
        self.assertEqual(0, inspect.check(self.library, "roots_placed")["count"])
        self.assertNotIn("roots_problem", inspect.summary(self.library))
        self.assertNotIn("roots_placed", [c["check"] for c in inspect.all_checks(self.library)["checks"]])

    def test_the_mcp_tools_report_it_and_do_not_raise(self):                                  # #473
        import asyncio
        import json

        from tagpup.mcp import server
        self.lose_the_map()

        def call(tool, arguments):
            result = asyncio.run(server.build().call_tool(tool, arguments))
            return json.dumps(result, default=repr)

        self.assertIn("machine_roots.json", call("checks", {"library": "photo_index"}))
        self.assertIn("machine_roots.json", call("check", {"library": "photo_index", "name": "roots_placed"}))
        self.assertIn("machine_roots.json", call("summary", {"library": "photo_index"}))

    def test_the_attention_list_answers_with_the_sentence_not_a_500(self):                    # #473
        self.lose_the_map()
        app = web.create_app("tuner", startup=self.library)
        app.testing = False
        reply = app.test_client().get("/api/activity/attention")
        self.assertEqual(200, reply.status_code)
        answer = reply.get_json()["libraries"][0]
        self.assertIn("machine_roots.json", answer.get("error", ""))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class OneOwnerOfAShareBeingAway(unittest.TestCase):
    SHARE = "\\\\idziserver\\Pictures\\Pictures"

    def setUp(self):
        shares.forget()
        self.addCleanup(shares.forget)

    def test_the_damaged_lists_and_verify_use_the_same_cache(self):                           # #474
        self.assertIs(damaged_photos._away, shares._away)
        release = threading.Event()
        self.addCleanup(release.set)
        state, _ = roots_verify._bounded(self.SHARE, lambda: release.wait(30), 0.2)
        self.assertEqual("away", state)
        waits, counting = share_waits.counted()
        with counting:
            self.assertIsNone(damaged_photos._folder_stamps(self.SHARE + "\\2024"),
                              "a share Verify found away was asked for again by the damaged photos' list")
        self.assertEqual([], waits, "and waited for")

    def test_and_the_other_way(self):                                                         # #474
        release = threading.Event()
        self.addCleanup(release.set)
        with mock.patch.object(damaged_photos, "_stamps_in", lambda folder: release.wait(30)), \
                mock.patch.object(damaged_photos, "SHARE_WAIT", 0.2):
            self.assertIsNone(damaged_photos._folder_stamps(self.SHARE + "\\2024"))
        self.assertIn(shares.share_of(self.SHARE), shares._away)
        waits, counting = share_waits.counted()
        with counting:
            self.assertEqual("away", roots_verify._bounded(self.SHARE, lambda: "never asked", 5)[0])
        self.assertEqual([], waits, "a share the damaged photos' list found away was waited for by Verify")

    def test_the_duplicate_is_gone(self):                                                     # #474
        source = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tagpup", "services",
                                   "roots_verify.py"), encoding="utf-8").read()
        self.assertNotIn("_blocked", source)
        self.assertNotIn("threading.Thread", source)


if __name__ == "__main__":
    unittest.main()
