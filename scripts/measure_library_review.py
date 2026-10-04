"""Time what the owner's review of the library views changed, the way a person uses it: in a real Chromium, in a sandbox.

The numbers in docs/ARCHITECTURE.md, "Phase 9: the owner's review" (#671-#673), come from this. On a copy of photo_index it
measures, each a person's action and not an endpoint:

  (a) twelve Julys at once: in Dates, the filter typed "July", the first July clicked, then the twelfth Shift-clicked --
      from the Shift-click to the first window of cards painted and the main thread idle, and the union's ids request;
  (b) the People tab drawn: from the click on its tab to its rows painted and idle (the branches open, every person drawn);
  (c) each sort's first page: the whole library and the largest keyword node opened in each order by the sort control --
      the second review's Sort by menu (#714), or the first review's select -- from the click (the change) to the first window
      painted and idle, and the ids request;
  (d) the floating header (#713): at three window widths, the whole library in caption order -- whether the page scrolls
      sideways, the header's height, and whether a card the arrow keys walk to is below the header and not under it
      (`--shots <folder>` keeps a screenshot of each width).

Before the server starts, the plans and times of the id list in caption order (all, the largest keyword) are printed, read on
the copy by the served code.

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
EVERYTHING = "abcd"
ORDERS = ("taken", "taken-desc", "name", "name-desc", "caption", "caption-desc")
#: What Sort by's menu calls each order: (the field's item, the direction's item).
MENU = {"taken": ("Date Taken", "Ascending"), "taken-desc": ("Date Taken", "Descending"), "name": ("File name", "Ascending"),
        "name-desc": ("File name", "Descending"), "caption": ("Caption", "Ascending"), "caption-desc": ("Caption", "Descending")}
WIDTHS = (1600, 1000, 720)

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
        has_menu = sorted_here[1].evaluate("() => Boolean(document.getElementById('btn-sort-by'))")
        has_sort = has_menu or sorted_here[1].evaluate("() => Boolean(document.getElementById('nav-sort'))")
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
                orders = ORDERS if has_menu else ORDERS[:4] if has_sort else ORDERS[:1]
                for order in orders:
                    for _ in range(args.rounds):
                        ctx, page = new_page(url)
                        page.wait_for_function("() => window.__m.marks.paintedIdle", timeout=120000)
                        page.evaluate("() => window.__n.idle()")
                        if order == "taken":
                            # the order a view opens in: timed from navigation, as the trunk can only be
                            r = page.evaluate("() => ({ms: window.__m.marks.paintedIdle})")
                        else:
                            page.evaluate("() => { window.__m.marks = {}; }")
                            if has_menu:
                                field, direction = MENU[order]
                                if field != "Date Taken":
                                    # The field first, in the view's direction: the click that is timed is the one that makes
                                    # the order (the direction when it is not ascending, else the field).
                                    if direction == "Ascending":
                                        page.evaluate("() => window.__n.timed(() => window.__m.marks.paintedIdle, 60000)")
                                    page.click("#btn-sort-by")
                                    page.click("#sort-menu [data-field] >> text=%s" % field)
                                    if direction == "Descending":
                                        page.wait_for_function("() => window.__m.marks.paintedIdle", timeout=120000)
                                        page.evaluate("() => window.__n.idle()")
                                        page.evaluate("() => { window.__m.marks = {}; }")
                                if direction == "Descending":
                                    page.evaluate("() => window.__n.timed(() => window.__m.marks.paintedIdle, 60000)")
                                    page.click("#btn-sort-by")
                                    page.click("#sort-menu [data-direction='desc']")
                            else:
                                page.evaluate("() => window.__n.timed(() => window.__m.marks.paintedIdle, 60000)")
                                page.select_option("#nav-sort", order)
                            r = page.evaluate("() => window.__p")
                        r.update(page.evaluate("() => window.__n.lastIds()"))
                        r["order"] = order
                        rows.append(r)
                        ctx.close()
                for order in orders:
                    show("(c) %s, %s: %s" % (label, order, "navigation to painted and idle" if order == "taken" else
                                             "the choice of the sort to the first window painted and idle"),
                         [r for r in rows if r["order"] == order])
                results["sort_" + label] = rows

        if "d" in wanted:
            rows = []
            for width in WIDTHS:
                ctx = browser.new_context(viewport={"width": width, "height": 900})
                page = ctx.new_page()
                page.add_init_script(INIT)
                page.route("**/api/photo-thumb*", lambda r: r.fulfill(body=TINY_PNG, content_type="image/png"))
                page.goto(root + ("?view=all&order=caption" if has_menu else "?view=all"), wait_until="domcontentloaded",
                          timeout=900000)
                page.wait_for_function("() => window.__m && window.__m.marks.paintedIdle", timeout=120000)
                page.evaluate(HELPERS_JS)
                page.evaluate("() => window.__n.idle()")
                page.focus("#thumbnails-grid .thumbnail-card:not(.placeholder)")
                # Down far enough that the grid scrolls, then up until the card walked to is at the top: where the header is.
                for key, times in (("ArrowDown", 30), ("ArrowUp", 12)):
                    for _ in range(times):
                        page.keyboard.press(key)
                        page.wait_for_timeout(30)
                page.wait_for_timeout(400)
                r = page.evaluate("""() => {
                    const main = document.getElementById('folder-view-main');
                    const header = document.getElementById('folder-view-top') || document.getElementById('library-strip');
                    const top = header.getBoundingClientRect();
                    const inside = [...header.querySelectorAll('*')].filter(e => e.getClientRects().length && !e.closest('#sort-menu'))
                        .every(e => { const r = e.getBoundingClientRect(); return r.left >= top.left - 1 && r.right <= top.right + 1; });
                    const focused = document.activeElement.closest('.thumbnail-card');
                    const card = focused ? focused.getBoundingClientRect() : null;
                    const doc = document.scrollingElement;
                    return {width: window.innerWidth, header_px: top.height, grid_column_px: main.clientWidth,
                            focused: focused ? focused.dataset.id || '' : String(document.activeElement && document.activeElement.className),
                            card_top_px: card ? card.top - top.bottom : null,
                            header_holds_its_controls: inside && header.scrollWidth <= header.clientWidth,
                            page_scrolls_sideways: doc.scrollWidth > doc.clientWidth || main.scrollWidth > main.clientWidth,
                            focused_card_below_header: Boolean(card) && card.top >= top.bottom,
                            header_top_px: top.top - main.getBoundingClientRect().top};
                }""")
                if has_menu:
                    page.click("#btn-sort-by")
                    r["menu_inside_window"] = page.evaluate("""() => { const m = document.getElementById('sort-menu').getBoundingClientRect();
                        return m.left >= 0 && m.right <= window.innerWidth; }""")
                if args.shots:
                    os.makedirs(args.shots, exist_ok=True)
                    page.screenshot(path=os.path.join(args.shots, "header-%d.png" % width))
                page.keyboard.press("Escape")
                rows.append(r)
                print("   width %d: the grid's column %d px; the header %.0f px tall, at %.0f px, holding its controls %s; the page scrolls"
                      " sideways %s; the focused card below the header %s (%s px below it); the menu inside the window %s"
                      % (r["width"], r["grid_column_px"], r["header_px"], r["header_top_px"], r["header_holds_its_controls"],
                         r["page_scrolls_sideways"], r["focused_card_below_header"], r["card_top_px"], r.get("menu_inside_window")))
                ctx.close()
            results["header"] = rows
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
    parser.add_argument("--shots", default="", help="(d) keeps a screenshot of the header at each width here")
    args = parser.parse_args()
    args.only = [letter for letter in args.only.lower() if letter in EVERYTHING]
    return args


def print_plan(args):
    print("measure_library_review.py would, with --run:")
    print("  copy %s (read-only source) into a sandbox under %s, its roots placed at empty sandbox folders, and migrate the copy"
          % (args.source, tempfile.gettempdir()))
    print("  serve the code of %s from that sandbox on a free port" % args.code_root)
    print("  in headless Chromium, %d fresh browsers each: (a) twelve Julys by a filter and a Shift-click; (b) the People tab"
          " drawn; (c) the whole library and the largest keyword in each order; (d) the header at %s px"
          % (args.rounds, ", ".join(str(w) for w in WIDTHS)))
    print("  run only: %s; print the numbers, then delete the sandbox" % ", ".join(args.only))
    print("Nothing was done.")


def migrate(sandbox, db_path, code_root):
    """Bring the copy up to date with the served code, timed: what the server would do as it starts."""
    started = time.time()
    processes.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); from tagpup.store import schema; "
                   "print(schema.ensure(%r))" % (code_root, db_path)],
                  cwd=sandbox, env=dict(os.environ, TAGPUP_HOME=sandbox), check=True)
    print("  the copy brought up to date by the served code in %.1f s" % (time.time() - started))


CAPTION_PLANS = r"""
import collections, sys, time
sys.path.insert(0, %r)
from tagpup.store import db, library_view as store
conn = db.connect(db.readonly_uri(%r), uri=True)
if not hasattr(store, "CAPTION"):
    print("  (the served code has no order by caption)")
    sys.exit(0)
