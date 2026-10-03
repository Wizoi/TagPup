"""Sync from each place it is asked for: the route both apps serve (/api/sync), the CLI's
`sync`, and the MCP server's `sync` and `sync_state` (tagpup.runtime.sync over
tagpup.services.sync; docs/ARCHITECTURE.md, phase 8).

Each is a dry run unless told to apply, answers counts -- never a path, since folders
name people -- and, applied, records the run, which GET /api/sync and `sync_state` read
back as "last in step". The index queue is the process's own; the folder indexer it runs
is stood in for, so no index is started, and the test waits for the queue as the CLI
does, without sleeping.
"""
import asyncio
import json
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

from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.mcp import server  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup_cli import cli  # noqa: E402

THEN = 1_700_000_000


class Drifted:
    """A library whose one row describes its file, beside a folder holding a file it has
    no row for."""

    def drift(self, root, db_path):
        self.folder = os.path.join(root, "Harbourview Regatta")
        os.makedirs(self.folder)
        known, self.new = os.path.join(self.folder, "regatta_01.jpg"), os.path.join(self.folder, "regatta_02.jpg")
        for path in (known, self.new):
            with open(path, "wb") as handle:
                handle.write(b"jpeg")
            os.utime(path, (THEN, THEN))
        conn = db.connect(db_path)
        try:
            photo_rows.add_read(conn, known, {"XMP:Subject": ["People/Rowan Thackeray"]})
            conn.commit()
        finally:
            conn.close()
        self.indexed, self.with_subfolders, self.runs = [], [], []

        def index_folder(library, subfolders=True):
            def index(folder, cluster, report):
                # A batch is handed as a list: one run of the indexer.
                folders = [folder] if isinstance(folder, str) else list(folder)
                self.runs.append(folders)
                self.indexed.extend(folders)
                self.with_subfolders.append(subfolders)
                return Result(attempted=1, changed=1)
            return index

        patcher = mock.patch("tagpup.runtime.index_folder", side_effect=index_folder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_no_path(self, text):
        for secret in (self.folder, self.folder.replace("\\", "\\\\"), "regatta_", "Rowan Thackeray"):
            self.assertNotIn(secret, text)


class TheRoutes(Drifted, unittest.TestCase):
    def test_a_dry_run_then_an_apply_then_last_in_step_on_both_apps(self):
        for kind in ("tagpup", "tuner"):
            with self.subTest(app=kind):
                app, home = web_client.app_for(self, kind)
                client = app.test_client()
                library = Library(home.library("library.db"))
                self.drift(os.path.join(home.root, kind), library.path)

                state = client.get("/library/api/sync")
                self.assertEqual(200, state.status_code, state.data)
                self.assertEqual({"library": "library", "last_run": None, "last_in_step": None, "syncing": False}, state.get_json())

                dry = client.post("/library/api/sync", json={})
                self.assertEqual(200, dry.status_code, dry.data)
                answer = dry.get_json()
                self.assert_no_path(dry.get_data(as_text=True))
                self.assertEqual((True, 1, 0, 0, False), (answer["dry_run"], answer["counts"]["new"],
                                                          answer["queued"], answer["changed"], answer["in_step"]))
                self.assertIsNone(client.get("/library/api/sync").get_json()["last_run"])
                self.assertEqual([], self.indexed)

                applied = client.post("/library/api/sync", json={"apply": True})
                self.assertEqual(200, applied.status_code, applied.data)
                self.assertEqual((False, 1), (applied.get_json()["dry_run"], applied.get_json()["queued"]))
                indexing_jobs.queue_for(library).wait()
                self.assertEqual([self.folder], self.indexed)
                self.assertEqual([False], self.with_subfolders, "an indexed folder's new files took its subfolders")
                indexing_jobs.forget(library)

                state = client.get("/library/api/sync").get_json()
                self.assert_no_path(json.dumps(state))
                self.assertEqual((False, 1), (state["last_run"]["in_step"], state["last_run"]["changed"]["queued_folders"]))
                self.assertIsNone(state["last_in_step"])
                self.indexed.clear()
                self.with_subfolders.clear()

    def test_the_folders_to_review_are_listed_included_and_ignored(self):
        app, home = web_client.app_for(self, "tuner")
        client = app.test_client()
        library = Library(home.library("library.db"))
        self.drift(os.path.join(home.root, "tuner"), library.path)
        # The owner sets the regatta's folder as the library's root; folders made after it
        # are the ones offered.
        from tagpup.services import settings as settings_service
        settings_service.of(library)
        self.assertTrue(settings_service.change(library, {settings_service.ROOTS: self.folder}).ok)
        found = os.path.join(self.folder, "Quayside")
        ignored = os.path.join(self.folder, "Scans")
        for folder in (found, ignored):
            os.makedirs(folder)
            with open(os.path.join(folder, "IMG_0500.jpg"), "wb") as handle:
                handle.write(b"jpeg")
        listed = client.get("/library/api/sync/review")
        self.assertEqual(200, listed.status_code, listed.data)
        self.assertEqual({"library": "library", "count": 2, "photos": 2,
                          "folders": [{"path": found, "photos": 1}, {"path": ignored, "photos": 1}]}, listed.get_json())
        reply = client.post("/library/api/sync/review/ignore", json={"folder": ignored})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual(1, reply.get_json()["changed"])
        self.assertEqual([{"path": found, "photos": 1}], client.get("/library/api/sync/review").get_json()["folders"])
        reply = client.post("/library/api/sync/review/include", json={"folder": found})
        self.assertEqual((200, 1), (reply.status_code, reply.get_json()["queued"]), reply.data)
        indexing_jobs.queue_for(library).wait()
        self.assertEqual(([found], [True]), (self.indexed, self.with_subfolders))
        indexing_jobs.forget(library)
        self.assertEqual(400, client.post("/library/api/sync/review/include", json={"folder": "Quayside"}).status_code)


class AFolderIsChecked(Drifted, unittest.TestCase):
    """A folder is checked as the index routes check one (tagpup.core.validation), and one
    the library holds no photo under is refused with a message saying so: sync keeps
    indexed folders in step; indexing a folder is TagTuner's Add Folder."""

    def test_the_route_refuses_a_folder_that_is_no_full_path_or_holds_no_indexed_photo(self):
        app, home = web_client.app_for(self, "tuner")
        client = app.test_client()
        self.drift(os.path.join(home.root, "tuner"), home.library("library.db"))
        elsewhere = os.path.join(home.root, "Elsewhere")
        os.makedirs(elsewhere)
        for folder, says in (("Harbourview Regatta", "full path"), (elsewhere, "no photo")):
            with self.subTest(folder=folder):
                reply = client.post("/library/api/sync", json={"folder": folder})
                self.assertEqual(400, reply.status_code, reply.data)
                self.assertIn(says, reply.get_json()["error"])
                self.assertFalse(reply.get_json()["in_step"])
        self.assertEqual(200, client.post("/library/api/sync", json={"folder": self.folder}).status_code)

    def test_the_cli_refuses_a_folder_that_holds_no_indexed_photo(self):
        home = own_home.for_test(self)
        db_path = home.library("harbour.db")
        library_actions.create(db_path)
        self.drift(home.root, db_path)
        elsewhere = os.path.join(home.root, "Elsewhere")
        os.makedirs(elsewhere)
        result = CliRunner().invoke(cli, ["--db", db_path, "sync", "--folder", elsewhere])
        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("no photo", result.output)


class TheCli(Drifted, unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.drift(self.home.root, self.db_path)

    def run_cli(self, *args):
        result = CliRunner().invoke(cli, ["--db", self.db_path, "sync", *args])
        self.assertEqual(0, result.exit_code, result.output + repr(result.exception))
        self.assert_no_path(result.output)
        return result.output

    def test_a_dry_run_says_what_it_found_and_changes_nothing(self):
        out = self.run_cli()
        self.assertIn("1 new file(s), in 1 folder(s)", out)
        self.assertIn("--apply", out)
        self.assertEqual([], self.indexed)
        self.assertIsNone(self.last()["last_run"])

    def test_apply_indexes_the_new_files_folder_and_waits_for_it(self):
        out = self.run_cli("--apply")
        self.assertEqual([self.folder], self.indexed)
        self.assertIn("Indexing 1 folder(s)", out)
        self.assertEqual(1, self.last()["last_run"]["changed"]["queued_folders"])
        indexing_jobs.forget(Library(self.db_path))

    def last(self):
        from tagpup.services import sync
        return sync.last(Library(self.db_path))

    def test_one_run_of_the_indexer_for_every_folder_of_new_files(self):
        other = os.path.join(self.home.root, "Harbourview Regatta 2")
        os.makedirs(other)
        for name in ("regatta_10.jpg", "regatta_11.jpg"):
            with open(os.path.join(other, name), "wb") as handle:
                handle.write(b"jpeg")
        conn = db.connect(self.db_path)
        try:
            photo_rows.add_read(conn, os.path.join(other, "regatta_10.jpg"), {})
            conn.commit()
        finally:
            conn.close()
        self.run_cli("--apply")
        self.assertEqual([sorted([self.folder, other])], [sorted(run) for run in self.runs])
        indexing_jobs.forget(Library(self.db_path))

    def test_a_batch_is_one_job_whose_folders_share_its_status(self):
        queue = indexing_jobs.IndexQueue()
        other = os.path.join(self.home.root, "Harbourview Regatta 2")
        os.makedirs(other)
        handed = []

        def index(folders, cluster, report):
            handed.append(folders)
            return Result(attempted=1, changed=1)

        with mock.patch.object(indexing_jobs.IndexQueue, "_ensure_runner"):
            started = queue.start([self.folder, other], index, together=True)
            self.assertEqual(2, started.changed)
            self.assertEqual(1, len(queue.pending()))
            self.assertEqual(0, queue.start([other], index, together=True).changed, "queued twice")
            queue.run_pending()
        self.assertEqual([[self.folder, other]], handed)
        self.assertEqual(("completed", "completed"), (queue.status(self.folder)["status"],
                                                      queue.status(other)["status"]))

    def test_jobs_run_sync_waits_for_the_indexing_it_queued(self):
        """The CLI is a process that ends: the index queue it filled is indexed on its
        thread, so `jobs run sync` waits for it, as `sync --apply` does."""
        waited = []
        real_wait = indexing_jobs.IndexQueue.wait

        def wait(queue):
            waited.append(queue)
            return real_wait(queue)

        with mock.patch.object(indexing_jobs.IndexQueue, "wait", autospec=True, side_effect=wait):
            result = CliRunner().invoke(cli, ["--db", self.db_path, "jobs", "run", "sync"])
        self.assertEqual(0, result.exit_code, result.output + repr(result.exception))
        self.assertEqual([self.folder], self.indexed)
        self.assertIn(indexing_jobs.queue_for(Library(self.db_path)), waited)
        indexing_jobs.forget(Library(self.db_path))


class TheTools(Drifted, unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.drift(self.home.root, self.db_path)
        patcher = mock.patch.object(server.config, "exiftool_path",
                                    return_value=os.path.join(self.home.root, "no-exiftool.exe"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, tool, **arguments):
        async def run():
            async with create_connected_server_and_client_session(server.build()) as client:
                return await client.call_tool(tool, dict(arguments, library="harbour"))
        result = asyncio.run(run())
        self.assertFalse(result.isError, result.content[0].text if result.content else result)
        self.assert_no_path(result.content[0].text)
        return json.loads(result.content[0].text)

    def test_sync_counts_and_queues_nothing_and_sync_state_reads_the_record(self):
        self.assertEqual({"last_run": None, "last_in_step": None}, self.call("sync_state"))
        with open(self.db_path, "rb") as handle:
            before = handle.read()
        dry = self.call("sync")
        with open(self.db_path, "rb") as handle:
            self.assertEqual(before, handle.read(), "a dry run changed the library")
        self.assertEqual((True, 1, False), (dry["dry_run"], dry["counts"]["new"], dry["in_step"]))
        applied = self.call("sync", apply=True)
        self.assertEqual((False, 0), (applied["dry_run"], applied["changed"]))
        self.assertEqual([], self.indexed, "the tool indexes nothing; it counts the new files")
        state = self.call("sync_state")
        self.assertEqual((1, 0), (state["last_run"]["found"]["new"], state["last_run"]["changed"]["queued_folders"]))
        revealed = asyncio.run(self._revealed())
        self.assertIn(self.new, revealed)

    async def _revealed(self):
        async with create_connected_server_and_client_session(server.build()) as client:
            result = await client.call_tool("sync", {"library": "harbour", "reveal": True})
        return json.loads(result.content[0].text)["reveal"]["new"]


if __name__ == "__main__":
    unittest.main()
