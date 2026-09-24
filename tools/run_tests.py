"""Run the Python tests across the machine's cores. The full check, or the files named.

    .venv/Scripts/python.exe tools/run_tests.py                 # every tests/test_*.py
    .venv/Scripts/python.exe tools/run_tests.py test_schema tests/test_doctor.py
    .venv/Scripts/python.exe tools/run_tests.py --jobs 4

Each test file runs as its own process: `python -m unittest tests.<file>`, as the suite
has always been run, so a file sees nothing of another's state. The files that use the
checkout's own data/ folder or config.ini run one after another in a lane of their
own, since their libraries have fixed names there (docs/findings.md, #14); the rest
share the other cores. Each file's time is kept in tests/.durations.json (not in git),
and the longest start first next time.

Prints each file that failed, with its output, and one line of totals. Exits 1 when
any file failed.
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
DURATIONS = os.path.join(TESTS, ".durations.json")

#: A test file that reads or writes the checkout's data/ folder or config.ini.
SHARED = re.compile(r"""(WORKSPACE_DIR|REPO_ROOT|ROOT),\s*["']data["']|os\.path\.join\(["']data["']|["']data/|config\.ini""")

#: The summary unittest ends with: "Ran 12 tests in 0.3s".
RAN = re.compile(r"^Ran (\d+) tests? in", re.M)


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
    if module in seen:
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
    done = subprocess.run([sys.executable, "-m", "unittest", "tests." + module], cwd=ROOT,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          encoding="utf-8", errors="replace")
    output = done.stdout or ""
    ran = RAN.search(output)
    return module, done.returncode == 0, int(ran.group(1)) if ran else 0, time.time() - started, output


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

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs - 1)) as pool:
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
    for module, _passed, _ran, _seconds, output in sorted(failed):
        print("\n" + "=" * 70 + "\n" + module + "\n" + "=" * 70)
        print(output.rstrip())
    print("\n%d file(s), %d test(s), %d file(s) failed, in %.0fs with %d processes" % (
        len(results), sum(r[2] for r in results), len(failed), time.time() - started, args.jobs))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
