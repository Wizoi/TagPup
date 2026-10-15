"""Imports inside tagpup/ only go down the layers in docs/ARCHITECTURE.md. The same file holds the two
other rules about where code may sit: SQL only in tagpup/store, and scripts/ is programs, not a library.

The two servers grew by importing whatever they needed from wherever it was, until
each carried its own copy of what a photo library is: 51 function names defined in
both, and fixes that reached one copy and not the other. The package is organized in
layers so that cannot happen again -- rules at the bottom, then the database and the
files, then the actions that use them -- and this fails the build on an import that
goes back up. It also fails on a bare import of a module in scripts/ (`import
dedupe_faces`): those resolve only through sys.path, and code in the package must not
depend on code outside it.

A new layer is added to MAY_IMPORT and to docs/ARCHITECTURE.md together; a package that is
in neither fails.
"""
import ast
import importlib.machinery
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import LAUNCHERS, ROOT, python_sources  # noqa: E402

#: What each layer may import from tagpup besides itself. Core is pure rules, so every
#: layer may import it. Config is read where a program starts -- the entry points --
#: and what it says is passed down, through the runtime, the one place settings become
#: objects (models, each library's photo index). docs/ARCHITECTURE.md, "Layers".
MAY_IMPORT = {
    "core": set(),
    "config": {"core"},
    "logs": {"core", "config"},
    "launcher": {"core", "config", "logs"},
    "store": {"core"},
    "files": {"core"},
    "ml": {"core", "files"},
    "services": {"core", "store", "files", "ml"},
    "jobs": {"core", "services"},
    "runtime": {"core", "config", "logs", "store", "files", "ml", "services", "jobs"},
    "web": {"core", "config", "logs", "runtime", "services", "jobs", "launcher"},
    "cli": {"core", "config", "logs", "runtime", "services", "jobs"},
    "mcp": {"core", "config", "logs", "runtime", "services", "jobs"},
}

#: The programs (the launchers in the repository's root, and scripts/) are not in the package, and used to be checked by nothing.
#: They are a shell over the actions: they may import every layer above the data, but not the database or the files or the
#: models directly (`store`, `files`, `ml`): that is the services' to do, and a program that did it wrote around the rules the
#: services keep (docs/ARCHITECTURE.md, "Layers"). These import such a layer today, each as many times as listed; the count can
#: only go down, and the test says when it has (lower the number here). A new program, or a new import, fails.
PROGRAMS_MAY_NOT_IMPORT = {"store", "files", "ml"}
PROGRAMS_TODAY = {
    "tagpup_cli.py": 6,
    "scripts/measure_suggest_folder.py": 2,
    "scripts/prepare_test_environment.py": 7,
    "scripts/sandbox.py": 2,
    "scripts/verify_workflow.py": 3,
}

#: A line importing the package itself, not a module whose name starts with "tagpup".
IMPORTS_THE_PACKAGE = re.compile(r"\s*(?:import|from)\s+tagpup(?:\.|\s|$)")

#: Modules that live outside the package and are importable only through sys.path.
OLD_MODULES = {os.path.splitext(name)[0] for name in LAUNCHERS} | {
    os.path.splitext(name)[0] for name in os.listdir(os.path.join(ROOT, "scripts"))
    if name.endswith(".py")
}


def module_name(relative_path):
    """tagpup/store/db.py -> tagpup.store.db; tagpup/store/__init__.py -> tagpup.store."""
    parts = os.path.normpath(os.path.splitext(relative_path)[0]).split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def layer_of(name):
    """tagpup.store.db -> store; tagpup -> None."""
    parts = name.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "tagpup" else None


