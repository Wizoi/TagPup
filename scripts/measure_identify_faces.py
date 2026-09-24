"""Time the Identify Faces screen the way a person uses it: in a browser, by clicking.

Runs entirely inside a sandbox it builds and then deletes. Nothing it does is visible
to a TagPup or TagTuner you have open:

* the library is **copied**, with SQLite's own backup API so the copy is consistent
  even while the original is being written to, and the original is opened read-only.
  (Opening a WAL database at all creates its `-wal` and `-shm` companions if nothing
  else has them open. Those are SQLite's, not this script's, and they are the same
  two files your own server creates; no row is read differently and nothing is
  written to the database itself.)
* the copy goes in a temporary directory, **not** in `data/`, so it never appears in
  the database picker of the app you are using;
* the code is snapshotted too, with its own `config.ini` pointing at the sandbox, so
  the server under test resolves databases inside the sandbox and cannot reach yours;
* the server runs as a separate process on a high port, so editing files in the repo
  does not restart it and it does not restart anything of yours.

This exists because of a specific failure. The screen was reported slow when ignoring
clusters. The cause was assumed to be the server, the server was measured in isolation
with a script that did POST /exclude then GET /person-matches, and that pair was
reported as going from 43s to 0.19s. The app never makes that pair of calls. What it
actually does is remove a few cards and then rebuild every card on the screen, and that
rebuild -- roughly ten seconds, entirely in the browser -- was never in the measurement
at all. The fix shipped, the screen was exactly as slow, and the person using it had to
say so.

So: a performance claim about this app is a claim about a click, and the only way to
support it is to perform the click. Server timings are a diagnosis, never a result.

    .venv/Scripts/python.exe scripts/measure_identify_faces.py

Take a baseline before changing anything. A harness that cannot reproduce the reported
slowness is not measuring the reported thing, and the fix that follows will be aimed at
whatever it does measure.

Headless Chromium runs faster than a real browser session -- no extensions, a fresh
profile, nothing else painting -- so the absolute numbers here come out low. Measured
against a reported ten seconds, this harness showed two. It is reliable for the
comparison, which is what a baseline is for; it is not a substitute for the number the
person actually sees.
"""
import argparse
import os
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from . import db as tagpup_db
except ImportError:  # imported as a top-level module
    import db as tagpup_db

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Copied into the sandbox. Everything the servers need to run, and nothing else.
#: tests/test_sandbox_has_all_the_code.py fails if a folder the servers import is missing.
SNAPSHOT = ("scripts", "tagpup", "gui", "gui_tagpup")


