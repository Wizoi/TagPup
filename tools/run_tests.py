"""Run the Python tests across the machine's cores. The full check, or the files named.

    .venv/Scripts/python.exe tools/run_tests.py                 # every tests/test_*.py
    .venv/Scripts/python.exe tools/run_tests.py test_schema tests/test_doctor.py
    .venv/Scripts/python.exe tools/run_tests.py --jobs 4

Each test file runs as its own process: `python -m unittest tests.<file>`, as the suite
has always been run, so a file sees nothing of another's state -- and with a TAGPUP_HOME
of its own, an empty temporary folder, so that anything a test does not give a home of
its own (tests/own_home.py) still lands there and not in the checkout's data/ folder or
config.ini. The tests' libraries were in data/ under fixed names, so the files that used
it ran one after another in a lane of their own (docs/findings.md, #14). None does now,
and tests/test_tests_have_homes_of_their_own.py keeps it so; a file that named the
checkout's data/ or config.ini would still get the lane. Each file's time is kept in
tests/.durations.json (not in git), and the longest start first next time.

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


def run(modules, jobs):
    """Run `modules`; returns [(module, passed, tests run, seconds, output)]."""
    durations = load_durations()
    order = sorted(modules, key=lambda m: -durations.get(m, 1.0))
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

    # The lane has a process of its own while it has anything to run.
    spread_jobs = max(1, jobs - 1) if shared else jobs
    with concurrent.futures.ThreadPoolExecutor(max_workers=spread_jobs) as pool:
        lane_thread = threading.Thread(target=lane)
        lane_thread.start()
        for future in concurrent.futures.as_completed([pool.submit(run_one, m) for m in spread]):
            record(future.result())
        lane_thread.join()
    save_durations(durations)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="test files to run (default: all)")
    parser.add_argument("--jobs", type=int, default=max(2, (os.cpu_count() or 2) // 2),
                        help="processes at once (default: half the cores)")
    args = parser.parse_args(argv)
    modules = test_files(args.files)
    started = time.time()
    results = run(modules, args.jobs)
    failed = [r for r in results if not r[1]]
    report = []
    for module, _passed, _ran, _seconds, output in sorted(failed):
        report.append("\n" + "=" * 70 + "\n" + module + "\n" + "=" * 70)
        report.append(output.rstrip())
    report.append("\n%d file(s), %d test(s), %d file(s) failed, in %.0fs with %d processes" % (
        len(results), sum(r[2] for r in results), len(failed), time.time() - started, args.jobs))
    print("\n".join(report))
    if failed:
        kept = keep_failed_run("\n".join(report) + "\n")
        if kept:
            print("This run's failures are kept in %s" % kept)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
