"""The runner window's Suggest and Write go by the library's own suggestions file (docs/findings.md, #293).

runner.py passed `--output suggestions.json` (or test_suggestions.json), relative to its working folder, so the
file was written there and not beside the library as `tagpup_cli suggest` does (#102). Left blank, the window asks
for no output and reads the file the CLI writes by default.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

try:
    import runner  # noqa: E402
except ImportError:      # no tkinter
    runner = None

from tagpup.core import library as libraries  # noqa: E402


class Text:
    def __init__(self, text=""):
        self.text = text

    def get(self):
        return self.text


@unittest.skipIf(runner is None, "no tkinter")
class TheRunnersSuggestions(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.folder = os.path.join(self.home.root, "Pictures")
        os.makedirs(self.folder)
        window = runner.RunnerApp.__new__(runner.RunnerApp)
        window.ent_target_dir = Text(self.folder)
        window.ent_suggest_file = Text("")
        window.ent_min_sim = Text("")
        window.ent_k = Text("")
        window.ent_min_score = Text("")
        window.combo_db = Text("harbour")
        window.var_test_db = Text(False)
        window.var_nobackup = Text(False)
        window.commands = []
        window.execute_command = lambda cmd, **kwargs: window.commands.append(cmd)
        self.window = window

    def test_suggest_with_no_file_named_asks_for_no_output(self):
        with mock.patch.object(runner.messagebox, "showerror") as shown:
            self.window.run_suggest_action()
        shown.assert_not_called()
        self.assertEqual(1, len(self.window.commands))
        self.assertNotIn("--output", self.window.commands[0])

    def test_write_with_no_file_named_reads_the_one_beside_the_library(self):
        expected = libraries.suggestions_file(os.path.join(runner.tagpup_config.data_dir(), "harbour.db"))
        os.makedirs(os.path.dirname(expected), exist_ok=True)
        with open(expected, "w") as handle:
            handle.write("[]")
        with mock.patch.object(runner.messagebox, "showerror") as shown:
            self.window.run_write_preview_action()
        shown.assert_not_called()
        self.assertEqual(expected, self.window.commands[0][3])


if __name__ == "__main__":
    unittest.main()
