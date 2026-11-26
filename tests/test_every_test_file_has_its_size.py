"""Every Python test file has one size in tests/owners.py's SIZES, and it is the size the file's source says: the guard
that keeps the table right, so `tools/run_tests.py --size sqlite` runs the files that start no process (work row #3375,
docs/plans/fast-trustworthy-test-suite.md, cut 5).

A size is the most a file's tests reach for, in order: pure (nothing but Python), sqlite (a database through
tagpup.store.db or sqlite3), subprocess (a process: processes.start/run, ExifToolSession, subprocess, multiprocessing,
os.startfile, or a Flask app, whose routes open libraries and start ExifTool), browser (tests.page_server, a real
server under a real page).

It is derived from the NAMES a file uses, not its imports (an import closure makes nearly every file subprocess: the
package reaches tagpup.core.processes through config's ffmpeg check). Each top-level function, class, method
(Class.method, Class().method, self.method) and module body is a node; a name a node uses is followed
- through tests/, tools/ and scripts/ (the helpers and the programs a test imports by its folder) as far as it goes,
- and ONE module deep into the package: a tagpup function's own module, not the modules it calls in turn.
A module named as a value, not called through (patch.object(module, "x"), reload(module)), reaches its body only.
The owners of a size (above) are marked, never followed. Some uses are not the file's size, each one per process or
the machine's: NOT_THE_FILES.

Measured against what the files did (an audit hook on subprocess.Popen and sqlite3.connect over the pure and sqlite
files, 2026-11-24, --real-exiftool), 226 files: 3 differ besides the Flask files (said subprocess by the rule; most
ran none under the test client), none of them a process missed: two said pure opened a library two modules deep,
one said sqlite opened none. Four files whose use is more than one module deep are OVERRIDES, each with its
reason: the general cause (a test handing a service 'exiftool') said 6 files that start none were subprocess.
"""
import ast
import os
import shutil
import tempfile
import unittest

from tests import owners

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BODY = "<body>"
#: The folders whose modules a test imports by their bare name (sys.path) and which are followed all the way.
FOLLOWED = ("tests", "tools", "scripts")
_STARTS = frozenset({"Popen", "run", "call", "check_call", "check_output"})
#: Modules that own a size: what a node names in them is marked, and they are never followed.
OWNER_MODULES = frozenset({"tagpup.store.db", "tagpup.core.processes", "tagpup.files.exiftool_session",
                           "tests.page_server"})
#: Uses that are not the file's size: (module, name) never followed and never marked.
NOT_THE_FILES = frozenset({
    ("tests.own_home", "_reap_after_exit"),   # one reaper per process, whatever its tests do
    ("tests.corpus", "identity"),             # a tool's version, asked once a run for an item already made
    ("tagpup.config", "_does_not_run"),       # ffmpeg/ffprobe -version, once a process
})
#: {test file: (size, why)}: a file the rule cannot see, for a reason only it has.
OVERRIDES = {
    "test_background_tasks": ("sqlite", "opens a library two modules deep (counted by the audit hook, #3375; "
                              "`--size pure` ran it and touched SQLite)"),
    "test_jobs_indexing": ("sqlite", "opens a library two modules deep (counted by the audit hook, #3375; "
                           "`--size pure` ran it and touched SQLite)"),
    "test_no_person_row_without_an_id": ("subprocess", "tags.delete and journal.undo, handed 'exiftool', start it "
                                         "two services deep"),
    "test_reindex_keeps_face_decisions": ("subprocess", "the CLI's `index` (CliRunner, by its name in a list) "
                                          "reads the photos through ExifTool in the index job, past one module"),
}


def owned(module, name):
    """The size naming `name` of `module` marks, or None."""
    head = (name or "").split(".")[0]
    if module in ("tagpup.store.db", "sqlite3"):
        return "sqlite"
    if (module == "tagpup.core.processes" and head in ("start", "run")
            or module == "tagpup.files.exiftool_session" and head == "ExifToolSession"
            or module == "subprocess" and head in _STARTS
            or module == "multiprocessing" or module == "os" and head == "startfile"
            or module == "tagpup.web.app" and head == "create_app"):
        return "subprocess"
    if module == "tests.page_server":
        return "browser"
    return None


