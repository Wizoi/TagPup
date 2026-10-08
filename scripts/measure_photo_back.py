"""Time the way back from a photo, and the jump from the selection's details, the way a person does them: in a real Chromium, in a sandbox.

#780: in a library view -- a search of 20,000 photos or more -- the owner scrolls, selects (Select All, one left out), opens a
photo with the magnifying glass, and wants to come back to the grid as it was left. #781: the selection details' People jump
opens the People view of a person the selected photos carry. On a copy of photo_index (68,466 photos when this was written) it
measures, each a person's action and not an endpoint:

  (a) Back from a photo: from the click on Back to the grid's cards in view painted (the browser's two frames) and the main
      thread idle; and whether it is the view as left -- the same row at the top of the view (`top_row`: the grid measures its cards
      again when it is shown and a card's height can differ by a pixel or two, so the same row is at another offset: `scroll`
      is shown for that, and is not the test), the same selection count (a Select all with one left out), the same address.
      Also Escape and the browser's own Back, the same.
  (b) a People jump: from the click on a person's link to the People view's cards painted and the main thread idle.

    .venv/Scripts/python.exe scripts/measure_photo_back.py                  # prints the plan, does nothing
    .venv/Scripts/python.exe scripts/measure_photo_back.py --run
    .venv/Scripts/python.exe scripts/measure_photo_back.py --run --code-root <a `git archive` of an older commit>

With the code of a commit that has no Back from a photo the script says so and reports what the page does instead (what Escape and
the browser's Back do), which is the baseline. Measured 2026-10-07, 20,995-photo search, Select all less one, scrolled to row 1,677,
headless Chromium: before (a985687) no button, Escape left the photo open, the browser's Back left the page (about:blank); after,
button 43 ms, Escape 31 ms, browser Back 28 ms (medians of 3) to the painted grid, the same top row, selection and address in 3 of 3 --
and before the grid's anchor was read from the cards (vgrid.js, #780) the row at the top was 16 rows off. A People jump (#781): 130 to 171 ms in four runs of three. Everything runs in a sandbox, built and deleted by scripts/sandbox.py: the library is
copied through SQLite's backup API (the original read-only), under a temporary TAGPUP_HOME of its own, its roots placed at empty
sandbox folders, on a free port. Nothing in data/ is touched and nothing is visible to an app you have open. The photos of the
library are not there: a thumbnail's or a photo's request is answered a small picture by the browser's own routing. No name of a
person or a folder is printed, only counts. Headless Chromium has no extensions and a fresh profile, so the absolute numbers come out
low; use them for comparison.
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
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import processes  # noqa: E402
from code_snapshot import REPO_ROOT, copy_code  # noqa: E402
from sandbox import copy_library, environment, free_port, place_roots, remove_sandbox  # noqa: E402

LIBRARY = "measured"
WANT = 20000          # photos the search view holds, at least

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002"
    "00000500010d0a2db40000000049454e44ae426082")

HELPERS = r"""
window.__h = {
  scroller: () => document.getElementById('folder-view-main'),
  content: () => document.getElementById('folder-view-content'),
  gridShown: () => !document.getElementById('folder-view-content').classList.contains('hidden'),
  photoShown: () => !document.getElementById('panel-content').classList.contains('hidden'),
  inView() {
    const sc = window.__h.scroller().getBoundingClientRect();
    return [...document.querySelectorAll('#thumbnails-grid .thumbnail-card')].filter(c => {
      const r = c.getBoundingClientRect();
      return r.bottom > sc.top + 120 && r.top < sc.bottom;
    });
  },
  // The cards a person could click without the page scrolling: whole, below the floating header, above the bottom.
  whole() {
    const sc = window.__h.scroller().getBoundingClientRect();
    return [...document.querySelectorAll('#thumbnails-grid .thumbnail-card:not(.placeholder)')].filter(c => {
      const r = c.getBoundingClientRect();
      return r.top >= sc.top + 140 && r.bottom <= sc.bottom - 10;
    }).map(c => c.getAttribute('data-id'));
  },
  painted() {
    const visible = window.__h.inView();
    return visible.length > 0 && visible.every(c => !c.classList.contains('placeholder'));
  },
  // `top` is the index in the view's order of the photo at the top of the view: what "the same place" means, since a card's height
  // is measured again when the grid is shown (a different offset can be the same row).
  async state() {
    const visible = window.__h.inView();
    const top = (await import('./state.js')).state.grid.extent().viewFrom;
    return {
      scrollTop: Math.round(window.__h.scroller().scrollTop), top,
      first: visible.length ? visible[0].getAttribute('data-id') : null,
      selected: document.getElementById('selected-thumbnails-count').textContent,
      address: location.search,
      gridShown: window.__h.gridShown(), photoShown: window.__h.photoShown(),
    };
  },
  // From now until the cards in view are real and painted, and the main thread has been idle for a frame.
  async untilPainted(limit = 20000) {
    const t = performance.now();
    for (;;) {
      await new Promise(r => requestAnimationFrame(r));
      if (window.__h.gridShown() && window.__h.painted()) break;
      if (performance.now() - t > limit) return { timeout: true };
    }
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    await new Promise(r => setTimeout(r, 0));
    return { ms: performance.now() - t };
  },
};
"""


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


def search_address(root):
    """A search of at least WANT photos: the largest years of the library, any of them (counted by the navigator's dates)."""
    with urllib.request.urlopen(root + "api/library/navigator?section=dates", timeout=600) as reply:
        years = json.load(reply)["years"]
    years = sorted((y for y in years if 1970 <= y["year"] <= 2100), key=lambda y: -y["count"])
    chosen, total = [], 0
    for year in years:
        chosen.append({"kind": "year", "value": str(year["year"])})
        total += year["count"]
        if total >= WANT:
            break
    value = json.dumps({"any_of": chosen}, separators=(",", ":"))
    return root + "?view=search&value=" + urllib.parse.quote(value, safe=""), total, len(chosen)


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def new_page(browser):
    context = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = context.new_page()
    page.route("**/api/photo-thumb*", lambda r: r.fulfill(body=TINY_PNG, content_type="image/png"))
    page.route("**/api/photo-file*", lambda r: r.fulfill(body=TINY_PNG, content_type="image/png"))
    return context, page


def open_search(page, address):
    page.goto(address, wait_until="domcontentloaded")
    page.wait_for_function("() => document.querySelectorAll('#thumbnails-grid .thumbnail-card:not(.placeholder)').length > 0", timeout=600000)
    page.evaluate(HELPERS)
    page.wait_for_timeout(1500)


def leave_the_view_as_it_is(page, fraction):
    """Scroll a good way down, Select all, leave one photo out: the view as a person leaves it to look at a photo."""
    page.evaluate("async (f) => { const sc = __h.scroller(); sc.scrollTop = f * (sc.scrollHeight - sc.clientHeight); await __h.untilPainted(); }", fraction)
    page.click("#btn-select-all-thumbnails")
    whole = page.evaluate("() => __h.whole()")
    page.locator('#thumbnails-grid [data-id="%s"] .thumbnail-checkbox' % whole[3]).click()
    page.wait_for_timeout(600)
    return page.evaluate("async () => await __h.state()")


def open_photo(page):
    whole = page.evaluate("() => __h.whole()")
    page.locator('#thumbnails-grid [data-id="%s"] .btn-thumbnail-detail' % whole[5]).click()
    page.wait_for_function("() => __h.photoShown()", timeout=20000)
    page.wait_for_timeout(500)


def trimmed(found):
    """A page's state with its address cut short: it holds the search, which is long."""
    return {key: (value[:24] + "..." if key == "address" and isinstance(value, str) else value) for key, value in found.items()}


def how_back(page, way, limit=8000):
    """Go back by `way` and say what came of it: the time to the painted grid, and what the page is. A way that leaves the page
    (the browser's Back, where the page made no place to return to) says so."""
    if way == "button":
        page.click("#btn-photo-back")
    elif way == "escape":
        page.keyboard.press("Escape")
    else:
        page.go_back()
    try:
        waited = page.evaluate("async (limit) => await __h.untilPainted(limit)", limit)
        result = {"ms": waited.get("ms"), "timeout": bool(waited.get("timeout"))}
        result.update(page.evaluate("async () => await __h.state()"))
    except Exception:
        result = {"ms": None, "timeout": True, "left_the_page": True, "address": page.url[-60:]}
    return result


def drive(base, args, results):
    from playwright.sync_api import sync_playwright
    root = "%s/%s/" % (base, LIBRARY)
    address, total, years = search_address(root)
    print("\nthe search: any of %d years, %d photos" % (years, total))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for way in ("button", "escape", "browser"):
            rows = []
            for n in range(args.rounds):
                context, page = new_page(browser)
                try:
                    open_search(page, address)
                    left = leave_the_view_as_it_is(page, 0.4)
                    open_photo(page)
                    has_button = page.evaluate("() => { const b = document.getElementById('btn-photo-back'); return Boolean(b) && b.offsetParent !== null; }")
                    if way == "button" and not has_button:
                        results["back_button"] = False
                        print("\n(a) Back from a photo, by the button: this code has no Back button on the photo")
                        context.close()
                        break
                    came = how_back(page, way)
                    same = {"top_row": abs(came.get("top", -99) - left["top"]) < 5, "scroll": abs(came.get("scrollTop", -9) - left["scrollTop"]) <= 2,
                            "selection": came.get("selected") == left["selected"], "address": came.get("address") == left["address"],
                            "grid_shown": bool(came.get("gridShown")) and not came.get("photoShown")}
                    rows.append({"ms": came["ms"], "timeout": came["timeout"], "same": same, "left": left, "came": came})
                finally:
                    context.close()
            else:
                results["back_" + way] = rows
                print("\n(a) Back from a photo, by %s (fresh browser each of %d), search of %d photos:" % (way, len(rows), total))
                print("   to the grid's cards in view painted and idle: median %s ms (%s)" % (
                    "%.0f" % med([r["ms"] for r in rows]) if med([r["ms"] for r in rows]) is not None else "-",
                    ", ".join("%.0f" % r["ms"] if r["ms"] is not None else "-" for r in rows)))
                for key in ("grid_shown", "top_row", "scroll", "selection", "address"):
                    print("   %-10s as left in %d of %d" % (key, sum(1 for r in rows if r["same"][key]), len(rows)))
                print("   left %s\n   came %s" % (trimmed(rows[0]["left"]), trimmed(rows[0]["came"])))
                continue
            continue
        # (b) a People jump
        rows = []
        for n in range(args.rounds):
            context, page = new_page(browser)
            try:
                open_search(page, address)
                page.locator("#thumbnails-grid .thumbnail-card:not(.placeholder) .thumbnail-checkbox").nth(2).click()
                page.locator("#thumbnails-grid .thumbnail-card:not(.placeholder) .thumbnail-checkbox").nth(4).click()
                try:
                    page.wait_for_selector("#selection-people-jump a.selection-jump-link", timeout=15000)
                except Exception:
                    print("\n(b) People jump: this code has no People jump in the selection details (or no person on the photos picked)")
                    break
                clicked = page.evaluate("() => performance.now()")
                page.locator("#selection-people-jump a.selection-jump-link").first.click()
                page.wait_for_function("() => location.search.indexOf('view=person') >= 0", timeout=20000)
                waited = page.evaluate("async () => await __h.untilPainted(30000)")
                rows.append({"ms": waited.get("ms"), "timeout": bool(waited.get("timeout")), "address": page.evaluate("() => location.search.slice(0, 12)")})
                del clicked
            finally:
                context.close()
        if rows:
            results["people_jump"] = rows
            print("\n(b) People jump: click on a person's link to the People view's cards painted and idle (fresh browser each of %d):" % len(rows))
            print("   median %.0f ms (%s); timeouts %d" % (med([r["ms"] for r in rows]),
                  ", ".join("%.0f" % r["ms"] if r["ms"] is not None else "-" for r in rows), sum(1 for r in rows if r["timeout"])))
        browser.close()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="do it; without this the plan is printed and nothing is done")
    parser.add_argument("--code-root", default=REPO_ROOT, help="the checkout whose code the sandbox serves (default: this one)")
    parser.add_argument("--source", default=tagpup_config.library_path("photo_index.db"),
                        help="the library to copy; opened read-only and never written")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", default="", help="also write the numbers here as JSON")
    parser.add_argument("--keep-sandbox", default="", help="keep the sandbox in this folder (made if it is not there, reused if it is: skips the copy)")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.run:
        print("measure_photo_back.py would, with --run:")
        print("  copy %s (read-only source) into a sandbox under %s, its roots placed at empty sandbox folders" % (args.source, tempfile.gettempdir()))
        print("  serve the code of %s from that sandbox on a free port" % args.code_root)
        print("  in headless Chromium, on a search of %d photos or more: scroll, Select all less one, open a photo with the magnifying glass," % WANT)
        print("  go back (the Back button, Escape, the browser's Back) and time it and compare the view; then a People jump from the selection details")
        print("  print the numbers, then delete the sandbox")
        print("Nothing was done.")
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
            import shutil
            for name in ("scripts", "tagpup", "web"):
                shutil.rmtree(os.path.join(sandbox, name), ignore_errors=True)
            copy_code(sandbox, code_root=args.code_root, launchers=True)
        else:
            os.makedirs(os.path.join(sandbox, "data"), exist_ok=True)
            copy_code(sandbox, code_root=args.code_root, launchers=True)
            copy_library(args.source, db_path)
            place_roots(db_path, sandbox)
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
