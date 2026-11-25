"""Run the Python tests across the machine's cores. The full check, or the files named.

    .venv/Scripts/python.exe tools/run_tests.py                 # every tests/test_*.py
    .venv/Scripts/python.exe tools/run_tests.py test_schema tests/test_doctor.py
    .venv/Scripts/python.exe tools/run_tests.py --jobs 4
    .venv/Scripts/python.exe tools/run_tests.py --fast          # the edit loop: tests/tiers.py's fast tier
    .venv/Scripts/python.exe tools/run_tests.py --no-slow       # fast and scenario; --all is every tier
    .venv/Scripts/python.exe tools/run_tests.py --owner people  # every Python file of a domain owner (tests/owners.py)
    .venv/Scripts/python.exe tools/run_tests.py --scenarios files   # only that owner's scenario files
    .venv/Scripts/python.exe tools/run_tests.py --owner people --size sqlite   # its files that start no process
    .venv/Scripts/python.exe tools/run_tests.py --slowest 20    # the 20 slowest files and tests recorded; runs nothing

The whole suite is the default and is what a commit and a merge run; --all says so. Each
file is in one of four tiers (tests/tiers.py): fast, scenario, slow and page, the last the page tests
(tests/frontend/*.test.mjs, each run as `node --test <file>`; harness.mjs and the other helpers are not tests).
--fast and --no-slow leave tiers out for the loop, never for the gate. The report ends with each tier's files,
tests and seconds, and each owner's (tests/owners.py gives every test file one owner). --owner and --scenarios
run one owner's Python files, and print the `node --test` command for that owner's page tests, which they leave out.
--size runs the files of that size or smaller (tests/owners.py SIZES: pure, then sqlite, subprocess, browser; a
page test is browser): `--size sqlite` is the pure and sqlite files, a loop of seconds; it goes with --owner,
--scenarios, --fast and file names.

A file that fails is run once more, alone, after the others have ended: a file that passes
then is reported "flaky: passed alone" with the first run's output kept in the log, and does
not fail the run; a file that fails twice fails. --no-retry turns that off. The line is
never left out: a test that fails under load is told of each time it does, and the log of
the first failure is kept for finding its cause.

Each test file runs as its own process: `python -m unittest tools.timed_unittest tests.<file>`, as the suite
has always been run with the module that writes each test's time named first (a page test: `node --test
tests/frontend/<file>`), so a file sees nothing of another's state -- and with a TAGPUP_HOME
of its own, an empty temporary folder, so that anything a test does not give a home of
its own (tests/own_home.py) still lands there and not in the checkout's data/ folder or
config.ini. Each file's process is given ExifTool's answers kept on this machine, running and keeping only the commands
not kept before; --real-exiftool runs every command and keeps its answer, the gate run before a release, with the
product's own DocumentID mint and ExifTool start (tests/exiftool_replay.py; a file in its STAYS_REAL always runs ExifTool). The run ends with the counts of each, and the
kept answers trimmed to their cap.
Every file's process is also given the run's corpus folder (TAGPUP_TEST_CORPUS, tests/corpus.py):
each photo, clip and library a test asks of it is made there once and copied. The tests' libraries were in data/ under fixed names, so the files that used
it ran one after another in a lane of their own (docs/findings.md, #14). None does now,
and tests/test_tests_have_homes_of_their_own.py keeps it so; a file that named the
checkout's data/ or config.ini would still get the lane. Every run, green or not, keeps each
file's time in tests/.durations.json and each Python test's in tests/.test_durations.json (both
generated here, not in git, never edited by hand), and the longest start first next time. A
file run again alone keeps its second time; the key of a file that no longer exists goes. Each
save merges into the file as it is on disk under a lock, and replaces it whole, so a run stopped
part-way leaves the last whole file and two runs at once both land. The same file
keeps how many tests each file ran when it passed: a file that passes having run fewer than
that fails, naming both counts (a run that counted 1,427 where the last counted 1,469 was the
shape of the original flake, #101; #290). Tests removed on purpose: `--accept-fewer` records
the new count.

Prints each file that failed, with its output, and one line of totals, and keeps the
same in data/logs/run_tests-<time>.log (not in git; the last ten failed runs), naming
the file: a failure that happens once in six runs is otherwise gone with the console
(docs/findings.md, #101). A file that exits 0 having run no tests -- it ended before
unittest's summary, or holds none -- fails. Exits 1 when any file failed.
"""
import argparse
import concurrent.futures
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tagpup.core import byte_lock, processes  # noqa: E402
from tests import corpus, exiftool_replay, owners, tiers  # noqa: E402
TESTS = os.path.join(ROOT, "tests")
FRONTEND = os.path.join(TESTS, "frontend")
DURATIONS = os.path.join(TESTS, ".durations.json")
#: Each Python test's seconds in its file's last run: {module: {test id: seconds}}.
TEST_DURATIONS = os.path.join(TESTS, ".test_durations.json")
#: The key both files say what they are under.
GENERATED = "_generated"
NOTE = "Written by tools/run_tests.py on every run (not in git); never edit it by hand."
#: Named first on each Python file's unittest command line: it writes each test's seconds to TIMES.
TIMED = "tools.timed_unittest"
TIMES = "TAGPUP_TEST_TIMES"
#: How long a save waits for another run's save (milliseconds, as a rule) before it gives up and says so.
SAVE_WAIT = 30.0
#: What each file's process does with ExifTool's answers (exiftool_replay.REPLAY or RECORD), and the file the processes
#: add their counts to: main's.
EXIFTOOL = {"mode": None, "counts": None}

