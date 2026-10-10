"""See TagPup's folder view at the widths of a narrow window, in a real Chromium, in a sandbox (docs/findings.md, #720).

The page keeps the sidebar (360 px) and the Selection Details panel (340 px) at their widths whatever the window, so the
grid between them was 296 px wide at 1,000 and 40 px at 720, where the page scrolled sideways. Below `NARROW` px the
panel is collapsed behind a button (aria-expanded, aria-controls) and, opened, lies over the grid instead of beside it:
the grid keeps the width, and nothing scrolls sideways. What this checks, at each width asked for (1,600, 1,000, 720):

  - no horizontal scroll of the page, the app, or the grid's scroller, collapsed or opened;
  - every card is at least 150 px wide;
  - wide: the panel is beside the grid and there is no button; narrow: the panel is collapsed, the button says so
    (aria-expanded="false"), a click opens it (true) over the grid, where the panel's own button closes it (it covers
    the first), and the browser remembers the choice for a reload.

    .venv/Scripts/python.exe scripts/measure_narrow_window.py                  # prints the plan, does nothing
    .venv/Scripts/python.exe scripts/measure_narrow_window.py --run
    .venv/Scripts/python.exe scripts/measure_narrow_window.py --run --code-root <a `git archive` of an older commit>   # the baseline

Exits 1 when a check fails. The sandbox (scripts/sandbox.py) holds one empty library and a folder of generated JPEGs,
on a free port, under a TAGPUP_HOME of its own; nothing in data/ is read or written, and it is deleted afterwards.
"""
import argparse
import os
import random
import subprocess
import sys
import tempfile
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup.core import paths, processes  # noqa: E402
from code_snapshot import REPO_ROOT, copy_code  # noqa: E402
from sandbox import enter, environment, free_port, remove_sandbox  # noqa: E402

LIBRARY = "regatta_harbour_photos"   # a long name, as the header must give way to it (fictional)
MIN_CARD = 150          # px: a card is never narrower (the grid's minmax)
SLACK = 1               # px of rounding in a scroll width

#: Measures the page as it is now.
MEASURE = """() => {
  const rect = (el) => el.getBoundingClientRect();
  const visible = (el) => { if (!el) return false; const s = getComputedStyle(el); const r = rect(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0; };
  const over = (el) => el ? el.scrollWidth - el.clientWidth : 0;
  const panel = document.getElementById('folder-selection-sidebar');
  const toggle = document.getElementById('btn-details-toggle');
  const limit = document.documentElement.clientWidth;
  const past = [...document.body.querySelectorAll('*')].filter((el) => rect(el).right > limit + 1 && visible(el)
      && !el.closest('#folder-view-main, #folder-selection-sidebar, #thumbnails-grid'))
    .slice(0, 4).map((el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + '.' + String(el.className).split(' ')[0]
      + ' right=' + Math.round(rect(el).right));
  const widths = [...document.querySelectorAll('#thumbnails-grid .thumbnail-card')].map((c) => rect(c).width);
  return {
    page: over(document.documentElement), body: over(document.body), app: over(document.querySelector('.app-content')),
    scroller: over(document.getElementById('folder-view-main')),
    past, cards: widths.length, narrowestCard: widths.length ? Math.min(...widths) : 0,
    panelVisible: visible(panel), panelWidth: panel ? rect(panel).width : 0,
    toggleVisible: visible(toggle), closeVisible: visible(document.getElementById('btn-details-close')), expanded: toggle ? toggle.getAttribute('aria-expanded') : null,
    controls: toggle ? toggle.getAttribute('aria-controls') : null,
  };
}"""


def make_photos(folder, count):
    from PIL import Image
    os.makedirs(folder, exist_ok=True)
    rng = random.Random(7)
    for i in range(count):
        Image.new("RGB", (320, 240), (rng.randrange(256), rng.randrange(256), rng.randrange(256))) \
            .save(os.path.join(folder, "IMG_%04d.jpg" % i), quality=80)