def imported_names(source, name, is_package):
    """The absolute name of everything a module imports, anywhere in it."""
    package = name if is_package else name.rpartition(".")[0]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[:len(package.split(".")) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module
            if target == "tagpup" or (node.level and not node.module):
                # `from tagpup import core` or `from .. import store`: the names are modules.
                for alias in node.names:
                    yield target + "." + alias.name
            else:
                yield target


def violations(relative_path, source):
    name = module_name(relative_path)
    own = layer_of(name)
    found = []
    for target in imported_names(source, name, relative_path.endswith("__init__.py")):
        if target.split(".")[0] in OLD_MODULES:
            found.append("imports %s, which lives outside the package" % target)
            continue
        other = layer_of(target)
        if other is None or other == own:
            continue
        if own is None:
            found.append("tagpup/__init__.py imports %s; importing tagpup must stay free" % target)
        elif own not in MAY_IMPORT:
            found.append("%s is not a layer in MAY_IMPORT or docs/ARCHITECTURE.md" % own)
        elif other not in MAY_IMPORT[own]:
            found.append("%s imports %s: %s may import only %s"
                         % (name, target, own, sorted(MAY_IMPORT[own]) or "itself"))
    return found


def package_sources():
    prefix = "tagpup" + os.sep
    return [p for p in python_sources() if p.startswith(prefix)]


class ImportsGoDown(unittest.TestCase):
    def test_no_import_goes_up_a_layer(self):
        problems = []
        for relative in package_sources():
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                source = handle.read()
            problems += ["%s: %s" % (relative, v) for v in violations(relative, source)]
        self.assertEqual(problems, [], "\n\nImports go down the layers in docs/ARCHITECTURE.md:\n\n"
                         + "\n".join(problems))

    def test_every_layer_is_in_the_table(self):
        """Packages and top-level modules both: tagpup/config.py is a layer as much as store/."""
        present = set()
        for entry in os.listdir(os.path.join(ROOT, "tagpup")):
            if os.path.isdir(os.path.join(ROOT, "tagpup", entry)) and entry != "__pycache__":
                present.add(entry)
            elif entry.endswith(".py") and entry != "__init__.py":
                present.add(os.path.splitext(entry)[0])
        self.assertEqual(sorted(present - set(MAY_IMPORT)), [],
                         "in tagpup/ but not in the layer table here or in docs/ARCHITECTURE.md")

    def test_the_package_is_checked(self):
        """A guard that finds no files passes forever."""
        self.assertIn(os.path.join("tagpup", "store", "db.py"), package_sources())

    def test_programs_reach_the_data_through_the_services(self):
        from code_snapshot import LAUNCHERS
        found = {}
        for relative in python_sources():
            name = relative.replace(os.sep, "/")
            if not (relative in LAUNCHERS or name.startswith("scripts/")):
                continue
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                source = handle.read()
            count = sum(1 for target in imported_names(source, module_name(relative), False)
                        if layer_of(target) in PROGRAMS_MAY_NOT_IMPORT)
            if count:
                found[name] = count
        over = {name: n for name, n in found.items() if n > PROGRAMS_TODAY.get(name, 0)}
        self.assertEqual({}, over, "a program imports store, files or ml directly: go through a service "
                                   "(docs/ARCHITECTURE.md), do not raise the number")
        under = {name: n for name, n in PROGRAMS_TODAY.items() if found.get(name, 0) < n}
        self.assertEqual({}, under, "fewer than listed (now %s): lower PROGRAMS_TODAY, it can only go down" % found)

    def test_scripts_reach_the_package_through_root(self):
        """A module in scripts/ imports _root before tagpup, or works only by luck.

        scripts/ is on sys.path but the repository root may not be: a script run
        directly has only its own folder. Whether `import tagpup` worked would depend
        on some earlier import having put the root there.
        """
        problems = []
        for relative in python_sources():
            if not relative.startswith("scripts" + os.sep) or relative.endswith("_root.py"):
                continue
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                lines = handle.read().splitlines()
            rooted = False
            for number, line in enumerate(lines, 1):
                if re.match(r"\s*import _root\b", line):
                    rooted = True
                elif IMPORTS_THE_PACKAGE.match(line) and not rooted:
                    problems.append("%s:%d" % (relative, number))
                    break
        self.assertEqual(problems, [], "import _root before tagpup in: " + ", ".join(problems))

    def test_the_root_check_recognises_the_package(self):
        for line, expected in {"from tagpup import config": True,
                               "    import tagpup.store.db": True,
                               "from tagpup.core import paths": True,
                               "import tagpup_server": False,
                               "from tagpup_server import start_server": False}.items():
            self.assertEqual(bool(IMPORTS_THE_PACKAGE.match(line)), expected, line)

    def test_the_guard_recognises_what_it_forbids(self):
        store = os.path.join("tagpup", "store", "db.py")
        core = os.path.join("tagpup", "core", "paths.py")
        files = os.path.join("tagpup", "files", "identity.py")
        samples = [
            (store, "from tagpup.core import paths", False),
            (store, "from . import schema", False),
            (store, "import sqlite3", False),
            (core, "from tagpup.store import db", True),
            (core, "import tagpup.files.identity", True),
            (files, "from ..store import db", True),
            (files, "from tagpup import store", True),
            (store, "import reloader", True),
            (store, "def f():\n    from reloader import start_reloader_thread", True),
            (os.path.join("tagpup", "__init__.py"), "from tagpup import core", True),
        ]
        for relative, source, expected in samples:
            self.assertEqual(bool(violations(relative, source)), expected, "%s: %s" % (relative, source))


# --- SQL is written only inside tagpup.store (#48): 210 statements sat outside it on 2026-09-24, and the same question
# --- was asked of the database in several places in several ways. A statement is a string starting with an SQL
# --- keyword in capitals, or a call to executemany or executescript; docstrings are prose.
STATEMENT = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|PRAGMA|BEGIN|REPLACE|WITH)\b")
STORE = os.path.join("tagpup", "store") + os.sep


