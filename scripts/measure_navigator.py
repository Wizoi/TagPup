"""Time TagPup's navigator and the keyboard the way a person uses them: in a real Chromium, in a sandbox.

The numbers in docs/ARCHITECTURE.md, "Phase 9c", come from this. On a copy of photo_index (68,472 photos when this was
written; 2,746 folders, 895 keyword nodes, 413 people) it measures, each a person's action and not an endpoint:

  (a) the Library pane opened: from the click on its switch to the first tab's (Folders) rows painted and the main
      thread idle -- the first one after the server started is cold, the others warm;
  (b) the folder tree opened to depth 3 by three clicks: the time of each click until painted and idle;
  (c) a click on the largest keyword node (~41,000 photos) in the Keywords tab: from the click to the first window of
      cards painted and idle, against the same view opened by its address;
  (d) typing in the People filter: the longest main-thread task, and the time from the last key to the list redrawn;
  (e) ArrowDown held through 2,000 photos (30 keys a second, as a keyboard repeats): frame times, long tasks, peak DOM
      nodes, and that the focus never left the grid;
  (f) the stale marks: GET /api/library/cards for 200 ids, the cost of the 200 stats (the sandbox holds no photos, so
      every file is missing; the cost of a stat of a file that is there is measured on 200 real local files here);
  (g) the folder view of a 759-photo folder opened and scrolled, which this stage changed the cards of (labels, roles,
      a tab stop): run the trunk's code the same way (--code-root) to see it did not slow;
  (h) a click on the library's largest folder row (68,000+ photos, with 68,000 empty photo files made under it, so that a
      walk of it has something to walk): the membership requests it makes, how long they take, and click to painted and idle --
      the banner must not walk a folder that large unasked.

    .venv/Scripts/python.exe scripts/measure_navigator.py                  # prints the plan, does nothing
    .venv/Scripts/python.exe scripts/measure_navigator.py --run
    .venv/Scripts/python.exe scripts/measure_navigator.py --run --code-root <a `git archive` of the trunk> --only f,g

There is no navigator on the trunk, so (a) to (e) have no baseline: they are read against the budgets of 9b-2's table
(a window of cards painted in 40 to 60 ms, no task over 50 ms while scrolling). (f) and (g) run on the trunk, and are the
comparison. Everything runs in a sandbox, built and deleted by scripts/sandbox.py: the library is copied through SQLite's
backup API (the original read-only) under a temporary TAGPUP_HOME of its own, its roots placed at empty sandbox folders by
the sandbox's own map, on a free port. Nothing in data/ is touched and nothing is visible to an app you have open. A
thumbnail's request is answered a small picture by the browser's routing. No name of a keyword, folder or person is printed,
only counts. Headless Chromium has no extensions and a fresh profile, so absolute numbers come out low; use them for
comparison. Run to run they move by up to 40%.
"""
import argparse
import json
import math
import os
import random
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
from sandbox import copy_library, environment, free_port, place_roots, remove_sandbox  # noqa: E402

