"""The replies the photo's suggestions box reads (web/tagpup/suggestions.js), made by the real SuggestionRuns.status() and written to
tests/frontend/fixtures/suggest_status.json for the page's test (tests/frontend/photo-suggestions-panel.test.mjs) to answer from, so
the box is held to what the server sends and not to a reply written by hand. One run of a folder of three photos through every state
the box speaks of: waiting for the index and cancelled there, waiting for the graphics card, under way with one photo read and one
that could not be, completed with that failure kept; a folder with no photo is an error; one never asked about is idle. The
temporary folder is spelled as the page test's folder (D:\\Library\\2020), so the file is the same on every run. A reply
that differs from the file fails, naming the command that writes it again (page_fixtures).
"""
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import page_fixtures  # noqa: E402
from service_fixture import TempLibrary  # noqa: E402
from waiting import wait_until  # noqa: E402

from tagpup.core import gpu_turns, paths  # noqa: E402
from tagpup.jobs import suggestions as jobs  # noqa: E402

FIXTURE = page_fixtures.path_of("suggest_status.json")
PAGE_FOLDER = "D:\\Library\\2020"
UNREADABLE = "cannot identify image file"


class Model:
    def __init__(self, held):
        self.held = held

    def suggest(self, photo, meta):
        name = os.path.basename(photo)
        if name == "b.jpg":
            self.held.wait(30)
        if name == "c.jpg":
            raise OSError(UNREADABLE)
        return {"path": photo, "suggested_tags": [{"tag": "Activity/Rowing", "score": 0.8}]}

    def offered(self, suggestion):
        return [dict(t) for t in suggestion["suggested_tags"]], [], None

    def consensus(self, suggestions):
        return suggestions


class Work:
    """A run's work as tagpup.jobs.suggestions.work_for makes it: its folder's index waited on while `indexing` is set,
    the graphics card's waiting line said until `card` is set, and photo b held until `held` is set."""

    def __init__(self, photos):
        self._photos = photos
        self.indexing_now = threading.Event()
        self.card = threading.Event()
        self.held = threading.Event()
        self._report = None

    def photos(self):
        return self._photos

    def indexing(self, folder):
        return self.indexing_now.is_set()

    def watch(self, folder, cancelled, report):
        self._report = report

    def begin(self):
        self._report(gpu_turns.waiting_line({"what": "Index 2020 (photo_index)"}))
        self.card.wait(30)
        self._report(None)
        return Model(self.held)


class RepliesForTheBox(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.runs = jobs.SuggestionRuns(self.lib.library.path)
        self.folder = os.path.join(self.lib.photos, "2020")
        self.photos = {paths.key(os.path.join(self.folder, n)): {"path": os.path.join(self.folder, n)}
                       for n in ("a.jpg", "b.jpg", "c.jpg")}
        self.lib.add_folder(*[meta["path"] for meta in self.photos.values()])

    def wait_for(self, folder, ready, what):
        return wait_until(lambda: (lambda reply: ready(reply) and reply)(self.runs.status(folder)), 30, what=what)

    def page_spelling(self, value):
        """`value` with the temporary folder spelled as the page test's."""
        if isinstance(value, dict):
            return {self.page_spelling(k): self.page_spelling(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.page_spelling(v) for v in value]
        if isinstance(value, str):
            return value.replace(paths.stored(self.folder), PAGE_FOLDER)
        return value

    def test_the_replies_the_box_reads_are_written_for_the_page_test(self):
        found = {"idle": self.runs.status(self.folder)}
        # Waiting for the folder's index, then cancelled there: nothing analysed.
        work = Work(self.photos)
        work.indexing_now.set()
        self.runs.start(self.folder, work)
        found["waiting_for_the_index"] = self.wait_for(
            self.folder, lambda r: r.get("message") == jobs.WAITING_FOR_THE_INDEX, "the index wait")
        self.assertTrue(self.runs.cancel(self.folder))
        found["cancelled"] = self.wait_for(self.folder, lambda r: r["status"] == "cancelled", "the cancel")
        # Waiting for the graphics card, then under way: a read, b held, c unreadable; then completed.
        work = Work(self.photos)
        self.addCleanup(work.card.set)
        self.addCleanup(work.held.set)
        self.runs.start(self.folder, work)
        found["waiting_for_the_card"] = self.wait_for(
            self.folder, lambda r: gpu_turns.is_waiting(r.get("message")), "the card's wait")
        work.card.set()
        found["running"] = self.wait_for(
            self.folder, lambda r: r["status"] == "running" and r["completed"] == 2, "a and c done")
        work.held.set()
        found["completed"] = self.wait_for(self.folder, lambda r: r["status"] == "completed", "the run's end")
        # A folder with no photo.
        empty = os.path.join(self.lib.photos, "2021")
        self.runs.start(empty, Work({}))
        found["error"] = self.wait_for(empty, lambda r: r["status"] == "error", "the empty folder")

        found = self.page_spelling(found)
        a, b, c = (PAGE_FOLDER + "\\" + n for n in ("a.jpg", "b.jpg", "c.jpg"))
        self.assertEqual({"status": "idle"}, found["idle"])
        self.assertEqual("preparing", found["waiting_for_the_card"]["status"])
        self.assertEqual({}, found["waiting_for_the_card"]["suggestions"])
        self.assertEqual({a, c}, set(found["running"]["suggestions"]))
        self.assertEqual(3, found["running"]["total"])
        self.assertEqual(UNREADABLE, found["completed"]["suggestions"][c]["error"])
        self.assertNotIn("error", found["completed"]["suggestions"][b])
        self.assertEqual(jobs.CANCELLED, found["cancelled"]["message"])
        self.assertEqual("No images found in this folder.", found["error"]["message"])
        text = "{\n" + ",\n".join("%s: %s" % (json.dumps(key), json.dumps(found[key], sort_keys=True)) for key in sorted(found)) + "\n}\n"
        page_fixtures.hold_to(self, FIXTURE, text, "tests.test_suggest_status_replies", "the suggestions box")


if __name__ == "__main__":
    unittest.main()