def _modules(root):
    """{dotted name: path} of tagpup/, the programs beside it and the followed folders."""
    found = {}
    for base, _dirs, names in os.walk(os.path.join(root, "tagpup")):
        for name in names:
            if name.endswith(".py"):
                dotted = os.path.relpath(os.path.join(base, name), root)[:-3].replace(os.sep, ".")
                found[dotted[: -len(".__init__")] if dotted.endswith(".__init__") else dotted] = os.path.join(base, name)
    for name in os.listdir(root):   # the programs: tagpup_cli, tagpup_web
        if name.endswith(".py"):
            found[name[:-3]] = os.path.join(root, name)
    for folder in FOLLOWED:
        for name in os.listdir(os.path.join(root, folder)):
            if name.endswith(".py") and name != "__init__.py":
                found[folder + "." + name[:-3]] = os.path.join(root, folder, name)
    return found


_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _is_main(node):
    return isinstance(node, ast.If) and any(isinstance(n, ast.Name) and n.id == "__name__" for n in ast.walk(node.test))


class _Module:
    """One file's nodes (its body, each function and class, each method as Class.method) and what each names."""

    def __init__(self, name, path, graph):
        self.name, self.graph = name, graph
        with open(path, encoding="utf-8") as handle:
            self.tree = ast.parse(handle.read())
        self.package = name if path.endswith("__init__.py") else name.rpartition(".")[0]
        self.defs = {n.name: n for n in self.tree.body if isinstance(n, _DEFS)}
        self.methods = {c: {m.name for m in node.body if isinstance(m, _DEFS)}
                        for c, node in self.defs.items() if isinstance(node, ast.ClassDef)}
        self.aliases = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.aliases[alias.asname] = (self.local(alias.name), None)
                    else:
                        top = alias.name.split(".")[0]
                        self.aliases[top] = (self.local(top), None)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = self.package.split(".")
                    source = ".".join(parts[: len(parts) - node.level + 1]) + ("." + node.module if node.module else "")
                else:
                    source = self.local(node.module or "")
                for alias in node.names:
                    sub = source + "." + alias.name
                    self.aliases[alias.asname or alias.name] = (sub, None) if sub in graph.paths else (source, alias.name)

    def local(self, dotted):
        """A bare name of a followed folder's module (imported through sys.path) as that module."""
        return self.graph.bare.get(dotted.split(".")[0], "") + dotted if self.name.split(".")[0] in FOLLOWED else dotted

    def nodes(self):
        """(node name, its statements, its class or None)."""
        yield BODY, [n for n in self.tree.body if not isinstance(n, _DEFS) and not _is_main(n)], None
        for name, node in self.defs.items():
            if isinstance(node, ast.ClassDef):
                yield name, [n for n in node.body if not isinstance(n, _DEFS)] + node.bases + node.decorator_list, None
                for method in node.body:
                    if isinstance(method, _DEFS):
                        yield name + "." + method.name, [method], name
            else:
                yield name, [node], None

    def names(self, statements, klass):
        """{(module, name or None)} these statements name; Class.attr, Class().attr and self.attr name the method."""
        out, inner = set(), set()
        for statement in statements:
            for node in ast.walk(statement):
                if isinstance(node, ast.Attribute):
                    inner.add(id(node.value))
                    if isinstance(node.value, ast.Call) and isinstance(node.value.func, (ast.Name, ast.Attribute)):
                        inner.add(id(node.value.func))
        for statement in statements:
            for node in ast.walk(statement):
                if id(node) in inner and not isinstance(node, ast.Call):
                    continue
                if isinstance(node, ast.Attribute):
                    out |= self._attribute(node, klass)
                elif isinstance(node, ast.Name):
                    target = self.resolve(node.id)
                    if target:   # a module as a value (patch.object(module, 'x')): its body, not its defs
                        out.add(target if target[1] else (target[0], BODY))
                elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                    called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    if called in ("__import__", "import_module"):
                        out.add((self.local(str(node.args[0].value)), None))
        return out

    def _attribute(self, node, klass):
        chain, value = [], node
        while isinstance(value, ast.Attribute):
            chain.append(value.attr)
            value = value.value
        chain.reverse()
        if isinstance(value, ast.Call):   # Class().method, module.Class().method
            value = value.func
            while isinstance(value, ast.Attribute):
                chain.insert(0, value.attr)
                value = value.value
        if not isinstance(value, ast.Name):
            return set()
        if value.id in ("self", "cls") and klass and chain[0] in self.methods[klass]:
            return {(self.name, klass + "." + chain[0])}
        target = self.resolve(value.id)
        if not target:
            return set()
        module, name = target
        if name is None:
            while chain and module + "." + chain[0] in self.graph.paths:
                module += "." + chain.pop(0)
            name = chain.pop(0) if chain else None
        return {(module, name + "." + chain[0] if name and chain else name)}

    def resolve(self, name):
        if name in self.aliases:
            return self.aliases[name]
        return (self.name, name) if name in self.defs else None


