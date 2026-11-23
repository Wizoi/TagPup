"""tools/add_findings.py numbers `| ? |` rows after the last finding, sets statuses, archives the closed
rows, keeps a worker's rows in a file of its own until the main session numbers them, and refuses a third fix for one
place."""
import contextlib
import io
import os
import shutil
import stat
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import add_findings  # noqa: E402
from tagpup.core import processes  # noqa: E402

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


class SimilarRows(unittest.TestCase):
    OLD = ("| 1847 | 2026-10-24 | low: `retire-photo-vectors` before `set-photo-model` would plan the fresh vectors of "
           "`embed-photos` for deletion. | decided |")

    def test_a_second_row_about_one_case_is_noticed_unless_it_cites_the_first(self):
        again = "| ? | 2026-10-24 | low: `retire-photo-vectors` run before `set-photo-model` deletes `embed-photos` vectors. | open |"
        existing = add_findings.existing_rows(self.OLD)
        self.assertEqual([("?", 1847)], add_findings.similar_rows([again], existing))
        self.assertEqual([], add_findings.similar_rows([again.replace("run before", "run (see #1847) before")], existing))

    def test_a_row_about_another_case_is_not_noticed(self):
        other = "| ? | 2026-10-24 | low: `dedupe_faces` keeps the older `faces_in_photo` row. | open |"
        self.assertEqual([], add_findings.similar_rows([other], add_findings.existing_rows(self.OLD)))


def _remove(path):
    def writable(func, name, _exc):
        os.chmod(name, stat.S_IWRITE)
        func(name)
    shutil.rmtree(path, onerror=writable)


