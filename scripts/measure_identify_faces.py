"""Time the Identify Faces screen the way a person uses it: in a browser, by clicking.

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

    .venv/Scripts/python.exe scripts/measure_identify_faces.py --db data/perfcopy.db

Run it against a COPY of a library, never the real one -- it excludes faces as it goes.
Take a baseline before changing anything: a harness that cannot reproduce the reported
slowness is not measuring the reported thing, and the fix that follows will be aimed at
whatever it does measure.

Headless Chromium is faster than a real browser session -- it has no extensions, a
fresh profile and nothing else painting -- so the absolute numbers here run low. It is
reliable for the comparison, which is what a baseline is for.
"""
import argparse
import os
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from .tuner_server import start_server
except ImportError:  # imported as a top-level module
    from tuner_server import start_server


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


def measure(args):
    from playwright.sync_api import sync_playwright

    db_name = os.path.splitext(os.path.basename(args.db))[0]
    url = ("http://127.0.0.1:%d/%s/?mode=unmatched-faces&person=%s"
           % (args.port, db_name, args.person.replace(" ", "+")))

    threading.Thread(
        target=start_server,
        kwargs={"port": args.port, "db_path": args.db, "gui_dir": "gui"},
        daemon=True,
    ).start()
    time.sleep(2.0)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})

        print("%s\n%s\n" % (args.db, url))
        started = time.time()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector("#matching-faces-grid [data-face-id]", timeout=args.timeout * 1000)
        first_paint = time.time() - started

        # Let the crops settle, so the clicks are not timed against image loading.
        page.wait_for_timeout(4000)
        cards = page.eval_on_selector_all(
            "#matching-faces-grid [data-face-id]", "els => els.length")

        print("opening the grid")
        print("  first faces on screen        : %7.2fs" % first_paint)
        print("  cards rendered               : %d\n" % cards)

        timings = []
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

    if timings:
        print("\nclick to usable: median %.2fs, worst %.2fs, over %d rounds"
              % (statistics.median(timings), max(timings), len(timings)))
    return timings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/perfcopy.db",
                        help="a COPY of a library; faces are excluded as this runs")
    parser.add_argument("--person", default="Unknown Faces")
    parser.add_argument("--port", type=int, default=9101)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=600, help="seconds")
    parser.add_argument("--headed", action="store_true",
                        help="show the browser; slower and closer to what a person sees")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        parser.error("%s does not exist. Copy a library to it first." % args.db)
    if os.path.basename(args.db) in ("photo_index.db", "kr-track.db"):
        parser.error("refusing to run against %s: this excludes faces as it goes, so "
                     "point it at a copy." % args.db)

    measure(args)


if __name__ == "__main__":
    main()