#: A test file that names the checkout's data/ folder or config.ini: joined to the
#: checkout's own folder, relative to the working directory (the checkout, as files are
#: run here), or config.ini by name. A test home's old config.ini is written by
#: tests/own_home.OwnHome.write_old_config.
SHARED = re.compile(
    r"""\b(?:WORKSPACE_DIR|REPO_ROOT|ROOT|PROJECT_ROOT|project_root|CODE_ROOT)\s*,\s*["'](?:data|config\.ini)["']"""
    r"""|os\.path\.join\(\s*["']data["']|["']data(?:/|\\\\)"""
    r"""|["']config\.ini["']""")

#: Test files SHARED matches that read the code, not the checkout's data or settings:
#: their subject is the text "config.ini" in the sources.
READS_CODE_NOT_DATA = frozenset({"test_config_single_owner"})

#: The summary unittest ends with: "Ran 12 tests in 0.3s".
RAN = re.compile(r"^Ran (\d+) tests? in", re.M)
#: The count node's spec reporter ends with: "(i) tests 12".
NODE_RAN = re.compile(r"^\S+ tests (\d+)\s*$", re.M)
#: A page test that skips itself under a plain `node --test`, its tests run by a Python scenario against the real
#: apps (tests/test_pages_against_the_real_server.py): five files, each counting 0 tests.
NODE_SKIPPED = re.compile(r"# SKIP\s*$", re.M)

#: Where a failed run's output is kept, and how many failed runs are.
FAILED_RUNS = os.path.join(ROOT, "data", "logs")
KEEP_FAILED_RUNS = 10
FAILED_RUN = re.compile(r"^run_tests-\d{8}-\d{6}(?:-\d+)?\.log$")


def test_files(names=None):
    """Module names (test_x) and page test names (x.test.mjs) of the files asked for, or of every tests/test_*.py and
    tests/frontend/*.test.mjs."""
    if not names:
        return sorted(name[:-3] for name in os.listdir(TESTS)
                      if name.startswith("test_") and name.endswith(".py")) + page_files()
    found = []
    for name in names:
        if name.endswith(".test.mjs"):
            if not os.path.exists(os.path.join(FRONTEND, os.path.basename(name))):
                raise SystemExit("There is no page test %s." % name)
            found.append(os.path.basename(name))
            continue
        module = os.path.splitext(os.path.basename(name))[0]
        if module.startswith("tests."):
            module = module[len("tests."):]
        if not os.path.exists(os.path.join(TESTS, module + ".py")):
            raise SystemExit("There is no test file %s." % module)
        found.append(module)
    return list(dict.fromkeys(found))


