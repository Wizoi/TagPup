"""The tests a change can affect: for running while working, not in place of the full check.

    .venv/Scripts/python.exe tools/affected_tests.py            # changed since the last commit
    .venv/Scripts/python.exe tools/affected_tests.py --since main
    .venv/Scripts/python.exe tools/affected_tests.py --run      # and run them (tools/run_tests.py)

A test is affected when it imports a changed module, directly or through modules that
import it -- by package name (tagpup.store.db) or by the old names in scripts/ (db), as
the tests do -- or when its source names a changed file, as the tests that read the
specs, the pages or config.ini do. Any Python change brings the lint test in. A change
to the pages (web/) or to tests/frontend/ also calls for the frontend
suite, which this prints the command for.

The full suite still runs before each commit and each merge: what a test reaches
through a subprocess, a file it opens by a name built at run time, or state another
test left, is not an import.
"""
import argparse
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tagpup.core import processes  # noqa: E402
FRONTEND = ("web/", "tests/frontend/")


def changed_files(since):
    """Repo-relative paths changed since `since` (default HEAD), untracked ones included."""
    tracked = processes.run(["git", "diff", "--name-only", since or "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split()
    untracked = processes.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.split()
    return sorted(set(tracked) | set(untracked))


def python_files():
    """Repo-relative paths of every Python file the tests can reach."""
    found = []
    for folder in ("tagpup", "scripts", "tools", "tests"):
        for current, dirs, names in os.walk(os.path.join(ROOT, folder)):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "frontend")]
            found += [os.path.relpath(os.path.join(current, n), ROOT).replace(os.sep, "/")
                      for n in names if n.endswith(".py")]
    found += [n for n in os.listdir(ROOT) if n.endswith(".py")]
    return sorted(found)


def module_names(relative):
    """The names a file is imported by: tagpup.store.db for tagpup/store/db.py;
    dedupe_faces for scripts/dedupe_faces.py, a root launcher or a test helper, as the
    tests import them."""
    stem = relative[:-3]
    if stem.startswith("tagpup/"):
        dotted = stem.replace("/", ".")
        return {dotted[:-len(".__init__")] if dotted.endswith(".__init__") else dotted}
    return {os.path.basename(stem), stem.replace("/", ".")}


def imports(relative):
    """The module names a file imports, each dotted prefix included: `from tagpup.store
    import db` imports tagpup, tagpup.store and tagpup.store.db."""
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update("%s.%s" % (node.module, alias.name) for alias in node.names)
    expanded = set()
    for name in names:
        parts = name.split(".")
        expanded.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
    return expanded


def affected(changed):
    """(Python test modules, whether the frontend suite is called for)."""
    files = python_files()
    owner = {}
    for relative in files:
        for name in module_names(relative):
            owner.setdefault(name, set()).add(relative)
    importers = {}
    for relative in files:
        for name in imports(relative):
            for imported in owner.get(name, ()):
                importers.setdefault(imported, set()).add(relative)

    reached = {c for c in changed if c.endswith(".py")}
    frontier = list(reached)
    while frontier:
        for importer in importers.get(frontier.pop(), ()):
            if importer not in reached:
                reached.add(importer)
                frontier.append(importer)

    tests = {f for f in reached if f.startswith("tests/test_")}
    named = [os.path.basename(c) for c in changed]
    for relative in files:
        if relative.startswith("tests/test_") and relative not in tests:
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                source = handle.read()
            if any(name in source for name in named):
                tests.add(relative)
    if any(c.endswith(".py") for c in changed):
        tests.add("tests/test_lint.py")
    modules = sorted(os.path.splitext(os.path.basename(t))[0] for t in tests
                     if os.path.exists(os.path.join(ROOT, t)))
    return modules, any(c.startswith(FRONTEND) for c in changed)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", help="a commit or branch to compare with (default: the last commit)")
    parser.add_argument("--run", action="store_true", help="run them (tools/run_tests.py)")
    args = parser.parse_args(argv)
    changed = changed_files(args.since)
    modules, frontend = affected(changed)
    print("%d file(s) changed; %d test file(s) affected%s" % (
        len(changed), len(modules), "; the pages too" if frontend else ""))
    for module in modules:
        print("  " + module)
    if frontend:
        print("frontend: node --test tests/frontend/*.test.mjs")
    if args.run and modules:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import run_tests
        return run_tests.main(modules)
    return 0


if __name__ == "__main__":
    sys.exit(main())
