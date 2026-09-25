"""The maintenance scripts journal what they apply, and the CLI's `history` and `undo`
take it back (docs/ARCHITECTURE.md, phase 7.5).

End to end: each script run as a person runs it, a dry run and then --apply, and the
change it prints undone through `tagpup_cli.py undo`. The refresh's script is run the
same way by tests/test_refresh_rows_from_files.py; the MCP tools by
tests/test_mcp_write_tools.py.
"""
import contextlib
import io
import os
import re
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from journal_library import JournalLibrary  # noqa: E402

from click.testing import CliRunner  # noqa: E402

import dedupe_faces as dedupe_script  # noqa: E402
import merge_duplicate_person_tags as merge_script  # noqa: E402
from tagpup_cli import cli  # noqa: E402


class ThroughTheScripts(JournalLibrary):
    def script(self, module, *arguments):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            module.main(["--db", self.db_path] + list(arguments))
        return out.getvalue()

    def cli(self, *arguments):
        result = CliRunner().invoke(cli, ["--db", self.db_path] + list(arguments))
        self.assertIsNone(result.exception, result.output)
        return result.output

    def round_trip(self, module):
        """A dry run, which rehearses and writes nothing; --apply, which records a change;
        `history`; and `undo`, rehearsed and then applied, which puts every row back."""
        before = self.dump()
        dry = self.script(module)
        self.assertIn("the undo restored every one exactly", dry)
        self.assertEqual(before, self.dump())
        self.assertEqual([], self.changes())

        applied = self.script(module, "--apply")
        self.assertNotEqual(before, self.dump())
        self.assertNotIn("backed up", applied)
        change = int(re.search(r"Recorded as change (\d+)", applied).group(1))

        listed = self.cli("history")
        self.assertIn("%d  %s  applied" % (change, module.__name__), listed)
        for secret in ("Rowan Thackeray", "Imogen Vale", "Maren Oakhollow", self.home.root):
            self.assertNotIn(secret, listed, "history names values without --reveal")
        self.assertIn("every one came back exactly", self.cli("undo", str(change)))
        self.assertIn("Undid change %d" % change, self.cli("undo", str(change), "--apply"))
        self.assertEqual(before, self.dump())
        self.assertIn("%d  %s  undone" % (change, module.__name__), self.cli("history", "--change", str(change)))

    def test_dedupe_faces(self):
        # A second copy of a face the library already has, knowing no more.
        self.execute("INSERT INTO faces (photo_id, box, embedding, prob, excluded)"
                     " SELECT photo_id, box, embedding, prob, 0 FROM faces WHERE id = ?", (self.ids["copy"],))
        self.round_trip(dedupe_script)

    def test_merge_duplicate_person_tags(self):
        self.round_trip(merge_script)

    def test_an_undo_that_is_refused_says_why_and_writes_nothing(self):
        applied = self.script(merge_script, "--apply")
        change = int(re.search(r"Recorded as change (\d+)", applied).group(1))
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
        applied = self.script(merge_script, "--apply")
        change = int(re.search(r"Recorded as change (\d+)", applied).group(1))
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

    def test_prune_journal_is_a_dry_run_unless_applied(self):
        self.script(merge_script, "--apply")
        self.assertIn("0 change(s) older than 90 days would be pruned", self.cli("prune-journal"))
        self.assertIn("1 change(s) older than 0 days would be pruned", self.cli("prune-journal", "--days", "0"))
        self.assertEqual([(1, "merge_duplicate_person_tags", "applied")], self.changes())


if __name__ == "__main__":
    unittest.main()
