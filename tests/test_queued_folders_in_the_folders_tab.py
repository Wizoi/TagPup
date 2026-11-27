"""Somebody adds folders to a library and looks at the Library pane's Folders tab while they are indexed (V32; plan
docs/plans/working-on-a-new-folder.md, cut F; DECISIONS 2026-11-26).

The tab is GET /api/library/navigator?section=folders (tagpup.services.library_view.navigator). A folder just added is
the library's at once but holds no photo until its index reaches it; the tab names it all the same, from the one place
that knows what is queued -- the library's index queue, IndexQueue.active, what /api/folder/index-active answers --
marked `queued`, then `indexing` with its count filling, then its real count alone, once. Another library's queue is
its own, and a cancelled folder leaves the tab.

The folders are added through the app's own route (records them added, queues them), its worker held back; the index
runs on the test's own thread (IndexQueue.run_pending) and writes its rows as the indexer records a read
(photo_rows.add_read), asking the tab while it runs: no sleep, no thread, no port. The three replies of a folder's way
through the queue are tests/fixtures/queued_folders.json, which the page's scenario draws
(tests/frontend/scenario-queued-folders-in-the-folders-tab.test.mjs): the page is tested on what the server answers.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402

HELD_BACK = "tagpup.jobs.indexing.IndexQueue._ensure_runner"
REPLIES = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "queued_folders.json"),
                         encoding="utf-8"))


def make_photos(folder, *names):
    os.makedirs(folder, exist_ok=True)
    made = []
    for name in names:
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"jpeg")
        made.append(path)
    return made


def record(db_path, *photos):
    """The rows the indexer records as it reads each photo."""
    db.write_with_connection(db_path, lambda conn: [photo_rows.add_read(conn, photo, {}) for photo in photos])


class _Tab(unittest.TestCase):
    """Pictures\\Season\\Meet 1 is indexed (two photos); the rest is on disk, not yet the library's."""

    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.library = Library(self.home.library("library.db"))
        self.pictures = os.path.join(self.home.root, "Pictures")
        self.season = os.path.join(self.pictures, "Season")
        self.meet = os.path.join(self.season, "Meet 1")
        self.heats = os.path.join(self.meet, "Heats")
        self.relay = os.path.join(self.season, "Relay Day")
        record(self.library.path, *make_photos(self.meet, "a.jpg", "b.jpg"))
        self.new_photos = {self.heats: make_photos(self.heats, "h1.jpg", "h2.jpg"),
                           self.relay: make_photos(self.relay, "r1.jpg", "r2.jpg", "r3.jpg")}
        self.asked = []
        patcher = mock.patch("tagpup.runtime.index_folder", side_effect=self.index_folder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def index_folder(self, library, subfolders=True):
        """The library's indexer, as the queue is handed it: records the folder's photos one at a time, asking the tab
        after the first."""
        def index(folder, report):
            for number, photo in enumerate(self.new_photos.get(folder, ())):
                record(library.path, photo)
                report("Indexing %s" % os.path.basename(photo), 50)
                if number == 0:
                    self.asked.append(self.tab())
            return Result(attempted=1, changed=1)
        return index

    def queue(self, library=None):
        return self.app.config["RUNTIME"].index_queues.of(library or self.library)

    def add(self, *folders, prefix="/library"):
        """The owner's Add, the worker held back: the folders wait."""
        with mock.patch(HELD_BACK):
            reply = self.client.post(prefix + "/api/folder/index-start", json={"folder_paths": list(folders)})
        self.assertEqual(200, reply.status_code, reply.get_json())

    def tab(self, prefix="/library"):
        reply = self.client.get(prefix + "/api/library/navigator?section=folders")
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()["folders"]

    def as_fixture(self, folders):
        """The reply's folders under Pictures, Pictures the top, spelled from {pictures} as the fixture is."""
        def spelled(path):
            return None if path is None or not paths.is_under(path, self.pictures) and not paths.same(path, self.pictures) \
                else "{pictures}" + path[len(self.pictures):]
        return [dict(each, path=spelled(each["path"]), parent=spelled(each["parent"])) for each in folders
                if spelled(each["path"])]

    def entries(self, folders, folder):
        return [each for each in folders if paths.same(each["path"], folder)]

    def entry(self, folders, folder):
        found = self.entries(folders, folder)
        self.assertEqual(1, len(found), "one entry for %s, not %d" % (os.path.basename(folder), len(found)))
        return found[0]


class AFolderJustAdded(_Tab):
    def test_is_queued_at_once_then_indexing_with_its_count_filling_then_its_real_count_once(self):
        self.add(self.relay)
        queued = self.entry(self.tab(), self.relay)
        self.assertEqual(("queued", 0, 0, "Relay Day"),
                         (queued["queue"], queued["direct"], queued["recursive"], queued["name"]))
        self.assertTrue(paths.same(self.season, queued["parent"]), "under the indexed folder above it")
        self.queue().run_pending()
        while_indexed = self.entry(self.asked[0], self.relay)
        self.assertEqual(("indexing", 1), (while_indexed["queue"], while_indexed["recursive"]),
                         "marked while its index runs, its first photo counted")
        done = self.entry(self.tab(), self.relay)
        self.assertEqual((None, 3, 3), (done["queue"], done["direct"], done["recursive"]))
        self.assertEqual(5, self.entry(self.tab(), self.season)["recursive"], "the folder above counts it")

    def test_the_replies_are_the_ones_the_page_is_tested_on(self):
        self.add(self.relay)
        queued = self.tab()
        self.queue().run_pending()
        self.assertEqual({"queued": REPLIES["queued"], "indexing": REPLIES["indexing"], "indexed": REPLIES["indexed"]},
                         {"queued": self.as_fixture(queued), "indexing": self.as_fixture(self.asked[0]),
                          "indexed": self.as_fixture(self.tab())})

    def test_a_folder_under_an_indexed_folder_is_its_subfolder(self):
        self.add(self.heats)
        heats = self.entry(self.tab(), self.heats)
        self.assertEqual("queued", heats["queue"])
        self.assertTrue(paths.same(self.meet, heats["parent"]))
        self.assertIsNone(self.entry(self.tab(), self.meet)["queue"], "the folder above is not the one queued")

    def test_the_second_waits_queued_while_the_first_is_indexed(self):
        self.add(self.relay, self.heats)
        self.queue().run_pending()
        first = self.asked[0]
        self.assertEqual("indexing", self.entry(first, self.relay)["queue"])
        self.assertEqual(("queued", 0), (self.entry(first, self.heats)["queue"], self.entry(first, self.heats)["recursive"]))
        self.assertEqual("indexing", self.entry(self.asked[1], self.heats)["queue"])
        self.assertEqual([None], list({each["queue"] for each in self.tab()}), "nothing marked once the queue is empty")

    def test_a_cancelled_folder_leaves_the_tab_and_the_indexed_folders_stay(self):
        before = self.tab()
        self.add(self.relay)
        self.assertEqual(1, len(self.entries(self.tab(), self.relay)))
        reply = self.client.post("/library/api/folder/index-cancel", json={"all": True})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(before, self.tab())

    def test_a_folder_already_indexed_and_queued_again_is_marked_on_its_own_row(self):
        self.add(self.meet)
        meet = self.entry(self.tab(), self.meet)
        self.assertEqual(("queued", 2), (meet["queue"], meet["recursive"]))

    def test_each_folder_of_a_batch_is_marked_while_it_waits_and_while_it_is_indexed(self):
        """A batch of sync's new files (IndexQueue.start's `together`) is one run of the indexer, its folders one status:
        the queue names each folder of it, by its own name, with the run's percent and message (IndexQueue.active, what
        /api/folder/index-active answers to both pages), so the tab marks each -- queued, then indexing -- and none once the
        run has ended."""
        heard = []

        def index(_folders, report):
            report("Reading metadata: 50%", 50)
            heard.append(self.client.get("/library/api/folder/index-active").get_json())
            self.asked.append(self.tab())
            return Result(attempted=1, changed=1)
        with mock.patch(HELD_BACK):
            self.queue().start([self.relay, self.heats], index, together=True)
        waiting = self.client.get("/library/api/folder/index-active").get_json()
        self.assertEqual(([], ["Relay Day", "Heats"], 2), ([job["name"] for job in waiting["active"]],
                                                           [job["name"] for job in waiting["queued"]], waiting["remaining"]))
        tab = self.tab()
        self.assertEqual(("queued", "queued"), (self.entry(tab, self.relay)["queue"], self.entry(tab, self.heats)["queue"]))
        self.queue().run_pending()
        self.assertEqual([("Relay Day", 50, "Reading metadata: 50%"), ("Heats", 50, "Reading metadata: 50%")],
                         [(job["name"], job["percent"], job["message"]) for job in heard[0]["active"]])
        self.assertTrue(paths.same(self.heats, heard[0]["active"][1]["folder"]), "the second folder named by its own path")
        self.assertEqual(([], 2), (heard[0]["queued"], heard[0]["remaining"]))
        tab = self.asked[0]
        self.assertEqual(("indexing", "indexing"), (self.entry(tab, self.relay)["queue"], self.entry(tab, self.heats)["queue"]))
        self.assertEqual([None], list({each["queue"] for each in self.tab()}), "nothing marked once the run has ended")


class ANewLibrary(_Tab):
    def test_a_library_with_no_photo_lists_the_folder_added_at_the_top(self):
        empty = Library(self.home.library("empty.db"))
        library_actions.create(empty.path)
        self.add(self.relay, prefix="/empty")
        listed = self.tab(prefix="/empty")
        self.assertEqual([("Relay Day", None, "queued", 0)],
                         [(each["name"], each["parent"], each["queue"], each["recursive"]) for each in listed])


class TwoLibraries(_Tab):
    def test_another_librarys_queue_is_not_this_librarys_tab(self):
        other = Library(self.home.library("other.db"))
        library_actions.create(other.path)
        self.add(self.relay, prefix="/other")
        self.assertEqual([], self.entries(self.tab(), self.relay), "another library's queue shown")
        self.assertEqual("queued", self.entry(self.tab(prefix="/other"), self.relay)["queue"])
        self.assertEqual([None], list({each["queue"] for each in self.tab()}))


if __name__ == "__main__":
    unittest.main()