def sql_in(source):
    """(line, what) of each SQL statement in `source`."""
    tree = ast.parse(source)
    prose = {id(n.body[0].value) for n in ast.walk(tree)
             if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body
             and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    prose |= {id(part) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for part in n.values}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose:
            if STATEMENT.match(node.value):
                found.append((node.lineno, node.value.strip().split("\n")[0][:60]))
        elif isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant) \
                and STATEMENT.match(str(node.values[0].value)):
            found.append((node.lineno, str(node.values[0].value).strip()[:60]))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("executemany", "executescript"):
            found.append((node.lineno, node.func.attr))
    return found


# --- scripts/ holds programs and what they share, and nothing else imports from it. Every module that moved into
# --- tagpup/ left a shim at its old name, and a test that patched a module by its old name could silently stop
# --- reaching the code under test (#128); they are gone (ARCHITECTURE.md, "Phase 6.5: No shims").
SCRIPTS = os.path.join(ROOT, "scripts")
#: Modules in scripts/ that are not programs, and why each is there.
HELPERS = {
    "_root": "puts the repository root on sys.path for a script run directly",
    "code_snapshot": "the code, as the installer and the measurement sandbox copy it",
    "sandbox": "a private copy of a library, a free port and a clean-up that tells, which the measurement "
               "scripts share (measure_identify_faces, measure_suggest_folder, measure_select_list)",
    "reloader": "the development auto-reloader tagpup_web.py starts",
}
#: A script that uses another program's code. None may be added.
SHARED_UNTIL_TOOLS = {(os.path.join("scripts", "generate_screenshots.py"), "prepare_test_environment")}
#: The shims and compositions phase 6.5 removed. Each name is the package's now.
RETIRED = ("db", "paths", "taxonomy", "exiftool_session", "identity",
           "embedder", "faces", "index", "suggester", "writer", "metadata")


