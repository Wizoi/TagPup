"""A library that holds a root, opened on a machine that does not say where it keeps it
(tagpup.services.roots, docs/ARCHITECTURE.md, "Roots and machines").

The photos are refused, never read as an empty library; the check, the listing, the doctor and the
command line say why and what to write, and the `roots` command lists and checks. An unplaced root is a check
result, not an exception (#473): the checks, the summary and the MCP tools answer with the sentence.

Lifted: the unplaced-root class of test_roots_what_the_owner_is_told (#473).
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.services import inspect  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

WINDOWS = os.name == "nt"


class RootedCase(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_unplaced_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=12, outside=2)
        self.map_file = config.machine_roots_path()

    def fingerprint(self):
        """The library's file content as rows: what 'nothing was written' means."""
        return json.dumps(self.side.dump(), default=repr, sort_keys=True)


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class WithoutTheMap(RootedCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.side.adopt().ok)
        os.remove(self.map_file)

    def test_a_read_of_its_photos_says_what_to_write_and_where(self):
        from tagpup.core import paths
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            with self.assertRaises(paths.UnmappedRoot) as raised:
                store_photos.rows_under(conn, self.side.pictures)
            with self.assertRaises(paths.UnmappedRoot):
                store_roots.sql_equals(conn, "path", os.path.join(self.side.outside_folder, "x.jpg"))
        finally:
            conn.close()
        said = str(raised.exception)
        self.assertIn("pictures", said)
        self.assertIn("machine_roots.json", said)
        self.assertIn(self.home.root, said)
        self.assertIn('"roots"', said)

    def test_a_write_is_refused_too_so_no_row_is_written_that_cannot_be_read(self):
        from tagpup.core import paths
        with self.assertRaises(paths.UnmappedRoot):
            store_photos.move_rows(self.side.db_path, {self.side.real[0]: self.side.real[0] + ".x"})
        self.assertIn("@pictures/2024 Regatta/IMG_1001.jpg", self.side.raw_paths())

    def test_the_library_file_is_untouched_by_the_refusals(self):
        before = self.fingerprint()
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            for ask in (lambda: store_photos.rows_under(conn, self.side.pictures),):
                try:
                    ask()
                except Exception:
                    pass
        finally:
            conn.close()
        self.assertEqual(before, self.fingerprint())

    def test_the_check_and_the_listing_say_so_instead_of_raising(self):
        problems = roots_service.check(self.side.library)
        self.assertTrue(problems and "machine_roots.json" in problems[0], problems)
        found = roots_service.listing(self.side.library, rl.machine())
        self.assertEqual([False], [entry["mapped"] for entry in found["roots"]])

    def test_the_problem_at_open_names_the_file_and_the_line_to_add(self):
        said = roots_service.problem(self.side.library)
        self.assertIn("machine_roots.json", said)
        config.add_machine_root("pictures", self.side.pictures)
        self.assertIsNone(roots_service.problem(self.side.library))

    def test_the_doctor_still_reports_and_says_why_it_leaves_out_what_needs_the_paths(self):
        said = []
        broken = rl.doctor().report(self.side.db_path, show=1, out=said.append)
        text = "\n".join(said)
        self.assertIn("this machine does not say where the library's roots are", text)
        self.assertIn("machine_roots.json", text)
        self.assertIn("rooted rows that do not convert back", text)
        self.assertGreaterEqual(broken, 1)
        self.assertNotIn("rows whose file is not on disk", text, "it cannot be said without the paths")

    def test_the_command_line_warns_at_open(self):
        from click.testing import CliRunner
        from tagpup_cli import cli
        said = CliRunner().invoke(cli, ["--db", self.side.db_path, "history"]).output
        self.assertIn("Warning:", said)
        self.assertIn("machine_roots.json", said)

    def test_the_machine_that_places_it_reads_the_library_again(self):
        config.add_machine_root("pictures", self.side.pictures)
        conn = db.connect(db.readonly_uri(self.side.db_path), uri=True)
        try:
            self.assertEqual(42 + 2, len(store_photos.tags_by_photo(conn)))
        finally:
            conn.close()


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheCommandLine(RootedCase):
    def run_cli(self, *args):
        from click.testing import CliRunner
        from tagpup_cli import cli
        return CliRunner().invoke(cli, ["--db", self.side.db_path, "roots", *args])

    def test_a_library_with_no_roots_says_so(self):
        self.assertIn("has no roots", self.run_cli().output)

    def test_the_listing_shows_where_the_machine_keeps_it_and_the_check_is_in_order(self):
        self.assertTrue(self.side.adopt().ok)
        listed = self.run_cli().output
        self.assertIn("pictures", listed)
        self.assertIn(self.side.pictures, listed)
        self.assertIn("The library's roots are in order.", self.run_cli("check").output)

    def test_a_library_that_is_not_placed_here_is_marked_in_the_listing(self):
        self.assertTrue(self.side.adopt().ok)
        os.remove(self.map_file)
        self.assertIn("NOT PLACED on this machine", self.run_cli().output)
        checked = self.run_cli("check")
        self.assertEqual(1, checked.exit_code)
        self.assertIn("machine_roots.json", checked.output)


class TheHistoryOfTheRemovedCommands(RootedCase):
    """The three live libraries hold a `roots adopt` change in their journal: History still lists it,
    and its undo is refused with a sentence (the command and its undo are removed), never raised."""

    def test_history_lists_the_adoption_and_its_undo_is_refused_in_words(self):
        from tagpup.services import journal as journal_service
        from tagpup.store import journal
        self.assertTrue(self.side.adopt().ok)
        entry = [e for e in journal.history(self.side.db_path, limit=50) if e["operation"].startswith("roots adopt")][0]
        refused = journal.refusals(self.side.db_path, [entry["id"]])[entry["id"]]
        self.assertEqual(1, len(refused), refused)
        self.assertIn("removed", refused[0])
        before = json.dumps(self.side.dump(), default=repr, sort_keys=True)
        undone = journal_service.undo(self.side.library, entry["id"], apply=True)
        self.assertTrue(undone.refused, "the undo of a removed command is refused, not raised")
        self.assertEqual(before, json.dumps(self.side.dump(), default=repr, sort_keys=True))


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class AnUnplacedRootIsACheckResult(RootedCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.side.adopt().ok)
        self.library = self.side.library

    def lose_the_map(self):
        os.remove(self.map_file)

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


if __name__ == "__main__":
    unittest.main()