class Graph:
    """The nodes of tagpup/ and the followed folders, each with the sizes it marks and the nodes it reaches."""

    def __init__(self, root=ROOT):
        self.root = root
        self.paths = _modules(root)
        self.bare = {dotted.split(".", 1)[1]: dotted.split(".")[0] + "." for dotted in self.paths
                     if dotted.split(".")[0] in FOLLOWED}
        self.parsed = {name: _Module(name, path, self) for name, path in self.paths.items()}
        self.edges, self.marks = {}, {}
        for name, module in self.parsed.items():
            for node, statements, klass in module.nodes():
                key = (name, node)
                self.edges[key], self.marks[key] = set(), set()
                if key in NOT_THE_FILES:
                    continue
                for ref_module, ref_name in module.names(statements, klass):
                    mark = owned(ref_module, ref_name)
                    if mark:
                        self.marks[key].add(mark)
                    if (ref_module not in self.parsed or ref_module in OWNER_MODULES
                            or (ref_module, ref_name) in NOT_THE_FILES
                            or name.split(".")[0] == "tagpup" and ref_module != name):   # one module deep
                        continue
                    self.edges[key] |= self._reached(ref_module, ref_name)

    def _reached(self, module, name):
        """The nodes a name reaches: the module's body, and the name (a whole class: every method; Class.m: m and
        the __init__ that made the object)."""
        target = self.parsed[module]
        found = {(module, BODY)}
        if name is None:
            found.update((module, d) for d in target.defs)
            found.update((module, c + "." + m) for c, ms in target.methods.items() for m in ms)
            return found
        head, _, rest = name.partition(".")
        if head in target.methods:
            found.add((module, head))
            if not rest:
                found.update((module, head + "." + m) for m in target.methods[head])
            elif rest in target.methods[head]:
                found.add((module, name))
                if "__init__" in target.methods[head]:
                    found.add((module, head + ".__init__"))
        elif head in target.defs:
            found.add((module, head))
        return found

    def test_files(self):
        return sorted(name[:-3] for name in os.listdir(os.path.join(self.root, "tests"))
                      if name.startswith("test_") and name.endswith(".py"))

    def size_of(self, test_file):
        """The size the source of tests/<test_file>.py says."""
        if test_file in OVERRIDES:
            return OVERRIDES[test_file][0]
        module = "tests." + test_file
        seen, stack, marks = set(), [k for k in self.edges if k[0] == module], set()
        while stack:
            key = stack.pop()
            if key not in seen:
                seen.add(key)
                marks |= self.marks[key]
                stack.extend(self.edges[key])
        return max(marks | {"pure"}, key=owners.SIZE_ORDER.index)


def sizes_block(sizes):
    """The _SIZES lines of tests/owners.py for {test file: size}, as the guard prints them to paste."""
    lines = ["_SIZES = {"]
    for size in owners.SIZE_ORDER:
        names = sorted(n for n, s in sizes.items() if s == size)
        lines.append('    "%s": (' % size)
        row = ""
        for name in names:
            if row and len(row) + len(name) > 104:
                lines.append('        "%s"' % row)
                row = ""
            row += name + " "
        if row:
            lines.append('        "%s"' % row)
        lines.append("    ),")
    lines.append("}")
    return "\n".join(lines)