def page_files():
    """tests/frontend/*.test.mjs by name: the glob, never the folder, which holds harness.mjs and the other helpers."""
    return sorted(name for name in os.listdir(FRONTEND) if name.endswith(".test.mjs"))


def is_test_file(name):
    """Is there still a test file of this name (test_x, or x.test.mjs)?"""
    if name.endswith(".test.mjs"):
        return os.path.exists(os.path.join(FRONTEND, name))
    return os.path.exists(os.path.join(TESTS, name + ".py"))


#: `from test_x import` or `import test_x`: a test file built on another's classes, and
#: on its library with them.
IMPORTS_A_TEST = re.compile(r"^\s*(?:from|import)\s+(test_\w+)", re.M)


def uses_the_checkout(module, seen=None):
    """Does this test file use the checkout's data/ or config.ini -- itself, or through a
    test file it imports? test_tag_view_api takes its library from test_tuner_server_api's
    base class, and the two ran at once on one file."""
    seen = set() if seen is None else seen
    if module in seen or module in READS_CODE_NOT_DATA:
        return False
    seen.add(module)
    path = os.path.join(TESTS, module + ".py")
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    if SHARED.search(source):
        return True
    return any(uses_the_checkout(imported, seen) for imported in IMPORTS_A_TEST.findall(source))


