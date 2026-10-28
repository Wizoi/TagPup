"""The changes the deleted maintenance services left in the live journals still list in History and undo through the CLI
(docs/ARCHITECTURE.md, phase 7.5; tagpup.services.journal.RETIRED).

`merge_duplicate_person_tags` (photo_index change 1) and `refresh_rows` (kr-track change 2) were each applied once,
counted read-only 2026-10-28, and their writers are deleted with the MCP server's write tools (DECISIONS 2026-10-28, THE
MCP SERVER ONLY READS). Each change is recorded here the way its writer recorded it -- the same operation, the same edits
-- and then listed with `tagpup_cli.py history`, which names no value, and undone with `undo`, rehearsed and then applied,
which puts every row back.
"""
import json
import os
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from journal_library import JournalLibrary  # noqa: E402

from click.testing import CliRunner  # noqa: E402

from tagpup.store import journal  # noqa: E402
from tagpup_cli import cli  # noqa: E402


class ThroughTheCli(JournalLibrary):
    def cli(self, *arguments):
        result = CliRunner().invoke(cli, ["--db", self.db_path] + list(arguments))
        self.assertIsNone(result.exception, result.output)
        return result.output

    def a_merge(self):
        """The bare person tag removed from the tree as merge_duplicate_person_tags removed it: its node, by id, while
        it still spells the tag; the photos are not changed."""
        return journal.apply(self.db_path, "merge_duplicate_person_tags", [
            journal.delete("tag_taxonomy", (self.ids["Rowan Thackeray"],), {"tag": "Rowan Thackeray"})]).change_id

    def a_refresh(self):
        """A row recorded from its file as refresh_rows recorded it: its tags, captions, raw metadata and stamp, held to
        what the row had when read, from files, skippable."""
        ((mtime, size, tags, captions, raw),) = self.query(
            "SELECT mtime, size, tags, captions, raw_metadata FROM photos WHERE id = ?", (self.ids["prize"],))
        read = dict(json.loads(raw), **{"XMP:Description": "Prize giving on the quay"})
        values = {"tags": json.dumps(["Activity/Sailing", "Harbour/Quay"]), "captions": json.dumps(
            ["Prize giving on the quay", "Start line"]), "raw_metadata": json.dumps(read), "mtime": mtime + 60,
            "size": size + 512}
        expect = {"mtime": mtime, "size": size, "tags": tags, "captions": captions, "raw_metadata": raw}
        return journal.apply(self.db_path, "refresh_rows", [journal.update(
            "photos", (self.ids["prize"],), expect, values, kind="from_files", skippable=True)]).change_id

    def round_trip(self, name, record):
        """The change listed by `history`, no value named; `undo` rehearsed, then applied, every row back."""
        before = self.dump()
        change = record()
        self.assertNotEqual(before, self.dump())
        listed = self.cli("history")
        self.assertIn("%d  %s  applied" % (change, name), listed)
        for secret in ("Rowan Thackeray", "Imogen Vale", "Maren Oakhollow", "Prize giving", self.home.root):
            self.assertNotIn(secret, listed, "history names values without --reveal")
        self.assertIn("every one came back exactly", self.cli("undo", str(change)))
        self.assertIn("Undid change %d" % change, self.cli("undo", str(change), "--apply"))
        self.assertEqual(before, self.dump())
        self.assertIn("%d  %s  undone" % (change, name), self.cli("history", "--change", str(change)))

    def test_a_merge_of_a_bare_person_tag_lists_and_undoes(self):
        self.round_trip("merge_duplicate_person_tags", self.a_merge)

    def test_a_refresh_of_a_row_from_its_file_lists_and_undoes(self):
        self.round_trip("refresh_rows", self.a_refresh)

    def test_an_undo_whose_rehearsal_is_not_exact_is_not_applied(self):
        change = self.a_merge()
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