class EveryTestFileHasItsSize(unittest.TestCase):
    """A file added, or a file that starts reaching for more: the failure names it and prints the table to paste."""

    def test_sizes_are_the_ones_the_source_says(self):
        graph = Graph()
        derived = {name: graph.size_of(name) for name in graph.test_files()}
        wrong = ["%s: %s, SIZES says %s" % (name, size, owners.SIZES.get(name, "nothing"))
                 for name, size in derived.items() if owners.SIZES.get(name) != size]
        gone = sorted(set(owners.SIZES) - set(derived))
        if wrong or gone:
            self.fail("\n".join(wrong + ["%s: no such test file" % name for name in gone]
                                + ["Replace _SIZES in tests/owners.py with:", sizes_block(derived)]))

    def test_a_new_file_is_given_the_size_of_what_it_reaches(self):
        """A file added: a method through self and Class(), a helper followed, the package one module deep, a module
        patched as a value, a Flask app, the reaper; the block printed for it pastes back as the same table."""
        root = tempfile.mkdtemp(prefix="tagpup_sizes_")
        self.addCleanup(shutil.rmtree, root, True)
        sources = {
            "tagpup/__init__.py": "",
            "tagpup/store/db.py": "def connect(path):\n    pass\n",
            "tagpup/core/processes.py": "def run(argv):\n    pass\ndef start(argv):\n    pass\n",
            "tagpup/web/app.py": "def create_app(kind):\n    pass\n",
            "tagpup/services/thing.py": (
                "from tagpup.store import db\nfrom tagpup.core import processes\nfrom tagpup.services import other\n"
                "def read(path):\n    return db.connect(path)\n"
                "def convert(path):\n    return processes.run(['ffmpeg', path])\n"
                "def deep():\n    return other.start()\n"),
            "tagpup/services/other.py": "from tagpup.core import processes\ndef start():\n    processes.run(['x'])\n",
            "tests/helper.py": (
                "from tagpup.core import processes\nclass Maker:\n    def make(self):\n        return self._start()\n"
                "    def _start(self):\n        processes.start(['x'])\n    def plain(self):\n        return 1\n"),
            "tests/own_home.py": (
                "from tagpup.core import processes\ndef _reap_after_exit():\n    processes.start(['reap'])\n"),
            "tests/test_plain.py": "import helper\nimport own_home\nown_home._reap_after_exit()\nhelper.Maker().plain()\n",
            "tests/test_makes.py": "from tests import helper\ndef test():\n    helper.Maker().make()\n",
            "tests/test_reads.py": "from tagpup.services import thing\ndef test():\n    thing.read('x')\n",
            "tests/test_two_deep.py": "from tagpup.services import thing\ndef test():\n    thing.deep()\n",
            "tests/test_patched.py": (
                "from unittest import mock\nfrom tagpup.services import thing\n"
                "def test():\n    mock.patch.object(thing, 'convert')\n"),
            "tests/test_routes.py": "from tagpup.web import app as web\ndef test():\n    web.create_app('tagpup')\n",
            "tests/test_page.py": "from tests import page_server\ndef test():\n    page_server.serve()\n",
            "tests/page_server.py": "def serve():\n    pass\n",
        }
        for folder in ("tools", "scripts"):
            os.makedirs(os.path.join(root, folder))
        for name, text in sources.items():
            os.makedirs(os.path.dirname(os.path.join(root, name)), exist_ok=True)
            with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
                handle.write(text)
        graph = Graph(root)
        derived = {name: graph.size_of(name) for name in graph.test_files()}
        self.assertEqual({"test_plain": "pure", "test_makes": "subprocess", "test_reads": "sqlite",
                          "test_two_deep": "pure", "test_patched": "pure", "test_routes": "subprocess",
                          "test_page": "browser"}, derived)
        pasted = {}
        exec(sizes_block(derived), {}, pasted)
        self.assertEqual(derived, {n: size for size, names in pasted["_SIZES"].items() for n in names.split()})


if __name__ == "__main__":
    unittest.main()