def load_durations(path=None):
    """tests/.durations.json (or `path`) as a dict, {} when there is none yet."""
    try:
        with open(path or DURATIONS, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


#: The key of tests/.durations.json that holds how many tests each file ran: {module: count}.
COUNTS = "_tests"


def check_counts(results, counts, accept_fewer=False):
    """(results, counts): a passing file that ran fewer tests than `counts` says it did last time becomes a
    failure, and keeps the count it fell from; every other passing file's count is recorded."""
    counts = dict(counts)
    checked = []
    for result in results:
        module, passed, ran, seconds, output = result
        before = counts.get(module, 0)
        if passed and ran < before and not accept_fewer:
            result = (module, False, ran, seconds, output + (
                "\n[run_tests] ran %d tests where the last passing run ran %d: a file that stopped early, or tests "
                "removed (--accept-fewer records the new count) (#290)" % (ran, before)))
        elif passed:
            counts[module] = ran
        checked.append(result)
    return checked, counts


def save_durations(durations, tests=None, prune=False):
    """Merge `durations` ({file: seconds}, and COUNTS: {file: count} for the counts that changed) into
    tests/.durations.json, and `tests` ({module: {test id: seconds}}, each module's whole) into
    tests/.test_durations.json, as each is on disk now; `prune` drops the keys of files that no longer exist."""
    # One save at a time between reading the times and replacing them: a second run's save waits its turn, so neither
    # drops the other's. The system lets go of the lock when a run ends however it ends: a run killed leaves none.
    held = byte_lock.Lock(DURATIONS + ".lock")
    if not held.acquire(wait=SAVE_WAIT):
        print("note: this run's times are not kept: another run held %s.lock for %ds" % (DURATIONS, SAVE_WAIT),
              flush=True)
        return
    try:
        _merge(DURATIONS, durations, prune)
        if tests or prune:
            _merge(TEST_DURATIONS, tests or {}, prune)
    except OSError as error:
        print("note: could not keep this run's times in %s: %s" % (DURATIONS, error), flush=True)
    finally:
        held.release()


def _merge(path, updates, prune):
    data = load_durations(path)
    for key, value in updates.items():
        data[key] = dict(data.get(COUNTS, {}), **value) if key == COUNTS else value
    if prune:
        data = {key: value for key, value in data.items() if key.startswith("_") or is_test_file(key)}
        if COUNTS in data:
            data[COUNTS] = {key: value for key, value in data[COUNTS].items() if is_test_file(key)}
    data[GENERATED] = NOTE
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
    for attempt in range(50):
        try:
            os.replace(temp, path)   # whole, or not at all: a run stopped here leaves the last whole file
            return
        except PermissionError:   # Windows: a run starting this moment has it open to read its order
            if attempt == 49:
                raise
            time.sleep(0.02)


#: Each Python test's seconds from its file's latest process, {module: {test id: seconds}}, until the run saves them.
_test_times = {}
_test_times_lock = threading.Lock()


def _keep_test_times(module, path):
    try:
        with open(path, encoding="utf-8") as handle:
            seconds = json.load(handle)
    except (OSError, ValueError):   # the process was killed before unittest ended: the file's own time still counts
        return
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
    prefix = "tests.%s." % module
    with _test_times_lock:
        _test_times[module] = {name[len(prefix):] if name.startswith(prefix) else name: s
                               for name, s in seconds.items()}


def _take_test_times(modules):
    with _test_times_lock:
        return {m: _test_times.pop(m) for m in modules if m in _test_times}


def run_one(module):
    """(module, passed, tests run, seconds, output). A Python file's tests' times wait in _test_times for the save."""
    started = time.time()
    home = tempfile.mkdtemp(prefix="tagpup_run_tests_")
    times = home + ".times.json"
    page = module.endswith(".test.mjs")
    command = (["node", "--test", "--test-reporter=spec", os.path.join("tests", "frontend", module)] if page
               else [sys.executable, "-m", "unittest", TIMED, "tests." + module])
    env = dict(os.environ, TAGPUP_HOME=home, **{TIMES: times})
    env.pop(exiftool_replay.ENV, None)
    if EXIFTOOL["mode"] and module not in exiftool_replay.STAYS_REAL:
        env.update({exiftool_replay.ENV: EXIFTOOL["mode"], exiftool_replay.COUNTS_ENV: EXIFTOOL["counts"]})
    try:
        done = processes.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              encoding="utf-8", errors="replace")
    finally:
        remove(home)
        if not page:
            _keep_test_times(module, times)
    output = done.stdout or ""
    ran = NODE_RAN.findall(output)[-1:] if page else RAN.search(output)
    count = int(ran[0] if page else ran.group(1)) if ran else 0
    passed = done.returncode == 0 and (count > 0 or bool(page and ran and NODE_SKIPPED.search(output)))
    if done.returncode == 0 and not passed:
        output += ("\n[run_tests] exited 0 having run no tests: it %s (#101)"
                   % ("holds none" if ran else "ended before unittest's summary"))
    return module, passed, count, time.time() - started, output


def remove(folder):
    """Delete a file's home once its process has ended, retrying while Windows lets go
    of what it held; say so if it cannot be."""
    for _attempt in range(20):
        shutil.rmtree(folder, ignore_errors=True)
        if not os.path.exists(folder):
            return
        time.sleep(0.25)
    print("note: could not delete %s" % folder, flush=True)