def free_port():
    """A port nothing is on, so a run can never collide with a server you are using."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def copy_code(sandbox, code_root=REPO_ROOT):
    """Snapshot the code into the sandbox, so editing the repo cannot change a run."""
    for name in SNAPSHOT:
        source = os.path.join(code_root, name)
        if os.path.isdir(source):
            shutil.copytree(source, os.path.join(sandbox, name),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def build_sandbox(source_db, sandbox):
    """A private copy of the code and the library, wired to each other.

    The database is copied through SQLite rather than the filesystem: a live library
    has a write-ahead log beside it, and copying the .db on its own captures a file
    that is missing whatever is still in the WAL.
    """
    os.makedirs(os.path.join(sandbox, "data"), exist_ok=True)
    copy_code(sandbox)

    # Its own config, so database names in URLs resolve inside the sandbox. The server
    # reads this relative to the code, which is why the code is snapshotted at all.
    with open(os.path.join(sandbox, "config.ini"), "w", encoding="utf-8") as handle:
        handle.write("[paths]\ndata_dir = %s\ndefault_db = measured.db\n"
                     % os.path.join(sandbox, "data"))

    target = os.path.join(sandbox, "data", "measured.db")
    started = time.time()
    source = tagpup_db.connect(
        tagpup_db.readonly_uri(source_db), uri=True)
    destination = tagpup_db.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    size = os.path.getsize(target) / 1e9
    print("  copied %.1f GB in %.1fs" % (size, time.time() - started))
    return target


def start_sandbox_server(sandbox, db_path, port):
    """The server under test, as its own process, running the snapshotted code."""
    launcher = os.path.join(sandbox, "run_measured_server.py")
    with open(launcher, "w", encoding="utf-8") as handle:
        handle.write(
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "from tuner_server import start_server\n"
            "start_server(port=%d, db_path=%r, gui_dir=%r)\n"
            % (os.path.join(sandbox, "scripts"), port, db_path,
               os.path.join(sandbox, "gui")))

    process = subprocess.Popen(
        [sys.executable, launcher],
        cwd=sandbox,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return process
        except OSError:
            if process.poll() is not None:
                raise RuntimeError(
                    "the sandbox server exited before it was ready") from None
            time.sleep(0.5)
    process.kill()
    raise RuntimeError("the sandbox server never came up on port %d" % port)


def remove_sandbox(sandbox):
    """Delete the sandbox, and say so if it cannot be.

    Windows releases a dead process's file handles a moment after it exits, so the
    first attempt can fail on a database the server still had open. Retried rather
    than ignored: this directory holds a copy of the whole library, and the first
    version of this quietly left 2.7 GB in the temp directory every run because the
    failure was swallowed.
    """
    for _attempt in range(10):
        shutil.rmtree(sandbox, ignore_errors=True)
        if not os.path.exists(sandbox):
            return True
        time.sleep(0.5)

    size = 0
    for root, _dirs, files in os.walk(sandbox):
        size += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    print("\nWARNING: could not delete the sandbox. %.1f GB left at:\n  %s"
          % (size / 1e9, sandbox), file=sys.stderr)
    return False


def first_ignorable_cluster(page):
    """The faces of the first group still offering an Ignore Cluster button."""
    return page.evaluate("""() => {
        const section = [...document.querySelectorAll('.matching-group-section')]
            .find(s => [...s.querySelectorAll('button')]
                .some(b => b.textContent.includes('Ignore Cluster')));
        if (!section) return null;
        return {
            ids: [...section.querySelectorAll('[data-face-id]')].map(el => el.dataset.faceId),
            title: section.querySelector('.matching-group-title').textContent,
        };
    }""")


def ignore_that_cluster(page):
    page.evaluate("""() => {
        const section = [...document.querySelectorAll('.matching-group-section')]
            .find(s => [...s.querySelectorAll('button')]
                .some(b => b.textContent.includes('Ignore Cluster')));
        [...section.querySelectorAll('button')]
            .find(b => b.textContent.includes('Ignore Cluster')).click();
    }""")
    page.wait_for_timeout(200)
    if page.query_selector("#ignore-confirm-modal:not(.hidden)"):
        page.click("#btn-ignore-confirm-ok")
        page.wait_for_timeout(150)


def drive(url, args):
    from playwright.sync_api import sync_playwright

    timings = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})

        started = time.time()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector("#matching-faces-grid [data-face-id]",
                               timeout=args.timeout * 1000)
        first_paint = time.time() - started

        # Let the crops settle, so the clicks are not timed against image loading.
        page.wait_for_timeout(4000)
        cards = page.eval_on_selector_all(
            "#matching-faces-grid [data-face-id]", "els => els.length")

        print("\nopening the grid")
        print("  first faces on screen        : %7.2fs" % first_paint)
        print("  cards rendered               : %d\n" % cards)

        for round_number in range(1, args.rounds + 1):
            target = first_ignorable_cluster(page)
            if not target or not target["ids"]:
                print("  nothing left to ignore")
                break

            ignore_that_cluster(page)

            # The stopwatch starts where the person's last click is: choosing a reason
            # is what sends the request and hands the work to the page.
            picker = page.query_selector("#exclude-reason-modal:not(.hidden)")
            started = time.time()
            if picker:
                page.eval_on_selector("#exclude-reason-choices button", "b => b.click()")

            # Usable again means two things, and the second is the one that was missed:
            # the cards are gone, AND the main thread will run something. A page busy
            # rebuilding twenty thousand cards satisfies neither, but a page that has
            # merely removed the cards first satisfies only the first.
            page.wait_for_function(
                """(ids) => ids.every(id =>
                       !document.querySelector(`#matching-faces-grid [data-face-id="${id}"]`))""",
                arg=target["ids"],
                timeout=args.timeout * 1000,
            )
            page.evaluate("() => new Promise(r => requestAnimationFrame(() => r(1)))")
            elapsed = time.time() - started
            timings.append(elapsed)

            print("  round %-2d %-40s %2d faces -> %6.2fs"
                  % (round_number, target["title"][:40], len(target["ids"]), elapsed))

        browser.close()
    return timings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="data/photo_index.db",
                        help="the library to copy; opened read-only and never written")
    parser.add_argument("--person", default="Unknown Faces")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=600, help="seconds")
    parser.add_argument("--headed", action="store_true",
                        help="show the browser; slower and closer to what a person sees")
    parser.add_argument("--keep", action="store_true",
                        help="leave the sandbox in place to look at afterwards")
    args = parser.parse_args()

    if not os.path.exists(args.source):
        parser.error("%s does not exist" % args.source)

    sandbox = tempfile.mkdtemp(prefix="tagpup_measure_")
    port = free_port()
    server = None
    try:
        print("sandbox : %s" % sandbox)
        print("source  : %s (read-only)" % args.source)
        db_path = build_sandbox(args.source, sandbox)

        server = start_sandbox_server(sandbox, db_path, port)
        url = ("http://127.0.0.1:%d/measured/?mode=unmatched-faces&person=%s"
               % (port, args.person.replace(" ", "+")))
        print("  serving on port %d, isolated from anything else you have running" % port)

        timings = drive(url, args)
        if timings:
            print("\nclick to usable: median %.2fs, worst %.2fs, over %d rounds"
                  % (statistics.median(timings), max(timings), len(timings)))
    finally:
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        if args.keep:
            print("\nsandbox left at %s" % sandbox)
        else:
            remove_sandbox(sandbox)


if __name__ == "__main__":
    main()
