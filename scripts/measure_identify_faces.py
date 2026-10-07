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
* the code is snapshotted too, and the server runs with the sandbox as its home
  (TAGPUP_HOME), so it resolves databases inside the sandbox's data/ and cannot reach
  yours;
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
    .venv/Scripts/python.exe scripts/measure_identify_faces.py --action new-person
    .venv/Scripts/python.exe scripts/measure_identify_faces.py --action people-by-face

Three clicks are measured, one per run: By face in Review People's list (`--action people-by-face`:
until the crops in view are painted), Ignore Cluster (the default), and New Person
(`--action new-person`: select one face in the grid, click New Person, until the faces
it offers are on screen and the page runs again; docs/findings.md, #5). The first New
Person of a run also reads the pool of nameless faces, so it is reported apart.

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
import socket
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import processes  # noqa: E402
# The sandbox's helpers, shared with the other measurement scripts; the tests reach them here too.
from sandbox import copy_library, environment, free_port, place_roots, remove_sandbox  # noqa: E402,F401
# The code a sandbox runs: scripts/code_snapshot.py, shared with the installer.
from code_snapshot import copy_code  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def build_sandbox(source_db, sandbox):
    """A private copy of the code and the library, wired to each other.

    The database is copied through SQLite rather than the filesystem: a live library
    has a write-ahead log beside it, and copying the .db on its own captures a file
    that is missing whatever is still in the WAL.
    """
    os.makedirs(os.path.join(sandbox, "data"), exist_ok=True)
    copy_code(sandbox, launchers=True)

    # The server runs with the sandbox as its TAGPUP_HOME (start_sandbox_server), so
    # library names in URLs resolve to the sandbox's data/.
    target = os.path.join(sandbox, "data", "measured.db")
    copy_library(source_db, target)
    place_roots(target, sandbox)
    return target


def start_sandbox_server(sandbox, db_path, port):
    """The server under test, as its own process, running the snapshotted code: its
    tagpup_web.py, which builds the process's runtime -- Suggest's models -- and warms
    them, as the apps people start get. A launcher written here once served the apps bare."""
    process = processes.start(
        [sys.executable, os.path.join(sandbox, "tagpup_web.py"), "--db", db_path,
         "--tuner-port", str(port), "--tagpup-port", str(free_port()), "--warm-up"],
        cwd=sandbox,
        # Its home is the sandbox, whatever TAGPUP_HOME this was run with.
        env=environment(sandbox),
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


def drive_new_person(url, args):
    """Open New Person on a different face each round: its click-to-usable times."""
    from playwright.sync_api import sync_playwright

    timings = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        started = time.time()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector("#matching-faces-grid [data-face-id]", timeout=args.timeout * 1000)
        print("\nopening the grid")
        print("  first faces on screen        : %7.2fs" % (time.time() - started))
        page.wait_for_timeout(4000)
        ids = page.eval_on_selector_all("#matching-faces-grid [data-face-id]",
                                        "els => els.map(e => e.dataset.faceId)")
        print("  cards rendered               : %d\n" % len(ids))

        for round_number in range(1, args.rounds + 1):
            face_id = ids[(round_number - 1) * 7 % len(ids)]
            # One face selected, as a click on its card leaves it.
            page.evaluate("""(id) => {
                document.querySelectorAll('#matching-faces-grid .selected').forEach(e => e.click());
                document.querySelector(`#matching-faces-grid [data-face-id="${id}"]`).click();
            }""", face_id)
            page.wait_for_function("() => !document.getElementById('btn-new-person').disabled",
                                   timeout=args.timeout * 1000)
            started = time.time()
            page.click("#btn-new-person")
            # Usable: the offered faces are on screen, and the main thread runs again.
            page.wait_for_function("""() => {
                const loading = document.getElementById('modal-matches-loading');
                return loading && loading.classList.contains('hidden');
            }""", timeout=args.timeout * 1000)
            page.evaluate("() => new Promise(r => requestAnimationFrame(() => r(1)))")
            elapsed = time.time() - started
            timings.append(elapsed)
            shown = page.eval_on_selector_all("#modal-matches-list .modal-face-card", "els => els.length")
            print("  round %-2d %4d faces offered -> %6.2fs" % (round_number, shown, elapsed))
            page.click("#btn-modal-cancel")
            page.wait_for_timeout(300)

        browser.close()
    return timings


def drive_people_by_face(url, args):
    """Review People's list: the click on By face until the crops in view are painted.

    Each round goes back to By name first, so it is the same click every time. The first
    round also computes the server's answer (/api/people-faces); later ones read its cache,
    and it is reported apart, with how long the answer takes on its own from the page."""
    from playwright.sync_api import sync_playwright

    timings = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        started = time.time()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector("#photo-list .person-row", timeout=args.timeout * 1000)
        people = page.eval_on_selector_all("#photo-list .person-row", "els => els.length")
        print("\nopening the list")
        print("  people on screen             : %7.2fs  (%d people)" % (time.time() - started, people))

        for round_number in range(1, args.rounds + 1):
            page.click("#people-view-name")
            page.wait_for_selector("#photo-list .person-row", timeout=args.timeout * 1000)
            page.wait_for_timeout(300)
            started = time.time()
            page.click("#people-view-face")
            page.wait_for_selector("#photo-list.by-face .person-card", timeout=args.timeout * 1000)
            # Painted: every crop the list's own viewport shows has loaded and decoded.
            page.wait_for_function("""() => {
                const box = document.getElementById('photo-list').getBoundingClientRect();
                const shown = [...document.querySelectorAll('#photo-list .person-face-crop img')].filter(img => {
                    const r = img.getBoundingClientRect();
                    return r.bottom > box.top && r.top < box.bottom;
                });
                return shown.length > 0 && shown.every(img => img.complete && img.naturalWidth > 0);
            }""", timeout=args.timeout * 1000)
            page.evaluate("() => new Promise(r => requestAnimationFrame(() => r(1)))")
            elapsed = time.time() - started
            timings.append(elapsed)
            cards, crops, loaded = page.evaluate("""() => {
                const imgs = [...document.querySelectorAll('#photo-list .person-face-crop img')];
                return [document.querySelectorAll('#photo-list .person-card').length, imgs.length,
                        imgs.filter(i => i.complete && i.naturalWidth > 0).length];
            }""")
            answer = page.evaluate("""async () => {
                const t = performance.now();
                const reply = await fetch(location.pathname.replace(/\\/?$/, '/') + 'api/people-faces');
                const body = await reply.json();
                return [performance.now() - t, Object.keys(body).length];
            }""")
            print("  round %-2d %4d cards, %3d of %3d crops loaded -> %6.2fs   (/api/people-faces again: %.0f ms, %d faces)"
                  % (round_number, cards, loaded, crops, elapsed, answer[0], answer[1]))
        browser.close()
    return timings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=tagpup_config.library_path("photo_index.db"),
                        help="the library to copy; opened read-only and never written")
    parser.add_argument("--action", choices=("ignore-cluster", "new-person", "people-by-face"), default="ignore-cluster",
                        help="the click to time")
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

        if args.action == "people-by-face":
            url = "http://127.0.0.1:%d/measured/?mode=face-matching" % port
            timings = drive_people_by_face(url, args)
            if len(timings) > 1:
                print("\nclick to painted: first %.2fs (computes the faces); then median %.2fs, worst %.2fs,"
                      " over %d rounds" % (timings[0], statistics.median(timings[1:]), max(timings[1:]),
                                           len(timings) - 1))
            timings = []
        elif args.action == "new-person":
            timings = drive_new_person(url, args)
            if len(timings) > 1:
                print("\nclick to usable: first %.2fs (reads the pool); then median %.2fs, worst %.2fs,"
                      " over %d rounds" % (timings[0], statistics.median(timings[1:]), max(timings[1:]),
                                           len(timings) - 1))
            timings = []
        else:
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
