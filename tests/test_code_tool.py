"""tools/read_code.py: a worker reads a large module by its outline and one definition, not whole (retro 2026-11-13).

The scenario is the worker's: it outlines the largest modules of the package, shows one function of one of them by the
line numbers the outline gave, meets a name defined twice and is told the candidates, mistypes a name and is told the
nearest, and asks for every reference to a common name and is told how many it was not shown. A file with CRLF and a
byte that is not UTF-8, and a Windows path, read as any other.

The tool is loaded from its file under a name of its own: `code` is also a module of Python's standard library.
"""
import ast
import importlib.util
import os
import re
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location("tagpup_code_tool", os.path.join(ROOT, "tools", "read_code.py"))
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)

CR, LF = chr(13), chr(10)
#: An outline's entry: its first and last line, its kind, its qualified name (then a function's arguments).
ENTRY = re.compile(r"^(\d+)-(\d+) +(?:def|async def|class|const) ([\w.]+)")


def largest_modules(count=4):
    sizes = []
    for folder, _dirs, files in os.walk(os.path.join(ROOT, "tagpup")):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(folder, name)
                sizes.append((os.path.getsize(path), path))
    return [path for _size, path in sorted(sizes, reverse=True)[:count]]


def file_lines(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read().splitlines()


class ReadingALargeModuleByPart(unittest.TestCase):
    def test_the_outline_of_each_large_module_names_its_definitions_on_their_lines_in_a_fraction_of_the_file(self):
        for path in largest_modules():
            with self.subTest(module=os.path.relpath(path, ROOT)):
                code, out = tool.run(["outline", path])
                self.assertEqual(0, code, out)
                self.assertNotIn("[cut:", out)
                lines = file_lines(path)
                self.assertIn("%d lines" % len(lines), out.splitlines()[0])
                tree = ast.parse("\n".join(lines))
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        self.assertRegex(out, r"(?m)^\d+-%d (def|async def|class) %s\b" % (node.end_lineno, node.name))
                for entry in out.splitlines()[1:]:
                    start, end, name = ENTRY.match(entry).groups()
                    self.assertLessEqual(int(start), int(end))
                    self.assertIn(name.rsplit(".", 1)[-1], "\n".join(lines[int(start) - 1:int(end)]))
                self.assertLess(len(out), os.path.getsize(path) / 5, "an outline is a small part of its file")

    def test_one_function_shown_by_name_is_the_files_own_lines_with_their_numbers(self):
        path = os.path.join(ROOT, "tagpup", "store", "journal.py")
        code, outline = tool.run(["outline", path])
        entry = next(line for line in outline.splitlines() if " def Refusal.__init__" in line)
        start, end = (int(n) for n in entry.split()[0].split("-"))
        code, out = tool.run(["show", path + "::Refusal.__init__"])
        self.assertEqual(0, code, out)
        lines = file_lines(path)
        shown = out.splitlines()
        self.assertIn("lines %d-%d of %d" % (start, end, len(lines)), shown[0])
        self.assertEqual(["%d\t%s" % (n, lines[n - 1]) for n in range(start, end + 1)], shown[1:])

    def test_a_windows_path_relative_to_the_repository_and_a_markdown_section_and_a_page_module(self):
        code, out = tool.run(["show", "tagpup\\store\\journal.py::Refusal"])
        self.assertEqual(0, code, out)
        self.assertTrue(out.startswith("tagpup/store/journal.py::Refusal  lines "), out)
        code, out = tool.run(["show", "CLAUDE.md::Running things"])
        self.assertEqual(0, code, out)
        self.assertIn("\t## Running things", out.splitlines()[1])
        self.assertNotIn("## Rules that keep being broken", out, "a section ends at the next heading of its level")
        code, out = tool.run(["outline", "web/common/api.js"])
        self.assertEqual(0, code, out)
        self.assertRegex(out, r"(?m)^\d+-\d+ function apiUrl$")
        start, end = (int(n) for n in next(line for line in out.splitlines() if line.endswith(" apiUrl")).split()[0].split("-"))
        js = file_lines(os.path.join(ROOT, "web", "common", "api.js"))
        self.assertEqual("}", js[end - 1].strip(), "a function ends at its closing brace")
        self.assertIn("function apiUrl", js[start - 1])


class AFileOfItsOwn(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tagpup_code_tool_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def file(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_a_name_defined_twice_lists_the_candidates_and_each_can_be_named(self):
        path = self.file("twice.py", (
            "def walk(a):\n    return a\n\n\n"
            "class Folder:\n    def walk(self):\n        return 1\n\n\n"
            "class Share:\n    def walk(self):\n        return 2\n\n\n"
            "def walk(a, b):\n    return b\n").encode())
        code, out = tool.run(["show", path + "::walk"])
        self.assertEqual(1, code)
        self.assertIn("names 4 definitions", out)
        for label in ("1-2 def walk(a)", "6-7   def Folder.walk(self)", "11-12   def Share.walk(self)", "15-16 def walk(a, b)"):
            self.assertIn(label, out)
        self.assertIn("Class.name or name@LINE", out)
        code, out = tool.run(["show", path + "::Share.walk"])
        self.assertEqual((0, "12\t        return 2"), (code, out.splitlines()[-1]))
        code, out = tool.run(["show", path + "::walk@15"])
        self.assertEqual((0, "16\t    return b"), (code, out.splitlines()[-1]))

    def test_a_missing_name_names_the_nearest_and_an_unparsable_file_says_so(self):
        path = self.file("near.py", b"def records_written():\n    pass\n\n\ndef record():\n    pass\n")
        code, out = tool.run(["show", path + "::recrod"])
        self.assertEqual(1, code)
        self.assertIn("no 'recrod' here. Nearest: record", out)
        broken = self.file("broken.py", b"def (:\n")
        code, out = tool.run(["outline", broken])
        self.assertEqual(2, code)
        self.assertIn("cannot parse", out)
        code, out = tool.run(["outline", os.path.join(self.dir, "not-there.py")])
        self.assertEqual(2, code)
        self.assertIn("no file", out)

    def test_crlf_and_a_byte_that_is_not_utf8_read_on_their_lines(self):
        text = ("# caf" + chr(0xE9) + " in latin-1" + CR + LF + "ANSWER = 42" + CR + LF + CR + LF
                + "def answer():" + CR + LF + "    return ANSWER" + CR + LF)
        path = self.file("old.py", text.encode("latin-1"))
        code, out = tool.run(["outline", path])
        self.assertEqual(0, code, out)
        self.assertIn("2-2 const ANSWER", out)
        self.assertIn("4-5 def answer()", out)
        code, out = tool.run(["show", path + "::answer"])
        self.assertEqual(0, code, out)
        self.assertNotIn(CR, out)
        self.assertEqual(["4\tdef answer():", "5\t    return ANSWER"], out.splitlines()[1:])

    def test_a_long_definition_is_cut_saying_where_to_read_on(self):
        body = "".join("    x%d = %d\n" % (n, n) for n in range(tool.MAX_LINES * 2))
        path = self.file("long.py", ("def long():\n" + body).encode())
        code, out = tool.run(["show", path + "::long"])
        self.assertEqual(0, code)
        shown = out.splitlines()
        self.assertEqual(tool.MAX_LINES + 1, len(shown))
        first_unshown = tool.MAX_LINES
        self.assertIn("[cut: %d more lines not shown" % (tool.MAX_LINES * 2 + 1 - (tool.MAX_LINES - 1)), shown[-1])
        self.assertIn("Read offset=%d limit=%d" % (first_unshown, tool.MAX_LINES * 2 + 2 - first_unshown), shown[-1])
        self.assertTrue(shown[-2].startswith("%d\t" % (first_unshown - 1)))


class FindingReferences(unittest.TestCase):
    def test_a_common_name_is_capped_and_says_how_many_more(self):
        code, out = tool.run(["refs", "readonly_uri", "--max", "5"])
        self.assertEqual(0, code)
        lines = out.splitlines()
        self.assertEqual(6, len(lines))
        for line in lines[:5]:
            self.assertRegex(line, r"^(tagpup|tests|web|scripts|tools)/[^:]+:\d+:.*\breadonly_uri\b")
        self.assertRegex(lines[5], r"^\[5 of \d+ references shown, in \d+ files; --max N for more")

    def test_the_default_cap_is_forty_and_a_word_is_matched_whole(self):
        code, out = tool.run(["refs", "db"])
        self.assertEqual(0, code)
        self.assertEqual(tool.REFS_DEFAULT + 1, len(out.splitlines()))
        code, out = tool.run(["refs", "nothing_" + "named_" + "so"])
        self.assertIn("no line names", out)


if __name__ == "__main__":
    unittest.main()
