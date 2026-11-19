"""Somebody removes a folder from the library, from TagPup or from TagTuner (D10; docs/plans/manage-folders.md, cut 1).

Both apps serve the one route (tagpup.web.folder_routes): the folder's rows and faces go, its files stay. While the
folder, or a folder above it, waits in the library's index queue or is being indexed, the removal is refused with a
sentence and nothing goes: the run would otherwise be refused at its next batch (store.photos.record_indexed, NotHeld:
no rows made) and stop, failed, part-way. A folder a parent's add still covers is put in the library's ignored folders;
adding it again takes it off them, one journaled change the History undoes, and says so (owner, 2026-11-19). A CLI index in another process is not in the queue: the removal goes ahead,
and the index stops at its next batch, saying why (AnIndexInTheCli). A second library holding the same folder is its own: an index of the folder in one never refuses
its removal from the other (two libraries holding one folder).

The index runs here on the test's own thread (IndexQueue.run_pending, its worker held back), and the removal is asked
from inside the index, while it runs: no thread, no sleep.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

from click.testing import CliRunner

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import photo_rows  # noqa: E402
import web_client  # noqa: E402
from test_an_index_reads_the_next_photos_ahead import COLOURS, StandIns  # noqa: E402
from test_an_index_reads_the_next_photos_ahead import photo as jpeg  # noqa: E402

import tagpup_cli  # noqa: E402

from tagpup.core import paths, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import NotHeld, Result  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import added_folders, db  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402


def ignored(library):
    """The library's ignored folders, as its settings hold them."""
    return library_settings.read(Library(library) if isinstance(library, str) else library).ignored


class _Library(unittest.TestCase):
    kind = None
    add_route = None    # each app's Add: TagPup's "Add to library", TagTuner's Add Folder

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

    def ignore(self, *folders):
        """The owner's own Ignore: these are the library's ignored folders (a journaled change of its settings)."""
        change = library_settings.change(self.library, {
            library_settings.IGNORED: validation.FOLDER_SEPARATOR.join(folders)})
        self.assertTrue(change.ok, change.message())

    def add(self, folder):
        """Add `folder` through this app's Add, its index queued and not run: the reply."""
        with mock.patch("tagpup.jobs.indexing.IndexQueue._ensure_runner"):
            reply = self.client.post(self.add_route, json={"folder_path": folder})
        body = reply.get_json()
        self.assertEqual((200, True), (reply.status_code, body["success"]), body)
        self.queue().cancel([], everything=True)
        return body

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

    def test_a_folder_a_parent_still_covers_says_so_when_it_cannot_be_ignored(self):
        """Its rows went, but the ignored folders could not be changed: the reply is not a success, says why, and
        counts what was removed (a write reports what it changed)."""
        conn = db.connect(self.library.path)
        try:
            added_folders.record(conn, self.season, subfolders=True)
            conn.commit()
        finally:
            conn.close()
        failed = Result(errors=[("library", "the disk is full")])
        with mock.patch("tagpup.services.settings.change", return_value=failed):
            status, body = self.remove(self.meet)
        self.assertEqual((500, False, 1), (status, body["success"], body.get("photos_removed")), body)
        self.assertIn("could not be put in the ignored folders: the disk is full", body["error"])
        self.assertEqual(2, self.held())

    def test_add_takes_off_the_ignored_entry_that_covers_the_folder_and_says_so(self):
        """Add of a folder on the ignored list, or under a folder on it, takes that entry off, and the reply says so
        (owner, 2026-11-19): left on, the folder was recorded and queued and its index left every photo out, silently.
        An entry below the folder added, or beside it, stays: adding Season leaves its ignored Meet 1 out."""
        self.ignore(self.meet, self.other)
        body = self.add(self.season)
        self.assertEqual([], body["unignored"], body)
        self.assertNotIn("note", body)
        self.assertEqual([paths.stored(self.meet), paths.stored(self.other)], ignored(self.library))
        self.ignore(self.season, self.other)
        body = self.add(self.meet)
        self.assertEqual([paths.stored(self.season)], body["unignored"], body)
        self.assertEqual("%s was on the ignored list and has been taken off." % paths.stored(self.season),
                         body["note"])
        self.assertEqual([paths.stored(self.other)], ignored(self.library))

    def test_the_picker_says_why_a_folder_windows_will_not_list_is_not_listed(self):
        """The folder dialog (both apps) expands a folder the owner may not read."""
        with mock.patch("tagpup.services.photos.subfolders", side_effect=PermissionError(13, "Access is denied")):
            reply = self.client.get("/library/api/folder/subfolders", query_string={"path": self.season})
        body = reply.get_json()
        self.assertEqual((403, False), (reply.status_code, body["success"]), body)
        self.assertIn("could not list the folders in", body["error"])
        self.assertTrue(body["error"].endswith(": Access is denied"), body["error"])

    def test_a_removal_the_library_cannot_take_says_why_in_a_sentence_the_page_reads(self):
        """A CLI sync --apply holds the library's write lock past the busy timeout: the removal raises. The page reads the
        reply as JSON; Flask's HTML 500 was "Unexpected token '<'" (#3199). The app answers any /api/ failure with the
        exception's sentence, as the running server does (not in testing mode, so it does not let it out)."""
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        locked = sqlite3.OperationalError("database is locked")
        with mock.patch("tagpup.services.faces.remove_folder", side_effect=locked):
            status, body = self.remove(self.meet)
        self.assertEqual((500, {"success": False, "error": "database is locked"}), (status, body))
        self.assertEqual(3, self.held())


