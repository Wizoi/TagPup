"""Run the Python tests across the machine's cores. The full check, or the files named.

    .venv/Scripts/python.exe tools/run_tests.py                 # every tests/test_*.py
    .venv/Scripts/python.exe tools/run_tests.py test_schema tests/test_doctor.py
    .venv/Scripts/python.exe tools/run_tests.py --jobs 4
    .venv/Scripts/python.exe tools/run_tests.py --fast          # the edit loop: tests/tiers.py's fast tier
    .venv/Scripts/python.exe tools/run_tests.py --no-slow       # fast and scenario; --all is every tier

The whole suite is the default and is what a commit and a merge run; --all says so. Each
file is in one of three tiers (tests/tiers.py): fast, scenario and slow. --fast and --no-slow
leave tiers out for the loop, never for the gate. The report ends with each tier's files,
tests and seconds.

A file that fails is run once more, alone, after the others have ended: a file that passes
then is reported "flaky: passed alone" with the first run's output kept in the log, and does
not fail the run; a file that fails twice fails. --no-retry turns that off. The line is
never left out: a test that fails under load is told of each time it does, and the log of
the first failure is kept for finding its cause.

Each test file runs as its own process: `python -m unittest tests.<file>`, as the suite
has always been run, so a file sees nothing of another's state -- and with a TAGPUP_HOME
of its own, an empty temporary folder, so that anything a test does not give a home of
its own (tests/own_home.py) still lands there and not in the checkout's data/ folder or
config.ini. The tests' libraries were in data/ under fixed names, so the files that used
it ran one after another in a lane of their own (docs/findings.md, #14). None does now,
and tests/test_tests_have_homes_of_their_own.py keeps it so; a file that named the
checkout's data/ or config.ini would still get the lane. Each file's time is kept in
tests/.durations.json (not in git), and the longest start first next time. The same file
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

from tagpup.core import processes  # noqa: E402
from tests import tiers  # noqa: E402
TESTS = os.path.join(ROOT, "tests")
DURATIONS = os.path.join(TESTS, ".durations.json")

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

#: Where a failed run's output is kept, and how many failed runs are.
FAILED_RUNS = os.path.join(ROOT, "data", "logs")
KEEP_FAILED_RUNS = 10
FAILED_RUN = re.compile(r"^run_tests-\d{8}-\d{6}(?:-\d+)?\.log$")


def test_files(names=None):
    """Module names (test_x) of the files asked for, or of every tests/test_*.py."""
    if not names:
        return sorted(name[:-3] for name in os.listdir(TESTS)
                      if name.startswith("test_") and name.endswith(".py"))
    found = []
    for name in names:
        module = os.path.splitext(os.path.basename(name))[0]
        if module.startswith("tests."):
            module = module[len("tests."):]
        if not os.path.exists(os.path.join(TESTS, module + ".py")):
            raise SystemExit("There is no test file %s." % module)
        found.append(module)
    return list(dict.fromkeys(found))


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


def load_durations():
    try:
        with open(DURATIONS, encoding="utf-8") as handle:
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


def save_durations(durations):
    try:
        with open(DURATIONS, "w", encoding="utf-8") as handle:
            json.dump(durations, handle, indent=1, sort_keys=True)
    except OSError:
        pass


def run_one(module):
    """(module, passed, tests run, seconds, output)."""
    started = time.time()
    home = tempfile.mkdtemp(prefix="tagpup_run_tests_")
    try:
        done = processes.run([sys.executable, "-m", "unittest", "tests." + module], cwd=ROOT,
                              env=dict(os.environ, TAGPUP_HOME=home),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              encoding="utf-8", errors="replace")
    finally:
        remove(home)
    output = done.stdout or ""
    ran = RAN.search(output)
    count = int(ran.group(1)) if ran else 0
    passed = done.returncode == 0 and count > 0
    if done.returncode == 0 and not count:
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
ALONE = ("test_supervisor",)


def run(modules, jobs, accept_fewer=False):
    """Run `modules`; returns [(module, passed, tests run, seconds, output)]."""
    durations = load_durations()
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
            durations[module] = round(seconds, 2)
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
    results, durations[COUNTS] = check_counts(results, durations.get(COUNTS, {}), accept_fewer)
    for module, passed, _ran, _seconds, _output in results:
        if not passed and module not in failed_already:
            print("FAILED  %s (fewer tests than last time)" % module, flush=True)
    save_durations(durations)
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="test files to run (default: all)")
    parser.add_argument("--jobs", type=int, default=max(2, (os.cpu_count() or 2) // 2),
                        help="processes at once (default: half the cores)")
    parser.add_argument("--all", action="store_true",
                        help="every tier (the default: the whole suite is what a commit runs)")
    parser.add_argument("--fast", action="store_true", help="the fast tier only, for the edit loop")
    parser.add_argument("--no-slow", action="store_true", help="the fast and scenario tiers")
    parser.add_argument("--no-retry", action="store_true",
                        help="do not run a failed file again alone")
    parser.add_argument("--accept-fewer", action="store_true",
                        help="record the count of a file that ran fewer tests than last time (tests removed on purpose)")
    args = parser.parse_args(argv)
    if sum([args.all, args.fast, args.no_slow]) > 1:
        parser.error("--all, --fast and --no-slow are one choice")
    modules = test_files(args.files)
    left_out = ()
    if not args.files and (args.fast or args.no_slow):
        left_out = ("scenario", "slow") if args.fast else ("slow",)
        modules = [m for m in modules if tiers.tier_of(m) not in left_out]
    started = time.time()
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
    report.append("Tiers run (tests/tiers.py)%s:" % (", without " + " and ".join(left_out) if left_out else ""))
    report.extend(tier_lines(results, args.jobs))
    for module, _first in flaky:
        report.append("flaky: passed alone: %s (failed in the pool; its first output is above and in the log)" % module)
    print("\n".join(report))
    if failed or flaky:
        kept = keep_failed_run("\n".join(report) + "\n")
        if kept:
            print("This run's failures are kept in %s" % kept)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
