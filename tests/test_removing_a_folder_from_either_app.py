"""Somebody removes a folder from the library, from TagPup or from TagTuner (D10; docs/plans/manage-folders.md, cut 1).

Both apps serve the one route (tagpup.web.folder_routes): the folder's rows and faces go, its files stay. While the
folder, or a folder above it, waits in the library's index queue or is being indexed, the removal is refused with a
sentence and nothing goes: the run would otherwise find its folder no longer the library's at its next row (NotHeld)
and fail part-way. A second library holding the same folder is its own: an index of the folder in one never refuses
its removal from the other (two libraries holding one folder).

The index runs here on the test's own thread (IndexQueue.run_pending, its worker held back), and the removal is asked
from inside the index, while it runs: no thread, no sleep.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402


class _Library(unittest.TestCase):
    kind = None

    def setUp(self):
        self.app, self.home = web_client.app_for(self, self.kind)
        self.client = self.app.test_client()
        self.library = Library(self.home.library("library.db"))
        self.pictures = os.path.join(self.home.root, "Pictures")
        self.season = os.path.join(self.pictures, "Season")
        self.meet = os.path.join(self.season, "Meet 1")
        self.other = os.path.join(self.pictures, "Other")
        self.meet2 = os.path.join(self.season, "Meet 2")
        self.photos = [os.path.join(self.meet, "a.jpg"), os.path.join(self.season, "Meet 2", "b.jpg"),
                       os.path.join(self.other, "c.jpg")]
        for photo in self.photos:
            os.makedirs(os.path.dirname(photo), exist_ok=True)
            with open(photo, "wb") as handle:
                handle.write(b"jpeg")
        self.seed(self.library.path)

    def seed(self, db_path):
        conn = db.connect(db_path)
        try:
            for photo in self.photos:
                photo_rows.add_read(conn, photo, {"XMP:Subject": ["Activity/Cross Country"]})
            conn.commit()
        finally:
            conn.close()

    def held(self, db_path=None):
        conn = db.connect(db.readonly_uri(db_path or self.library.path), uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        finally:
            conn.close()

    def remove(self, folder, library="/library"):
        reply = self.client.post(library + "/api/folder/remove", json={"folder_path": folder})
        return reply.status_code, reply.get_json()

    def queue(self):
        return self.app.config["RUNTIME"].index_queues.of(self.library)

    def queue_held_back(self, folders):
        """Queue `folders` through the route, its worker held back: they wait."""
        with mock.patch("tagpup.jobs.indexing.IndexQueue._ensure_runner"):
            reply = self.client.post("/library/api/folder/index-start", json={"folder_paths": folders})
        self.assertEqual(200, reply.status_code, reply.get_json())

    def while_indexing(self, folder, ask, together=False):
        """Index `folder` (a list: one batch of them, `together`) on this thread, and `ask()` while it runs: what it
        answered."""
        answered = []

        def index(_folder, report):
            report("Generating embeddings: 33% (1/3)", 30)
            answered.append(ask())
            return Result(attempted=1, changed=1)
        with mock.patch("tagpup.jobs.indexing.IndexQueue._ensure_runner"):
            self.queue().start(folder if together else [folder], index, together=together)
        self.queue().run_pending()
        self.assertEqual(1, len(answered), "the index never ran")
        return answered[0]


class RemovingAFolder:
    """Each scenario, run against each app (the classes below)."""

    def test_its_rows_go_and_its_files_stay(self):
        status, body = self.remove(self.meet)
        self.assertEqual(200, status, body)
        self.assertEqual((True, 1), (body["success"], body["photos_removed"]))
        self.assertEqual(2, self.held())
        self.assertTrue(os.path.exists(self.photos[0]), "removal deleted the photo file")

    def test_is_refused_while_the_folder_is_being_indexed(self):
        status, body = self.while_indexing(self.meet, lambda: self.remove(self.meet))
        self.assertEqual((409, False), (status, body["success"]), body)
        self.assertEqual(3, self.held(), "a folder being indexed lost its rows")
        status, body = self.remove(self.meet)
        self.assertEqual((200, 1), (status, body["photos_removed"]), "once the index ended it is removed")

    def test_is_refused_while_a_folder_above_it_is_being_indexed(self):
        status, body = self.while_indexing(self.season, lambda: self.remove(self.meet))
        self.assertEqual(409, status, body)
        self.assertEqual(3, self.held())

    def test_another_folder_is_removed_while_one_is_indexed(self):
        status, body = self.while_indexing(self.season, lambda: self.remove(self.other))
        self.assertEqual((200, 1), (status, body["photos_removed"]), body)

    def test_is_refused_while_it_or_a_folder_above_it_waits_and_removed_once_cancelled(self):
        self.queue_held_back([self.season])
        for folder in (self.season, self.meet):
            with self.subTest(folder=os.path.basename(folder)):
                status, body = self.remove(folder)
                self.assertEqual((409, False), (status, body["success"]), body)
        self.assertEqual(3, self.held(), "a folder waiting to be indexed lost its rows")
        reply = self.client.post("/library/api/folder/index-cancel", json={"all": True})
        self.assertEqual(200, reply.status_code, reply.get_json())
        status, body = self.remove(self.season)
        self.assertEqual((200, 2), (status, body["photos_removed"]), body)

    def test_is_refused_while_a_folder_below_it_waits_alone_or_in_a_batch(self):
        """A batch (sync's new files, IndexQueue.start `together`) is named by its first folder only in the queue's
        pending() and active(): the second is below the folder removed too."""
        self.queue_held_back([self.meet2])
        status, body = self.remove(self.season)
        self.assertEqual((409, False), (status, body["success"]), body)
        self.client.post("/library/api/folder/index-cancel", json={"all": True})
        with mock.patch("tagpup.jobs.indexing.IndexQueue._ensure_runner"):
            self.queue().start([self.meet, self.meet2], lambda _folders, _report: Result(), together=True)
        status, body = self.remove(self.season)
        self.assertEqual((409, False), (status, body["success"]), body)
        self.assertIn(self.meet, body["error"], "the sentence names the folder that waits")
        self.assertEqual(3, self.held(), "a folder below one waiting to be indexed lost its rows")

    def test_is_refused_while_a_batch_of_folders_below_it_is_being_indexed(self):
        status, body = self.while_indexing([self.meet, self.meet2], lambda: self.remove(self.season), together=True)
        self.assertEqual((409, False), (status, body["success"]), body)
        self.assertEqual(3, self.held())

    def test_another_library_holding_the_folder_removes_it_while_this_one_indexes_it(self):
        meadow = self.home.library("meadow.db")
        library_actions.create(meadow)
        self.seed(meadow)
        status, body = self.while_indexing(self.meet, lambda: self.remove(self.meet, "/meadow"))
        self.assertEqual((200, 1), (status, body["photos_removed"]), body)
        self.assertEqual((3, 2), (self.held(), self.held(meadow)))


class FromTagPup(RemovingAFolder, _Library):
    kind = "tagpup"


class FromTagTuner(RemovingAFolder, _Library):
    kind = "tuner"


if __name__ == "__main__":
    unittest.main()
