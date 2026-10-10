"""Time TagPup's views of the library the way a person uses them: in a real Chromium, in a sandbox.

The numbers in docs/ARCHITECTURE.md, "Phase 9b-2", come from this. On a copy of photo_index (68,466 photos
when this was written) it measures, each a person's action and not an endpoint:

  (a) opening ?view=all: from the navigation to the first window of cards painted, their thumbnails
      asked for and the main thread idle; and the ids reply (its size, and the time of the request);
  (b) opening a keyword view of the library's largest keyword node, the same way;
  (c) scrolling `all` top to bottom in 10 s: the longest main-thread task, frames over 100 ms, the
      requests made (ids, cards, thumbnails; and per second), peak DOM nodes, peak JS heap;
  (d) jumping to the middle by dragging the scroll bar (the scroll offset set at once, as a drag does):
      until the cards in view are painted and the main thread is idle;
  (e) the same open and scroll on the folder view of a 759-photo folder (photo_index's largest), and on
      the library view of photo_index's largest folder, to show the folder view did not slow;
  and the query plans of the id lists, read on the copy.

    .venv/Scripts/python.exe scripts/measure_library_view.py                  # prints the plan, does nothing
    .venv/Scripts/python.exe scripts/measure_library_view.py --run
    .venv/Scripts/python.exe scripts/measure_library_view.py --run --code-root <a `git archive` of the trunk>

With --code-root of a checkout that has no library view (the trunk before 9b-2) only (e) runs, on the folder
view: the baseline of the folder view. Everything runs in a sandbox, built and deleted by scripts/sandbox.py:
the library is copied through SQLite's backup API (the original read-only), under a temporary TAGPUP_HOME of
its own, its roots placed at empty sandbox folders by the sandbox's own map, on a free port. Nothing in
data/ is touched and nothing is visible to an app you have open. The photos of the library are not there:
a thumbnail's request is answered a small picture by the browser's own routing (counted, not decoded by the
server), and the 759-photo folder is made of JPEGs generated here. No name of a keyword, folder or person is
printed, only counts. Headless Chromium has no extensions and a fresh profile, so the absolute numbers come
out low; use them for comparison. Run to run they move by up to 40%.
"""
import argparse
import json
import os
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import paths, processes  # noqa: E402
from code_snapshot import REPO_ROOT, copy_code  # noqa: E402
from sandbox import copy_library, enter, environment, free_port, place_roots, remove_sandbox  # noqa: E402

LIBRARY = "measured"

INIT = r"""
window.__m = { longtasks: [], marks: {} };
try {
  new PerformanceObserver(l => l.getEntries().forEach(e => window.__m.longtasks.push({s: e.startTime, d: e.duration})))
    .observe({type: 'longtask', buffered: true});
} catch (e) {}
const PICTURE = /\/api\/(photo-thumb|photo-file)/;
document.addEventListener('DOMContentLoaded', () => {
  const grid = document.getElementById('thumbnails-grid');
  new MutationObserver(() => {
    const m = window.__m.marks;
    if (m.cards || !grid.querySelector('.thumbnail-card:not(.placeholder)')) return;
    m.cards = performance.now();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      m.painted = performance.now();
      setTimeout(() => { m.paintedIdle = performance.now(); }, 0);   // as 9b-1 measured the folder: painted, and idle
      const started = performance.now();
      (function waitForPictures() {
        const asked = performance.getEntriesByType('resource').filter(e => PICTURE.test(e.name));
        if (asked.length || performance.now() - started > 8000) {
          m.pictures = asked.length ? asked[0].startTime : null;
          setTimeout(() => { m.idle = performance.now(); }, 0);
        } else setTimeout(waitForPictures, 10);
      })();
    }));
  }).observe(grid, {childList: true, subtree: true});
});
"""

