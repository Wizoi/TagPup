"""scripts/ holds programs to run, and what they share; nothing else imports from it.

Every module that moved into tagpup/ left a shim at its old name in scripts/, so that
old code kept working: `import db` was tagpup.store.db. By phase 6.5 eleven were left,
imported by some sixty files, and a test that patched a module by its old name could
silently stop reaching the code under test (docs/findings.md, #128). They are gone
(docs/ARCHITECTURE.md, "Phase 6.5: No shims"); this keeps it so. A module in scripts/
is an entry point -- it has a `__main__` block -- or one of the helpers below, and
only the scripts, the launchers (for a helper) and the tests import from scripts/.
"""
import ast
import importlib.machinery
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import LAUNCHERS, ROOT, python_sources  # noqa: E402

SCRIPTS = os.path.join(ROOT, "scripts")

#: Modules in scripts/ that are not programs, and why each is there.
HELPERS = {
    "_root": "puts the repository root on sys.path for a script run directly",
    "code_snapshot": "the code, as the installer and the measurement sandbox copy it",
    "reloader": "the development auto-reloader tagpup_web.py starts; "
                "tagpup/dev/reloader.py in docs/ARCHITECTURE.md, 'Where everything goes'",
}

#: A script that uses another program's code: the sandbox tools, until they move to
#: tools/ with one sandbox module (docs/ARCHITECTURE.md, "Where everything goes").
#: None may be added.
SHARED_UNTIL_TOOLS = {
    (os.path.join("scripts", "measure_suggest_folder.py"), "measure_identify_faces"),
    (os.path.join("scripts", "generate_screenshots.py"), "prepare_test_environment"),
}

#: The shims and compositions phase 6.5 removed. Each name is the package's now.
RETIRED = ("db", "paths", "taxonomy", "exiftool_session", "identity",
           "embedder", "faces", "index", "suggester", "writer", "metadata")


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
    """The top-level name of every module a file imports, anywhere in it; relative
    imports are left out (scripts/ is not a package)."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0])
    return found


def read(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


def tools_sources():
    return [os.path.join("tools", name) for name in sorted(os.listdir(os.path.join(ROOT, "tools")))
            if name.endswith(".py")]


class ScriptsAreEntryPoints(unittest.TestCase):
    def test_every_module_in_scripts_is_a_program_or_a_helper(self):
        stray = [name for name in script_modules()
                 if name not in HELPERS and not is_entry_point(read(os.path.join("scripts", name + ".py")))]
        self.assertEqual([], stray, "in scripts/ but neither an entry point (a __main__ block) nor a "
                                    "helper named in HELPERS; a module goes in tagpup/ (docs/ARCHITECTURE.md)")

    def test_every_helper_is_there(self):
        """A helper that has moved comes off the list, rather than excuse a newcomer."""
        self.assertEqual([], sorted(set(HELPERS) - set(script_modules())))

    def test_nothing_imports_a_script_but_scripts_launchers_and_tests(self):
        scripts = set(script_modules())
        problems = []
        sources = set(python_sources()) | set(tools_sources())
        for relative in sorted(sources):
            used = imported_modules(read(relative)) & scripts
            if relative.startswith("tagpup" + os.sep) or relative.startswith("tools" + os.sep):
                allowed = set()
            elif relative in LAUNCHERS:
                allowed = set(HELPERS)
            else:   # a script
                own = os.path.splitext(os.path.basename(relative))[0]
                allowed = set(HELPERS) | {own} | {m for r, m in SHARED_UNTIL_TOOLS if r == relative}
            problems += ["%s imports %s" % (relative, name) for name in sorted(used - allowed)]
        self.assertEqual([], problems, "only a script's tests import it; what two programs share "
                                       "belongs in tagpup/ (or, for scripts alone, in HELPERS)")

    def test_the_shared_sandbox_code_is_still_shared(self):
        """An exception that is no longer needed comes off the list."""
        for relative, module in sorted(SHARED_UNTIL_TOOLS):
            self.assertIn(module, imported_modules(read(relative)), relative)

    def test_the_old_names_are_gone(self):
        """`import db` fails, from scripts/ or the root: nothing answers to an old name."""
        for name in RETIRED:
            self.assertIsNone(importlib.machinery.PathFinder.find_spec(name, [SCRIPTS, ROOT]), name)

    def test_the_guard_recognises_what_it_forbids(self):
        self.assertTrue(is_entry_point('import sys\n\nif __name__ == "__main__":\n    sys.exit(0)\n'))
        self.assertTrue(is_entry_point("if '__main__' == __name__:\n    pass\n"))
        self.assertFalse(is_entry_point('def main():\n    if __name__ == "__main__":\n        pass\n'))
        self.assertFalse(is_entry_point("import _root\nfrom tagpup.store import db\n"))
        self.assertEqual({"dedupe_faces", "tagpup", "os"},
                         imported_modules("import os\nimport dedupe_faces\n"
                                          "def f():\n    from tagpup.store import db\n"))
        self.assertIn("reloader", imported_modules(read("tagpup_web.py")))


if __name__ == "__main__":
    unittest.main()
