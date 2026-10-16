"""The guard against a library from a newer TagPup (docs/ARCHITECTURE.md, "People by id, stage 2", phase 2; docs/findings.md,
#1012, #1013), and what an older app can still do with one.

Stage 2 turned the id/name direction round, so a version of the app from before it that opened a migrated library would
write names without ids and read ids it does not know. `schema.ensure` refuses a library whose schema is newer than the
app's `LATEST` -- NewerLibrary, a sentence naming the library and what to start -- and every entry point shows that sentence,
not a traceback: the server (409 under the library's address, the picker's Create 400), the CLI, the MCP, the doctor, and a
snapshot restore. The recovery tools of an older checkout (the snapshots, the journal's read, the doctor's report, the MCP's
reads) still work on it, as it is. None of this needs a real migration: a library newer than the code is made by writing a
version the code does not know, or by giving the code a synthetic step the other copy lacks (tests/migration_names.py).
"""
import hashlib
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import migration_names  # noqa: E402
import web_client  # noqa: E402
from click.testing import CliRunner  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


def look(path, sql, params=()):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def fingerprint(path):
    """The rows of every table of the library, as the library holds them (what 'nothing was written' compares)."""
    tables = [name for (name,) in look(path, "SELECT name FROM sqlite_master WHERE type = 'table' "
                                             "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return hashlib.sha256(json.dumps({table: look(path, "SELECT * FROM %s" % table) for table in tables},
                                     default=repr).encode("utf-8")).hexdigest()


def make_newer(path, by=1):
    """The library at `path` as a newer TagPup left it: one migration more than this version knows."""
    conn = db.connect(path)
    try:
        conn.execute("INSERT INTO schema_version (version, name, applied_at) VALUES (?, 'a later step', '2026-10-09')",
                     (schema.LATEST + by,))
        conn.commit()
    finally:
        conn.close()
    schema.forget()


class AnOlderAppAndALibraryItHasNotSeen(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        library_actions.create(self.path)
        make_newer(self.path)

    def test_ensure_refuses_it_with_a_sentence_and_writes_nothing(self):
        before = fingerprint(self.path)
        with self.assertRaises(schema.NewerLibrary) as refused:
            schema.ensure(self.path)
        said = str(refused.exception)
        self.assertIn("harbour.db", said)
        self.assertNotIn(self.home.root, said, "the library's name, not its place")
        self.assertIn("schema is %d" % (schema.LATEST + 1), said)
        self.assertIn("knows up to %d" % schema.LATEST, said)
        self.assertIn("Start the newest TagPup", said)
        self.assertIn("backups", said, "where the backups are")
        self.assertIn("snapshots restore", said, "and how to go back")
        self.assertIn(".db-wal", said, "and what to delete before copying a backup by hand")
        self.assertEqual((schema.LATEST + 1, schema.LATEST), (refused.exception.found, refused.exception.known))
        self.assertEqual(before, fingerprint(self.path))

    def test_the_app_before_a_step_refuses_a_library_that_has_it(self):
        # The library has a step the app asking does not know (the guard first bites at the update AFTER the one that
        # brought the step is installed, which is why it must not stand in the way of recovery: RecoveryOfANewerLibrary).
        path = self.home.library("older.db")
        with migration_names.ahead():
            library_actions.create(path)
        with self.assertRaises(schema.NewerLibrary):
            schema.ensure(path)

    def test_a_library_at_the_apps_own_version_or_behind_it_is_opened(self):
        path = self.home.library("current.db")
        library_actions.create(path)
        with migration_names.ahead():   # one step behind
            self.assertEqual(1, len(schema.ensure(path)))
            self.assertEqual([], schema.ensure(path))
            self.assertIsNone(schema.newer_problem(path))

    def test_what_writes_through_the_store_is_refused_before_it_writes(self):
        before = fingerprint(self.path)
        with self.assertRaises(schema.NewerLibrary):
            journal.apply(self.path, "add a node", [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})])
        self.assertEqual(before, fingerprint(self.path))

    def test_a_file_that_is_not_a_library_is_not_called_newer(self):
        junk = self.home.library("junk.db")
        with open(junk, "wb") as handle:
            handle.write(b"not a database at all" * 100)
        self.assertIsNone(schema.newer_problem(junk))
        self.assertIsNone(schema.newer_problem(os.path.join(os.path.dirname(junk), "nobody.db")))