top = conn.execute("SELECT t.tag FROM photo_tags pt JOIN tag_taxonomy t ON t.id = pt.tag_id GROUP BY pt.tag_id"
                   " ORDER BY COUNT(*) DESC LIMIT 1").fetchone()[0]
for label, source in (("the whole library", store.Source(store.ALL)), ("the largest keyword node", store.Source(store.KEYWORD, top))):
    for order in (store.CAPTION, store.CAPTION_DESC):
        times = []
        for _ in range(3):
            statements, ms = store.id_plans(conn, source, 200000, order)
            times.append(ms)
        ids, total = store.all_ids(conn, source, 200000, order)
        print("  %%s, %%s: %%d ids, %%.0f ms (best of 3; %%s)" %% (label, order, len(ids), min(times),
              ", ".join("%%.0f" %% t for t in times)))
        for sql, lines in statements:
            if "FROM photos" in sql:
                print("      " + " | ".join(lines))
    started = time.perf_counter()
    rows, more = store.page(conn, source, None, 200, store.CAPTION)
    cursor = store.next_cursor(rows[-1])
    for _ in range(50):
        rows, more = store.page(conn, source, cursor, 200, store.CAPTION)
        if not more:
            break
        cursor = store.next_cursor(rows[-1])
    print("  %%s: 50 keyset pages of 200 in caption order, %%.0f ms" %% (label, (time.perf_counter() - started) * 1000))
"""


def caption_plans(sandbox, db_path):
    """The plans and times of the id list in caption order on the copy, read by the served code: what SQLite does with it."""
    done = processes.run([sys.executable, "-c", CAPTION_PLANS % (sandbox, db_path)], cwd=sandbox,
                         env=dict(os.environ, TAGPUP_HOME=sandbox), check=True, capture_output=True, text=True)
    print(done.stdout.rstrip())


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
        caption_plans(sandbox, db_path)
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
