"""Time TagPup's folder grid the way a person uses it: in a real Chromium, in a sandbox.

The numbers in docs/ARCHITECTURE.md, "Phase 9b-1", come from this. It measures, on a folder of N
photos (759 by default, the size of photo_index's largest folder) and on a synthetic list of 20,000
records through the same folder view:

  (a) opening the folder: the scan's reply to the cards painted and the main thread idle;
  (b) scrolling it top to bottom: DOM nodes at the peak, longest task, frame intervals, pictures asked for;
  (c) Select all, Invert, Select none: in the handler, and until painted;
  (d) the 20,000 records: the render, a scroll, and the three selections;
  and an empty page's frame interval, the floor the others are read against.

    .venv/Scripts/python.exe scripts/measure_grid.py                  # prints what it would do, does nothing
    .venv/Scripts/python.exe scripts/measure_grid.py --run
    .venv/Scripts/python.exe scripts/measure_grid.py --run --code-root <another checkout>   # the baseline

Take the baseline first, on the code before a change: `git archive <commit> | tar -x -C <folder>`, then
--code-root <folder>. The sandbox serves that tree's code. The browser is half the system: these are
timings of the page in Chromium, not of the server.

Everything runs in a sandbox, built and deleted by scripts/sandbox.py: the library is copied through
SQLite's backup API (the original read-only), under a temporary TAGPUP_HOME of its own, its roots placed
at empty sandbox folders by the sandbox's own machine map, on a free port; nothing in data/ is touched
and nothing is visible to an app you have open. The photos are JPEGs generated here: no real photo is
read or copied. Headless Chromium has no extensions and a fresh profile, so the absolute numbers come
out low; use them for comparison. Run to run they move by up to 40%.
"""
import argparse
import json
import os
import random
import shutil
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
from sandbox import copy_library, free_port, place_roots, remove_sandbox  # noqa: E402

INIT = r"""
window.__m = { longtasks: [], marks: {} };
try {
  new PerformanceObserver(l => l.getEntries().forEach(e => window.__m.longtasks.push({s: e.startTime, d: e.duration})))
    .observe({type: 'longtask', buffered: true});
} catch (e) {}
const origJson = Response.prototype.json;
Response.prototype.json = function () {
  return origJson.call(this).then(v => {
    if (String(this.url).includes('/api/folder/scan')) window.__m.marks.scanDone = performance.now();
    return v;
  });
};
document.addEventListener('DOMContentLoaded', () => {
  const grid = document.getElementById('thumbnails-grid');
  new MutationObserver(() => {
    if (window.__m.marks.cards || !grid.querySelector('.thumbnail-card')) return;
    window.__m.marks.cards = performance.now();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      window.__m.marks.painted = performance.now();
      const t = performance.now();
      setTimeout(() => { window.__m.marks.idle = performance.now(); window.__m.marks.idleLag = performance.now() - t; }, 0);
    }));
  }).observe(grid, {childList: true, subtree: true});
});
"""

