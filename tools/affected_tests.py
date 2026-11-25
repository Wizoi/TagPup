"""The tests a change can affect: for running while working, not in place of the full check.

    .venv/Scripts/python.exe tools/affected_tests.py            # changed since the last commit
    .venv/Scripts/python.exe tools/affected_tests.py --since main
    .venv/Scripts/python.exe tools/affected_tests.py --run      # and run them (tools/run_tests.py)
    .venv/Scripts/python.exe tools/affected_tests.py --tests    # name each test the coverage map says is reached

For a changed file of tagpup/ that the coverage map knows (tests/coverage_map.txt.xz, tools/test_map.py), a test is
affected when it entered a function the diff changes, on either side of it: a changed module or class body, a def
line or a decorator (what runs at import) reaches every test that entered the file. A test file the map does not
trust (it failed, wrote nothing, or starts a Python child the map cannot see into) is taken by its imports instead.
For any other change, a test is affected when it imports a changed module, directly or through modules that
import it -- by package name (tagpup.store.db) or by the old names in scripts/ (db), as
the tests do. Either way a test whose source names a changed file is affected, as the tests that read the
specs, the pages or config.ini are. Any Python change brings the lint test in. A change
to the pages (web/) or to tests/frontend/ also calls for the frontend
suite, which this prints the command for, and for the pages' test against the real server.

The full suite still runs before each commit and each merge: what a test reaches
through a subprocess, a file it opens by a name built at run time, or state another
test left, is not an import.
"""
import argparse
import ast
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tagpup.core import processes  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "tools"))
import test_map  # noqa: E402
FRONTEND = ("web/", "tests/frontend/")

#: The Python test that runs the pages against the real server (the node suite skips those describes).
PAGES_TEST = "tests/test_pages_against_the_real_server.py"


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
    measure_suggest_folder for scripts/measure_suggest_folder.py, a root launcher or a test helper, as the
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


def importers_of(files):
    """{file: the files that import it}."""
    owner = {}
    for relative in files:
        for name in module_names(relative):
            owner.setdefault(name, set()).add(relative)
    importers = {}
    for relative in files:
        for name in imports(relative):
            for imported in owner.get(name, ()):
                importers.setdefault(imported, set()).add(relative)
    return importers


def by_imports(changed, importers):
    """The test files that import one of `changed`, directly or through other modules, or are one."""
    reached = {c for c in changed if c.endswith(".py")}
    frontier = list(reached)
    while frontier:
        for importer in importers.get(frontier.pop(), ()):
            if importer not in reached:
                reached.add(importer)
                frontier.append(importer)
    return {f for f in reached if f.startswith("tests/test_")}


#: A hunk's header in `git diff -U0`: the old side's first line and count, then the new side's.
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)


def changed_lines(diff):
    """(old side's lines, new side's lines) a unified diff changes."""
    old, new = set(), set()
    for a, b, c, d in HUNK.findall(diff):
        old.update(range(int(a), int(a) + int(b or 1)))
        new.update(range(int(c), int(c) + int(d or 1)))
    return old, new


def names_at(source, lines):
    """The functions (Class.method, function) whose bodies hold `lines`; '<module>' for a line that runs at import:
    a module's or class's body, a def line, a decorator, a default. A function nested in another is the outer one."""
    spans = []

    def walk(body, prefix):
        for node in body:
            if isinstance(node, ast.ClassDef):
                walk(node.body, prefix + node.name + ".")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                spans.append((node.body[0].lineno, node.end_lineno, prefix + node.name))
    walk(ast.parse(source).body, "")
    names = set()
    for line in lines:
        inside = [name for first, last, name in spans if first <= line <= last]
        names.add(max(inside, key=len) if inside else "<module>")
    return names


