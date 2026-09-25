"""tools/patch.py: the one helper patch scripts use to edit files."""
import os
import shutil
import sys
import tempfile
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "tools"))

from patch import Patch, PatchError  # noqa: E402

CR, LF = chr(13), chr(10)


class PatchingAFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tagpup_patch_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def file(self, text):
        path = os.path.join(self.dir, "a.py")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return path

    def read(self, path):
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()

    def test_a_crlf_file_stays_crlf(self):
        path = self.file("a = 1" + CR + LF + "b = 2" + CR + LF)
        with Patch(path) as p:
            p.replace("a = 1\nb = 2\n", "a = 1\nb = 3\nc = 4\n")
        self.assertEqual("a = 1" + CR + LF + "b = 3" + CR + LF + "c = 4" + CR + LF, self.read(path))

    def test_trailing_blanks_do_not_stop_a_match(self):
        path = self.file("def f():   \n    return 1\n")
        with Patch(path) as p:
            p.replace("def f():\n    return 1\n", "def f():\n    return 2\n")
        self.assertEqual("def f():\n    return 2\n", self.read(path))

    def test_a_text_found_twice_is_refused_and_nothing_is_written(self):
        path = self.file("x = 1\nx = 1\ny = 0\n")
        with self.assertRaises(PatchError):
            with Patch(path) as p:
                p.replace("y = 0", "y = 9")
                p.replace("x = 1", "x = 2")
        self.assertEqual("x = 1\nx = 1\ny = 0\n", self.read(path))

    def test_a_count_says_how_many(self):
        path = self.file("x = 1\nx = 1\n")
        with Patch(path) as p:
            p.replace("x = 1", "x = 2", count=2)
        self.assertEqual("x = 2\nx = 2\n", self.read(path))

    def test_between_after_and_before(self):
        path = self.file("import os\n\ndef a():\n    pass\n\ndef b():\n    pass\n")
        with Patch(path) as p:
            p.between("def a():", "def b():", "def a():\n    return 1\n\n")
            p.after("import os\n", "import sys\n")
            p.before("def b():", "# b\n")
        self.assertEqual("import os\nimport sys\n\ndef a():\n    return 1\n\n# b\ndef b():\n    pass\n",
                         self.read(path))


if __name__ == "__main__":
    unittest.main()
