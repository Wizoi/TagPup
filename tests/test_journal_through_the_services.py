"""The maintenance services journal what they apply, and the CLI's `history` and `undo`
take it back (docs/ARCHITECTURE.md, phase 7.5).

End to end: each service run as the MCP server runs it, a dry run and then apply, and the
change it records undone through `tagpup_cli.py undo`. The refresh is run the same way by
tests/test_refresh_rows.py; the MCP tools by tests/test_mcp_write_tools.py.
"""
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from journal_library import JournalLibrary  # noqa: E402

from click.testing import CliRunner  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import duplicate_faces, maintenance, person_tags  # noqa: E402
from tagpup_cli import cli  # noqa: E402


class ThroughTheServices(JournalLibrary):
    def run_service(self, service, apply=False):
        """The Result of `service` on the library, a dry run unless `apply`."""
        return service(Library(self.db_path), apply=apply)

    def cli(self, *arguments):
        result = CliRunner().invoke(cli, ["--db", self.db_path] + list(arguments))
        self.assertIsNone(result.exception, result.output)
        return result.output

    def round_trip(self, name, service):
        """A dry run, which rehearses and writes nothing; --apply, which records a change;
        `history`; and `undo`, rehearsed and then applied, which puts every row back."""
        before = self.dump()
        dry = maintenance.rehearsed(self.run_service(service))
        self.assertIn("the undo restored every one exactly", dry)
        self.assertEqual(before, self.dump())
        self.assertEqual([], self.changes())

        applied = self.run_service(service, apply=True)
        self.assertNotEqual(before, self.dump())
        change = applied.details["change"]

        listed = self.cli("history")
        self.assertIn("%d  %s  applied" % (change, name), listed)
        for secret in ("Rowan Thackeray", "Imogen Vale", "Maren Oakhollow", self.home.root):
            self.assertNotIn(secret, listed, "history names values without --reveal")
        self.assertIn("every one came back exactly", self.cli("undo", str(change)))
        self.assertIn("Undid change %d" % change, self.cli("undo", str(change), "--apply"))
        self.assertEqual(before, self.dump())
        self.assertIn("%d  %s  undone" % (change, name), self.cli("history", "--change", str(change)))

    def test_dedupe_faces(self):
        # A second copy of a face the library already has, knowing no more.
        self.execute("INSERT INTO faces (photo_id, box, embedding, prob, excluded)"
                     " SELECT photo_id, box, embedding, prob, 0 FROM faces WHERE id = ?", (self.ids["copy"],))
        self.round_trip("dedupe_faces", duplicate_faces.dedupe_faces)

    def test_merge_duplicate_person_tags(self):
        self.round_trip("merge_duplicate_person_tags", person_tags.merge_duplicate_person_tags)

    def test_an_undo_that_is_refused_says_why_and_writes_nothing(self):
        change = self.run_service(person_tags.merge_duplicate_person_tags, apply=True).details["change"]
        self.execute("INSERT INTO tag_taxonomy (id, tag, name) SELECT ?, 'Rowan Thackeray', 'Rowan Thackeray'",
                     (self.ids["Rowan Thackeray"],))
        after = self.dump()
        result = CliRunner().invoke(cli, ["--db", self.db_path, "undo", str(change), "--apply"])
        self.assertEqual(1, result.exit_code)
        self.assertIn("tag_taxonomy %d is there again" % self.ids["Rowan Thackeray"], result.output)
        self.assertEqual(after, self.dump())

    def test_an_undo_whose_rehearsal_is_not_exact_is_not_applied(self):
        from unittest import mock
        from tagpup.store import journal
        change = self.run_service(person_tags.merge_duplicate_person_tags, apply=True).details["change"]
        after = self.dump()
        real = journal._again

        def leaves_it_there(row):
            # The change deleted the node; applied again, this leaves it where the undo put it.
            again = real(row)
            if again.action == "delete":
                again.action, again.new = "update", {"has_face": 0}
            return again

        with mock.patch.object(journal, "_again", side_effect=leaves_it_there):
            result = CliRunner().invoke(cli, ["--db", self.db_path, "undo", str(change), "--apply"])
        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("did not restore every row exactly", result.output)
        self.assertEqual(after, self.dump())

if __name__ == "__main__":
    unittest.main()