def start_server(sandbox, db_path, tuner_port, tagpup_port):
    import socket
    process = processes.start(
        [sys.executable, os.path.join(sandbox, "tagpup_web.py"), "--db", db_path,
         "--tuner-port", str(tuner_port), "--tagpup-port", str(tagpup_port)],
        cwd=sandbox, env=environment(sandbox, TAGPUP_NO_JOBS="1"),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            with socket.create_connection(("127.0.0.1", tagpup_port), timeout=1):
                return process
        except OSError:
            if process.poll() is not None:
                raise RuntimeError("the sandbox server exited before it was ready") from None
            time.sleep(0.5)
    process.kill()
    raise RuntimeError("the sandbox server never came up")


def problems_of(found, width, narrow, state):
    """What is wrong in one measurement `found` at `width`: [sentence]. `narrow` says the page should have collapsed
    the panel; `state` names the moment ("collapsed", "opened")."""
    bad = []
    for key in ("page", "body", "app", "scroller"):
        if found[key] > SLACK:
            bad.append("%d px: the %s scrolls sideways by %d px" % (width, key, found[key]))
    if found["cards"] == 0:
        bad.append("%d px: no card was drawn" % width)
    elif found["narrowestCard"] < MIN_CARD - SLACK:
        bad.append("%d px: a card is %.0f px wide, under %d" % (width, found["narrowestCard"], MIN_CARD))
    if not narrow:
        if not found["panelVisible"]:
            bad.append("%d px: the panel is not beside the grid" % width)
        if found["toggleVisible"]:
            bad.append("%d px: the details button shows on a wide window" % width)
    elif state == "collapsed":
        if found["panelVisible"]:
            bad.append("%d px: the panel is not collapsed" % width)
        if not found["toggleVisible"] or found["expanded"] != "false" or found["controls"] != "folder-selection-sidebar":
            bad.append("%d px: no button saying aria-expanded=false for the panel (%s, %s, %s)"
                       % (width, found["toggleVisible"], found["expanded"], found["controls"]))
    elif state == "opened":
        if not found["panelVisible"] or found["expanded"] != "true":
            bad.append("%d px: the button did not open the panel (%s, %s)" % (width, found["panelVisible"], found["expanded"]))
        if not found["closeVisible"]:
            bad.append("%d px: the opened panel has no close button, and covers the Details button" % width)
    return bad


def settle(page):
    """Wait for the cards, and answer the page's question about a folder the library does not hold: Just look."""
    page.wait_for_selector("#thumbnails-grid .thumbnail-card", timeout=120000)
    page.wait_for_timeout(600)
    if page.locator("#add-folder-modal.active").count():
        page.click("#btn-just-look")
        page.wait_for_selector("#add-folder-modal.active", state="detached", timeout=30000)
        page.wait_for_timeout(300)


def drive(base, folder, widths, narrow_below):
    from playwright.sync_api import sync_playwright
    url = "%s/%s/?path=%s" % (base, LIBRARY, urllib.parse.quote(paths.stored(folder), safe=""))
    bad = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for width in widths:
                narrow = width < narrow_below
                context = browser.new_context(viewport={"width": width, "height": 900})
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded")
                settle(page)
                found = page.evaluate(MEASURE)
                print("%5d px  %-9s %s" % (width, "collapsed" if narrow else "wide", found))
                bad += problems_of(found, width, narrow, "collapsed")
                if narrow and page.locator("#btn-details-toggle").count():
                    page.click("#btn-details-toggle")
                    page.wait_for_timeout(300)
                    found = page.evaluate(MEASURE)
                    print("%5d px  %-9s %s" % (width, "opened", found))
                    bad += problems_of(found, width, narrow, "opened")
                    page.reload(wait_until="domcontentloaded")
                    settle(page)
                    found = page.evaluate(MEASURE)
                    print("%5d px  %-9s %s" % (width, "reloaded", found))
                    if found["expanded"] != "true":
                        bad.append("%d px: the browser did not remember the panel open" % width)
                    page.click("#btn-details-close")
                    page.wait_for_timeout(300)
                    found = page.evaluate(MEASURE)
                    bad += problems_of(found, width, narrow, "collapsed")
                context.close()
        finally:
            browser.close()
    return bad


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="do it; without this the plan is printed and nothing is done")
    parser.add_argument("--code-root", default=REPO_ROOT, help="the checkout whose code the sandbox serves (default: this one)")
    parser.add_argument("--widths", default="1600,1000,720", help="window widths in px, comma separated")
    parser.add_argument("--narrow-below", type=int, default=1100, help="the width under which the panel is expected collapsed")
    parser.add_argument("--photos", type=int, default=30)
    args = parser.parse_args()
    if not args.run:
        print("measure_narrow_window.py would, with --run: build a sandbox with an empty library and %d generated JPEGs serving the code of %s,"
              % (args.photos, args.code_root))
        print("open its folder in headless Chromium at %s px wide, measure the page's horizontal scroll, the cards and the details panel, delete the sandbox."
              % args.widths)
        print("Nothing was done.")
        return 0
    sandbox = tempfile.mkdtemp(prefix="tagpup_narrow_")
    server = None
    bad = []
    try:
        copy_code(sandbox, code_root=args.code_root, launchers=True)
        os.makedirs(os.path.join(sandbox, "data"))
        enter(sandbox)
        from tagpup.services import libraries as library_actions
        db_path = os.path.join(sandbox, "data", LIBRARY + ".db")
        library_actions.create(db_path)
        folder = os.path.join(sandbox, "photos", "narrow")
        make_photos(folder, args.photos)
        tuner, tagpup = free_port(), free_port()
        server = start_server(sandbox, db_path, tuner, tagpup)
        bad = drive("http://127.0.0.1:%d" % tagpup, folder, [int(w) for w in args.widths.split(",")], args.narrow_below)
    finally:
        if server and server.poll() is None:
            processes.kill_tree(server.pid)
        print("sandbox deleted: %s" % remove_sandbox(sandbox))
    for line in bad:
        print("FAIL  " + line)
    print("%s" % ("FAILED: %d check(s)" % len(bad) if bad else "all checks passed"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