def read(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


def script_modules():
    return sorted(os.path.splitext(name)[0] for name in os.listdir(SCRIPTS) if name.endswith(".py"))


def is_entry_point(source):
    """Has a module-level `if __name__ == "__main__":`."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            names = [node.test.left] + list(node.test.comparators)
            if (any(isinstance(n, ast.Name) and n.id == "__name__" for n in names)
                    and any(isinstance(n, ast.Constant) and n.value == "__main__" for n in names)):
                return True
    return False


def imported_modules(source):
    """The top-level name of every module a file imports, anywhere in it (relative imports left out)."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0])
    return found


class SqlOnlyInTheStore(unittest.TestCase):
    def test_no_shipped_file_outside_the_store_holds_sql(self):
        problems = ["%s:%d  %s" % (relative, line, what) for relative in python_sources()
                    if not relative.startswith(STORE) for line, what in sql_in(read(relative))]
        self.assertEqual(problems, [], "SQL outside tagpup.store:\n  " + "\n  ".join(problems))

    def test_the_guard_sees_a_statement_however_it_is_written(self):
        source = ('def plain(c):\n    """SELECT in a docstring is prose."""\n    return c.execute("SELECT path FROM photos")\n'
                  'def built(c, w):\n    return c.execute("UPDATE faces SET name = NULL WHERE " + w)\n'
                  'def formatted(c, t):\n    return c.execute(f"DELETE FROM {t}")\n'
                  'def many(c, rows):\n    c.executemany("?", rows)\n')
        self.assertEqual([3, 5, 7, 9], sorted(line for line, _ in sql_in(source)))
        self.assertEqual([], sql_in('message = "select a folder first"\nhint = "Update the app"\n'))


class ScriptsAreEntryPoints(unittest.TestCase):
    def test_every_module_in_scripts_is_a_program_or_a_helper(self):
        stray = [name for name in script_modules()
                 if name not in HELPERS and not is_entry_point(read(os.path.join("scripts", name + ".py")))]
        self.assertEqual([], stray, "in scripts/ but neither an entry point (a __main__ block) nor a "
                                    "helper named in HELPERS; a module goes in tagpup/ (docs/ARCHITECTURE.md)")
        self.assertEqual([], sorted(set(HELPERS) - set(script_modules())), "a helper that has moved comes off the list")

    def test_nothing_imports_a_script_but_scripts_launchers_and_tests(self):
        scripts = set(script_modules())
        problems = []
        tools = [os.path.join("tools", n) for n in sorted(os.listdir(os.path.join(ROOT, "tools"))) if n.endswith(".py")]
        for relative in sorted(set(python_sources()) | set(tools)):
            used = imported_modules(read(relative)) & scripts
            if relative.startswith(("tagpup" + os.sep, "tools" + os.sep)):
                allowed = set()
            elif relative in LAUNCHERS:
                allowed = set(HELPERS)
            else:   # a script
                own = os.path.splitext(os.path.basename(relative))[0]
                allowed = set(HELPERS) | {own} | {m for r, m in SHARED_UNTIL_TOOLS if r == relative}
            problems += ["%s imports %s" % (relative, name) for name in sorted(used - allowed)]
        self.assertEqual([], problems, "only a script's tests import it; what two programs share belongs in tagpup/")
        for relative, module in sorted(SHARED_UNTIL_TOOLS):   # an exception no longer needed comes off the list
            self.assertIn(module, imported_modules(read(relative)), relative)

    def test_the_old_names_are_gone(self):
        """`import db` fails, from scripts/ or the root: nothing answers to an old name."""
        for name in RETIRED:
            self.assertIsNone(importlib.machinery.PathFinder.find_spec(name, [SCRIPTS, ROOT]), name)

    def test_the_guard_recognises_what_it_forbids(self):
        self.assertTrue(is_entry_point('import sys\n\nif __name__ == "__main__":\n    sys.exit(0)\n'))
        self.assertTrue(is_entry_point("if '__main__' == __name__:\n    pass\n"))
        self.assertFalse(is_entry_point('def main():\n    if __name__ == "__main__":\n        pass\n'))
        self.assertEqual({"dedupe_faces", "tagpup", "os"},
                         imported_modules("import os\nimport dedupe_faces\ndef f():\n    from tagpup.store import db\n"))
        self.assertIn("reloader", imported_modules(read("tagpup_web.py")))


if __name__ == "__main__":
    unittest.main()
