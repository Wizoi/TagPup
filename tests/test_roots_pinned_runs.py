"""A run holds one map for its whole length (tagpup.services.roots.pinned; docs/ARCHITECTURE.md,
"Roots and machines"): an index run, a sync pass and a change of photo files.

The map -- where this machine keeps a root -- may be moved while a run is under way (by hand, by
another process: Change location is refused while one runs, but the file is the owner's); the run must
not convert half its paths by the old place and half by the new. And when another process changes the
library's own roots, the run stops, with a sentence, rather than write rows spelled by roots the library
no longer has. A library whose root this machine does not place is refused with the sentence naming
machine_roots.json, never a traceback.
"""
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402
from click.testing import CliRunner  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.services import file_changes, indexing  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.services import sync as sync_service  # noqa: E402
from tagpup.services import tagging  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

WINDOWS = os.name == "nt"


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class PinnedRuns(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_pinned_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=0, outside=0)
        self.assertIsNone(self.side.adopt().refused)
        self.library = self.side.library
        self.copy = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.side.pictures, self.copy, copy_function=shutil.copy2)

    def place_seen_by_a_new_connection(self):
        conn = db.connect(self.side.db_path)
        try:
            return store_roots.roots_for(conn).locations["pictures"][0]
        finally:
            conn.close()

    def move_the_map(self):
        config.set_location("pictures", self.copy)

    def drop_the_root(self):
        """Another process changing the library's own roots."""
        db.write_with_connection(self.side.db_path, lambda conn: store_roots.delete(conn, "pictures"))


class TheSyncPass(PinnedRuns):
    def test_a_map_moved_during_a_pass_changes_nothing_until_it_ends(self):
        seen = []

        def pass_over(*args, **kwargs):
            self.move_the_map()
            seen.append(self.place_seen_by_a_new_connection())
            from tagpup.core.result import Result
            return Result()

        with mock.patch.object(sync_service, "_sync", pass_over):
            sync_service.sync(self.library)
        self.assertEqual([self.side.pictures], seen, "a connection opened mid-run converted by the new place")
        self.assertEqual(self.copy, self.place_seen_by_a_new_connection(), "the pin outlived the run")

    def test_without_the_pin_the_same_move_is_seen_at_once(self):
        """What the pin is for: the new place is what a connection opened afterwards would use."""
        self.move_the_map()
        self.assertEqual(self.copy, self.place_seen_by_a_new_connection())

    def test_the_roots_changed_by_another_process_stop_the_pass_with_a_sentence(self):
        def pass_over(*args, **kwargs):
            self.drop_the_root()
            conn = db.connect(self.side.db_path)
            try:
                store_roots.roots_for(conn)
            finally:
                conn.close()
            raise AssertionError("the pass went on past a change of the library's roots")

        with mock.patch.object(sync_service, "_sync", pass_over):
            result = sync_service.sync(self.library, apply=True)
        self.assertEqual(roots_service.STOPPED, result.refused)
        self.assertFalse(result.ok)
        self.assertEqual({}, result.details["counts"])
        self.assertFalse(result.details["in_step"])

    def test_a_library_whose_root_this_machine_does_not_place_is_a_sentence_not_a_traceback(self):
        os.remove(config.machine_roots_path())
        result = sync_service.sync(self.library)
        self.assertIn("machine_roots.json", result.refused)
        self.assertIn('"pictures"', result.refused)