HELPERS_JS = r"""
window.__h = {
  nodes: () => document.querySelectorAll('#thumbnails-grid *').length,
  cards: () => document.querySelectorAll('#thumbnails-grid .thumbnail-card').length,
  scroller: () => document.getElementById('folder-view-main'),
  async settle() {
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    await new Promise(r => setTimeout(r, 0));
  },
  async timed(fn) {
    const t = performance.now();
    const before = window.__m.longtasks.length;
    fn();
    const sync = performance.now() - t;
    await window.__h.settle();
    const total = performance.now() - t;
    const lt = window.__m.longtasks.slice(before).map(x => x.d);
    return {sync, total, longest: lt.length ? Math.max(...lt) : 0};
  },
  async flyover(ms) {
    const sc = window.__h.scroller();
    sc.scrollTop = 0;
    await window.__h.settle();
    const total = sc.scrollHeight - sc.clientHeight;
    const frames = [];
    let peak = 0;
    const lt0 = window.__m.longtasks.length;
    let last = performance.now();
    const start = last;
    await new Promise(res => {
      function step(now) {
        frames.push(now - last); last = now;
        const p = Math.min(1, (now - start) / ms);
        sc.scrollTop = p * total;
        peak = Math.max(peak, window.__h.nodes());
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
      longest: lt.length ? Math.max(...lt) : 0, longTasks: lt.length, peakNodes: peak,
    };
  },
  selectAll() { document.getElementById('btn-select-all-thumbnails').click(); },
  selectNone() { document.getElementById('btn-select-none-thumbnails').click(); },
  invert() {
    const grid = document.getElementById('thumbnails-grid');
    const target = grid.querySelector('.thumbnail-card') || grid;
    target.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 200, clientY: 200}));
    document.querySelector('#grid-context-menu [data-action="invert"]').click();
  },
  selectedLabel: () => document.getElementById('selected-thumbnails-count').textContent,
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
        env=dict(os.environ, TAGPUP_HOME=sandbox, TAGPUP_NO_JOBS="1"),
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
    return statistics.median(xs) if xs else None


def drive(base, folder, args, results):
    from playwright.sync_api import sync_playwright
    url = "%s/measured/?path=%s" % (base, urllib.parse.quote(paths.stored(folder), safe=""))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)

        def new_page(route_images=False):
            ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
            page = ctx.new_page()
            page.add_init_script(INIT)
            requests = []
            page.on("request", lambda r: requests.append(r.url) if "/api/photo-file" in r.url else None)
            if route_images:
                page.route("**/api/photo-file*", lambda r: r.fulfill(body=TINY_PNG, content_type="image/png"))
            return ctx, page, requests

        # warm-up: the first scan reads every file; later ones answer from the library
        ctx, page, _ = new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector("#thumbnails-grid .thumbnail-card", timeout=600000)
        page.wait_for_timeout(2000)
        ctx.close()

        opens = []
        for n in range(args.rounds):
            ctx, page, _ = new_page()
            t0 = time.time()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_function("() => window.__m && window.__m.marks.idle", timeout=600000)
            wall = time.time() - t0
            m = page.evaluate("() => ({marks: window.__m.marks, longtasks: window.__m.longtasks,"
                              " nodes: window.__h ? 0 : 0})")
            marks = m["marks"]
            sd = marks.get("scanDone")
            after = [x["d"] for x in m["longtasks"] if sd is not None and x["s"] + x["d"] >= sd]
            opens.append({
                "wall_s": wall,
                "scan_to_cards_dom_ms": marks["cards"] - sd if sd else None,
                "scan_to_painted_ms": marks["painted"] - sd if sd else None,
                "scan_to_idle_ms": marks["idle"] - sd if sd else None,
                "longest_task_after_scan_ms": max(after) if after else 0,
                "task_total_after_scan_ms": sum(after),
            })
            ctx.close()
        results["open"] = opens
        keys = [k for k in opens[0] if opens[0][k] is not None]
        print("\n(a) open the %d-photo folder (fresh browser each of %d; scan reply -> ...)" % (args.photos, args.rounds))
        for k in keys:
            vals = [o[k] for o in opens]
            print("   %-30s median %9.1f   (%s)" % (k, med(vals), ", ".join("%.1f" % v for v in vals)))

        # (b), (c) in one loaded page
        ctx, page, requests = new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_function("() => window.__m && window.__m.marks.idle", timeout=600000)
        page.evaluate(HELPERS_JS)
        page.wait_for_timeout(4000)
        info = page.evaluate("() => ({cards: __h.cards(), nodes: __h.nodes(),"
                             " h: __h.scroller().scrollHeight, view: __h.scroller().clientHeight})")
        print("\n    after opening: %s" % info)
        results["after_open"] = info

        fly = []
        for n in range(args.rounds):
            requests.clear()
            page.evaluate("() => { performance.clearResourceTimings(); performance.setResourceTimingBufferSize(30000); }")
            r = page.evaluate("() => __h.flyover(3000)")
            page.wait_for_timeout(1500)
            r["image_requests"] = len(requests)
            res = page.evaluate("() => { const e = performance.getEntriesByType('resource').filter(x => x.name.includes('/api/photo-file'));"
                                " return {done: e.length, network: e.filter(x => x.transferSize > 0).length}; }")
            r["pictures_done"] = res["done"]
            r["pictures_from_network"] = res["network"]
            fly.append(r)
            page.wait_for_timeout(1500)
        results["scroll"] = fly
        print("\n(b) scroll the folder top to bottom in 3 s, %d rounds" % args.rounds)
        for k in fly[0]:
            vals = [f[k] for f in fly]
            print("   %-30s median %9.1f   (%s)" % (k, med(vals), ", ".join("%.1f" % v for v in vals)))

        sel = []
        for n in range(args.rounds):
            row = {}
            for name in ("selectAll", "invert", "selectNone"):
                if name == "invert":
                    page.evaluate("() => __h.selectNone()")  # invert of nothing = all; start from a half
                    page.evaluate("() => { const c=[...document.querySelectorAll('#thumbnails-grid .thumbnail-checkbox')];"
                                  " c.slice(0, 5).forEach(x => x.click()); }")
                row[name] = page.evaluate("async () => await __h.timed(() => __h.%s())" % name)
                row[name]["label"] = page.evaluate("() => __h.selectedLabel()")
            sel.append(row)
        results["select"] = sel
        print("\n(c) select all, invert, select none (ms: sync / until painted+idle / longest task)")
        for name in ("selectAll", "invert", "selectNone"):
            print("   %-12s sync %8.1f  total %8.1f  longest %8.1f   (%s)" % (
                name, med([r[name]["sync"] for r in sel]), med([r[name]["total"] for r in sel]),
                med([r[name]["longest"] for r in sel]), sel[-1][name]["label"]))
        ctx.close()

        # (d) synthetic: 20,000 records through the folder view, thumbnails answered locally
        ctx, page, requests = new_page(route_images=True)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_function("() => window.__m && window.__m.marks.idle", timeout=600000)
        page.evaluate(HELPERS_JS)
        page.wait_for_timeout(1500)
        n = args.synthetic
        t = page.evaluate("""async (n) => {
            const { state } = await import('./state.js');
            const grid = await import('./grid.js');
            const photos = [];
            for (let i = 0; i < n; i++) {
                photos.push({path: 'D:\\\\synthetic\\\\2020\\\\IMG_' + String(i).padStart(5, '0') + '.jpg',
                    filename: 'IMG_' + String(i).padStart(5, '0') + '.jpg', tags: [], people: [], title: '',
                    mtime: 1600000000, size: 1024, year: '2020', raw_metadata: {}});
            }
            state.folderPhotos = photos;
            const lt0 = window.__m.longtasks.length;
            const t = performance.now();
            grid.renderThumbnails();
            const sync = performance.now() - t;
            await __h.settle();
            const total = performance.now() - t;
            const lt = window.__m.longtasks.slice(lt0).map(x => x.d);
            return {sync, total, longest: lt.length ? Math.max(...lt) : 0, nodes: __h.nodes(), cards: __h.cards(),
                    scrollHeight: __h.scroller().scrollHeight};
        }""", n)
        results["synthetic_render"] = t
        print("\n(d) %d synthetic records in the folder view" % n)
        print("   render: sync %.0f ms, until painted+idle %.0f ms, longest task %.0f ms; cards in DOM %d, nodes %d"
              % (t["sync"], t["total"], t["longest"], t["cards"], t["nodes"]))
        page.wait_for_timeout(2000)
        requests.clear()
        page.evaluate("() => { performance.clearResourceTimings(); }")
        r = page.evaluate("() => __h.flyover(10000)")
        r["image_requests"] = len(requests)
        results["synthetic_scroll"] = r
        print("   scroll top to bottom in 10 s: %s" % json.dumps(r))
        t2 = []
        for name in ("selectAll", "invert", "selectNone"):
            if name == "invert":
                page.evaluate("() => __h.selectNone()")
            x = page.evaluate("async () => await __h.timed(() => __h.%s())" % name)
            x["label"] = page.evaluate("() => __h.selectedLabel()")
            t2.append((name, x))
            print("   %-10s sync %8.1f total %8.1f longest %8.1f  %s" % (name, x["sync"], x["total"], x["longest"], x["label"]))
        results["synthetic_select"] = dict(t2)
        ctx.close()

        # the floor: how often this Chromium runs an animation frame on a page with nothing in it
        blank = browser.new_page(viewport={"width": 1600, "height": 1000})
        gaps = blank.evaluate("""() => new Promise(res => { const g = []; let last = performance.now(), n = 0;
            function f(now) { g.push(now - last); last = now; if (++n < 60) requestAnimationFrame(f); else res(g); }
            requestAnimationFrame(f); })""")
        gaps.sort()
        results["blank_page_frame_ms"] = {"median": gaps[len(gaps) // 2], "p95": gaps[int(len(gaps) * 0.95)]}
        print("\nfloor: an empty page's animation frames in this Chromium: median %.1f ms, p95 %.1f ms"
              % (results["blank_page_frame_ms"]["median"], results["blank_page_frame_ms"]["p95"]))
        browser.close()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="do it; without this the plan is printed and nothing is done")
    parser.add_argument("--code-root", default=REPO_ROOT, help="the checkout whose code the sandbox serves (default: this one)")
    parser.add_argument("--source", default=tagpup_config.library_path("photo_index.db"),
                        help="the library to copy; opened read-only and never written")
    parser.add_argument("--photos", type=int, default=759, help="photos in the folder opened (a), (b), (c)")
    parser.add_argument("--synthetic", type=int, default=20000, help="records for (d)")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", default="", help="also write the numbers here as JSON")
    parser.add_argument("--headed", action="store_true", help="show the browser; slower, closer to a person's")
    parser.add_argument("--keep-sandbox", default="", help="reuse a sandbox folder an earlier run left (skips the copy)")
    return parser.parse_args()


def print_plan(args):
    print("measure_grid.py would, with --run:")
    print("  copy %s (read-only source) into a sandbox under %s, its roots placed at empty sandbox folders"
          % (args.source, tempfile.gettempdir()))
    print("  serve the code of %s from that sandbox on a free port" % args.code_root)
    print("  generate %d synthetic JPEGs there and, in headless Chromium, %d times: open the folder, scroll it,"
          " Select all / Invert / Select none; then %d synthetic records through the folder view"
          % (args.photos, args.rounds, args.synthetic))
    print("  print the numbers, then delete the sandbox")
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
        if args.keep_sandbox:
            db_path = os.path.join(sandbox, "data", "measured.db")
            for name in ("scripts", "tagpup", "web"):
                shutil.rmtree(os.path.join(sandbox, name), ignore_errors=True)
            copy_code(sandbox, code_root=args.code_root, launchers=True)
        else:
            os.makedirs(os.path.join(sandbox, "data"), exist_ok=True)
            copy_code(sandbox, code_root=args.code_root, launchers=True)
            db_path = os.path.join(sandbox, "data", "measured.db")
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