HELPERS_JS = r"""
window.__h = {
  nodes: () => document.querySelectorAll('#thumbnails-grid *').length,
  cards: () => document.querySelectorAll('#thumbnails-grid .thumbnail-card:not(.placeholder)').length,
  holes: () => document.querySelectorAll('#thumbnails-grid .thumbnail-card.placeholder').length,
  scroller: () => document.getElementById('folder-view-main'),
  heap: () => (performance.memory ? performance.memory.usedJSHeapSize : null),
  async settle() {
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    await new Promise(r => setTimeout(r, 0));
  },
  inView() {
    const sc = window.__h.scroller().getBoundingClientRect();
    return [...document.querySelectorAll('#thumbnails-grid .thumbnail-card')].filter(c => {
      const r = c.getBoundingClientRect();
      return r.bottom > sc.top && r.top < sc.bottom;
    });
  },
  async flyover(ms) {
    const sc = window.__h.scroller();
    sc.scrollTop = 0;
    await window.__h.settle();
    const total = sc.scrollHeight - sc.clientHeight;
    const frames = [];
    let peak = 0;
    let heap = window.__h.heap() || 0;
    const lt0 = window.__m.longtasks.length;
    let last = performance.now();
    const start = last;
    await new Promise(res => {
      function step(now) {
        frames.push(now - last); last = now;
        const p = Math.min(1, (now - start) / ms);
        sc.scrollTop = p * total;
        peak = Math.max(peak, window.__h.nodes());
        heap = Math.max(heap, window.__h.heap() || 0);
        if (p < 1) requestAnimationFrame(step); else res();
      }
      requestAnimationFrame(step);
    });
    const lt = window.__m.longtasks.slice(lt0).map(x => x.d);
    frames.sort((a, b) => a - b);
    return {
      distance: total, frames: frames.length, maxFrame: frames[frames.length - 1],
      p95Frame: frames[Math.floor(frames.length * 0.95)],
      over33: frames.filter(f => f > 33).length, over100: frames.filter(f => f > 100).length,
      longest: lt.length ? Math.max(...lt) : 0, longTasks: lt.length, peakNodes: peak, peakHeapMB: heap / 1e6,
    };
  },
  // The scroll bar dragged to a place: the offset set at once, then until the cards in view are all real and painted.
  async jump(fraction) {
    const sc = window.__h.scroller();
    const total = sc.scrollHeight - sc.clientHeight;
    const lt0 = window.__m.longtasks.length;
    const t = performance.now();
    sc.scrollTop = fraction * total;
    let sawHoles = false;
    for (;;) {
      await new Promise(r => requestAnimationFrame(r));
      const visible = window.__h.inView();
      const holes = visible.filter(c => c.classList.contains('placeholder')).length;
      if (holes) sawHoles = true;
      if (visible.length && !holes) break;
      if (performance.now() - t > 20000) return {timeout: true};
    }
    await window.__h.settle();
    const lt = window.__m.longtasks.slice(lt0).map(x => x.d);
    return {ms: performance.now() - t, sawHoles, longest: lt.length ? Math.max(...lt) : 0, visible: window.__h.inView().length};
  },
};
"""

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002"
    "00000500010d0a2db40000000049454e44ae426082")


