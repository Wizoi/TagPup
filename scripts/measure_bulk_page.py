"""Time editing across folders the way a person does it: selecting by id and a bulk job's strip, in a real Chromium, in a sandbox.

The numbers in docs/ARCHITECTURE.md, "Phase 9d-2", come from this. On a copy of photo_index (68,472 photos when this was written)
it measures, each a person's action and not an endpoint:

  (a) Select all on the view of the whole library: from the click to the count shown and every card painted selected;
  (b) a Shift-click from the first photo to one about 20,000 on (the grid scrolled there, so the cards between are not held): the
      same, with the count it selected;
  (c) Invert of that: the same;
  (d) the selection panel's tally for all 68,000 selected: from the click on Select all to the tags and people painted (the
      250 ms wait and the server's count are in it);
  (e) a tags job over 500 small JPEGs of a library of its own in the sandbox, written by the real ExifTool: from the click on Add
      (the questions answered at once) to the strip shown and to the first progress, then the main thread while the strip polls:
      the longest task, the share of the thread the page used, the requests made;
  (f) the strip when the server reports 3,000 errors (it names 50): the DOM it makes, and from the answer to the rows painted.

    .venv/Scripts/python.exe scripts/measure_bulk_page.py                  # prints the plan, does nothing
    .venv/Scripts/python.exe scripts/measure_bulk_page.py --run
    .venv/Scripts/python.exe scripts/measure_bulk_page.py --run --code-root <a `git archive` of the trunk> --only a,b,c

BASELINE. The trunk has no selection by id: its Select all, Shift range and Invert in a library view fetch the card of every photo
selected, for its path (`selectInLibrary`, 200 cards a request), and ask first when it is more than 5,000. Run with `--code-root` a
`git archive` of the trunk and `--only abc`: the same clicks, the dialogs accepted, are the baseline of (a), (b) and (c) -- the
selection is complete when the count reads what it should. (d), (e) and (f) have no trunk equivalent (there is no tally of a view,
no job, no strip).

Everything runs in a sandbox, built and deleted by scripts/sandbox.py: the library copied through SQLite's backup API (the original
read-only) under a temporary TAGPUP_HOME of its own, a server on a free port, ExifTool writing only the sandbox's 500 photos. Nothing
in data/ is touched and nothing is visible to an app you have open. A thumbnail's request is answered a small picture by the
browser's routing. No name of a keyword, folder or person is printed, only counts. Headless Chromium has no extensions and a fresh
profile, so absolute numbers come out low; use them for comparison. Run to run they move by up to 40%.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import processes  # noqa: E402
from code_snapshot import REPO_ROOT, copy_code  # noqa: E402
from sandbox import copy_library, enter, environment, free_port, place_roots, remove_sandbox  # noqa: E402

LIBRARY = "measured"
SMALL = "smalljobs"
EVERYTHING = "abcdef"

INIT = r"""
window.__m = { longtasks: [] };
try {
  new PerformanceObserver(l => l.getEntries().forEach(e => window.__m.longtasks.push({s: e.startTime, d: e.duration})))
    .observe({type: 'longtask', buffered: true});
} catch (e) {}
"""

HELPERS_JS = r"""
window.__n = {
  number: (id) => Number((document.getElementById(id).textContent.match(/[\d,]+/) || ['0'])[0].replace(/,/g, '')),
  async idle() {
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    await new Promise(r => setTimeout(r, 0));
  },
  realCards: () => [...document.querySelectorAll('#thumbnails-grid .thumbnail-card:not(.placeholder)')],
  allSelected: () => { const c = window.__n.realCards(); return c.length > 0 && c.every(x => x.classList.contains('selected')); },
  // Time from the next click (its capture phase) to the predicate holding, painted and idle.
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
                     nodes: document.querySelectorAll('#thumbnails-grid *').length,
                     heapMB: performance.memory ? performance.memory.usedJSHeapSize / 1048576 : null});
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


def show(title, rows):
    print("\n" + title)
    if not rows:
        print("   (nothing measured)")
        return
    for key in [k for k in rows[0] if not isinstance(rows[0][k], (dict, list, str))]:
        vals = [r.get(key) for r in rows]
        if all(v is None for v in vals):
            continue
        print("   %-34s median %10.1f   (%s)" % (key, med(vals), ", ".join("%.1f" % v if v is not None else "-" for v in vals)))


