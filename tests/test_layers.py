"""Imports inside tagpup/ only go down the layers in docs/ARCHITECTURE.md.

The two servers grew by importing whatever they needed from wherever it was, until
each carried its own copy of what a photo library is: 51 function names defined in
both, and fixes that reached one copy and not the other. The package is organized in
layers so that cannot happen again -- rules at the bottom, then the database and the
files, then the actions that use them -- and this fails the build on an import that
goes back up. It also fails on a bare import of an old module in scripts/ (`import
db`): those resolve only through sys.path, and code in the package must not depend on
code outside it.

A new layer is added to MAY_IMPORT and to docs/ARCHITECTURE.md together; a package that is
in neither fails.
"""
import ast
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
    "config": set(),
    "logs": {"config"},
    "store": {"core"},
    "files": {"core"},
    "ml": {"core", "files"},
    "services": {"core", "store", "files", "ml"},
    "jobs": {"core", "services"},
    "runtime": {"core", "config", "logs", "store", "files", "ml", "services", "jobs"},
    "web": {"core", "config", "logs", "runtime", "services", "jobs"},
    "cli": {"core", "config", "logs", "runtime", "services", "jobs"},
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
            (store, "import db", True),
            (store, "def f():\n    from index import PhotoIndex", True),
            (os.path.join("tagpup", "__init__.py"), "from tagpup import core", True),
        ]
        for relative, source, expected in samples:
            self.assertEqual(bool(violations(relative, source)), expected, "%s: %s" % (relative, source))


if __name__ == "__main__":
    unittest.main()
