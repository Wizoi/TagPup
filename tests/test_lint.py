"""The linter runs as part of the suite, so it cannot be forgotten.

Ruff is configured in ruff.toml to find defects rather than to have opinions: the full
default rule set reports about 900 things here, most of them style preferences and
several of them deliberate. A linter reporting 900 findings gets muted, and a muted
linter reports nothing.

Narrowed to pyflakes and the behavioural half of bugbear, it found two live bugs in
its first run:

- `runner.py` logged execution errors through a lambda reading the `except` variable.
  Python deletes that name when the block ends and the lambda runs later on the UI
  thread, so the error handler raised NameError every time an error occurred.
- `tuner_server.save_suggestions_cache` called `make_json_serializable`, which lives
  in `tagpup_server` and was never imported. The NameError went into a catch that
  logged and carried on, so TagTuner had never once written its suggestion cache.

It also found two `read_json_body` methods on one class, where the later one silently
replaced a version that returned `{}` for an empty body.
"""
import os
import subprocess
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestNothingTheLinterObjectsTo(unittest.TestCase):
    def test_ruff_is_available(self):
        # Skipping silently on a machine without it would make this test a decoration.
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True, text=True, cwd=WORKSPACE_DIR)
        self.assertEqual(
            result.returncode, 0,
            "ruff is not installed: .venv/Scripts/python.exe -m pip install ruff")

    def test_the_tree_is_clean(self):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--output-format", "concise", "."],
            capture_output=True, text=True, cwd=WORKSPACE_DIR)
        self.assertEqual(
            result.returncode, 0,
            "ruff found something:\n\n%s\n\nFix it, or if the rule is wrong for this "
            "codebase, add it to ruff.toml with a comment saying why."
            % (result.stdout or result.stderr))


if __name__ == "__main__":
    unittest.main()
