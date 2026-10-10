"""tools/add_findings.py numbers `| ? |` rows after the last finding, sets statuses, archives the closed
rows, and keeps a worker's rows in a file of its own until the main session numbers them."""
import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import add_findings  # noqa: E402

TABLE = """| # | Found | Finding | Status |
| :-- | :-- | :-- | :-- |
| 1 | 2026-09-20 | A thing. | open |
| 2 | 2026-09-21 | Another. | fixed: abc |

After the table.
"""

MIXED = """# Findings

| # | Found | Finding | Status |
|---|---|---|---|
| 1 | 2026-09-20 | Old and done. | fixed: abc |
| 2 | 2026-09-21 | Still wanted. | open |
| 3 | 2026-09-22 | Chosen. | decided *(owner, 2026-09-22)* |
| 251 | 2026-09-23 | In the second range. | accepted |
| 252 | 2026-09-24 | Waiting. | tabled until phase 10 |
"""


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = add_findings.main(argv)
    return code, out.getvalue()


class AddFindings(unittest.TestCase):
    def test_rows_are_numbered_after_the_last_in_order(self):
        rows = add_findings.rows_in("intro\n| ? | 2026-09-28 | First. | open |\n| ? | 2026-09-28 | Second. | decided |\n")
        text, given = add_findings.add(TABLE, rows)
        self.assertEqual([3, 4], given)
        self.assertIn("| 2 | 2026-09-21 | Another. | fixed: abc |\n| 3 | 2026-09-28 | First. | open |\n"
                      "| 4 | 2026-09-28 | Second. | decided |\n\nAfter the table.", text)

    def test_a_status_replaces_the_last_column(self):
        text, given = add_findings.add(TABLE, [], [(1, "fixed: def")])
        self.assertIn("| 1 | 2026-09-20 | A thing. | fixed: def |", text)
        self.assertEqual([], given)

    def test_a_missing_row_changes_nothing(self):
        with self.assertRaises(ValueError):
            add_findings.add(TABLE, [], [(9, "fixed")])


class Archive(unittest.TestCase):
    def test_closed_rows_go_to_the_archive_of_their_range_and_open_ones_stay(self):
        text, changed, moved = add_findings.archive(MIXED, {})
        self.assertEqual(3, moved)
        self.assertEqual(["findings_closed_0001-0250.md", "findings_closed_0251-0500.md"], sorted(changed))
        self.assertIn("| 2 | 2026-09-21 | Still wanted. | open |", text)
        self.assertIn("| 252 | 2026-09-24 | Waiting. | tabled until phase 10 |", text)
        for gone in ("| 1 |", "| 3 |", "| 251 |"):
            self.assertNotIn(gone, text)
        first = changed["findings_closed_0001-0250.md"]
        self.assertIn("| 1 | 2026-09-20 | Old and done. | fixed: abc |\n| 3 | 2026-09-22 | Chosen. | decided *(owner, 2026-09-22)* |", first)

    def test_archiving_twice_changes_nothing_more(self):
        text, changed, _ = add_findings.archive(MIXED, {})
        again, changed_again, moved = add_findings.archive(text, changed)
        self.assertEqual(text, again)
        self.assertEqual(0, moved)
        self.assertEqual({}, changed_again)

    def test_a_row_archived_later_joins_the_rows_already_there_in_order(self):
        _, changed, _ = add_findings.archive(MIXED, {})
        more = "| # | Found | Finding | Status |\n|---|---|---|---|\n| 2 | 2026-09-21 | Still wanted. | fixed: def |\n"
        _, changed2, _ = add_findings.archive(more, changed)
        rows = [line.split(" | ")[0] for line in changed2["findings_closed_0001-0250.md"].splitlines() if line.startswith("| 1")
                or line.startswith("| 2") or line.startswith("| 3")]
        self.assertEqual(["| 1", "| 2", "| 3"], rows)

    def test_numbers_continue_after_the_highest_archived_number(self):
        remaining, changed, _ = add_findings.archive(MIXED.replace("| 252 | 2026-09-24 | Waiting. | tabled until phase 10 |\n", ""), {})
        rows = ["| ? | 2026-10-09 | New. | open |"]
        text, given = add_findings.add(remaining, rows, floor=add_findings.highest(*changed.values()))
        self.assertEqual([252], given)
        self.assertIn("| 252 | 2026-10-09 | New. | open |", text)

    def test_numbers_are_given_when_every_row_is_archived(self):
        text, changed, _ = add_findings.archive(MIXED.replace("| 2 | 2026-09-21 | Still wanted. | open |\n", "").replace(
            "| 252 | 2026-09-24 | Waiting. | tabled until phase 10 |\n", ""), {})
        text, given = add_findings.add(text, ["| ? | 2026-10-09 | New. | open |"], floor=251)
        self.assertEqual([252], given)
        self.assertIn("|---|---|---|---|\n| 252 | 2026-10-09 | New. | open |", text)


