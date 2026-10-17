"""tools/stale_findings.py lists the open findings that name a file or a code name the code no longer has,
so a row about something deleted is re-checked before a worker is sent after it."""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import stale_findings  # noqa: E402

FINDINGS = """| # | Found | Finding | Status |
|---|---|---|---|
| 1 | 2026-10-01 | low: `store.photos.still_here` in `tagpup/store/photos.py:12` reads twice. | open |
| 2 | 2026-10-02 | low: `services/sync.py` ~:40 calls `gone_helper()` and `photos` in `tagpup/store/vanished.py`. | open |
| 3 | 2026-10-03 | low: `long_gone_name` was deleted. | fixed: abc |
| 4 | 2026-10-04 | low: the `held` folder and `a sentence with spaces` stay. | open |
"""


class AnOpenRowNamingWhatIsGone(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "tagpup", "store"))
        os.makedirs(os.path.join(self.root, "tagpup", "services"))
        with open(os.path.join(self.root, "tagpup", "store", "photos.py"), "w", encoding="utf-8") as handle:
            handle.write("def still_here():\n    pass\n")
        with open(os.path.join(self.root, "tagpup", "services", "sync.py"), "w", encoding="utf-8") as handle:
            handle.write("# nothing\n")
        self.findings = os.path.join(self.root, "findings.md")
        with open(self.findings, "w", encoding="utf-8") as handle:
            handle.write(FINDINGS)

    def run_tool(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = stale_findings.main(["--findings", self.findings, "--root", self.root])
        return code, out.getvalue()

    def test_is_listed_with_what_is_missing_and_a_row_naming_what_is_there_is_not(self):
        code, out = self.run_tool()
        self.assertEqual(1, code, out)
        self.assertIn("#2 names what the code no longer has: gone_helper(), tagpup/store/vanished.py", out)
        self.assertNotIn("#1 ", out)
        self.assertIn("1 open row(s) to re-check", out)

    def test_a_closed_row_and_plain_words_are_not_looked_for(self):
        _code, out = self.run_tool()
        self.assertNotIn("long_gone_name", out)
        self.assertNotIn("#4", out)

    def test_nothing_gone_exits_0(self):
        with open(os.path.join(self.root, "tagpup", "store", "vanished.py"), "w", encoding="utf-8") as handle:
            handle.write("def gone_helper():\n    pass\n")
        code, out = self.run_tool()
        self.assertEqual(0, code, out)
        self.assertIn("0 open row(s) to re-check", out)


if __name__ == "__main__":
    unittest.main()
