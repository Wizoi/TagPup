"""Time what the owner's review of the library views changed, the way a person uses it: in a real Chromium, in a sandbox.

The numbers in docs/ARCHITECTURE.md, "Phase 9: the owner's review" (#671-#673), come from this. On a copy of photo_index it
measures, each a person's action and not an endpoint:

  (a) twelve Julys at once: in Dates, the filter typed "July", the first July clicked, then the twelfth Shift-clicked --
      from the Shift-click to the first window of cards painted and the main thread idle, and the union's ids request;
  (b) the People tab drawn: from the click on its tab to its rows painted and idle (the branches open, every person drawn);
  (c) each sort's first page: the whole library and the largest keyword node opened in each of the four orders by the sort
      control -- from the change to the first window painted and idle, and the ids request.

    .venv/Scripts/python.exe scripts/measure_library_review.py                  # prints the plan, does nothing
    .venv/Scripts/python.exe scripts/measure_library_review.py --run
    .venv/Scripts/python.exe scripts/measure_library_review.py --run --code-root <a `git archive` of the trunk> --only b

The trunk has no union and no sort, so (a) and (c) have no baseline but the trunk's own click on one row and its own order
(run with --code-root: (b) and the date order of (c) only). Everything runs in a sandbox built and deleted by
scripts/sandbox.py: the library copied through SQLite's backup API (the original read-only) under a temporary TAGPUP_HOME,
its roots placed at empty sandbox folders, migrated by this script before the server starts (migration 22's time is
printed), served on a free port. Nothing in data/ is touched. Thumbnails are answered a 1-pixel picture by the browser.
Only counts are printed, never a name, a keyword or a folder.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import processes  # noqa: E402
from code_snapshot import REPO_ROOT, copy_code  # noqa: E402
from sandbox import copy_library, free_port, place_roots, remove_sandbox  # noqa: E402

LIBRARY = "measured"
EVERYTHING = "abc"
ORDERS = ("taken", "taken-desc", "name", "name-desc")

INIT = r"""
window.__m = { longtasks: [], marks: {} };
try {
  new PerformanceObserver(l => l.getEntries().forEach(e => window.__m.longtasks.push({s: e.startTime, d: e.duration})))
    .observe({type: 'longtask', buffered: true});
} catch (e) {}
document.addEventListener('DOMContentLoaded', () => {
  const grid = document.getElementById('thumbnails-grid');
  new MutationObserver(() => {
    const m = window.__m.marks;
    if (m.cards || !grid.querySelector('.thumbnail-card:not(.placeholder)')) return;
    m.cards = performance.now();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      m.painted = performance.now();
      setTimeout(() => { m.paintedIdle = performance.now(); }, 0);
    }));
  }).observe(grid, {childList: true, subtree: true});
});
"""

HELPERS_JS = r"""
window.__n = {
  rows: (section) => [...document.querySelectorAll('#nav-panel-' + section + ' .nav-row')],
  async idle() {
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    await new Promise(r => setTimeout(r, 0));
  },
  // Start at the next click or change (capture phase); finish when the predicate holds, painted and idle.
  timed(predicate, timeout) {
    window.__p = new Promise(resolve => {
      let t0 = null;
      const lt0 = window.__m.longtasks.length;
      const start = () => { if (t0 === null) t0 = performance.now(); };
      document.addEventListener('click', start, {capture: true, once: true});
      document.addEventListener('change', start, {capture: true, once: true});
      (async function poll() {
        for (;;) {
          if (t0 !== null && predicate()) {
            await window.__n.idle();
            const lt = window.__m.longtasks.slice(lt0).map(x => x.d);
            resolve({ms: performance.now() - t0, longest: lt.length ? Math.max(...lt) : 0});
            return;
          }
          if (t0 !== null && performance.now() - t0 > timeout) { resolve({timeout: true}); return; }
          await new Promise(r => requestAnimationFrame(r));
        }
      })();
    });
  },
  lastIds() {
    const e = performance.getEntriesByType('resource').filter(e => e.name.includes('/api/library/ids')).pop();
    return e ? {ids_request_ms: e.responseEnd - e.startTime, ids_kb: e.encodedBodySize / 1024} : {};
  },
};
"""

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002"
    "00000500010d0a2db40000000049454e44ae426082")


def start_server(sandbox, db_path, tuner_port, tagpup_port):
    import socket
    process = processes.start(
        [sys.executable, os.path.join(sandbox, "tagpup_web.py"), "--db", db_path,
         "--tuner-port", str(tuner_port), "--tagpup-port", str(tagpup_port)],
        cwd=sandbox, env=dict(os.environ, TAGPUP_HOME=sandbox, TAGPUP_NO_JOBS="1"),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(240):
        try:
            with socket.create_connection(("127.0.0.1", tagpup_port), timeout=1):
                return process
        except OSError:
            if process.poll() is not None:
                raise RuntimeError("the sandbox server exited before it was ready") from None
            time.sleep(0.5)
    process.kill()
    raise RuntimeError("the sandbox server never came up")


def show(title, rows):
    print("\n" + title)
    if not rows:
        print("   (nothing measured)")
        return
    for key in [k for k in rows[0] if isinstance(rows[0][k], (int, float)) and not isinstance(rows[0][k], bool)]:
        values = [r.get(key) for r in rows if isinstance(r.get(key), (int, float))]
        if values:
            print("   %-34s median %10.1f   (%s)" % (key, statistics.median(values), ", ".join("%.1f" % v for v in values)))


def drive(base, args, results):
    from playwright.sync_api import sync_playwright
    root = "%s/%s/" % (base, LIBRARY)
    wanted = set(args.only)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)

        def new_page(url):
            ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
            page = ctx.new_page()
            page.add_init_script(INIT)
            page.route("**/api/photo-thumb*", lambda r: r.fulfill(body=TINY_PNG, content_type="image/png"))
            page.goto(url, wait_until="domcontentloaded", timeout=900000)
            page.wait_for_function("() => window.__m && document.getElementById('sidebar-switch')", timeout=120000)
            page.evaluate(HELPERS_JS)
            return ctx, page

        sorted_here = new_page(root)
        has_sort = sorted_here[1].evaluate("() => Boolean(document.getElementById('nav-sort'))")
        top_keyword = sorted_here[1].evaluate("""async () => {
            const kw = (await (await fetch('/%s/api/library/navigator?section=keywords')).json()).keywords;
            const top = kw.slice().sort((a, b) => b.count - a.count)[0]; return {tag: top.tag, count: top.count}; }""" % LIBRARY)
        sorted_here[0].close()
        print("\nthis code %s; the largest keyword node holds %d photos" % (
            "has the union and the sort" if has_sort else "has neither the union nor the sort (the trunk)", top_keyword["count"]))

        if "a" in wanted and has_sort:
            rows = []
            for _ in range(args.rounds):
                ctx, page = new_page(root + "?view=all")
                page.wait_for_function("() => window.__m.marks.paintedIdle", timeout=120000)
                page.click("#nav-tab-dates")
                page.wait_for_selector("#nav-panel-dates .nav-row", timeout=60000)
                page.fill("#nav-panel-dates .nav-filter", "July")
                page.wait_for_function("() => window.__n.rows('dates').length > 12 && window.__n.rows('dates').every(r => r.querySelector('.nav-label').textContent === 'July')", timeout=30000)
                page.locator("#nav-panel-dates .nav-row").nth(0).click()
                page.wait_for_function("() => window.__m.marks.paintedIdle", timeout=60000)
                page.evaluate("() => window.__n.idle()")
                page.evaluate("() => { window.__m.marks = {}; }")
                # Painted after the union's view opened: a late card of the one July before must not end the timing.
                page.evaluate("""() => window.__n.timed(() => new URLSearchParams(location.search).get('view') === 'any_of'
                    && window.__m.marks.paintedIdle
                    && Number(document.getElementById('library-strip-total').textContent.replace(/[^0-9]/g, '')) > 0, 60000)""")
                page.locator("#nav-panel-dates .nav-row").nth(11).click(modifiers=["Shift"])
                r = page.evaluate("() => window.__p")
                r.update(page.evaluate("() => window.__n.lastIds()"))
                r["photos"] = page.evaluate("() => window.__n && document.getElementById('library-strip-total').textContent.replace(/[^0-9]/g, '') * 1")
                r["months"] = page.evaluate("() => JSON.parse(new URLSearchParams(location.search).get('value') || '[]').length")
                rows.append(r)
                ctx.close()
            results["twelve_julys"] = rows
            show("(a) twelve Julys: the Shift-click to the first window painted and idle", rows)

        if "b" in wanted:
            rows = []
            for _ in range(args.rounds):
                ctx, page = new_page(root + "?view=all")
                page.wait_for_function("() => window.__m.marks.paintedIdle", timeout=120000)
                page.evaluate("() => window.__n.idle()")
                page.evaluate("() => window.__n.timed(() => window.__n.rows('people').length > 0, 60000)")
                page.click("#nav-tab-people")
                r = page.evaluate("() => window.__p")
                e = page.evaluate("""() => { const e = performance.getEntriesByType('resource').find(e => e.name.includes('section=people'));
                    return e ? {request_ms: e.responseEnd - e.startTime, reply_kb: e.encodedBodySize / 1024} : {}; }""")
                r.update(e)
                r["rows"] = page.evaluate("() => window.__n.rows('people').length")
                r["headers"] = page.evaluate("() => window.__n.rows('people').filter(x => x.dataset.row.startsWith('g:')).length")
                rows.append(r)
                ctx.close()
            results["people_tab"] = rows
            show("(b) the People tab: click to its rows painted and idle", rows)

        if "c" in wanted:
            for label, url in (("the whole library", root + "?view=all"),
                               ("the largest keyword node", root + "?view=keyword&value=" + urllib.parse.quote(top_keyword["tag"], safe=""))):
                rows = []
                for order in (ORDERS if has_sort else ORDERS[:1]):
                    for _ in range(args.rounds):
                        ctx, page = new_page(url)
                        page.wait_for_function("() => window.__m.marks.paintedIdle", timeout=120000)
                        page.evaluate("() => window.__n.idle()")
                        if order == "taken":
                            # the order a view opens in: timed from navigation, as the trunk can only be
                            r = page.evaluate("() => ({ms: window.__m.marks.paintedIdle})")
                        else:
                            page.evaluate("() => { window.__m.marks = {}; }")
                            page.evaluate("() => window.__n.timed(() => window.__m.marks.paintedIdle, 60000)")
                            page.select_option("#nav-sort", order)
                            r = page.evaluate("() => window.__p")
                        r.update(page.evaluate("() => window.__n.lastIds()"))
                        r["order"] = order
                        rows.append(r)
                        ctx.close()
                for order in (ORDERS if has_sort else ORDERS[:1]):
                    show("(c) %s, %s: %s" % (label, order, "navigation to painted and idle" if order == "taken" else
                                             "the change of the sort to the first window painted and idle"),
                         [r for r in rows if r["order"] == order])
                results["sort_" + label] = rows
        browser.close()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="do it; without this the plan is printed and nothing is done")
    parser.add_argument("--code-root", default=REPO_ROOT, help="the checkout whose code the sandbox serves (default: this one)")
    parser.add_argument("--source", default=tagpup_config.library_path("photo_index.db"),
                        help="the library to copy; opened read-only and never written")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--only", default=EVERYTHING, help="which of a to c to run, as letters (default: all)")
    parser.add_argument("--out", default="", help="also write the numbers here as JSON")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    args = parser.parse_args()
    args.only = [letter for letter in args.only.lower() if letter in EVERYTHING]
    return args


def print_plan(args):
    print("measure_library_review.py would, with --run:")
    print("  copy %s (read-only source) into a sandbox under %s, its roots placed at empty sandbox folders, and migrate the copy"
          % (args.source, tempfile.gettempdir()))
    print("  serve the code of %s from that sandbox on a free port" % args.code_root)
    print("  in headless Chromium, %d fresh browsers each: (a) twelve Julys by a filter and a Shift-click; (b) the People tab"
          " drawn; (c) the whole library and the largest keyword in each order" % args.rounds)
    print("  run only: %s; print the numbers, then delete the sandbox" % ", ".join(args.only))
    print("Nothing was done.")


def migrate(sandbox, db_path, code_root):
    """Bring the copy up to date with the served code, timed: what the server would do as it starts."""
    started = time.time()
    processes.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); from tagpup.store import schema; "
                   "print(schema.ensure(%r))" % (code_root, db_path)],
                  cwd=sandbox, env=dict(os.environ, TAGPUP_HOME=sandbox), check=True)
    print("  the copy brought up to date by the served code in %.1f s" % (time.time() - started))


def main():
    args = parse_args()
    if not args.run:
        print_plan(args)
        return
    if not os.path.exists(args.source):
        sys.exit("%s does not exist (is TAGPUP_HOME set? or pass --source)" % args.source)
    sandbox = tempfile.mkdtemp(prefix="tagpup_measure_")
    server = None
    results = {"code_root": args.code_root}
    try:
        print("sandbox : %s" % sandbox)
        db_path = os.path.join(sandbox, "data", LIBRARY + ".db")
        os.makedirs(os.path.join(sandbox, "data"), exist_ok=True)
        copy_code(sandbox, code_root=args.code_root, launchers=True)
        copy_library(args.source, db_path)
        place_roots(db_path, sandbox)
        migrate(sandbox, db_path, sandbox)
        tuner, tagpup = free_port(), free_port()
        server = start_server(sandbox, db_path, tuner, tagpup)
        print("  serving the code of %s on port %d" % (args.code_root, tagpup))
        drive("http://127.0.0.1:%d" % tagpup, args, results)
    finally:
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        if args.out:
            with open(args.out, "w") as handle:
                json.dump(results, handle, indent=1)
        print("sandbox deleted: %s" % remove_sandbox(sandbox))


if __name__ == "__main__":
    main()