class Files(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder, ignore_errors=True))
        self.findings = os.path.join(self.folder, "findings.md")
        self.archives = os.path.join(self.folder, "history")
        with open(self.findings, "w", encoding="utf-8", newline="") as handle:
            handle.write(MIXED)
        self.base = ["--findings", self.findings, "--archive-dir", self.archives]

    def read(self, *path):
        with open(os.path.join(*path), encoding="utf-8", newline="") as handle:
            return handle.read()

    def test_archive_moves_the_closed_rows_and_writes_the_archives(self):
        code, out = run(self.base + ["--archive"])
        self.assertEqual(0, code)
        self.assertIn("archived 3", out)
        self.assertNotIn("Old and done", self.read(self.findings))
        self.assertIn("Old and done", self.read(self.archives, "findings_closed_0001-0250.md"))

    def test_a_new_row_is_numbered_after_an_archived_one(self):
        run(self.base + ["--archive"])
        kept = self.read(self.findings).replace("| 252 | 2026-09-24 | Waiting. | tabled until phase 10 |\n", "")
        with open(self.findings, "w", encoding="utf-8", newline="") as handle:
            handle.write(kept)
        rows = os.path.join(self.folder, "rows.md")
        with open(rows, "w", encoding="utf-8") as handle:
            handle.write("| ? | 2026-10-09 | New. | open |\n")
        _, out = run(self.base + [rows])
        self.assertIn("numbered 252-252", out)

    def test_a_status_reaches_a_row_in_the_archive(self):
        run(self.base + ["--archive"])
        run(self.base + ["--status", "1", "fixed: 123abc"])
        self.assertIn("| 1 | 2026-09-20 | Old and done. | fixed: 123abc |", self.read(self.archives, "findings_closed_0001-0250.md"))

    def test_a_row_that_is_nowhere_is_an_error_and_nothing_is_written(self):
        before = self.read(self.findings)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run(self.base + ["--status", "999", "fixed"])
        self.assertEqual(before, self.read(self.findings))

    def test_a_workers_rows_wait_in_a_file_of_their_own_with_provisional_numbers(self):
        rows = os.path.join(self.folder, "rows.md")
        with open(rows, "w", encoding="utf-8") as handle:
            handle.write("| ? | 2026-10-09 | First. | open |\n| ? | 2026-10-09 | Second, after B-1. | open |\n")
        branch = os.path.join(self.folder, "pending", "branch.md")
        before = self.read(self.findings)
        _, out = run(self.base + [rows, "--branch-file", branch])
        self.assertIn("provisional B-1-B-2", out)
        self.assertEqual(before, self.read(self.findings))
        run(self.base + ["--branch-file", branch, "--status", "B-1", "fixed: abc"])
        text = self.read(branch)
        self.assertIn("| B-1 | 2026-10-09 | First. | fixed: abc |", text)
        run(self.base + [rows, "--branch-file", branch])
        self.assertIn("| B-3 | 2026-10-09 | First. | open |", self.read(branch))

    def test_taking_a_branch_numbers_its_rows_after_the_highest_and_removes_the_file(self):
        rows = os.path.join(self.folder, "rows.md")
        with open(rows, "w", encoding="utf-8") as handle:
            handle.write("| ? | 2026-10-09 | First. | open |\n| ? | 2026-10-09 | Second, after B-1. | open |\n")
        branch = os.path.join(self.folder, "pending", "branch.md")
        run(self.base + [rows, "--branch-file", branch])
        # another branch took 253 meanwhile: the numbers follow whatever is there
        with open(self.findings, "a", encoding="utf-8", newline="") as handle:
            handle.write("| 253 | 2026-10-09 | Another branch. | open |\n")
        _, out = run(self.base + ["--take", branch])
        self.assertIn("B-1 -> 254", out)
        text = self.read(self.findings)
        self.assertIn("| 254 | 2026-10-09 | First. | open |\n| 255 | 2026-10-09 | Second, after #254. | open |", text)
        self.assertFalse(os.path.exists(branch))

    def test_taking_a_branch_counts_past_the_archives(self):
        run(self.base + ["--archive"])
        with open(self.findings, "w", encoding="utf-8", newline="") as handle:
            handle.write("| # | Found | Finding | Status |\n|---|---|---|---|\n")
        rows = os.path.join(self.folder, "rows.md")
        with open(rows, "w", encoding="utf-8") as handle:
            handle.write("| ? | 2026-10-09 | New. | open |\n")
        branch = os.path.join(self.folder, "branch.md")
        run(self.base + [rows, "--branch-file", branch])
        run(self.base + ["--take", branch])
        self.assertIn("| 252 | 2026-10-09 | New. | open |", self.read(self.findings))


if __name__ == "__main__":
    unittest.main()