def make_photos(folder, count):
    from PIL import Image, ImageDraw
    os.makedirs(folder, exist_ok=True)
    rng = random.Random(7)
    for i in range(count):
        im = Image.new("RGB", (640, 480), (rng.randrange(256), rng.randrange(256), rng.randrange(256)))
        d = ImageDraw.Draw(im)
        for _ in range(6):
            x, y = rng.randrange(600), rng.randrange(440)
            d.rectangle([x, y, x + rng.randrange(20, 200), y + rng.randrange(20, 200)],
                        fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
        im.save(os.path.join(folder, "IMG_%04d.jpg" % i), quality=80)


def start_server(sandbox, db_path, tuner_port, tagpup_port):
    import socket
    process = processes.start(
        [sys.executable, os.path.join(sandbox, "tagpup_web.py"), "--db", db_path,
         "--tuner-port", str(tuner_port), "--tagpup-port", str(tagpup_port)],
        cwd=sandbox,
        env=environment(sandbox, TAGPUP_NO_JOBS="1"),
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


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def show(title, rows, keys=None):
    print("\n" + title)
    for key in keys or [k for k in rows[0] if rows[0][k] is not None and not isinstance(rows[0][k], (dict, list, str))]:
        vals = [r.get(key) for r in rows]
        if all(v is None for v in vals):
            continue
        print("   %-34s median %10.1f   (%s)" % (key, med(vals), ", ".join("%.1f" % v if v is not None else "-" for v in vals)))


def plans(db_path):
    """The statements that read a source's whole id list, with their plans, on the sandbox copy (read-only)."""
    from tagpup.store import db, library_view as store
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        keyword = max(store.keyword_counts(conn), key=lambda node: node["count"])["tag"]
        folder = max(store.folders(conn), key=lambda node: node["direct"])["path"]
        sources = [("all", store.Source(store.ALL)), ("year", store.Source(store.YEAR, 2020)),
                   ("month", store.Source(store.MONTH, "2020-06")), ("keyword (largest node)", store.Source(store.KEYWORD, keyword)),
                   ("folder, with subfolders", store.Source(store.FOLDER, folder, True))]
        for label, source in sources:
            store.all_ids(conn, source, 200000)         # warm
            statements, elapsed = store.id_plans(conn, source, 200000)
            ids, total = store.all_ids(conn, source, 200000)
            print("\n  %-26s %6d ids in %6.1f ms (warm, in SQLite alone)" % (label, len(ids), elapsed))
            for _sql, lines in statements:
                for line in lines:
                    print("      " + line)
    finally:
        conn.close()


def drive(base, library_folder, args, results):
    from playwright.sync_api import sync_playwright
    root = "%s/%s/" % (base, LIBRARY)
    folder_url = "%s?path=%s" % (root, urllib.parse.quote(paths.stored(library_folder), safe=""))
    with sync_playwright() as pw:
        # precise memory info: without it Chromium rounds the JS heap to 10 MB steps
        browser = pw.chromium.launch(headless=not args.headed, args=["--enable-precise-memory-info"])
        counts = {}

        def new_page():
            ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
            page = ctx.new_page()
            page.add_init_script(INIT)
            seen = []

            def noted(request):
                for tag in ("/api/library/ids", "/api/library/cards", "/api/photo-thumb", "/api/photo-file", "/api/library/photo"):
                    if tag in request.url:
                        seen.append(tag)
                        return
            page.on("request", noted)
            page.route("**/api/photo-thumb*", lambda r: r.fulfill(body=TINY_PNG, content_type="image/png"))
            return ctx, page, seen

        # What the code can do: a view of the library, or only a folder.
        probe = new_page()
        status = probe[1].goto(root + "api/library/ids?kind=all", timeout=900000).status
        views = status == 200
        probe[0].close()
        print("\n%s (/api/library/ids answered %s)" % (
            "this code has views of the library" if views else "this code has no views of the library: the folder view only", status))

        if views:
            # The ids route itself, in the page: size and time, cold (the first ask also migrates the copy) then warm.
            ctx, page, _ = new_page()
            page.goto(root, wait_until="domcontentloaded")
            asks = []
            for n in range(4):
                asks.append(page.evaluate("""async () => { const t = performance.now();
                    const r = await fetch('/%s/api/library/ids?kind=all'); const text = await r.text();
                    return {ms: performance.now() - t, bytes: text.length, ids: JSON.parse(text).ids.length}; }""" % LIBRARY))
            floor = [page.evaluate("""async () => { const t = performance.now();
                const r = await fetch('/%s/api/library/cards?ids=1'); await r.text(); return performance.now() - t; }""" % LIBRARY)
                for _ in range(6)]
            ctx.close()
            results["ids_route"] = asks
            results["one_card_route_ms"] = floor
            print("\n    GET /api/library/ids?kind=all, in the page: %s" % ", ".join(
                "%.0f ms (%d ids, %.0f KB)" % (a["ms"], a["ids"], a["bytes"] / 1024) for a in asks))
            print("    the floor of any request of this server, GET /api/library/cards?ids=1: %s ms"
                  % ", ".join("%.0f" % ms for ms in floor))

            ctx, page, _ = new_page()
            page.goto(root, wait_until="domcontentloaded")
            lookups = page.evaluate("""async () => {
                const get = async (u) => (await fetch('/%s/api/library/navigator?section=' + u)).json();
                const kw = (await get('keywords')).keywords.sort((a, b) => b.count - a.count)[0];
                const fo = (await get('folders')).folders.sort((a, b) => b.direct - a.direct)[0];
                return {keyword: kw.tag, keywordCount: kw.count, folder: fo.path, folderCount: fo.direct};
            }""" % LIBRARY)
            ctx.close()
            print("    the largest keyword node holds %d photos; the largest library folder %d (names not printed)"
                  % (lookups["keywordCount"], lookups["folderCount"]))
            results["largest"] = {"keyword": lookups["keywordCount"], "folder": lookups["folderCount"]}

            def open_view(url, label, key):
                rows = []
                for n in range(args.rounds):
                    ctx, page, _ = new_page()
                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_function("() => window.__m && window.__m.marks.idle", timeout=120000)
                    m = page.evaluate("() => ({marks: window.__m.marks, longtasks: window.__m.longtasks,"
                                      " ids: (performance.getEntriesByType('resource').find(e => e.name.includes('/api/library/ids')) || {})})")
                    marks = m["marks"]
                    ids_end = m["ids"].get("responseEnd")
                    after = [x["d"] for x in m["longtasks"] if ids_end is not None and x["s"] + x["d"] >= ids_end]
                    rows.append({
                        "navigation_to_ids_reply_ms": ids_end,
                        "ids_reply_to_cards_in_dom_ms": marks["cards"] - ids_end if ids_end else None,
                        "ids_reply_to_painted_ms": marks["painted"] - ids_end if ids_end else None,
                        "ids_reply_to_painted_and_idle_ms": marks["paintedIdle"] - ids_end if ids_end else None,
                        "navigation_to_painted_ms": marks["painted"],
                        "navigation_to_thumbnails_asked_ms": marks.get("pictures"),
                        "navigation_to_thumbnails_asked_and_idle_ms": marks["idle"],
                        "ids_reply_to_thumbnails_asked_and_idle_ms": marks["idle"] - ids_end if ids_end else None,
                        "longest_task_after_ids_ms": max(after) if after else 0,
                        "ids_transfer_kb": (m["ids"].get("encodedBodySize") or 0) / 1024,
                    })
                    ctx.close()
                results[key] = rows
                show("%s (fresh browser each of %d)" % (label, args.rounds), rows)

            open_view(root + "?view=all", "(a) open ?view=all: the whole library", "open_all")
            open_view(root + "?view=keyword&value=" + urllib.parse.quote(lookups["keyword"], safe=""),
                      "(b) open the largest keyword node (%d photos)" % lookups["keywordCount"], "open_keyword")

            # (c) and (d) in one loaded page
            ctx, page, seen = new_page()
            page.goto(root + "?view=all", wait_until="domcontentloaded")
            page.wait_for_function("() => window.__m && window.__m.marks.idle", timeout=120000)
            page.evaluate(HELPERS_JS)
            page.wait_for_timeout(3000)
            info = page.evaluate("() => ({cards: __h.cards(), holes: __h.holes(), nodes: __h.nodes(),"
                                 " scrollHeightPx: __h.scroller().scrollHeight, heapMB: __h.heap() / 1e6})")
            print("\n    after opening: %s" % info)
            fly = []
            for n in range(args.rounds):
                del seen[:]
                r = page.evaluate("() => __h.flyover(10000)")
                page.wait_for_timeout(1500)
                for tag, name in (("/api/library/ids", "ids"), ("/api/library/cards", "cards"), ("/api/photo-thumb", "thumbnails")):
                    r["requests_" + name] = seen.count(tag)
                r["cards_per_second"] = r["requests_cards"] / 10.0
                fly.append(r)
                page.wait_for_timeout(1000)
            results["scroll_all"] = fly
            show("(c) scroll `all` top to bottom in 10 s (%d rounds)" % args.rounds, fly)

            jumps = []
            for fraction in (0.5, 0.17, 0.83, 0.5)[:max(args.rounds, 3) + 1]:
                del seen[:]
                page.evaluate("() => { __h.scroller().scrollTop = 0; }")
                page.wait_for_timeout(800)
                j = page.evaluate("async (f) => await __h.jump(f)", fraction)
                j["card_requests"] = seen.count("/api/library/cards")
                jumps.append(j)
                page.wait_for_timeout(500)
            results["jump"] = jumps
            show("(d) drag the scroll bar to the middle and elsewhere: until the cards in view are real and painted", jumps)
            ctx.close()

            open_view("%s?view=folder&value=%s" % (root, urllib.parse.quote(lookups["folder"], safe="")),
                      "(e) library view of the largest library folder (%d photos)" % lookups["folderCount"], "open_library_folder")

        # (e) the folder view, as 9b-1 measured it
        rows = []
        for n in range(args.rounds + 1):    # the first scan reads every file: not counted
            ctx, page, _ = new_page()
            page.goto(folder_url, wait_until="domcontentloaded")
            page.wait_for_function("() => window.__m && window.__m.marks.idle", timeout=600000)
            m = page.evaluate("() => ({marks: window.__m.marks, longtasks: window.__m.longtasks,"
                              " scan: (performance.getEntriesByType('resource').find(e => e.name.includes('/api/folder/scan')) || {})})")
            scan_end = m["scan"].get("responseEnd")
            after = [x["d"] for x in m["longtasks"] if scan_end is not None and x["s"] + x["d"] >= scan_end]
            if n:
                rows.append({
                    "scan_reply_to_painted_ms": m["marks"]["painted"] - scan_end if scan_end else None,
                    "scan_reply_to_painted_and_idle_ms": m["marks"]["paintedIdle"] - scan_end if scan_end else None,
                    "scan_reply_to_thumbnails_asked_and_idle_ms": m["marks"]["idle"] - scan_end if scan_end else None,
                    "longest_task_after_scan_ms": max(after) if after else 0,
                })
            ctx.close()
        results["open_folder"] = rows
        show("(e) open the %d-photo folder view (fresh browser each of %d)" % (args.photos, args.rounds), rows)

        ctx, page, seen = new_page()
        page.goto(folder_url, wait_until="domcontentloaded")
        page.wait_for_function("() => window.__m && window.__m.marks.idle", timeout=600000)
        page.evaluate(HELPERS_JS)
        page.wait_for_timeout(3000)
        fly = []
        for n in range(args.rounds):
            r = page.evaluate("() => __h.flyover(3000)")
            page.wait_for_timeout(1500)
            fly.append(r)
        results["scroll_folder"] = fly
        show("(e) scroll the folder view top to bottom in 3 s", fly,
             ["frames", "maxFrame", "p95Frame", "over100", "longest", "peakNodes", "peakHeapMB"])
        ctx.close()
        browser.close()
        del counts


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="do it; without this the plan is printed and nothing is done")
    parser.add_argument("--code-root", default=REPO_ROOT, help="the checkout whose code the sandbox serves (default: this one)")
    parser.add_argument("--source", default=tagpup_config.library_path("photo_index.db"),
                        help="the library to copy; opened read-only and never written")
    parser.add_argument("--photos", type=int, default=759, help="photos in the folder of (e)")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", default="", help="also write the numbers here as JSON")
    parser.add_argument("--headed", action="store_true", help="show the browser; slower, closer to a person's")
    parser.add_argument("--keep-sandbox", default="", help="keep the sandbox in this folder (made if it is not there, reused if it is: skips the copy)")
    return parser.parse_args()


def print_plan(args):
    print("measure_library_view.py would, with --run:")
    print("  copy %s (read-only source) into a sandbox under %s, its roots placed at empty sandbox folders"
          % (args.source, tempfile.gettempdir()))
    print("  serve the code of %s from that sandbox on a free port" % args.code_root)
    print("  in headless Chromium: open ?view=all and the largest keyword node (%d fresh browsers each), scroll `all` top to bottom"
          " in 10 s, drag the scroll bar to the middle; open the largest library folder as a view; open and scroll a"
          " %d-photo folder view" % (args.rounds, args.photos))
    print("  print the numbers and the query plans of the id lists, then delete the sandbox")
    print("Nothing was done.")


def main():
    args = parse_args()
    if not args.run:
        print_plan(args)
        return
    if not os.path.exists(args.source):
        sys.exit("%s does not exist (is TAGPUP_HOME set? or pass --source)" % args.source)
    sandbox = args.keep_sandbox or tempfile.mkdtemp(prefix="tagpup_measure_")
    server = None
    results = {"code_root": args.code_root}
    try:
        print("sandbox : %s" % sandbox)
        db_path = os.path.join(sandbox, "data", LIBRARY + ".db")
        if args.keep_sandbox and os.path.exists(db_path):
            for name in ("scripts", "tagpup", "web"):
                shutil.rmtree(os.path.join(sandbox, name), ignore_errors=True)
            copy_code(sandbox, code_root=args.code_root, launchers=True)
        else:
            os.makedirs(os.path.join(sandbox, "data"), exist_ok=True)
            copy_code(sandbox, code_root=args.code_root, launchers=True)
            copy_library(args.source, db_path)
            place_roots(db_path, sandbox)
        roots = os.path.join(sandbox, "roots")
        place = os.path.join(roots, sorted(os.listdir(roots))[0])
        folder = os.path.join(place, "measure_big")
        if not os.path.isdir(folder):
            make_photos(folder, args.photos)
        tuner, tagpup = free_port(), free_port()
        server = start_server(sandbox, db_path, tuner, tagpup)
        print("  serving the code of %s on port %d" % (args.code_root, tagpup))
        drive("http://127.0.0.1:%d" % tagpup, folder, args, results)
        try:
            print("\nthe query plans of the id lists, on the copy (the server brought it up to date as it opened it):")
            enter(sandbox)     # the sandbox's own machine map places its roots
            plans(db_path)
        except (sqlite3.Error, ImportError, AttributeError) as why:
            print("  (no plans: %s)" % why)
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
        if args.keep_sandbox:
            print("sandbox kept at %s" % sandbox)
        else:
            print("sandbox deleted: %s" % remove_sandbox(sandbox))


if __name__ == "__main__":
    main()
