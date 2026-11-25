"""Which tagpup/ functions each test reaches, for tools/test_map.py, which names this module first on unittest's line:

    TAGPUP_TEST_COVERAGE=<covered.json> python -m unittest tools.covered_unittest tests.test_x

The run is still `python -m unittest` (tagpup.ml.under_test knows a test run by it), its output and exit code
unittest's own, and this module holds no tests. The venv has no coverage.py (no pip), so this traces calls itself:
sys.settrace and threading.settrace with a call-only trace (no line events), recording each function of tagpup/ a frame
is entered in, by its file and its qualified name cut at the first `<` (a nested function or comprehension counts as
the function that holds it); a module's or a class's body, which runs when it is imported, is the file's `<module>`.
What runs between tests (the test file's import, setUpClass, a module's fixture) is the file's SHARED context: it serves
every test of the file.

Covered: the test's own process, every thread in it (a Flask test client, a job's thread). Not covered: a process a test
starts (a CLI run, a server): a context that starts a Python child through tagpup.core.processes is marked `children`,
and tools/affected_tests.py does not trust the map for that file. A process killed part-way writes nothing, and the map
says so for its file.
"""
import atexit
import inspect
import json
import os
import sys
import threading
import unittest

#: The variable that names the file the coverage goes to (tools/test_map.py's COVERAGE sets it for each file's
#: process; not imported from there, which would import tagpup/ before the trace starts).
OUT = "TAGPUP_TEST_COVERAGE"
#: The context of what runs outside every test: the file's import, setUpClass, a module fixture.
SHARED = "<shared>"

_ROOT = os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + os.sep  # not a path: of a photo, of this checkout
_TAGPUP = _ROOT + "tagpup" + os.sep
_STARTS = {"tagpup/core/processes.py::start", "tagpup/core/processes.py::run"}
_NOT_YET = object()
_keys = {}          # code object -> its key, or None for code outside tagpup/
_covered = {SHARED: set()}
_children = set()   # contexts that started a Python child
_status = {}
_now = {"name": SHARED, "into": _covered[SHARED]}


def key_of(filename, qualname, body=False):
    """'tagpup/store/db.py::connect' for a function of tagpup/, '...::<module>' for a module's or class's `body`, None
    for anything outside tagpup/."""
    path = os.path.normcase(os.path.abspath(filename))   # not a path: of a photo, of a source file
    if not path.startswith(_TAGPUP):
        return None
    if body:
        qualname = "<module>"
    name = []
    for part in qualname.split("."):
        if part.startswith("<"):
            break
        name.append(part)
    return "%s::%s" % (path[len(_ROOT):].replace(os.sep, "/"), ".".join(name) or "<module>")


def _starts_python(frame):
    code = frame.f_code
    args = frame.f_locals.get(code.co_varnames[0]) if code.co_argcount else None
    first = args[0] if isinstance(args, (list, tuple)) and args else args
    return isinstance(first, str) and os.path.basename(first).lower().startswith("python")


def _trace(frame, event, arg):
    code = frame.f_code
    key = _keys.get(code, _NOT_YET)
    if key is _NOT_YET:
        key = _keys[code] = key_of(code.co_filename, code.co_qualname, not code.co_flags & inspect.CO_OPTIMIZED)
    if key is not None:
        _now["into"].add(key)
        if key in _STARTS and _starts_python(frame):
            _children.add(_now["name"])
    return None


def _enter(name):
    _now["into"] = _covered.setdefault(name, set())
    _now["name"] = name


class CoveredResult(unittest.TextTestResult):
    def startTest(self, test):
        # The class's id(), not the instance's: a test may keep a value of its own called `id`.
        self._before = len(self.failures) + len(self.errors)
        _enter(type(test).id(test))
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        name = _now["name"]
        _status[name] = "failed" if len(self.failures) + len(self.errors) > self._before else "ok"
        _enter(SHARED)


def _write(path):
    sys.settrace(None)
    threading.settrace(None)
    found = {name: {"covered": sorted(keys), "status": _status.get(name, "ok"), "children": name in _children}
             for name, keys in _covered.items()}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(found, handle)


unittest.TextTestRunner.resultclass = CoveredResult
atexit.register(_write, os.environ[OUT])
threading.settrace(_trace)
sys.settrace(_trace)