def make_small_library(sandbox, count):
    """A library of its own in the sandbox with `count` small real JPEGs (a Date Taken, one keyword), recorded as the indexer
    records a read: what a bulk job has to write with the real ExifTool."""
    enter(sandbox)
    from PIL import Image
    from tagpup.files.exiftool_session import ExifToolSession
    from tagpup.files.metadata import MetadataExtractor
    from tagpup.services import libraries
    from tagpup.store import db, photos, taxonomy
    path = os.path.join(sandbox, "data", SMALL + ".db")
    libraries.create(path)
    folder = os.path.join(sandbox, "Pictures", "Coast")
    os.makedirs(folder)
    made = []
    for n in range(count):
        target = os.path.join(folder, "IMG_%04d.jpg" % n)
        Image.new("RGB", (64, 48), (n % 255, 90, 120)).save(target, "JPEG")
        made.append(target)
    exiftool = tagpup_config.exiftool_path("")
    with ExifToolSession(executable=exiftool) as et:
        for start in range(0, count, 100):
            et.set_tags(made[start:start + 100], tags={"EXIF:DateTimeOriginal": "2024:07:04 10:00:00", "XMP:Subject": ["Old/Stuff"]},
                        params=["-overwrite_original"])
    conn = db.connect(path)
    try:
        for tag in ("Old/Stuff", "Trips/Coast"):
            taxonomy.add_path(conn, tag)
        for target in made:
            record = MetadataExtractor()._structure(target, {"SourceFile": target, "EXIF:DateTimeOriginal": "2024:07:04 10:00:00",
                                                             "XMP:Subject": ["Old/Stuff"]}, None)
            photos.record_indexed(conn, target, record)
        conn.commit()
    finally:
        conn.close()


def say(text):
    print(text, flush=True)