class EveryEntryPointSaysItInWords(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.path = self.home.library("library.db")
        make_newer(self.path)
        self.sentence = schema.newer_problem(self.path)
        self.assertTrue(self.sentence)

    def test_the_server_answers_a_page_and_an_api_under_the_library_with_the_sentence(self):
        client = self.app.test_client()
        api = client.get("/library/api/databases")
        self.assertEqual(409, api.status_code)
        self.assertEqual({"success": False, "error": self.sentence}, api.get_json())
        page = client.get("/library/")
        self.assertEqual(409, page.status_code)
        self.assertIn(self.sentence, page.get_data(as_text=True))
        self.assertNotIn("Traceback", page.get_data(as_text=True))

    def test_the_status_a_hand_over_waits_for_still_answers(self):
        status = self.app.test_client().get("/api/server")
        self.assertEqual(200, status.status_code, "the supervisor asks it, and names no library")
        self.assertIn("version", status.get_json())

    def test_the_pickers_create_refuses_a_name_that_is_a_newer_library(self):
        before = fingerprint(self.path)
        answer = self.app.test_client().post("/api/databases/create", json={"db_name": "library"})
        self.assertEqual(400, answer.status_code)
        self.assertEqual(self.sentence, answer.get_json()["error"])
        self.assertEqual(before, fingerprint(self.path))
        result = library_actions.create(self.path)
        self.assertEqual(self.sentence, result.refused)
        self.assertEqual(0, result.changed)
        self.assertEqual(before, fingerprint(self.path))

    def test_the_cli_says_it_and_exits_1_for_any_command(self):
        before = fingerprint(self.path)
        from tagpup_cli import cli
        for command in (["sync"], ["undo", "1"]):
            with self.subTest(command=command[0]):
                result = CliRunner().invoke(cli, ["--db", self.path] + command)
                self.assertEqual(1, result.exit_code, result.output)
                self.assertIn(" ".join(self.sentence.split()), " ".join(result.output.split()))
                self.assertNotIn("Traceback", result.output)
        self.assertEqual(before, fingerprint(self.path))

    def test_the_cli_does_not_judge_a_library_that_is_not_there(self):
        from tagpup_cli import cli
        missing = os.path.join(os.path.dirname(self.path), "nobody.db")
        result = CliRunner().invoke(cli, ["--db", missing, "history"])
        self.assertNotIn("newer version", result.output)

    def test_the_mcp_refuses_to_name_it(self):
        from mcp.server.fastmcp.exceptions import ToolError
        from tagpup.mcp import server
        with mock.patch.object(server.config, "data_dir", return_value=os.path.dirname(self.path)), \
                mock.patch.object(server.config, "library_path", side_effect=lambda f: os.path.join(os.path.dirname(self.path), f)):
            with self.assertRaises(ToolError) as refused:
                server.find_library("library", writes=True)
        self.assertEqual(self.sentence, str(refused.exception))

    def test_the_doctor_says_it_in_both_its_modes(self):
        import doctor
        for run in (lambda: doctor.rebuild_derived(self.path), lambda: doctor.rebuild_derived(self.path, apply=True)):
            with self.assertRaises(SystemExit) as refused:
                run()
            self.assertEqual(self.sentence, str(refused.exception))


class RecoveryOfANewerLibrary(unittest.TestCase):
    """An older checkout is how the owner goes back; it must still list and restore snapshots, read the journal, run the
    doctor's report and the MCP's reads on a library from a newer TagPup (schema 99 here), and every writer is refused.
    ONE owner of which may: schema.reading_newer()."""

    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.path = self.home.library("library.db")
        from tagpup.store import snapshots
        snapshots.take(self.path, now=1_800_000_000)
        self.snapshot = snapshots.listed(self.path, "daily")[0].name
        make_newer(self.path, by=99 - schema.LATEST)
        self.assertEqual(99, schema.version_of(self.path))

    def cli(self, *command):
        from tagpup_cli import cli
        return CliRunner().invoke(cli, ["--db", self.path] + list(command))

    def test_ensure_lets_it_through_only_inside_reading_newer_and_changes_nothing(self):
        before = fingerprint(self.path)
        with self.assertRaises(schema.NewerLibrary):
            schema.ensure(self.path)
        with schema.reading_newer():
            self.assertEqual([], schema.ensure(self.path))
        with self.assertRaises(schema.NewerLibrary):
            schema.ensure(self.path)
        self.assertEqual(before, fingerprint(self.path))

    def test_the_cli_lists_and_restores_snapshots_and_reads_the_journal_with_a_note(self):
        for command in (["history"], ["snapshots", "list"], ["snapshots", "restore", self.snapshot]):
            with self.subTest(command=command):
                result = self.cli(*command)
                self.assertEqual(0, result.exit_code, result.output)
                self.assertIn("is from a newer TagPup", result.output)
                self.assertNotIn("Traceback", result.output)
        self.assertIn(self.snapshot, self.cli("snapshots", "list").output)

    def test_a_snapshot_restore_applied_puts_the_older_library_back_and_it_opens_again(self):
        result = self.cli("snapshots", "restore", self.snapshot, "--apply")
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual(schema.LATEST, schema.version_of(self.path))
        schema.forget(self.path)
        self.assertEqual([], schema.ensure(self.path))

    def test_every_cli_writer_is_refused_with_the_backups_named(self):
        for command in (["undo", "1", "--apply"], ["sync", "--apply"]):
            with self.subTest(command=command):
                before = fingerprint(self.path)
                result = self.cli(*command)
                self.assertEqual(1, result.exit_code, result.output)
                self.assertIn("backups", " ".join(result.output.split()))
                self.assertEqual(before, fingerprint(self.path))

    def test_the_doctors_report_reads_it_and_every_write_is_refused(self):
        import doctor
        said = []
        doctor.report(self.path, out=said.append)
        self.assertTrue(said[0].startswith("Note:") and "newer TagPup" in said[0], said[:1])
        before = fingerprint(self.path)
        for kwargs in ({}, {"apply": True}):
            with self.assertRaises(SystemExit):
                doctor.rebuild_derived(self.path, **kwargs)
        self.assertEqual(before, fingerprint(self.path))

    def mcp(self):
        """The MCP server's tools, called as a client calls them, on this home's libraries."""
        import asyncio
        from tagpup.mcp import server
        folder = os.path.dirname(self.path)
        patches = [mock.patch.object(server.config, "data_dir", return_value=folder),
                   mock.patch.object(server.config, "library_path", side_effect=lambda f: os.path.join(folder, f))]
        for each in patches:
            each.start()
            self.addCleanup(each.stop)
        tools = server.build()
        return lambda name, **arguments: asyncio.run(tools.call_tool(name, arguments))

    def test_each_mcp_writing_tool_is_refused_and_writes_nothing(self):
        from mcp.server.fastmcp.exceptions import ToolError
        call = self.mcp()
        before = fingerprint(self.path)
        for name, arguments in (("refresh_rows", {"apply": True}), ("sync", {"apply": True}), ("sync", {}),
                                ("merge_duplicate_person_tags", {"apply": True}), ("dedupe_faces", {"apply": True}),
                                ("undo", {"change": 1, "apply": True})):
            with self.subTest(tool=name, arguments=arguments):
                with self.assertRaises(ToolError) as refused:
                    call(name, library="library", **arguments)
                self.assertIn("backups", str(refused.exception))
        self.assertEqual(before, fingerprint(self.path))

    def test_a_writing_tool_is_never_inside_reading_newer_even_when_the_library_turns_newer_after_the_check(self):
        # An update migrates the library while a long sync(apply) runs: find_library's check was passed, so the
        # last guard is `ensure`, and it must not be switched off around a writer.
        from mcp.server.fastmcp.exceptions import ToolError
        from tagpup.mcp import server
        call = self.mcp()
        before = fingerprint(self.path)
        refused = []
        with mock.patch.object(server, "find_library", side_effect=lambda *a, **k: Library(self.path)):
            for name, arguments in (("sync", {"apply": True}), ("refresh_rows", {"apply": True}),
                                    ("merge_duplicate_person_tags", {"apply": True}), ("dedupe_faces", {"apply": True}),
                                    ("undo", {"change": 1, "apply": True})):
                with self.subTest(tool=name):
                    try:
                        call(name, library="library", **arguments)
                    except ToolError:
                        refused.append(name)
        self.assertIn("sync", refused, "the one that writes the library's last sync is stopped by ensure")
        self.assertEqual(before, fingerprint(self.path), "nothing written into the newer library")
        self.assertEqual([(0,)], look(self.path, "SELECT COUNT(*) FROM job_runs WHERE job IN ('sync-whole', 'sync-folder')"))

    def test_the_mcp_read_tools_answer_with_the_note(self):
        call = self.mcp()
        before = fingerprint(self.path)
        for name in ("summary", "history", "checks", "sync_state", "folders"):
            with self.subTest(tool=name):
                answer = call(name, library="library")
                self.assertIn("newer TagPup", json.dumps(answer, default=repr))
        self.assertEqual(before, fingerprint(self.path))

    def test_the_server_still_answers_its_status_and_refuses_the_library(self):
        client = self.app.test_client()
        self.assertEqual(200, client.get("/api/server").status_code)
        self.assertEqual(409, client.get("/library/api/people").status_code)


class ASnapshotFromANewerVersion(unittest.TestCase):
    def test_it_is_refused_in_the_same_words_and_the_library_is_not_touched(self):
        from tagpup.services import snapshots as snapshot_service
        from tagpup.store import snapshots
        home = own_home.for_test(self)
        library = Library(home.library("harbour.db"))
        library_actions.create(library.path)
        snapshots.take(library.path, now=1_800_000_000)
        daily = snapshots.listed(library.path, "daily")[0]
        conn = db.connect(daily.path)
        try:
            conn.execute("INSERT INTO schema_version (version, name, applied_at) VALUES (?, 'later', '2026-10-09')",
                         (schema.LATEST + 1,))
            conn.commit()
            conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            conn.close()
        before = fingerprint(library.path)
        result = snapshot_service.restore(library, daily.name, apply=True)
        self.assertEqual(schema.newer_sentence("The snapshot " + daily.name, schema.LATEST + 1, schema.LATEST, recover=False),
                         result.refused)
        self.assertIn("Start the newest TagPup", result.refused)
        self.assertEqual((0, before), (result.changed, fingerprint(library.path)))


if __name__ == "__main__":
    unittest.main()