def keep_failed_run(text):
    """Write a failed run's report to FAILED_RUNS and drop all but the latest
    KEEP_FAILED_RUNS; returns the file's path, or None if it could not be written."""
    try:
        os.makedirs(FAILED_RUNS, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(FAILED_RUNS, "run_tests-%s.log" % stamp)
        suffix = 1
        while os.path.exists(path):
            suffix += 1
            path = os.path.join(FAILED_RUNS, "run_tests-%s-%d.log" % (stamp, suffix))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        runs = sorted(n for n in os.listdir(FAILED_RUNS) if FAILED_RUN.match(n))   # by their time
        for old in runs[:-KEEP_FAILED_RUNS]:
            if os.path.join(FAILED_RUNS, old) != path:
                os.remove(os.path.join(FAILED_RUNS, old))
        return path
    except OSError as error:
        print("note: could not keep this run's output in %s: %s" % (FAILED_RUNS, error), flush=True)
        return None


#: Files that run in a lane of their own, one at a time, beside the pool and not in it: their deadlines are real
#: time -- they start server processes and wait for them -- and a pool slot's share of a loaded machine made two
#: of them fail that wait (docs/findings.md, #476). Beside the pool, the suite takes no longer than it did.
ALONE = ()   # none now; the supervisor's tests were the two that needed it


def run(modules, jobs, accept_fewer=False):
    """Run `modules`; returns [(module, passed, tests run, seconds, output)]."""
    durations = load_durations()
    measured = {}
    order = sorted(modules, key=lambda m: -durations.get(m, 1.0))
    alone = [m for m in order if m in ALONE]
    order = [m for m in order if m not in ALONE]
    shared = [m for m in order if uses_the_checkout(m)]
    spread = [m for m in order if m not in shared]
    results = []
    lock = threading.Lock()

    def record(result):
        with lock:
            results.append(result)
            module, passed, _ran, seconds, _output = result
            measured[module] = round(seconds, 2)
            if not passed:
                print("FAILED  %s (%.1fs)" % (module, seconds), flush=True)

    def lane():
        for module in shared:
            record(run_one(module))

    def solo():
        for module in alone:
            record(run_one(module))

    # Each lane has a process of its own while it has anything to run.
    spread_jobs = max(1, jobs - (1 if shared else 0) - (1 if alone else 0))
    with concurrent.futures.ThreadPoolExecutor(max_workers=spread_jobs) as pool:
        lane_thread = threading.Thread(target=lane)
        lane_thread.start()
        solo_thread = threading.Thread(target=solo)
        solo_thread.start()
        for future in concurrent.futures.as_completed([pool.submit(run_one, m) for m in spread]):
            record(future.result())
        lane_thread.join()
        solo_thread.join()
    failed_already = {r[0] for r in results if not r[1]}
    before = durations.get(COUNTS, {})
    results, counts = check_counts(results, before, accept_fewer)
    measured[COUNTS] = {m: n for m, n in counts.items() if before.get(m) != n}
    for module, passed, _ran, _seconds, _output in results:
        if not passed and module not in failed_already:
            print("FAILED  %s (fewer tests than last time)" % module, flush=True)
    save_durations(measured, _take_test_times(modules), prune=True)
    return results


def rerun_alone(results, accept_fewer=False):
    """Run each failed file once more, one at a time, now that the pool has ended.
    Returns (results with a pass in place of the failure, [(module, first output)]).
    A rerun that passes having run fewer tests than the last passing run did is still
    a failure (#290): a file that stops early is the flake this must not hide."""
    counts = load_durations().get(COUNTS, {})
    flaky = []
    again = []
    for result in results:
        module, passed, _ran, seconds, first = result
        if passed:
            again.append(result)
            continue
        print("RERUN   %s alone" % module, flush=True)
        second = check_counts([run_one(module)], counts, accept_fewer)[0][0]
        save_durations({module: round(second[3], 2)}, _take_test_times([module]))   # the time it took alone
        if second[1]:
            flaky.append((module, first))
            again.append((module, True, second[2], seconds + second[3], second[4]))
        else:
            again.append((module, False, second[2], seconds + second[3],
                          first.rstrip() + "\n\n[run_tests] failed again, alone:\n" + second[4]))
    return again, flaky


def tier_lines(results, jobs):
    """One line per tier that ran: files, tests, summed seconds and that over the processes."""
    lines = []
    for tier in tiers.TIERS:
        mine = [r for r in results if tiers.tier_of(r[0]) == tier]
        if mine:
            total = sum(r[3] for r in mine)
            lines.append("  %-8s %3d file(s) %5d test(s) %6.0fs of file time (about %.0fs across %d processes)" % (
                tier, len(mine), sum(r[2] for r in mine), total, total / jobs, jobs))
    return lines


def owner_lines(results):
    """One line per owner that ran: files, tests and summed seconds (tests/owners.py)."""
    lines = []
    for owner in owners.OWNERS:
        mine = [r for r in results if owners.FILES.get(r[0], ("",))[0] == owner]
        if mine:
            lines.append("  %-8s %3d file(s) %5d test(s) %6.0fs of file time" % (
                owner, len(mine), sum(r[2] for r in mine), sum(r[3] for r in mine)))
    return lines


def slowest(count):
    """The `count` slowest files and Python tests the runs have recorded; runs nothing."""
    files = sorted(((s, n) for n, s in load_durations().items() if not n.startswith("_")), reverse=True)
    lines = ["The %d slowest test files (tests/.durations.json, each one's last run):" % min(count, len(files))]
    lines += ["  %7.1fs  %-8s %s" % (s, tiers.tier_of(n), n) for s, n in files[:count]]
    tests = sorted(((s, m, t) for m, each in load_durations(TEST_DURATIONS).items() if not m.startswith("_")
                    for t, s in each.items()), reverse=True)
    lines.append("The %d slowest tests (tests/.test_durations.json):" % min(count, len(tests)))
    lines += ["  %7.2fs  %s  %s" % (s, m, t) for s, m, t in tests[:count]]
    return lines


def exiftool_lines(path):
    """The run's ExifTool counts, summed over the lines its processes added to `path` (tests/exiftool_replay.py)."""
    total, why = {}, {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                each = json.loads(line)
                for name, n in each["counts"].items():
                    total[name] = total.get(name, 0) + n
                for name, n in each["why"].items():
                    why[name] = why.get(name, 0) + n
    except (OSError, ValueError):
        return ["ExifTool: no counts were kept"]
    lines = ["ExifTool, %s, %s: %d command(s), %d answered from kept answers, %d run, %d process start(s), %d kept" % (
        EXIFTOOL["mode"], exiftool_replay.cache_root(), total.get("commands", 0), total.get("hits", 0),
        total.get("misses", 0), total.get("starts", 0), total.get("kept", 0))]
    lines += ["  %6d  %s" % (n, name) for name, n in sorted(why.items(), key=lambda item: -item[1])]
    (left, size), (gone, freed) = exiftool_replay.trim()
    lines.append("  kept answers: %d entries, %.0f MB%s" % (left, size / 1e6, (
        "; %d used longest ago removed (%.0f MB, the cap %.0f MB)" % (gone, freed / 1e6, exiftool_replay.CAP / 1e6)
        if gone else "")))
    return lines


def node_command(owner, scenarios_only=False):
    """The command for an owner's page tests (tests/frontend/*.test.mjs): they run under node, not here."""
    names = [n for n in owners.page_files_of(owner) if not scenarios_only or owners.FILES[n][1] == "scenario"]
    return "node --test " + " ".join("tests/frontend/" + n for n in names) if names else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="test files to run (default: all)")
    parser.add_argument("--jobs", type=int, default=max(2, (os.cpu_count() or 2) // 2),
                        help="processes at once (default: half the cores)")
    parser.add_argument("--all", action="store_true",
                        help="every tier (the default: the whole suite is what a commit runs)")
    parser.add_argument("--fast", action="store_true", help="the fast tier only, for the edit loop")
    parser.add_argument("--no-slow", action="store_true", help="the fast and scenario tiers (no slow, no page)")
    parser.add_argument("--owner", choices=owners.OWNERS, help="every Python test file of this domain owner")
    parser.add_argument("--scenarios", choices=owners.OWNERS, help="only this owner's scenario files")
    parser.add_argument("--size", choices=owners.SIZE_ORDER,
                        help="only the files of this size or smaller (tests/owners.py SIZES): sqlite is pure and sqlite")
    parser.add_argument("--no-retry", action="store_true",
                        help="do not run a failed file again alone")
    parser.add_argument("--accept-fewer", action="store_true",
                        help="record the count of a file that ran fewer tests than last time (tests removed on purpose)")
    parser.add_argument("--real-exiftool", action="store_true",
                        help="run the real ExifTool for every command and keep each answer, with the product's own "
                             "DocumentID mint and ExifTool start (the gate before a release); "
                             "without it, a command kept before is answered from this machine's kept answers")
    parser.add_argument("--slowest", type=int, metavar="N",
                        help="print the N slowest files and tests of the recorded runs, and run nothing")
    args = parser.parse_args(argv)
    if args.slowest is not None:
        print("\n".join(slowest(args.slowest)))
        return 0
    if sum([args.all, args.fast, args.no_slow]) > 1:
        parser.error("--all, --fast and --no-slow are one choice")
    if args.owner and args.scenarios and args.owner != args.scenarios:
        parser.error("--owner and --scenarios name one owner")
    chosen = args.owner or args.scenarios
    if chosen and args.files:
        parser.error("--owner and --scenarios take the place of file names")
    modules = owners.files_of(chosen, "scenario" if args.scenarios else None) if chosen else test_files(args.files)
    node = node_command(chosen, bool(args.scenarios)) if chosen and args.size in (None, "browser") else None
    left_out = ()
    if not args.files and (args.fast or args.no_slow):
        # The page tier is about 2,550 file-seconds (about 320 s across 8 processes, 2026-11-24): twice the fast
        # tier's wall, so the loop leaves it out, and the gate runs it.
        left_out = ("scenario", "slow", "page") if args.fast else ("slow", "page")
        modules = [m for m in modules if tiers.tier_of(m) not in left_out]
    if args.size:
        modules = [m for m in modules if owners.within(m, args.size)]
    started = time.time()
    EXIFTOOL["mode"] = exiftool_replay.RECORD if args.real_exiftool else exiftool_replay.REPLAY
    counted = tempfile.mkdtemp(prefix="tagpup_exiftool_counts_")
    EXIFTOOL["counts"] = os.path.join(counted, "counts.jsonl")
    with corpus.for_run():   # each photo, clip and library made once for the run's files (tests/corpus.py)
        results = run(modules, args.jobs, args.accept_fewer)
        flaky = []
        if not args.no_retry and any(not r[1] for r in results):
            results, flaky = rerun_alone(results, args.accept_fewer)
    failed = [r for r in results if not r[1]]
    report = []
    for module, first in flaky:
        report.append("\n" + "=" * 70 + "\nflaky: passed alone: " + module + "\n" + "=" * 70)
        report.append(first.rstrip())
    for module, _passed, _ran, _seconds, output in sorted(failed):
        report.append("\n" + "=" * 70 + "\n" + module + "\n" + "=" * 70)
        report.append(output.rstrip())
    report.append("\n%d file(s), %d test(s), %d file(s) failed, in %.0fs with %d processes" % (
        len(results), sum(r[2] for r in results), len(failed), time.time() - started, args.jobs))
    if args.size:
        report.append("Sizes run (tests/owners.py SIZES): %s" % ", ".join(
            owners.SIZE_ORDER[: owners.SIZE_ORDER.index(args.size) + 1]))
    report.append("Tiers run (tests/tiers.py)%s:" % (", without " + " and ".join(left_out) if left_out else ""))
    report.extend(tier_lines(results, args.jobs))
    report.append("Owners run (tests/owners.py):")
    report.extend(owner_lines(results))
    report.extend(exiftool_lines(EXIFTOOL["counts"]))
    remove(counted)
    if node:
        report.append("Page tests of %s (run beside this): %s" % (chosen, node))
    for module, _first in flaky:
        report.append("flaky: passed alone: %s (failed in the pool; its first output is above and in the log)" % module)
    print("\n".join(report))
    if failed or flaky:
        kept = keep_failed_run("\n".join(report) + "\n")
        if kept:
            print("This run's failures are kept in %s" % kept)
    return 1 if failed else 0


if __name__ == "__main__":
    # node's reporter writes marks (and a page test's text) a Windows console's code page has no letter for.
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.exit(main())