def drive(base, args, results):
    from playwright.sync_api import sync_playwright
    root = "%s/%s/" % (base, LIBRARY)
    wanted = set(args.only)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed, args=["--enable-precise-memory-info"])

        def new_page():
            ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
            page = ctx.new_page()
            page.add_init_script(INIT)
            page.on("dialog", lambda dialog: dialog.accept())     # the questions are answered yes at once
            page.route("**/api/photo-thumb*", lambda r: r.fulfill(body=TINY_PNG, content_type="image/png"))
            return ctx, page

        def ready(page, url):
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_function("() => window.__m && document.getElementById('thumbnails-grid')", timeout=120000)
            page.evaluate(HELPERS_JS)
            page.wait_for_function("() => window.__n.realCards().length > 0", timeout=120000)
            page.evaluate("() => window.__n.idle()")

        ctx, page = new_page()
        ready(page, root + "?view=all")
        total = page.evaluate("() => Number(document.getElementById('library-strip-total').textContent.replace(/\\D/g, ''))")
        print("\nthe view of the whole library holds %d photos" % total)
        results["photos"] = total
        ctx.close()

        if wanted & set("abc"):
            rows_a, rows_b, rows_c = [], [], []
            for round_ in range(args.rounds):
                say("  (a)-(c) round %d of %d ..." % (round_ + 1, args.rounds))
                ctx, page = new_page()
                ready(page, root + "?view=all")
                if "a" in wanted:
                    page.evaluate("(want) => window.__n.timed(() => window.__n.number('selected-thumbnails-count') >= want && window.__n.allSelected(), 600000)", total)
                    page.click("#btn-select-all-thumbnails")
                    r = page.evaluate("() => window.__p")
                    r["selected"] = page.evaluate("() => window.__n.number('selected-thumbnails-count')")
                    rows_a.append(r)
                    page.click("#btn-select-none-thumbnails")
                    page.evaluate("() => window.__n.idle()")
                if wanted & set("bc"):
                    # one photo picked, the grid scrolled to about the 20,000th, a Shift-click there
                    page.locator("#thumbnails-grid .thumbnail-card:not(.placeholder) .thumbnail-checkbox").first.click()
                    page.evaluate("""() => { const c = window.__n.realCards();
                        const columns = c.filter(x => x.offsetTop === c[0].offsetTop).length || 1;
                        const second = c.find(x => x.offsetTop !== c[0].offsetTop);
                        const stride = second ? second.offsetTop - c[0].offsetTop : 216;
                        document.getElementById('folder-view-main').scrollTop = c[0].offsetTop + Math.floor(20000 / columns) * stride; }""")
                    page.wait_for_function("() => window.__n.realCards().some(x => Number(x.dataset.id) > 0 && x.getBoundingClientRect().top > 0 && "
                                           "x.getBoundingClientRect().top < 900 && document.getElementById('folder-view-main').scrollTop > 100000)", timeout=120000)
                    page.evaluate("() => window.__n.idle()")
                    target = page.locator("#thumbnails-grid .thumbnail-card:not(.placeholder)").nth(8)
                    target_id = target.get_attribute("data-id")
                    # done when the count is there and the card clicked (the range ends at it) is painted selected
                    page.evaluate("""(id) => window.__n.timed(() => window.__n.number('selected-thumbnails-count') > 15000 &&
                        document.querySelector('#thumbnails-grid [data-id="' + id + '"]').classList.contains('selected'), 600000)""", target_id)
                    target.locator(".thumbnail-checkbox").click(modifiers=["Shift"])
                    r = page.evaluate("() => window.__p")
                    r["selected"] = page.evaluate("() => window.__n.number('selected-thumbnails-count')")
                    rows_b.append(r)
                    if "c" in wanted:
                        before = r["selected"]
                        page.evaluate("(want) => window.__n.timed(() => window.__n.number('selected-thumbnails-count') === want, 600000)", total - before)
                        # the menu is opened as a right-click does (a scroll would close it, so nothing is scrolled to)
                        page.evaluate("""() => document.getElementById('thumbnails-grid').dispatchEvent(
                            new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: 300, clientY: 300}))""")
                        page.click('#grid-context-menu [data-action="invert"]')
                        r = page.evaluate("() => window.__p")
                        r["selected"] = page.evaluate("() => window.__n.number('selected-thumbnails-count')")
                        rows_c.append(r)
                ctx.close()
            if "a" in wanted:
                results["select_all"] = rows_a
                show("(a) Select all of %d: click to the count shown and every card painted selected" % total, rows_a)
            if "b" in wanted:
                results["shift_range"] = rows_b
                show("(b) Shift-click across ~20,000 photos the grid does not hold: click to the count and the cards painted", rows_b)
            if "c" in wanted:
                results["invert"] = rows_c
                show("(c) Invert of that selection: click to the count and the cards painted", rows_c)

        if "d" in wanted:
            rows = []
            for _ in range(args.rounds):
                ctx, page = new_page()
                ready(page, root + "?view=all")
                page.evaluate("() => window.__n.timed(() => document.querySelector('#selection-tags-list .selection-summary-chip'), 120000)")
                page.click("#btn-select-all-thumbnails")
                r = page.evaluate("() => window.__p")
                ask = page.evaluate("""() => { const e = performance.getEntriesByType('resource').find(x => x.name.includes('/selection/tally'));
                    return e ? {ms: e.responseEnd - e.requestStart, wait: e.responseStart - e.requestStart, kb: e.encodedBodySize / 1024} : {}; }""")
                r["tally_request_ms"], r["tally_server_wait_ms"], r["tally_reply_kb"] = ask.get("ms"), ask.get("wait"), ask.get("kb")
                r["tag_chips"] = page.evaluate("() => document.querySelectorAll('#selection-tags-list .selection-summary-chip').length")
                r["people_chips"] = page.evaluate("() => document.querySelectorAll('#selection-people-list .selection-summary-chip').length")
                r["panel_nodes"] = page.evaluate("() => document.querySelectorAll('#folder-selection-sidebar *').length")
                rows.append(r)
                ctx.close()
            results["tally_panel"] = rows
            show("(d) the tally panel for all %d selected: click on Select all to the tags and people painted" % total, rows)

        if "e" in wanted:
            rows = []
            for _ in range(1):
                ctx, page = new_page()
                small = "%s/%s/" % (base, SMALL)
                ready(page, small + "?view=all")
                cdp = ctx.new_cdp_session(page)
                cdp.send("Performance.enable")
                requests = []
                page.on("request", lambda request: requests.append(request.url))
                page.click("#btn-select-all-thumbnails")
                page.fill("#bulk-add-tags-input", "Trips/Lighthouse")
                page.evaluate("""() => { window.__j = {}; const strip = document.getElementById('bulk-strip');
                    const prog = document.getElementById('bulk-strip-progress');
                    new MutationObserver(() => { const j = window.__j;
                      if (!strip.classList.contains('hidden') && !j.shown) j.shown = performance.now();
                      if (!j.first && /^[1-9]/.test(prog.textContent)) j.first = performance.now();
                      if (/changed \\d+/.test(document.getElementById('bulk-strip-counts').textContent) &&
                          document.getElementById('btn-bulk-dismiss') && !document.getElementById('btn-bulk-dismiss').classList.contains('hidden')) j.done = performance.now();
                    }).observe(strip, {childList: true, subtree: true, attributes: true, characterData: true}); }""")
                t0 = page.evaluate("() => { window.__t0 = null; document.addEventListener('click', () => { if (window.__t0 === null) window.__t0 = performance.now(); }, {capture: true, once: true}); return 0; }")
                start_metrics = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
                begun = time.time()
                lt0 = page.evaluate("() => window.__m.longtasks.length")
                page.click("#btn-bulk-add-tags")
                page.wait_for_function("() => window.__j.done", timeout=int(args.poll_seconds * 1000) + 120000)
                took = time.time() - begun
                end_metrics = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
                j = page.evaluate("() => ({t0: window.__t0, shown: window.__j.shown, first: window.__j.first, done: window.__j.done, "
                                  "lt: window.__m.longtasks.slice(%d).map(x => x.d), message: document.getElementById('bulk-strip-message').textContent})" % lt0)
                polls = [u for u in requests if "/bulk/status" in u]
                busy = end_metrics["TaskDuration"] - start_metrics["TaskDuration"]
                rows.append({
                    "click_to_strip_shown_ms": j["shown"] - j["t0"] if j["shown"] and j["t0"] else None,
                    "click_to_first_progress_ms": j["first"] - j["t0"] if j["first"] and j["t0"] else None,
                    "job_seconds": took, "status_requests": len(polls),
                    "longest_task_ms": max(j["lt"]) if j["lt"] else 0, "long_tasks": len(j["lt"]),
                    "main_thread_busy_percent": 100.0 * busy / max(took, 0.001),
                    "main_thread_busy_ms_per_poll": 1000.0 * busy / max(len(polls), 1),
                })
                print("    the strip ended: %s" % j["message"].replace("\n", " "))
                ctx.close()
            results["job_strip"] = rows
            show("(e) a tags job over %d small JPEGs, the real ExifTool: Add clicked to the strip, and the page while it polls" % args.photos, rows)

        if "f" in wanted:
            rows = []
            names = ["IMG_%04d_%s.jpg" % (n, "a-long-file-name-" * 8) for n in range(50)]
            running = {"success": True, "job": 7, "op": "tags", "state": "running", "total": 3000, "done": 100, "changed": 90, "unchanged": 0,
                       "skipped_missing": 0, "skipped_damaged": 0, "errors": [], "error_count": 0, "started": 1, "finished": None,
                       "eta_seconds": 60, "message": None, "cancelling": False, "resumable": False, "what": "bulk tags"}
            ended = dict(running, state="done", done=3000, changed=0, error_count=3000, finished=2,
                         errors=[{"id": n, "name": names[n], "why": "The file is read-only."} for n in range(50)])
            for _ in range(args.rounds):
                ctx, page = new_page()
                answered = {"n": 0}

                def status(route):
                    answered["n"] += 1
                    route.fulfill(json=running if answered["n"] < 3 else ended)
                page.route("**/api/library/bulk/status*", status)
                page.route("**/api/library/bulk/current*", lambda route: route.fulfill(json={"success": True, "job": running}))
                page.goto(root + "?view=all", wait_until="domcontentloaded")
                page.wait_for_function("() => window.__m && document.getElementById('bulk-strip')", timeout=120000)
                page.evaluate(HELPERS_JS)
                page.evaluate("""() => { const list = document.getElementById('bulk-strip-error-list');
                    new MutationObserver(() => { if (list.children.length > 50 && !window.__rows) window.__rows = performance.now(); })
                      .observe(list, {childList: true}); }""")
                page.wait_for_function("() => window.__rows", timeout=60000)
                page.evaluate("() => window.__n.idle()")
                r = page.evaluate("""() => { const e = performance.getEntriesByType('resource').filter(x => x.name.includes('/bulk/status')).pop();
                    const lt = window.__m.longtasks.map(x => x.d);
                    return {answer_to_rows_ms: window.__rows - e.responseEnd, strip_nodes: document.querySelectorAll('#bulk-strip *').length,
                            error_rows: document.querySelectorAll('#bulk-strip-error-list li').length, longest_task_ms: lt.length ? Math.max(...lt) : 0}; }""")
                rows.append(r)
                ctx.close()
            results["strip_errors"] = rows
            show("(f) the strip when the server reports 3,000 errors (50 named): the answer to the rows painted, and the DOM", rows)
        browser.close()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="do it; without this the plan is printed and nothing is done")
    parser.add_argument("--code-root", default=REPO_ROOT, help="the checkout whose code the sandbox serves (default: this one)")
    parser.add_argument("--source", default=tagpup_config.library_path("photo_index.db"),
                        help="the library to copy; opened read-only and never written")
    parser.add_argument("--photos", type=int, default=500, help="small JPEGs for the job of (e)")
    parser.add_argument("--poll-seconds", type=float, default=60.0, help="how long (e) may poll before it gives up")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--only", default=EVERYTHING, help="which of a to f to run, as letters (default: all)")
    parser.add_argument("--out", default="", help="also write the numbers here as JSON")
    parser.add_argument("--headed", action="store_true", help="show the browser; slower, closer to a person's")
    parser.add_argument("--keep-sandbox", default="", help="keep the sandbox in this folder (made if it is not there)")
    args = parser.parse_args()
    args.only = [letter for letter in args.only.lower() if letter in EVERYTHING]
    return args