def mapped_keys(found, relative, since):
    """The map's keys a change to `relative` reaches, or None when the map cannot say (not tagpup/, or no test of the
    map entered the file)."""
    known = found.keys_in(relative) if relative.startswith("tagpup/") and relative.endswith(".py") else []
    if not known:
        return None
    base = since or "HEAD"
    try:
        diff = processes.run(["git", "diff", "-U0", base, "--", relative], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout
        old_source = processes.run(["git", "show", "%s:%s" % (base, relative)], cwd=ROOT, capture_output=True,
                                   text=True, check=True).stdout
        with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
            new_source = handle.read()
    except (OSError, subprocess.CalledProcessError):   # gone, or new since `since`: all of it
        return known
    return keys_for(known, relative, diff, old_source, new_source)


def keys_for(known, relative, diff, old_source, new_source):
    """The keys of `relative` a diff between two sources changes; all it has (`known`) when one runs at import."""
    old, new = changed_lines(diff)
    try:
        names = names_at(old_source, old) | names_at(new_source, new)
    except SyntaxError:
        return known
    if "<module>" in names:
        return known
    return ["%s::%s" % (relative, name) for name in sorted(names)]


def affected(changed, since=None, found=None):
    """(Python test modules, whether the frontend suite is called for, {module: test ids reached, or None: all}).
    `found` is the coverage map (test_map.Map), or None to go by the imports alone."""
    files = python_files()
    importers = importers_of(files)
    reached, rest = {}, []
    for relative in changed:
        keys = mapped_keys(found, relative, since) if found else None
        if keys is None:
            rest.append(relative)
            continue
        for module, names in found.reaching(keys).items():
            if names is None or not found.trusted(module) or reached.get(module, ()) is None:
                reached[module] = None
            else:
                reached.setdefault(module, set()).update(names)
        for test in by_imports([relative], importers):
            module = os.path.splitext(os.path.basename(test))[0]
            if not found.trusted(module):
                reached[module] = None
    for test in by_imports(rest, importers):
        reached[os.path.splitext(os.path.basename(test))[0]] = None
    tests = {"tests/%s.py" % module for module in reached}
    named = [os.path.basename(c) for c in changed]
    for relative in files:
        if relative.startswith("tests/test_") and relative not in tests:
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                source = handle.read()
            if any(name in source for name in named):
                tests.add(relative)
                reached[os.path.splitext(os.path.basename(relative))[0]] = None
    if any(c.endswith(".py") for c in changed):
        tests.add("tests/test_lint.py")
    frontend = any(c.startswith(FRONTEND) for c in changed)
    if frontend:
        tests.add(PAGES_TEST)
    modules = sorted(os.path.splitext(os.path.basename(t))[0] for t in tests
                     if os.path.exists(os.path.join(ROOT, t)))
    return modules, frontend, {m: reached.get(m) for m in modules}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", help="a commit or branch to compare with (default: the last commit)")
    parser.add_argument("--run", action="store_true", help="run them (tools/run_tests.py)")
    parser.add_argument("--tests", action="store_true", help="name each test the coverage map says is reached")
    args = parser.parse_args(argv)
    changed = changed_files(args.since)
    found = test_map.load()
    stale = test_map.check() if found else "there is no map"
    modules, frontend, reached = affected(changed, args.since, None if found is None or found.edited() else found)
    print("%d file(s) changed; %d test file(s) affected%s" % (
        len(changed), len(modules), "; the pages too" if frontend else ""))
    if stale:
        print("  (the coverage map: %s; python tools/test_map.py makes it again)" % stale)
    for module in modules:
        names = reached[module]
        print("  %s%s" % (module, "" if names is None else ": %d of %d tests, by the coverage map" % (
            len(names), sum(1 for t in found.tests if t[0] == module and t[1] != test_map.SHARED))))
        if args.tests and names:
            for name in sorted(names):
                print("      tests.%s.%s" % (module, name))
    if frontend:
        print("frontend: node --test tests/frontend/*.test.mjs")
    if args.run and modules:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import run_tests
        return run_tests.main(modules)
    return 0


if __name__ == "__main__":
    sys.exit(main())