class AThirdFixForOnePlace(unittest.TestCase):
    """The clip that would not save (retro 2026-11-13, #2924): a save refused, fixed in the clip patcher; still refused,
    fixed there again; a third row about the same place is refused until it says which single place owns the rule. The
    fixes are real commits of a repository made here, followed by their `fixed: <commit>`."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(_remove, self.folder)
        self.repo = os.path.join(self.folder, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q")
        first = self.commit("tagpup/files/clip_patch.py", "BOUND = None\n", "tagpup/files/field_values.py", "A = 1\n")
        second = self.commit("tagpup/files/clip_patch.py", "BOUND = None\nNESTED = True\n")
        self.findings = os.path.join(self.folder, "findings.md")
        self.rows = os.path.join(self.folder, "rows.md")
        self.write(self.findings, (
            "| # | Found | Finding | Status |\n|---|---|---|---|\n"
            "| 40 | 2026-11-12 | wrong: Files: a title save on a clip with a large packet is refused (`clip_patch.LIMIT`). "
            "| fixed: %s (wrote 0 rows) |\n"
            "| 41 | 2026-11-12 | low: the fixture of #40 is padding-shaped. | fixed: %s |\n"
            "| 45 | 2026-11-12 | wrong: Files: a save on a clip whose XMP box is nested is refused by `clip_patch._xmp_patch`. "
            "| fixed: %s; wrote 0 rows |\n" % (first, first, second)))
        self.base = ["--findings", self.findings, "--archive-dir", os.path.join(self.folder, "history"),
                     "--watching-file", os.path.join(self.folder, "watching.md"), "--repo", self.repo]

    def git(self, *args):
        done = processes.run(["git", "-C", self.repo, "-c", "user.name=Tess Tarrow", "-c", "user.email=tess@example.invalid",
                              "-c", "commit.gpgsign=false", *args], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    def commit(self, *changes):
        for path, text in zip(changes[::2], changes[1::2]):
            self.write(os.path.join(self.repo, *path.split("/")), text)
            self.git("add", path)
        self.git("commit", "-q", "-m", "a fix")
        return self.git("rev-parse", "--short=8", "HEAD")

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def read(self, path):
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()

    def add(self, row, *more):
        """(the exit code, what was said on stderr) of adding `row`."""
        self.write(self.rows, row + "\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                code, _out = run(self.base + [self.rows, *more])
            except SystemExit as stop:
                code = stop.code
        return code, err.getvalue()

    def test_a_third_fix_of_the_clip_patcher_is_refused_and_nothing_is_written(self):
        before = self.read(self.findings)
        third = ("| ? | 2026-11-13 | wrong: Files: a save into the clip is still refused after #45's fix: "
                 "`clip_patch.packet_of` hands the editor the data atom's bytes. | open |")
        code, said = self.add(third)
        self.assertNotEqual(0, code)
        self.assertIn("tagpup/files/clip_patch.py, which the fixes of #40, #45 changed: a third fix for one place", said)
        self.assertEqual(before, self.read(self.findings))
        branch = os.path.join(self.folder, "pending.md")
        code, said = self.add(third, "--branch-file", branch)
        self.assertIn("a third fix for one place", said)
        self.assertFalse(os.path.exists(branch))

        said_place = "second fix of #45: the single place is `clip_patch.packet_of`, the one reader of an item's packet."
        code, said = self.add(third.replace("| open |", said_place + " | open |"))
        self.assertNotEqual(0, code, "the single place without a guard is still a third fix")
        self.assertIn("guarded by", said)
        self.assertEqual(before, self.read(self.findings))

        code, said = self.add(third.replace("| open |", said_place + " Guarded by `tests/test_clip_patch.py` "
                                                                    "(fails on a second reader). | open |"))
        self.assertEqual((0, ""), (code, said))
        self.assertIn("| 46 | 2026-11-13 | wrong: Files: a save into the clip is still refused", self.read(self.findings))

    def test_fixes_closed_on_a_branch_before_the_merge_count_toward_the_chain(self):
        """Two rows of one branch, each closed `fixed` before any is numbered, then a third about the same module."""
        branch = os.path.join(self.folder, "pending.md")
        one = self.commit("tagpup/files/field_values.py", "A = 2\n")
        two = self.commit("tagpup/files/field_values.py", "A = 3\n")
        for text in ("wrong: `field_values.parse` reads a date as a title.", "wrong: `field_values.parse` drops a year."):
            code, said = self.add("| ? | 2026-11-23 | %s | open |" % text, "--branch-file", branch)
            self.assertEqual((0, ""), (code, said))
        for name, commit in (("B-1", one), ("B-2", two)):
            code, said = run(["--branch-file", branch, "--status", name, "fixed: " + commit, *self.base[:2]])
            self.assertEqual(0, code)
        before = self.read(branch)
        third = "| ? | 2026-11-23 | wrong: `field_values.parse` reads an empty date. | open |"
        code, said = self.add(third, "--branch-file", branch)
        self.assertNotEqual(0, code)
        self.assertIn("tagpup/files/field_values.py, which the fixes of #40, B-1, B-2 changed: a third fix for one place", said)
        self.assertEqual(before, self.read(branch))
        code, said = self.add(third.replace("| open |", "second fix of B-2: the single place is `field_values.parse`; "
                                                       "guarded by `tests/test_field_values.py`. | open |"),
                              "--branch-file", branch)
        self.assertEqual((0, ""), (code, said))
        self.assertIn("| B-3 |", self.read(branch))

    def test_a_low_row_or_a_place_fixed_once_is_added(self):
        code, said = self.add("| ? | 2026-11-13 | low: `clip_patch.packet_of` has no docstring (live: 1). | open |")
        self.assertEqual((0, ""), (code, said))
        code, said = self.add("| ? | 2026-11-13 | wrong: `field_values.parse` reads a date as a title. | open |")
        self.assertEqual((0, ""), (code, said))
        self.assertIn("| 47 | 2026-11-13 | wrong: `field_values.parse`", self.read(self.findings))

    def test_a_place_whose_single_place_was_decided_is_not_refused(self):
        with open(self.findings, "a", encoding="utf-8", newline="") as handle:
            handle.write("| 46 | 2026-11-13 | low: three rows in `clip_patch.py`. | decided: the single place is "
                         "`clip_patch.packet_of` |\n")
        code, said = self.add("| ? | 2026-11-13 | wrong: `clip_patch.packet_of` misreads an empty item. | open |")
        self.assertEqual((0, ""), (code, said))


class OpenRows(unittest.TestCase):
    def test_a_row_waiting_on_something_is_open_whatever_its_status_word(self):
        text = MIXED + "| 253 | 2026-09-25 | wrong: A fault. | planned: DECISIONS 2026-09-25 |\n"
        found = add_findings.open_rows(text)
        self.assertEqual([2, 252, 253], [number for number, _, _ in found])
        self.assertEqual("wrong", found[2][1])

    def test_open_prints_the_rows_and_the_count_to_quote(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "findings.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(MIXED)
            code, out = run(["--open", "--findings", path])
        self.assertEqual(0, code)
        self.assertTrue(out.endswith("2 open\n"), out)


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
        self.base = ["--findings", self.findings, "--archive-dir", self.archives,
                     "--watching-file", os.path.join(self.folder, "watching.md")]

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

    def test_a_sweep_row_is_not_closed_fixed_without_what_the_fix_wrote(self):
        sweep = "| 5 | 2026-11-07 | The caption sweep reported Cleared: 0 and wrote nothing. | open |\n"
        with open(self.findings, "a", encoding="utf-8", newline="") as handle:
            handle.write(sweep)
        before = self.read(self.findings)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run(self.base + ["--status", "5", "fixed: abc"])
        self.assertEqual(before, self.read(self.findings))
        run(self.base + ["--status", "5", "fixed: abc; wrote 60 of 60 on a copy"])
        self.assertIn("fixed: abc; wrote 60 of 60 on a copy |", self.read(self.findings))
        run(self.base + ["--status", "2", "decided: by design"])

    def test_a_data_or_wrong_row_is_not_closed_fixed_without_a_commit(self):
        rows = ("| 6 | 2026-11-13 | data: a write loses a tag. | open |\n"
                "| 7 | 2026-11-13 | wrong: a count is off. | open |\n"
                "| 8 | 2026-11-13 | low: a label. | open |\n")
        with open(self.findings, "a", encoding="utf-8", newline="") as handle:
            handle.write(rows)
        before = self.read(self.findings)
        for key in ("6", "7"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                run(self.base + ["--status", key, "fixed: the loop is closed"])
        self.assertEqual(before, self.read(self.findings))
        run(self.base + ["--status", "6", "fixed: 0123abcd"])
        run(self.base + ["--status", "7", "decided: by design"])
        run(self.base + ["--status", "8", "fixed: no commit needed"])
        text = self.read(self.findings)
        self.assertIn("fixed: 0123abcd |", text)
        self.assertIn("fixed: no commit needed |", text)

    def test_a_row_that_is_nowhere_is_an_error_and_nothing_is_written(self):
        before = self.read(self.findings)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run(self.base + ["--status", "999", "fixed"])
        self.assertEqual(before, self.read(self.findings))

    def test_a_row_with_nothing_to_do_is_watched_not_counted_open_and_keeps_its_number(self):
        watching = os.path.join(self.folder, "watching.md")
        base = self.base + ["--watching-file", watching]
        code, out = run(base + ["--watch", "2", "no repro; read when a report sounds like it"])
        self.assertEqual(0, code)
        self.assertNotIn("| 2 |", self.read(self.findings))
        self.assertIn("| 2 | 2026-09-21 | Still wanted. | watching: no repro; read when a report sounds like it |",
                      self.read(watching))
        _, out = run(base + ["--open"])
        self.assertNotIn("#2 ", out)
        self.assertIn("#252", out)
        rows = os.path.join(self.folder, "rows.md")
        with open(rows, "w", encoding="utf-8") as handle:
            handle.write("| ? | 2026-11-02 | New. | open |\n")
        run(base + [rows])
        self.assertIn("| 253 |", self.read(self.findings))
        with open(watching, encoding="utf-8", newline="") as handle:
            kept = handle.read()
        with open(rows, "w", encoding="utf-8") as handle:
            handle.write("| ? | 2026-11-02 | Next. | open |\n")
        run(base + ["--watch", "252", "waiting"] + [rows])
        self.assertIn("| 252 |", self.read(watching))
        self.assertNotEqual(kept, self.read(watching))

    def test_watching_a_row_that_is_not_open_is_an_error_and_nothing_is_written(self):
        watching = os.path.join(self.folder, "watching.md")
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                run(self.base + ["--watching-file", watching, "--watch", "9", "x"])
        self.assertFalse(os.path.exists(watching))
        self.assertEqual(MIXED, self.read(self.findings))

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


class ALowNeedsALiveCount(unittest.TestCase):
    """Seven lows a sweep or a review spawned were built, then reverted for 0 live instances (2026-11-14, retro
    2026-11-14): a new `low` row says how many live rows it touches, or the user action that makes it."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.findings = os.path.join(self.folder, "findings.md")
        with open(self.findings, "w", encoding="utf-8", newline="") as handle:
            handle.write(TABLE)
        self.base = ["--findings", self.findings, "--archive-dir", os.path.join(self.folder, "history"),
                     "--watching-file", os.path.join(self.folder, "watching.md")]

    def submit(self, row):
        path = os.path.join(self.folder, "rows.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(row + "\n")
        return run(self.base + [path])

    def written(self):
        with open(self.findings, encoding="utf-8", newline="") as handle:
            return handle.read()

    def test_a_low_with_no_count_is_refused_and_nothing_is_written(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.submit("| ? | 2026-11-14 | low: Files: a stalled share is looked at twice. | open |")
        self.assertEqual(TABLE, self.written())

    def test_a_low_with_a_live_count_above_zero_is_numbered_open(self):
        self.submit("| ? | 2026-11-14 | low: Files: a note names a path (live: 12). | open |")
        self.assertIn("| 3 | 2026-11-14 | low: Files: a note names a path (live: 12). | open |", self.written())

    def test_a_low_with_a_user_action_is_numbered_open(self):
        self.submit("| ? | 2026-11-14 | low: Files: a rename fails (user action: Smart Rename on a held file). | open |")
        self.assertIn("(user action: Smart Rename on a held file). | open |", self.written())

    def test_a_low_with_live_zero_is_closed_decided_by_the_same_call(self):
        self.submit("| ? | 2026-11-14 | low: Media: ffmpeg gone in the CLI index (live: 0). | open |")
        self.assertIn("(live: 0). | decided: no live instance, not built |", self.written())

    def test_a_wrong_row_needs_no_count(self):
        self.submit("| ? | 2026-11-14 | wrong: Views: a count is off by one. | open |")
        self.assertIn("| 3 | 2026-11-14 | wrong:", self.written())


if __name__ == "__main__":
    unittest.main()