def print_plan(args):
    print("measure_bulk_page.py would, with --run:")
    print("  copy %s (read-only source) into a sandbox under %s, its roots placed at empty sandbox folders"
          % (args.source, tempfile.gettempdir()))
    print("  make a second library of %d small JPEGs there (the real ExifTool writes only those)" % args.photos)
    print("  serve the code of %s from that sandbox on a free port" % args.code_root)
    print("  in headless Chromium, %d fresh browsers each: (a) Select all of the whole library; (b) a Shift range across ~20,000 "
          "photos; (c) Invert; (d) the tally panel for all selected; (e) a %d-photo tags job and its strip; (f) the strip with "
          "3,000 errors" % (args.rounds, args.photos))
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
    sandbox = args.keep_sandbox or tempfile.mkdtemp(prefix="tagpup_measure_bulk_")
    server = None
    results = {"code_root": args.code_root}
    sys.stdout.reconfigure(line_buffering=True)
    try:
        print("sandbox : %s" % sandbox)
        db_path = os.path.join(sandbox, "data", LIBRARY + ".db")
        os.makedirs(os.path.join(sandbox, "data"), exist_ok=True)
        if args.keep_sandbox and os.path.exists(db_path):
            # a sandbox kept from an earlier run: its library is reused (the copy takes minutes), the code is replaced
            for name in ("scripts", "tagpup", "web"):
                if not remove_sandbox(os.path.join(sandbox, name)):
                    sys.exit("could not replace the sandbox's code; the sandbox is kept at %s" % sandbox)
            copy_code(sandbox, code_root=args.code_root, launchers=True)
        else:
            copy_code(sandbox, code_root=args.code_root, launchers=True)
            copy_library(args.source, db_path)
            place_roots(db_path, sandbox)
        if "e" in args.only:
            # the job of (e) changes its photos: a library of its own is made for each run
            for leftover in (SMALL + ".db", SMALL + ".db-wal", SMALL + ".db-shm"):
                if os.path.exists(os.path.join(sandbox, "data", leftover)):
                    os.remove(os.path.join(sandbox, "data", leftover))
            if os.path.isdir(os.path.join(sandbox, "Pictures")):
                remove_sandbox(os.path.join(sandbox, "Pictures"))
            make_small_library(sandbox, args.photos)
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
        if args.keep_sandbox:
            print("sandbox kept at %s" % sandbox)
        else:
            print("sandbox deleted: %s" % remove_sandbox(sandbox))


if __name__ == "__main__":
    main()