LIBRARY = "measured"
EVERYTHING = "abcdefgh"

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
      setTimeout(() => { m.paintedIdle = performance.now(); }, 0);
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
window.__n = {
  nodes: (selector) => document.querySelectorAll(selector).length,
  rows: (section) => [...document.querySelectorAll('#nav-panel-' + section + ' .nav-row')],
  async idle() {
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    await new Promise(r => setTimeout(r, 0));
  },
  count(row) { return Number(row.querySelector('.nav-count').textContent.replace(/,/g, '')); },
  // Start timing at the next click (its capture phase) and finish when the predicate holds, painted and idle.
  timed(predicate, timeout) {
    window.__p = new Promise(resolve => {
      let t0 = null;
      const lt0 = window.__m.longtasks.length;
      document.addEventListener('click', () => { if (t0 === null) t0 = performance.now(); }, {capture: true, once: true});
      (async function poll() {
        for (;;) {
          if (t0 !== null && predicate()) {
            const seen = performance.now() - t0;
            await window.__n.idle();
            const lt = window.__m.longtasks.slice(lt0).map(x => x.d);
            resolve({ms: performance.now() - t0, firstSeenMs: seen, longest: lt.length ? Math.max(...lt) : 0,
                     nodes: document.querySelectorAll('#sidebar-pane-library *').length});
            return;
          }
          if (t0 !== null && performance.now() - t0 > timeout) { resolve({timeout: true}); return; }
          await new Promise(r => requestAnimationFrame(r));
        }
      })();
    });
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


def make_empty_photos(folder, count, per_folder=1000):
    """`count` empty files named like photos, `per_folder` to a folder: enough for a walk of the folder to have something to walk."""
    for n in range(count):
        if n % per_folder == 0:
            os.makedirs(os.path.join(folder, "d%03d" % (n // per_folder)), exist_ok=True)
        open(os.path.join(folder, "d%03d" % (n // per_folder), "IMG_%06d.jpg" % n), "wb").close()


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
    if not rows:
        print("   (nothing measured)")
        return
    for key in keys or [k for k in rows[0] if rows[0][k] is not None and not isinstance(rows[0][k], (dict, list, str))]:
        vals = [r.get(key) for r in rows]
        if all(v is None for v in vals):
            continue
        print("   %-34s median %10.1f   (%s)" % (key, med(vals), ", ".join("%.1f" % v if v is not None else "-" for v in vals)))


def percentile(values, share):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * share))] if values else None


def drive(base, library_folder, args, results):
    from playwright.sync_api import sync_playwright
    root = "%s/%s/" % (base, LIBRARY)
    folder_url = "%s?path=%s" % (root, urllib.parse.quote(paths.stored(library_folder), safe=""))
    wanted = set(args.only)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed, args=["--enable-precise-memory-info"])

        def new_page():
            ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
            page = ctx.new_page()
            page.add_init_script(INIT)
            seen = []
            page.on("request", lambda request: seen.append(request.url))
            page.route("**/api/photo-thumb*", lambda r: r.fulfill(body=TINY_PNG, content_type="image/png"))
            return ctx, page, seen

        def ready(page, url):
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_function("() => window.__m && document.getElementById('sidebar-switch')", timeout=120000)
            page.evaluate(HELPERS_JS)

        probe = new_page()
        status = probe[1].goto(root + "api/library/navigator?section=people", timeout=900000).status
        probe[0].close()
        navigator = status == 200
        print("\n%s (/api/library/navigator answered %s)" % (
            "this code has the navigator" if navigator else "this code has no navigator: only (f) and (g) can run", status))
        # What the library holds, from the route (counts only are printed).
        ctx, page, _ = new_page()
        page.goto(root, wait_until="domcontentloaded")
        facts = {}
        if navigator:
            facts = page.evaluate("""async () => {
                const get = async (s) => (await fetch('/%s/api/library/navigator?section=' + s)).json();
                const kw = (await get('keywords')).keywords;
                const top = kw.slice().sort((a, b) => b.count - a.count)[0];
                const chain = []; let at = top;
                const byTag = new Map(kw.map(n => [n.tag, n]));
                while (at) { chain.unshift(at.tag); at = at.parent ? byTag.get(at.parent) : null; }
                const people = (await get('people')).people;
                const folders = (await get('folders')).folders;
                const dates = await get('dates');
                const commonest = people.slice().sort((a, b) => b.count - a.count)[0].name;
                return {keyword: top.tag, keywordCount: top.count, chain, nodes: kw.length, people: people.length,
                        folders: folders.length, years: dates.years.length, typed: commonest.slice(0, 5).toLowerCase()};
            }""" % LIBRARY)
            print("    the library holds %d folders, %d keyword nodes, %d people, %d years; the largest keyword node holds %d photos"
                  % (facts["folders"], facts["nodes"], facts["people"], facts["years"], facts["keywordCount"]))
            results["library"] = {k: facts[k] for k in ("folders", "nodes", "people", "years", "keywordCount")}
        ctx.close()

        if navigator and "a" in wanted:
            rows = []
            for n in range(args.rounds + 1):
                ctx, page, _ = new_page()
                ready(page, root)
                page.evaluate("() => window.__n.timed(() => document.querySelector('#nav-panel-folders .nav-row'), 60000)")
                page.click("#sidebar-tab-library")
                r = page.evaluate("() => window.__p")
                ask = page.evaluate("""() => { const e = performance.getEntriesByType('resource').find(e => e.name.includes('section=folders'));
                    return e ? {ttfb: e.responseStart - e.requestStart, total: e.responseEnd - e.startTime, kb: e.encodedBodySize / 1024} : {}; }""")
                r.update({"request_ttfb_ms": ask.get("ttfb"), "request_total_ms": ask.get("total"), "reply_kb": ask.get("kb"),
                          "rows": page.evaluate("() => window.__n.rows('folders').length")})
                r["cold"] = n == 0
                rows.append(r)
                ctx.close()
            results["open_pane_cold"] = rows[:1]
            results["open_pane_warm"] = rows[1:]
            show("(a) the Library pane opened: click to the first tab's rows painted and idle -- COLD (the first after the server started)", rows[:1])
            show("(a) the same, WARM (fresh browser each of %d)" % args.rounds, rows[1:])

        if navigator and "b" in wanted:
            rows = []
            for n in range(args.rounds):
                ctx, page, _ = new_page()
                ready(page, root)
                page.click("#sidebar-tab-library")
                page.wait_for_selector("#nav-panel-folders .nav-row", timeout=60000)
                page.evaluate("() => window.__n.idle()")
                steps = []
                for level in (1, 2, 3):
                    index = page.evaluate("""(level) => { const c = window.__n.rows('folders')
                        .map((r, i) => ({r, i})).filter(x => x.r.getAttribute('aria-level') == level && x.r.getAttribute('aria-expanded') === 'false');
                        c.sort((a, b) => window.__n.count(b.r) - window.__n.count(a.r)); return c.length ? c[0].i : -1; }""", level)
                    if index < 0:
                        break
                    before = page.evaluate("() => window.__n.rows('folders').length")
                    page.evaluate("(b) => { window.__n.timed(() => window.__n.rows('folders').length > b, 30000); }", before)
                    page.locator("#nav-panel-folders .nav-row").nth(index).locator(".nav-twisty").click()
                    r = page.evaluate("() => window.__p")
                    r["rows_after"] = page.evaluate("() => window.__n.rows('folders').length")
                    r["rows_before"] = before
                    steps.append(r)
                rows.append({"click%d_ms" % (i + 1): s.get("ms") for i, s in enumerate(steps)}
                            | {"click%d_longest_task" % (i + 1): s.get("longest") for i, s in enumerate(steps)}
                            | {"rows_after_3": steps[-1]["rows_after"] if steps else None})
                ctx.close()
            results["expand"] = rows
            show("(b) the folder tree opened to depth 3 by three clicks: each until painted and idle", rows)

        if navigator and "c" in wanted:
            clicks, urls = [], []
            for n in range(args.rounds):
                ctx, page, seen = new_page()
                ready(page, root)
                page.click("#sidebar-tab-library")
                page.click("#nav-tab-keywords")
                page.wait_for_selector("#nav-panel-keywords .nav-row", timeout=60000)
                for tag in facts["chain"][:-1]:
                    page.evaluate("""(id) => { const row = window.__n.rows('keywords').find(r => r.dataset.row === id);
                        if (row && row.getAttribute('aria-expanded') === 'false') row.querySelector('.nav-twisty').click(); }""", "k:" + tag)
                    page.evaluate("() => window.__n.idle()")
                del seen[:]
                page.evaluate("() => { window.__m.marks = {}; }")
                page.evaluate("() => window.__n.timed(() => window.__m.marks.paintedIdle, 60000)")
                page.locator('#nav-panel-keywords .nav-row[data-row="%s"]' % ("k:" + facts["chain"][-1]).replace('"', '\\"')).click()
                r = page.evaluate("() => window.__p")
                m = page.evaluate("""() => { const all = performance.getEntriesByType('resource');
                    const e = all.filter(e => e.name.includes('/api/library/ids')).pop() || {};
                    const c = all.filter(e => e.name.includes('/api/library/cards') && e.startTime >= (e.startTime && (all.filter(x => x.name.includes('/api/library/ids')).pop() || {}).responseEnd || 0))[0] || {};
                    return {marks: window.__m.marks, idsEnd: e.responseEnd, idsStart: e.startTime, kb: (e.encodedBodySize || 0) / 1024,
                            cardsAsked: c.startTime, cardsEnd: c.responseEnd}; }""")
                clicks.append({
                    "click_to_painted_and_idle_ms": r.get("ms"),
                    "ids_reply_to_painted_ms": m["marks"]["painted"] - m["idsEnd"] if m["idsEnd"] else None,
                    "ids_reply_to_painted_and_idle_ms": m["marks"]["paintedIdle"] - m["idsEnd"] if m["idsEnd"] else None,
                    "longest_task_after_click_ms": r.get("longest"), "ids_reply_kb": m["kb"],
                    "ids_reply_to_cards_asked_ms": m["cardsAsked"] - m["idsEnd"] if m.get("cardsAsked") and m["idsEnd"] else None,
                    "cards_request_ms": m["cardsEnd"] - m["cardsAsked"] if m.get("cardsAsked") and m.get("cardsEnd") else None,
                })
                ctx.close()
                ctx, page, _ = new_page()
                page.goto(root + "?view=keyword&value=" + urllib.parse.quote(facts["chain"][-1], safe=""), wait_until="domcontentloaded")
                page.wait_for_function("() => window.__m && window.__m.marks.paintedIdle", timeout=120000)
                m = page.evaluate("""() => { const e = performance.getEntriesByType('resource').find(e => e.name.includes('/api/library/ids')) || {};
                    return {marks: window.__m.marks, idsEnd: e.responseEnd, longtasks: window.__m.longtasks}; }""")
                after = [x["d"] for x in m["longtasks"] if m["idsEnd"] and x["s"] + x["d"] >= m["idsEnd"]]
                urls.append({
                    "navigation_to_painted_and_idle_ms": m["marks"]["paintedIdle"],
                    "ids_reply_to_painted_ms": m["marks"]["painted"] - m["idsEnd"] if m["idsEnd"] else None,
                    "ids_reply_to_painted_and_idle_ms": m["marks"]["paintedIdle"] - m["idsEnd"] if m["idsEnd"] else None,
                    "longest_task_after_ids_ms": max(after) if after else 0,
                })
                ctx.close()
            results["click_keyword"], results["url_keyword"] = clicks, urls
            show("(c) a click on the largest keyword node (%d photos) in the Keywords tab" % facts["keywordCount"], clicks)
            show("(c) the same view opened by its address (a fresh browser each)", urls)

        if navigator and "d" in wanted:
            rows = []
            for n in range(args.rounds):
                ctx, page, _ = new_page()
                ready(page, root)
                page.click("#sidebar-tab-library")
                page.click("#nav-tab-people")
                page.wait_for_selector("#nav-panel-people .nav-row", timeout=60000)
                page.evaluate("() => window.__n.idle()")
                before = page.evaluate("() => ({rows: window.__n.rows('people').length, lt: window.__m.longtasks.length})")
                page.focus("#nav-panel-people .nav-filter")
                page.keyboard.type(facts["typed"], delay=60)
                last = time.time()
                page.wait_for_function("() => window.__n.rows('people').length < %d" % max(before["rows"], 2), timeout=10000)
                page.evaluate("() => window.__n.idle()")
                after = page.evaluate("() => ({rows: window.__n.rows('people').length, lt: window.__m.longtasks.slice(%d).map(x => x.d)})" % before["lt"])
                rows.append({"last_key_to_redrawn_ms": (time.time() - last) * 1000,
                             "longest_task_ms": max(after["lt"]) if after["lt"] else 0, "long_tasks": len(after["lt"]),
                             "rows_before": before["rows"], "rows_after": after["rows"]})
                ctx.close()
            results["people_filter"] = rows
            show("(d) typing %d letters in the People filter (60 ms between keys)" % len(facts["typed"]), rows)

        if navigator and "e" in wanted:
            rows = []
            for n in range(args.rounds):
                ctx, page, seen = new_page()
                ready(page, root + "?view=all")
                page.wait_for_function("() => window.__m.marks.idle", timeout=120000)
                page.wait_for_timeout(2000)
                columns = page.evaluate("() => getComputedStyle(document.getElementById('thumbnails-grid')).gridTemplateColumns.split(' ').length")
                presses = math.ceil(2000 / columns)
                page.focus("#thumbnails-grid .thumbnail-card:not(.placeholder)")
                page.evaluate("""() => { const grid = document.getElementById('thumbnails-grid');
                    window.__f = {frames: [], peak: 0, lost: 0, last: performance.now(), lt0: window.__m.longtasks.length, stop: false};
                    (function frame(now) { const f = window.__f; f.frames.push(now - f.last); f.last = now;
                      f.peak = Math.max(f.peak, grid.querySelectorAll('*').length);
                      if (!grid.contains(document.activeElement)) f.lost += 1;
                      if (!f.stop) requestAnimationFrame(frame); })(performance.now()); }""")
                started = time.time()
                for _ in range(presses):
                    page.keyboard.press("ArrowDown")
                    time.sleep(0.033)
                page.wait_for_timeout(800)
                r = page.evaluate("""() => { const f = window.__f; f.stop = true;
                    const fr = f.frames.slice().sort((a, b) => a - b); const lt = window.__m.longtasks.slice(f.lt0).map(x => x.d);
                    const el = document.activeElement; const id = el && el.getAttribute ? el.getAttribute('data-id') : null;
                    return {frames: fr.length, p95Frame: fr[Math.floor(fr.length * 0.95)], maxFrame: fr[fr.length - 1],
                            over100: fr.filter(x => x > 100).length, longest: lt.length ? Math.max(...lt) : 0, longTasks: lt.length,
                            peakNodes: f.peak, framesWithFocusOutOfGrid: f.lost, focusIsACard: Boolean(id)}; }""")
                r["photos_passed"] = presses * columns
                r["seconds"] = time.time() - started
                rows.append(r)
                ctx.close()
            results["keyboard"] = rows
            show("(e) ArrowDown held through ~2,000 photos (%d columns, 30 keys a second)" % columns, rows)

        if "f" in wanted:
            ctx, page, _ = new_page()
            page.goto(root, wait_until="domcontentloaded")
            asks = page.evaluate("""async () => {
                const ids = (await (await fetch('/%s/api/library/ids?kind=all')).json()).ids;
                const take = (from) => ids.slice(from, from + 200).join(',');
                const out = [];
                for (let n = 0; n < 12; n++) {
                    const t = performance.now();
                    const r = await fetch('/%s/api/library/cards?ids=' + take(n * 997));
                    const body = await r.json();
                    out.push({ms: performance.now() - t, cards: body.cards.length, stale: body.cards.filter(c => c.stale).length,
                              kb: JSON.stringify(body).length / 1024});
                }
                return out;
            }""" % (LIBRARY, LIBRARY))
            ctx.close()
            results["cards_200"] = asks[2:]       # the first two are cold
            show("(f) GET /api/library/cards for 200 ids (the sandbox has no photo files: every card is checked and is 'missing')", asks[2:])
            from tagpup.services import damaged_photos
            folder = tempfile.mkdtemp(prefix="tagpup_stat_")
            try:
                files = []
                for i in range(200):
                    path = os.path.join(folder, "p%03d.jpg" % i)
                    with open(path, "wb") as handle:
                        handle.write(b"x" * 100)
                    files.append(path)
                costs = []
                for _ in range(20):
                    t = time.perf_counter()
                    for path in files:
                        damaged_photos.stamp_of(path)
                    costs.append((time.perf_counter() - t) * 1000)
                missing = []
                for _ in range(20):
                    t = time.perf_counter()
                    for path in files:
                        damaged_photos.stamp_of(path + ".gone")
                    missing.append((time.perf_counter() - t) * 1000)
            finally:
                # Retried and reported, never quietly left behind (findings #572; scripts/sandbox.py).
                print("    the 200 files made for the stat timing deleted: %s" % remove_sandbox(folder))
            results["stat_200_local"] = {"present_ms": costs, "gone_ms": missing}
            print("    200 stats of files that are there, local disk: median %.2f ms (%.1f us each); of files that are gone: %.2f ms"
                  % (med(costs), med(costs) * 1000 / 200, med(missing)))

        if "g" in wanted:
            rows = []
            for n in range(args.rounds + 1):        # the first scan reads every file: not counted
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
                        "longest_task_after_scan_ms": max(after) if after else 0,
                    })
                ctx.close()
            results["open_folder"] = rows
            show("(g) open the %d-photo folder view (fresh browser each of %d)" % (args.photos, args.rounds), rows)
            ctx, page, _ = new_page()
            page.goto(folder_url, wait_until="domcontentloaded")
            page.wait_for_function("() => window.__m && window.__m.marks.idle", timeout=600000)
            page.wait_for_timeout(3000)
            fly = []
            for n in range(args.rounds):
                r = page.evaluate("""async () => {
                    const sc = document.getElementById('folder-view-main'); sc.scrollTop = 0;
                    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
                    const total = sc.scrollHeight - sc.clientHeight; const frames = []; let peak = 0;
                    const lt0 = window.__m.longtasks.length; let last = performance.now(); const start = last;
                    await new Promise(res => { (function step(now) { frames.push(now - last); last = now;
                        const p = Math.min(1, (now - start) / 3000); sc.scrollTop = p * total;
                        peak = Math.max(peak, document.querySelectorAll('#thumbnails-grid *').length);
                        if (p < 1) requestAnimationFrame(step); else res(); })(performance.now()); });
                    frames.sort((a, b) => a - b); const lt = window.__m.longtasks.slice(lt0).map(x => x.d);
                    return {frames: frames.length, p95Frame: frames[Math.floor(frames.length * 0.95)], over100: frames.filter(f => f > 100).length,
                            longest: lt.length ? Math.max(...lt) : 0, peakNodes: peak}; }""")
                fly.append(r)
                page.wait_for_timeout(1000)
            results["scroll_folder"] = fly
            show("(g) scroll the folder view top to bottom in 3 s", fly)
            ctx.close()
        if navigator and "h" in wanted:
            big = os.path.join(os.path.dirname(library_folder), "walk_big")
            if not os.path.isdir(big):
                make_empty_photos(big, 68000)
                print("    made 68,000 empty photo files in %d folders under the root place, for a walk to have something to walk" % 68)
            rows = []
            for n in range(args.rounds):
                ctx, page, seen = new_page()
                ready(page, root)
                page.click("#sidebar-tab-library")
                page.wait_for_selector("#nav-panel-folders .nav-row", timeout=60000)
                page.evaluate("() => window.__n.idle()")
                index = page.evaluate("""() => { const r = window.__n.rows('folders'); let best = 0;
                    r.forEach((x, i) => { if (window.__n.count(x) > window.__n.count(r[best])) best = i; }); return best; }""")
                del seen[:]
                page.evaluate("() => { window.__m.marks = {}; }")
                page.evaluate("() => window.__n.timed(() => window.__m.marks.paintedIdle, 120000)")
                page.locator("#nav-panel-folders .nav-row").nth(index).click()
                r = page.evaluate("() => window.__p")
                page.wait_for_timeout(4000)         # long enough for any walk to be asked for and answered
                asked = [url for url in seen if "/api/folder/membership" in url]
                walk = page.evaluate("""() => { const e = performance.getEntriesByType('resource').filter(e => e.name.includes('/api/folder/membership'))[0];
                    return e ? e.responseEnd - e.startTime : null; }""")
                rows.append({"click_to_painted_and_idle_ms": r.get("ms"), "membership_requests": len(asked), "membership_ms": walk,
                             "quiet_link_shown": 1 if page.evaluate("() => { const b = document.getElementById('btn-moves-check'); return Boolean(b) && !b.classList.contains('hidden'); }") else 0})
                ctx.close()
            results["root_click"] = rows
            show("(h) a click on the largest folder row, with 68,000 files on disk under it", rows)
        browser.close()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="do it; without this the plan is printed and nothing is done")
    parser.add_argument("--code-root", default=REPO_ROOT, help="the checkout whose code the sandbox serves (default: this one)")
    parser.add_argument("--source", default=tagpup_config.library_path("photo_index.db"),
                        help="the library to copy; opened read-only and never written")
    parser.add_argument("--photos", type=int, default=759, help="photos in the folder of (g)")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--only", default=EVERYTHING, help="which of a to g to run, as letters (default: all)")
    parser.add_argument("--out", default="", help="also write the numbers here as JSON")
    parser.add_argument("--headed", action="store_true", help="show the browser; slower, closer to a person's")
    parser.add_argument("--keep-sandbox", default="", help="keep the sandbox in this folder (made if it is not there, reused if it is: skips the copy)")
    args = parser.parse_args()
    args.only = [letter for letter in args.only.lower() if letter in EVERYTHING]
    return args


def print_plan(args):
    print("measure_navigator.py would, with --run:")
    print("  copy %s (read-only source) into a sandbox under %s, its roots placed at empty sandbox folders"
          % (args.source, tempfile.gettempdir()))
    print("  serve the code of %s from that sandbox on a free port" % args.code_root)
    print("  in headless Chromium, %d fresh browsers each: (a) open the Library pane, cold and warm; (b) open the folder tree to"
          " depth 3 in three clicks; (c) click the largest keyword node, and open it by its address; (d) type in the People"
          " filter; (e) hold ArrowDown through 2,000 photos; (f) a card batch of 200 with its stats; (g) open and scroll a"
          " %d-photo folder view; (h) click the largest folder with 68,000 files on disk" % (args.rounds, args.photos))
    print("  run only: %s" % ", ".join(args.only))
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
        db_path = os.path.join(sandbox, "data", LIBRARY + ".db")
        if args.keep_sandbox and os.path.exists(db_path):
            for name in ("scripts", "tagpup", "web"):
                if not remove_sandbox(os.path.join(sandbox, name)):
                    sys.exit("could not replace the sandbox's code; the sandbox is kept at %s" % sandbox)
            copy_code(sandbox, code_root=args.code_root, launchers=True)
        else:
            os.makedirs(os.path.join(sandbox, "data"), exist_ok=True)
            copy_code(sandbox, code_root=args.code_root, launchers=True)
            copy_library(args.source, db_path)
            place_roots(db_path, sandbox)
        roots = os.path.join(sandbox, "roots")
        place = os.path.join(roots, sorted(os.listdir(roots))[0])
        folder = os.path.join(place, "measure_big")
        if not os.path.isdir(folder) and "g" in args.only:
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
