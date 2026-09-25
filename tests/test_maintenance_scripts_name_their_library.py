"""A maintenance script is told which library it works on.

`dedupe_faces.py` and `merge_duplicate_person_tags.py` defaulted `--db` to kr-track in
the data folder: a library nobody named, which #100 took out of the apps and the CLI.
Both are dry runs by default, but `--apply` alone wrote it. Found registering their
services as MCP tools, whose every call names its library.
"""
import contextlib
import io
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402

SCRIPTS = ("dedupe_faces", "merge_duplicate_person_tags", "refresh_rows_from_files")


class EachScriptNamesItsLibrary(unittest.TestCase):
    def test_without_db_it_refuses_and_touches_no_library(self):
        home = own_home.for_test(self)
        for name in SCRIPTS:
            module = __import__(name)
            with self.subTest(script=name), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    module.main(["--apply"])
                self.assertNotEqual(0, stopped.exception.code)
            self.assertEqual([], os.listdir(home.data), "%s opened a library it was not given" % name)


if __name__ == "__main__":
    unittest.main()