class AChangeOfPhotoFiles(PinnedRuns):
    def test_a_map_moved_during_a_change_changes_nothing_until_it_ends(self):
        seen = []

        def write(*args, **kwargs):
            self.move_the_map()
            seen.append(self.place_seen_by_a_new_connection())
            from tagpup.core.result import Result
            return Result()

        with mock.patch.object(file_changes, "_write_fields", write):
            file_changes.write_fields(self.library, "test", rl.EXIFTOOL, [self.side.real[0]], read=[],
                                      plan_one=None)
        self.assertEqual([self.side.pictures], seen)
        self.assertEqual(self.copy, self.place_seen_by_a_new_connection())

    def test_the_roots_changed_meanwhile_stop_a_change_with_a_sentence_and_write_nothing(self):
        def write(*args, **kwargs):
            self.drop_the_root()
            conn = db.connect(self.side.db_path)
            try:
                store_roots.roots_for(conn)
            finally:
                conn.close()

        before = self.side.dump(leave_out=("roots", "generations", "changes", "change_rows", "schema_version"))
        with mock.patch.object(file_changes, "_write_fields", write):
            result = file_changes.write_fields(self.library, "test", rl.EXIFTOOL, [self.side.real[0]], read=[],
                                               plan_one=None)
        self.assertEqual(roots_service.STOPPED, result.refused)
        self.assertEqual(0, result.changed)
        self.assertIsNone(result.details["change"])
        self.assertEqual(before, self.side.dump(leave_out=("roots", "generations", "changes", "change_rows",
                                                           "schema_version")))

    def test_a_change_in_a_library_this_machine_does_not_place_is_refused_in_words_and_writes_nothing(self):
        os.remove(config.machine_roots_path())
        result = file_changes.write_fields(self.library, "test", rl.EXIFTOOL, [self.side.real[0]], read=[],
                                           plan_one=None)
        self.assertIn("machine_roots.json", result.refused or "")
        self.assertEqual(0, result.changed)
        # A service that looks at the rows first is refused by the store, as it always was, naming the
        # same file; the servers answer it before it gets there (tagpup.web.roots_gate).
        with self.assertRaises(paths.UnmappedRoot) as why:
            tagging.change_tags(self.library, self.side.real[:1], ["Harbour"], [], rl.EXIFTOOL)
        self.assertIn("machine_roots.json", str(why.exception))

    def test_a_rename_raises_the_stop_for_its_caller(self):
        def rename_all(*args, **kwargs):
            self.drop_the_root()
            conn = db.connect(self.side.db_path)
            try:
                store_roots.roots_for(conn)
            finally:
                conn.close()

        old = self.side.real[0]
        new = os.path.join(os.path.dirname(old), "Renamed.jpg")
        with mock.patch.object(file_changes.file_journal, "plan", rename_all):
            with self.assertRaises(roots_service.RootsChanged):
                file_changes.rename(self.library, "test", {old: new}, {})
        self.assertTrue(os.path.exists(old), "nothing was renamed")

    def test_an_ordinary_tag_write_still_works_pinned(self):
        result = tagging.change_tags(self.library, self.side.real[:1], ["Harbour"], [], rl.EXIFTOOL)
        self.assertIsNone(result.refused, result.refused)
        self.assertEqual(1, result.changed)


class AnIndexRun(PinnedRuns):
    def run_index(self, body):
        """The command's own body is `body`; the wrapper that holds the map is what is under test, and
        `index` is made with it (test_index_is_the_command_wrapped)."""
        import click
        import tagpup_cli

        @click.command()
        @click.option("--reset", is_flag=True)
        @click.pass_context
        @tagpup_cli._holds_the_roots
        def fake(ctx, reset):
            body()

        return CliRunner().invoke(fake, [], obj={"db": self.side.db_path, "test": False})

    def test_index_is_the_command_wrapped(self):
        import tagpup_cli
        wrapper = tagpup_cli.index.callback.__wrapped__
        self.assertTrue(getattr(wrapper, "holds_the_roots", False), "the index command does not hold one map")
        self.assertEqual("index", wrapper.__wrapped__.__name__)

    def test_the_indexer_holds_the_map_it_started_with(self):
        seen = []

        def body(*args, **kwargs):
            self.move_the_map()
            seen.append(self.place_seen_by_a_new_connection())

        result = self.run_index(body)
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual([self.side.pictures], seen)

    def test_the_roots_changed_meanwhile_stop_it_with_the_exit_code_and_a_sentence(self):
        def body(*args, **kwargs):
            self.drop_the_root()
            conn = db.connect(self.side.db_path)
            try:
                store_roots.roots_for(conn)
            finally:
                conn.close()

        result = self.run_index(body)
        self.assertEqual(roots_service.EXIT_ROOTS_CHANGED, result.exit_code)
        self.assertIn("roots were changed by another process", result.output)

    def test_an_unplaced_root_is_a_sentence_and_exit_1(self):
        os.remove(config.machine_roots_path())
        result = self.run_index(lambda *args, **kwargs: None)
        self.assertEqual(1, result.exit_code)
        self.assertIn("machine_roots.json", result.output)

    def test_the_servers_queue_tells_the_owner_what_the_exit_code_means(self):
        class Child:
            returncode = roots_service.EXIT_ROOTS_CHANGED
            stdout = mock.MagicMock()

            def wait(self):
                return None

        Child.stdout.readline.side_effect = [""]
        Child.stdout.__enter__ = lambda s: s
        Child.stdout.__exit__ = lambda s, *a: False
        with mock.patch.object(indexing.processes, "start", return_value=Child()):
            result = indexing.index_folder(self.library, self.side.pictures, os.getcwd())
        self.assertEqual(roots_service.STOPPED, result.message())
        self.assertNotIn("exit code", result.message())
        self.assertIsNotNone(subprocess)


if __name__ == "__main__":
    unittest.main()