class FromTagPup(RemovingAFolder, _Library):
    kind = "tagpup"
    add_route = "/library/api/folder/add"


class FromTagTuner(RemovingAFolder, _Library):
    kind = "tuner"
    add_route = "/library/api/folder/index-start"


class AnIndexInTheCli(unittest.TestCase):
    """The owner indexes a folder with the CLI (how long indexes are run, CLAUDE.md) and removes it in an app while the
    run goes on. The server's queue cannot see the CLI's run, so the removal goes ahead; the folder stays gone, and the
    run stops at its next batch, saying why in a sentence on one line, exit 1, not a traceback (NotHeld), its rows and
    faces not made. The models are stood in for
    (test_an_index_reads_the_next_photos_ahead.StandIns)."""

    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tuner")
        self.client = self.app.test_client()
        self.library = self.home.library("library.db")
        self.models = StandIns(self)
        self.season = os.path.join(self.home.root, "Pictures", "Season")
        self.meet, self.meet2 = os.path.join(self.season, "Meet 1"), os.path.join(self.season, "Meet 2")

    def photos(self, folder, count):
        return [jpeg(os.path.join(folder, "p%02d.jpg" % n), COLOURS[n % len(COLOURS)]) for n in range(count)]

    def index(self, folder):
        return CliRunner().invoke(tagpup_cli.cli, ["--db", self.library, "index", folder])

    def remove(self, folder):
        reply = self.client.post("/library/api/folder/remove", json={"folder_path": folder})
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()

    def held(self, folder):
        """(rows under `folder`, their faces)."""
        conn = db.connect(db.readonly_uri(self.library), uri=True)
        try:
            where, params = store_roots.sql_under(conn, "p.path", folder)
            return (conn.execute("SELECT COUNT(*) FROM photos p WHERE " + where, params).fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM faces f JOIN photos p ON p.id = f.photo_id WHERE " + where,
                                 params).fetchone()[0])
        finally:
            conn.close()

    def test_a_removal_between_two_batches_stays_removed_and_the_index_stops_saying_why(self):
        self.photos(self.meet, 5)
        read_again, batches = tagpup_cli._read_again, []

        def removed_before_the_second_batch(*args):
            batches.append(self.held(self.meet))
            if len(batches) == 2:
                self.assertTrue(self.remove(self.meet)["success"])
            return read_again(*args)
        with mock.patch.object(tagpup_cli, "PHOTO_CHUNK", 2), \
                mock.patch.object(tagpup_cli, "_read_again", removed_before_the_second_batch):
            result = self.index(self.meet)
        self.assertEqual(2, batches[1][1], "the first batch was not recorded, with its faces, before the removal")
        self.assertEqual((0, 0), self.held(self.meet), "the index brought the removed folder back")
        conn = db.connect(db.readonly_uri(self.library), uri=True)
        try:
            self.assertFalse(added_folders.covers(conn, self.meet))
        finally:
            conn.close()
        self.assertEqual(1, result.exit_code, result.output)
        self.assertIsInstance(result.exception, SystemExit, "NotHeld left the CLI as a traceback")
        self.assertIn("Error: %s" % NotHeld(paths.stored(self.meet)), result.output.splitlines())
        self.assertEqual(2, len(batches), "the index went on past the refusal")
        self.assertEqual(4, len(self.models.shown), "the index read on past the refusal")

    def test_an_index_of_the_folder_above_leaves_a_removed_folder_out(self):
        self.photos(self.meet, 2)
        self.photos(self.meet2, 2)
        result = self.index(self.season)
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual((2, 2), self.held(self.meet))
        # Season was added with its subfolders: Meet 1, removed, is put in the ignored folders.
        self.assertTrue(self.remove(self.meet)["ignored"])
        result = self.index(self.season)
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual((0, 0), self.held(self.meet), "the index of the folder above brought the removed folder back")
        self.assertEqual((2, 2), self.held(self.meet2))

    def test_a_removed_folder_added_again_is_held_indexed_and_listed_and_its_history_undoes_it(self):
        """The owner removes Meet 1 (Season, added with its subfolders, still covers it: it is ignored), then adds it
        again in TagTuner: it comes off the ignored list as one change of the settings, its index (the CLI, as the
        queue runs it) makes its rows and faces, Manage Folders lists it, and the History shows the change; undoing it
        puts the ignore back (owner, 2026-11-19)."""
        self.photos(self.meet, 2)
        self.photos(self.meet2, 2)
        self.assertEqual(0, self.index(self.season).exit_code)
        self.assertTrue(self.remove(self.meet)["ignored"])
        self.assertEqual([paths.stored(self.meet)], ignored(self.library))
        with mock.patch("tagpup.jobs.indexing.IndexQueue._ensure_runner"):
            reply = self.client.post("/library/api/folder/index-start", json={"folder_paths": [self.meet]})
        body = reply.get_json()
        self.assertEqual((200, [paths.stored(self.meet)]), (reply.status_code, body.get("unignored")), body)
        self.assertIn("was on the ignored list and has been taken off", body["note"])
        self.assertEqual([], ignored(self.library))
        self.assertTrue(library_actions.holds(Library(self.library), self.meet))
        self.client.post("/library/api/folder/index-cancel", json={"all": True})
        result = self.index(self.meet)
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual((2, 2), self.held(self.meet), "the folder added again was not indexed")
        listed = self.client.get("/library/api/folder/indexed").get_json()["folders"]
        self.assertTrue(any(paths.same(each["path"], self.meet) for each in listed), "Manage Folders leaves it out")
        changes = self.client.get("/library/api/history").get_json()["changes"]
        settings_changes = [each for each in changes if each["operation"] == library_settings.CHANGE]
        self.assertEqual(2, len(settings_changes), "the ignore and the taking off, each a change in the History")
        undo = self.client.post("/library/api/history/%d/undo" % settings_changes[0]["id"], json={"apply": True})
        self.assertEqual(200, undo.status_code, undo.get_json())
        self.assertEqual([paths.stored(self.meet)], ignored(self.library), "the undo did not put the ignore back")


if __name__ == "__main__":
    unittest.main()
