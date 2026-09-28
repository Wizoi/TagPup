"""No row is made for a photo in a folder the library does not hold, unless the folder
was added to it.

With kr-track selected, the owner opened a folder photo_index held, on a network share
outside kr-track's root folders, and started Suggest. Suggest made 25 rows in kr-track
-- store.photos.ensure_row, through the faces it recorded, the vectors it kept and what
it offered -- and the folder was kr-track's from then on: the watcher kept it in step,
its other photos were indexed, and their Family/Work tags grew kr-track's tag tree
(2026-09-28). "I thought tagpup wasn't going to just add them without asking?"

Now ensure_row makes a row only in a folder the library holds, or when the folder is
being added (tagpup.services.libraries.add, and the indexer). Every entry point that
could make one is asked here: the Suggest route (409, naming the folder and the
library), the CLI's suggest, the MCP server's write tools, and the adding routes of
both apps, which make the rows at once and queue the index.
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402

from click.testing import CliRunner  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Conflict, Result  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.mcp import server  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import suggestions as saved_suggestions  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import suggestions as store_suggestions  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402
from tagpup_cli import cli  # noqa: E402


def make_photos(folder, *names):
    os.makedirs(folder, exist_ok=True)
    made = []
    for name in names:
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"jpeg")
        made.append(path)
    return made


def index(db_path, photo_paths, tags=("Trips/Harbour",)):
    """Record each photo as the indexer does after reading it."""
    conn = db.connect(db_path)
    try:
        for path in photo_paths:
            photo_rows.add_read(conn, path, {"XMP:Subject": list(tags)})
        conn.commit()
    finally:
        conn.close()


def rows_under(db_path, folder):
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return store_photos.count_under(conn, folder)
    finally:
        conn.close()


def all_rows(db_path):
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    finally:
        conn.close()


class Folders:
    """The library holds Regatta; the share's Lighthouse is another library's, quayside's."""

    def make(self, root, db_path):
        self.regatta = os.path.join(root, "Pictures", "Regatta")
        self.lighthouse = os.path.join(root, "Share", "Lighthouse")
        self.held = make_photos(self.regatta, "regatta_01.jpg", "regatta_02.jpg")
        self.shared = make_photos(self.lighthouse, "IMG_0001.jpg", "IMG_0002.jpg")
        index(db_path, self.held[:1])
        self.quayside = self.home.library("quayside.db")
        library_actions.create(self.quayside)
        index(self.quayside, self.shared, tags=("Family/Work",))


