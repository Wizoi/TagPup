"""Migration 28 and the guard against a library from a newer TagPup (docs/ARCHITECTURE.md, "People by id, stage 2",
phase 2; docs/findings.md, #1012, #1013).

Migration 28 is additive: `idx_faces_tag` and `idx_photo_people_tag`, the indexes a person is read by id with
(`WHERE tag_id = ?` scanned `idx_faces_person` whole on faces and had no index on photo_people), and the empty
`name_review_dismissals`. No row of any table changes, and a change journaled before it can still be undone.

The guard: stage 2 turns the id/name direction round, so a version of the app from before it that opened a migrated
library would write names without ids and read ids it does not know. `schema.ensure` refuses a library whose schema is
newer than the app's `LATEST` -- NewerLibrary, a sentence naming the library and what to start -- and every entry point
shows that sentence, not a traceback: the server (409 under the library's address, the picker's Create 400), the CLI,
the MCP, the doctor, and a snapshot restore.
"""
import hashlib
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import migration_names  # noqa: E402
import photo_rows  # noqa: E402
import web_client  # noqa: E402
from click.testing import CliRunner  # noqa: E402
from face_rows import add_face  # noqa: E402
from test_migrations import at_version  # noqa: E402

from tagpup.core import processes  # noqa: E402
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
    schema._current.clear()