class TheStore(Folders, unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.make(self.home.root, self.db_path)

    def write(self, action):
        return db.write_with_connection(self.db_path, action)

    def test_makes_a_row_in_a_folder_the_library_holds(self):
        photo_id = self.write(lambda conn: store_photos.ensure_row(conn, self.held[1]))
        self.assertIsNotNone(photo_id)
        self.assertEqual(2, rows_under(self.db_path, self.regatta))

    def test_makes_none_in_a_folder_it_does_not_hold(self):
        writes = {
            "a row": lambda conn: store_photos.ensure_row(conn, self.shared[0]),
            "a face": lambda conn: store_faces.insert(conn, self.shared[0], [1, 2, 3, 4], b"\0" * 16),
            "a vector": lambda conn: store_embeddings.put(conn, self.shared[0], "clip", 1.0, 4, b"\0" * 16),
            "a suggestion": lambda conn: store_suggestions.put(conn, self.shared[0], {"tags": ["Family/Work"]}),
        }
        for what, write in writes.items():
            with self.subTest(what=what):
                with self.assertRaises(Conflict) as raised:
                    self.write(write)
                self.assertEqual("NotHeld", type(raised.exception).__name__)
                self.assertEqual(self.lighthouse, raised.exception.folder)
                self.assertEqual(0, rows_under(self.db_path, self.lighthouse))

    def test_what_suggest_keeps_is_refused_there_too(self):
        with self.assertRaises(Conflict):
            saved_suggestions.keep(self.db_path, self.shared[0], {"tags": ["Family/Work"]})
        self.assertEqual(0, rows_under(self.db_path, self.lighthouse))

    def test_a_folder_added_gets_no_rows_until_one_is_used(self):
        self.assertEqual(1, library_actions.record_added(Library(self.db_path), [self.lighthouse]))
        self.assertEqual(0, library_actions.record_added(Library(self.db_path), [self.lighthouse]), "added twice")
        self.assertEqual(0, rows_under(self.db_path, self.lighthouse), "the add made rows")
        # Then Suggest may keep what it found there: that photo's row, and no other.
        saved_suggestions.keep(self.db_path, self.shared[0], {"tags": ["Family/Work"]})
        self.assertEqual(1, rows_under(self.db_path, self.lighthouse))

    def test_a_folder_added_with_its_subfolders_covers_them(self):
        below = make_photos(os.path.join(self.lighthouse, "Keepers"), "IMG_0100.jpg")[0]
        library_actions.record_added(Library(self.db_path), [self.lighthouse])
        saved_suggestions.keep(self.db_path, below, {"tags": ["Family/Work"]})
        self.assertEqual(1, rows_under(self.db_path, self.lighthouse))

    def test_removing_the_folder_forgets_the_add(self):
        from tagpup.services import faces as face_actions
        library_actions.record_added(Library(self.db_path), [self.lighthouse])
        face_actions.remove_folder(Library(self.db_path), self.lighthouse)
        with self.assertRaises(Conflict):
            saved_suggestions.keep(self.db_path, self.shared[0], {"tags": ["Family/Work"]})


class FakeIndex:
    """The folder indexer the queue runs, stood in for: no index is started."""

    def __init__(self):
        self.folders = []

    def __call__(self, library, folder, code_folder, **_kwargs):
        self.folders.append(folder)
        return Result(attempted=1, changed=1)


class TheSuggestRoute(Folders, unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup", runtime=mock.Mock())
        self.client = self.app.test_client()
        self.library = Library(self.home.library("library.db"))
        self.make(self.home.root, self.library.path)
        self.addCleanup(suggestion_jobs.forget, self.library)
        self.addCleanup(tagpup_routes.folders.forget, self.library)
        self.addCleanup(indexing_jobs.forget, self.library)
        self.started = []
        patcher = mock.patch.object(suggestion_jobs.SuggestionRuns, "start",
                                    lambda runs, folder, work: self.started.append(folder) or "running")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.indexed = FakeIndex()
        patcher = mock.patch.object(tagpup_routes.indexing, "index_folder", self.indexed)
        patcher.start()
        self.addCleanup(patcher.stop)

    def suggest(self, folder):
        return self.client.post("/library/api/folder/suggest-start", json={"folder_path": folder})

    def test_refuses_a_folder_the_library_does_not_hold_and_names_it(self):
        reply = self.suggest(self.lighthouse)
        self.assertEqual(409, reply.status_code, reply.data)
        self.assertEqual("%s is not in library. Add it to library first." % self.lighthouse, reply.get_json()["error"])
        self.assertEqual([], self.started)
        self.assertEqual(0, rows_under(self.library.path, self.lighthouse))
        self.assertEqual(2, rows_under(self.quayside, self.lighthouse), "the other library was changed")

    def test_refuses_a_held_folder_holding_one_it_does_not(self):
        scans = os.path.join(self.regatta, "Scans")
        make_photos(scans, "scan_01.jpg")
        reply = self.suggest(self.regatta)
        self.assertEqual(409, reply.status_code, reply.data)
        self.assertIn("1 folder(s) under %s are not in library, %s first" % (self.regatta, scans),
                      reply.get_json()["error"])
        self.assertEqual([], self.started)

    def test_starts_on_a_folder_the_library_holds(self):
        reply = self.suggest(self.regatta)
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual([self.regatta], self.started)

    def test_starts_once_the_folder_is_added(self):
        reply = self.client.post("/library/api/folder/add", json={"folder_path": self.lighthouse})
        self.assertEqual(200, reply.status_code, reply.data)
        answer = reply.get_json()
        self.assertEqual((True, "library", self.lighthouse, 1, [self.lighthouse]),
                         (answer["success"], answer["library"], answer["folder"], answer["added"], answer["queued"]))
        # No row is made by the add: the indexer makes each as it reads the photo.
        self.assertEqual(1, all_rows(self.library.path))
        indexing_jobs.queue_for(self.library).wait()
        self.assertEqual([self.lighthouse], self.indexed.folders)
        reply = self.suggest(self.lighthouse)
        self.assertEqual(200, reply.status_code, reply.data)

    def test_adding_refuses_what_is_not_a_folder(self):
        for body in ({}, {"folder_path": os.path.join(self.home.root, "Nowhere")}):
            with self.subTest(body=body):
                self.assertEqual(400, self.client.post("/library/api/folder/add", json=body).status_code)
        self.assertEqual(1, all_rows(self.library.path))


class TagTunersAddFolder(Folders, unittest.TestCase):
    """TagTuner's Add Folder adds as TagPup's Add does: recorded, then indexed."""

    def test_records_and_queues_the_folder(self):
        app, self.home = web_client.app_for(self, "tuner")
        library = Library(self.home.library("library.db"))
        self.make(self.home.root, library.path)
        self.addCleanup(indexing_jobs.forget, library)
        indexed = FakeIndex()
        with mock.patch("tagpup.web.tuner_routes.indexing.index_folder", indexed):
            reply = app.test_client().post("/library/api/folder/index-start", json={"folder_paths": [self.lighthouse]})
            self.assertEqual(200, reply.status_code, reply.data)
            indexing_jobs.queue_for(library).wait()
        self.assertEqual(([self.lighthouse], 1), (reply.get_json()["queued"], reply.get_json()["added"]))
        self.assertEqual(0, rows_under(library.path, self.lighthouse))
        self.assertIsNone(library_actions.not_in(library, self.lighthouse))
        self.assertEqual([self.lighthouse], indexed.folders)


class TheCli(Folders, unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.make(self.home.root, self.db_path)

    def test_suggest_refuses_a_folder_the_library_does_not_hold(self):
        result = CliRunner().invoke(cli, ["--db", self.db_path, "suggest", self.lighthouse])
        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("is not in harbour", " ".join(result.output.split()))
        self.assertEqual(0, rows_under(self.db_path, self.lighthouse))

    def test_suggest_adds_the_folder_when_asked(self):
        # Stopped where the models would load: what is under test is the adding.
        with mock.patch("tagpup_cli.library_index", side_effect=SystemExit(0)):
            result = CliRunner().invoke(cli, ["--db", self.db_path, "suggest", self.lighthouse, "--add"])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("Added the folder to harbour", " ".join(result.output.split()))
        self.assertEqual(0, rows_under(self.db_path, self.lighthouse))
        self.assertIsNone(library_actions.not_in(Library(self.db_path), self.lighthouse))


class TheMcpWriteTools(Folders, unittest.TestCase):
    """The MCP server's write tools keep rows in step; none makes one in a folder the
    library does not hold."""

    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.make(self.home.root, self.db_path)

    def call(self, tool, arguments):
        async def run():
            async with create_connected_server_and_client_session(server.build()) as client:
                return await client.call_tool(tool, dict(arguments, library="harbour"))
        return asyncio.run(run())

    def test_sync_and_refresh_applied_leave_the_other_folder_alone(self):
        with mock.patch("tagpup.runtime.index_folder", side_effect=AssertionError("nothing is indexed")):
            for tool, arguments in (("sync", {"apply": True}), ("refresh", {"apply": True}),
                                    ("sync", {"apply": True, "folder": self.lighthouse})):
                with self.subTest(tool=tool, arguments=arguments):
                    self.call(tool, arguments)
                    self.assertEqual(0, rows_under(self.db_path, self.lighthouse))
        self.assertEqual({paths.key(self.held[0])}, {paths.key(p) for p in self.rows()})

    def rows(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return [p for (p,) in conn.execute("SELECT path FROM photos")]
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