class MigrationTwentyEight(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        at_version(self.path, 27)
        conn = db.connect(self.path)
        try:
            for name in ("a.jpg", "b.jpg"):
                photo = os.path.join(self.home.root, "Pictures", "Coast", name)
                photo_rows.add_read(conn, photo, {"EXIF:DateTimeOriginal": "2024:06:01 10:00:00"})
                add_face(conn, photo, name="Wren Halloway")
            conn.commit()
        finally:
            conn.close()
        schema._current.clear()

    def schema_rows(self):
        return look(self.path, "SELECT type, name, tbl_name FROM sqlite_master ORDER BY type, name")

    def test_a_library_at_27_gets_two_indexes_and_an_empty_table_and_nothing_else(self):
        before_schema = self.schema_rows()
        tables = [name for (name,) in look(self.path, "SELECT name FROM sqlite_master WHERE type = 'table' "
                                                       "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables = [table for table in tables if table not in ("schema_version", "changes", "change_rows")]
        before = {table: look(self.path, "SELECT * FROM %s" % table) for table in tables}
        self.assertEqual(migration_names.named(28), schema.ensure(self.path))
        added = {row for row in self.schema_rows() if row not in before_schema}
        self.assertEqual({("index", "idx_faces_tag", "faces"), ("index", "idx_photo_people_tag", "photo_people"),
                          ("table", "name_review_dismissals", "name_review_dismissals"),
                          ("index", "sqlite_autoindex_name_review_dismissals_1", "name_review_dismissals")}, added,
                         "no column, no trigger, no other table")
        self.assertEqual(before, {table: look(self.path, "SELECT * FROM %s" % table) for table in tables})
        self.assertEqual([(0,)], look(self.path, "SELECT COUNT(*) FROM name_review_dismissals"))
        migration = [m for m in schema.MIGRATIONS if m.version == 28][0]
        self.assertEqual((schema.ADDITIVE, ("name_review_dismissals",)), (migration.kind, migration.touches))

    def test_the_planner_reads_a_person_by_id_from_the_indexes(self):
        schema.ensure(self.path)
        faces = " ".join(row[3] for row in look(self.path, "EXPLAIN QUERY PLAN SELECT id FROM faces WHERE tag_id = ?", (1,)))
        listed = " ".join(row[3] for row in look(
            self.path, "EXPLAIN QUERY PLAN SELECT photo_id FROM photo_people WHERE tag_id = ?", (1,)))
        self.assertIn("idx_faces_tag", faces)
        self.assertIn("idx_photo_people_tag", listed)
        self.assertNotIn("SCAN", listed)

    def test_before_it_a_person_by_id_scanned_the_name_index_and_the_table(self):
        # The gap the migration closes (#1013): at 27 neither query has an index of its own.
        faces = " ".join(row[3] for row in look(self.path, "EXPLAIN QUERY PLAN SELECT id FROM faces WHERE tag_id = ?", (1,)))
        listed = " ".join(row[3] for row in look(
            self.path, "EXPLAIN QUERY PLAN SELECT photo_id FROM photo_people WHERE tag_id = ?", (1,)))
        self.assertIn("SCAN", faces)
        self.assertIn("SCAN", listed)

    def test_a_change_made_at_27_is_still_undoable_after_it(self):
        applied = journal.apply(self.path, "add a node", [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})])
        schema._current.clear()
        schema.ensure(self.path)
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual([], journal.refusal(conn, applied.change_id))
            self.assertIsNone(journal.schema_gap_blocker(27, 28))
        finally:
            conn.close()

    def test_interrupted_it_leaves_27_and_the_next_open_runs_it_again(self):
        def crashes(conn):
            schema._person_indexes(conn)
            raise RuntimeError("the power went")

        broken = [m for m in schema.MIGRATIONS[:27]] + [schema.MIGRATIONS[27]._replace(apply=crashes)]
        with mock.patch.object(schema, "MIGRATIONS", tuple(broken)):
            with self.assertRaises(RuntimeError):
                schema.ensure(self.path)
        names = [name for (_t, name, _tbl) in self.schema_rows()]
        self.assertEqual(27, look(self.path, "SELECT MAX(version) FROM schema_version")[0][0])
        self.assertNotIn("idx_faces_tag", names)
        self.assertNotIn("name_review_dismissals", names)
        schema._current.clear()
        self.assertEqual(migration_names.named(28), schema.ensure(self.path))
        self.assertIn("idx_faces_tag", [name for (_t, name, _tbl) in self.schema_rows()])

    def test_two_processes_opening_it_at_once_apply_it_once(self):
        code = ("import sys; sys.path.insert(0, %r); from tagpup.store import schema; "
                "print(len(schema.ensure(%r)))" % (ROOT, self.path))
        env = dict(os.environ, TAGPUP_HOME=self.home.root)
        running = [processes.start([sys.executable, "-c", code], env=env, stdout=-1, stderr=-1, text=True) for _ in range(2)]
        answers = []
        for each in running:
            out, err = each.communicate(timeout=120)
            self.assertEqual(0, each.returncode, err)
            answers.append(out.strip())
        self.assertEqual(["0", "1"], sorted(answers))
        self.assertEqual(1, len(look(self.path, "SELECT 1 FROM schema_version WHERE version = 28")))

    def test_two_threads_opening_it_at_once_apply_it_once(self):
        applied = []
        barrier = threading.Barrier(2)

        def open_it():
            barrier.wait()
            applied.append(len(schema.ensure(self.path)))

        threads = [threading.Thread(target=open_it) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual([0, 1], sorted(applied))


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
        self.assertIn("data/backups", said, "where the backups are")
        self.assertIn("snapshots restore", said, "and how to go back")
        self.assertEqual((schema.LATEST + 1, schema.LATEST), (refused.exception.found, refused.exception.known))
        self.assertEqual(before, fingerprint(self.path))

    def test_the_app_before_migration_28_refuses_a_library_that_has_it(self):
        # 28 is in the library, and the app asking is one that knows 27. NOT the installed app of today: its `_ensure`
        # is `if version >= LATEST: return`, so it opens a 28 library as it opens any; the guard first bites at the
        # update AFTER this one is installed (an app that has it), which is why it must not stand in the way of
        # recovery (RecoveryOfANewerLibrary).
        path = self.home.library("older.db")
        library_actions.create(path)
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS[:27]), mock.patch.object(schema, "LATEST", 27):
            schema._current.clear()
            with self.assertRaises(schema.NewerLibrary):
                schema.ensure(path)

    def test_a_library_at_the_apps_own_version_or_behind_it_is_opened(self):
        path = self.home.library("current.db")
        at_version(path, schema.LATEST - 1)
        schema._current.clear()
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
        for command in (["stats"], ["list-index"], ["sync"], ["undo", "1"]):
            with self.subTest(command=command[0]):
                result = CliRunner().invoke(cli, ["--db", self.path] + command)
                self.assertEqual(1, result.exit_code, result.output)
                self.assertIn(" ".join(self.sentence.split()), " ".join(result.output.split()))
                self.assertNotIn("Traceback", result.output)
        self.assertEqual(before, fingerprint(self.path))

    def test_the_cli_does_not_judge_a_library_that_is_not_there(self):
        from tagpup_cli import cli
        missing = os.path.join(os.path.dirname(self.path), "nobody.db")
        result = CliRunner().invoke(cli, ["--db", missing, "stats"])
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


class TwoProcessesBringingALibraryAt26UpToDate(unittest.TestCase):
    def test_the_servers_startup_thread_and_a_cli_run_migrate_27_then_28_once(self):
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        at_version(path, 26)
        server = ("import sys; sys.path.insert(0, %r); from tagpup.core.library import Library; "
                  "from tagpup.services import libraries; "
                  "libraries.bring_up_to_date_in_background([Library(%r)]).join(); print('server')" % (ROOT, path))
        cli = ("import sys; sys.path.insert(0, %r); from tagpup.services import libraries; "
               "print(len(libraries.bring_up_to_date(%r)))" % (ROOT, path))
        env = dict(os.environ, TAGPUP_HOME=home.root)
        running = [processes.start([sys.executable, "-c", code], env=env, stdout=-1, stderr=-1, text=True)
                   for code in (server, cli)]
        for each in running:
            out, err = each.communicate(timeout=180)
            self.assertEqual(0, each.returncode, err)
        self.assertEqual([(27,), (28,)], look(path, "SELECT version FROM schema_version WHERE version >= 27 ORDER BY version"))
        self.assertEqual(["migration 27: photos by their camera and lens", "migration 28: people read by id, and the names set aside"],
                         [row[0] for row in look(path, "SELECT operation FROM changes WHERE operation LIKE 'migration 2%' "
                                                       "AND operation >= 'migration 27' ORDER BY id")],
                         "each recorded once")
        self.assertEqual(schema.LATEST, look(path, "SELECT MAX(version) FROM schema_version")[0][0])


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
        schema._current.clear()
        self.assertEqual([], schema.ensure(self.path))

    def test_every_cli_writer_is_refused_with_the_backups_named(self):
        for command in (["undo", "1", "--apply"], ["sync", "--apply"], ["prune-journal", "--apply"], ["stats"]):
            with self.subTest(command=command):
                before = fingerprint(self.path)
                result = self.cli(*command)
                self.assertEqual(1, result.exit_code, result.output)
                self.assertIn("data/backups", " ".join(result.output.split()))
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

    def test_the_mcp_reads_with_a_note_and_refuses_each_writer(self):
        from mcp.server.fastmcp.exceptions import ToolError
        from tagpup.mcp import server
        folder = os.path.dirname(self.path)
        with mock.patch.object(server.config, "data_dir", return_value=folder), \
                mock.patch.object(server.config, "library_path", side_effect=lambda f: os.path.join(folder, f)):
            answer = server._answer(lambda: (server.find_library("library", photos=False), {"ok": 1})[1])
            self.assertEqual(1, answer["ok"])
            self.assertIn("newer TagPup", answer["note"])
            for photos in (True, False):
                with self.assertRaises(ToolError) as refused:
                    server.find_library("library", photos=photos, writes=True)
                self.assertIn("data/backups", str(refused.exception))
            written = [name for name in ("refresh_rows", "sync", "merge_duplicate_person_tags", "dedupe_faces", "undo",
                                         "prune_journal")]
            tools = {each.name for each in __import__("asyncio").run(server.build().list_tools())}
            self.assertLessEqual(set(written), tools)

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
