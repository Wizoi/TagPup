"""Time the search page (phase 9e-2) the way a person uses it: in a real Chromium, in a sandbox.

The numbers in docs/ARCHITECTURE.md, "Phase 9e-2", come from this. On a copy of photo_index it measures, each a person's action
and not an endpoint, from the key or the click to the first window of cards painted and the main thread idle:

  (a) a common word: typed in the Library pane's search box, Enter;
  (b) All of three tags: each typed in All of's picker and added with Enter -- the third Enter timed;
  (c) Any of two people less a tag: the two added to Any of, the tag to None of -- the last Enter timed;
  (d) a word within a year: the year's view open, "Within the sidebar's selection" ticked, the word typed, Enter;
  (e) Select all and the tally of a large result (None of the tag with the most photos, opened by its address): from the
      click on Select all to the tags and people of the selection painted.

The word is the caption word most photos hold (letters only, four or more), the tags three keywords that are not people and
have nothing under them -- the one with the most photos, the one most often beside it, the one most often beside both --, the people the two most photographed people, the year the one with the most photos -- chosen on the copy,
never printed: only counts are printed, never a name, a word, a keyword or a folder.

    .venv/Scripts/python.exe scripts/measure_search.py                 # prints the plan, does nothing
    .venv/Scripts/python.exe scripts/measure_search.py --run
    .venv/Scripts/python.exe scripts/measure_search.py --run --only ae --rounds 5

Everything runs in a sandbox built and deleted by scripts/sandbox.py: the library copied through SQLite's backup API (the original
read-only) under a temporary TAGPUP_HOME, its roots placed at empty sandbox folders, migrated by the served code before the server
starts (migration 24, the word index: its time is printed), served on a free port. Nothing in data/ is touched. Thumbnails are
answered a 1-pixel picture by the browser.
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
EVERYTHING = "abcde"

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
  async idle() {
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    await new Promise(r => setTimeout(r, 0));
  },
  total() {
    const text = document.getElementById('library-strip-total').textContent;
    return /photo/.test(text) ? Number(text.replace(/[^0-9]/g, '')) : null;
  },
  // A search has opened and is usable: its total is said, and its first window painted (or it holds nothing), and idle.
  searched() {
    if (new URLSearchParams(location.search).get('view') !== 'search') return false;
    const total = window.__n.total();
    return total !== null && (total === 0 || Boolean(window.__m.marks.paintedIdle));
  },
  // Start at the next click, change, or Enter (capture phase); finish when the predicate holds, painted and idle.
  timed(predicate, timeout) {
    window.__p = new Promise(resolve => {
      let t0 = null;
      const lt0 = window.__m.longtasks.length;
      const start = () => { if (t0 === null) t0 = performance.now(); };
      document.addEventListener('click', start, {capture: true, once: true});
      document.addEventListener('change', start, {capture: true, once: true});
      const onKey = (e) => { if (e.key === 'Enter') { start(); document.removeEventListener('keydown', onKey, true); } };
      document.addEventListener('keydown', onKey, true);
      (async function poll() {
        for (;;) {
          if (t0 !== null && predicate()) {
            await window.__n.idle();
            const lt = window.__m.longtasks.slice(lt0).map(x => x.d);
            resolve({ms: performance.now() - t0, longest_task_ms: lt.length ? Math.max(...lt) : 0});
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

#: What is searched for, chosen on the copy by the served code; printed to this script's own pipe, never to the console.
CHOOSE = r"""
import collections, json, re, sys
sys.path.insert(0, %r)
from tagpup.core import vocabulary
from tagpup.store import db, person_ids
from tagpup.store import library_view as store
conn = db.connect(db.readonly_uri(%r), uri=True)
holding = collections.Counter()
for (captions,) in conn.execute("SELECT captions FROM photos WHERE captions IS NOT NULL AND captions != ''"):
    try:
        items = json.loads(captions)
    except ValueError:
        continue
    found = set()
    for text in items if isinstance(items, list) else []:
        if isinstance(text, str):
            found.update(word.lower() for word in re.findall(r"[^\W\d_]{4,}", text))
    holding.update(found)
word, word_photos = holding.most_common(1)[0]
faces = {row[0] for row in conn.execute("SELECT id FROM tag_taxonomy WHERE has_face = 1")}
nodes = store.keyword_counts(conn)
parents = {node["parent_id"] for node in nodes}
leaves = {node["id"]: node for node in nodes if node["id"] not in parents and node["id"] not in faces and "/" in node["tag"]}
photos_of = collections.defaultdict(set)
for photo_id, tag_id in conn.execute("SELECT photo_id, tag_id FROM photo_tags"):
    if tag_id in leaves:
        photos_of[tag_id].add(photo_id)
# The leaf tag with the most photos, then the one most often beside it, then the one most often beside both: All of them holds photos.
first = max(photos_of, key=lambda tag_id: len(photos_of[tag_id]))
picked, held = [first], set(photos_of[first])
while len(picked) < 3:
    beside = max((tag_id for tag_id in photos_of if tag_id not in picked), key=lambda tag_id: len(photos_of[tag_id] & held))
    picked.append(beside)
    held &= photos_of[beside]
tags = [leaves[tag_id] for tag_id in picked]
branches = set(person_ids.read(conn).branches)
people = [name for name, _count in store.people_counts(conn) if vocabulary.key(name) not in branches][:2]
year, year_photos = conn.execute("SELECT year, COUNT(*) FROM photos WHERE year IS NOT NULL GROUP BY year ORDER BY 2 DESC LIMIT 1").fetchone()
print(json.dumps({"word": word, "word_photos": word_photos, "tags": [node["tag"] for node in tags],
                  "tag_photos": [node["count"] for node in tags], "all_three": len(held), "people": people, "year": year,
                  "year_photos": year_photos}))
"""


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
            print("   %-28s median %10.1f   (%s)" % (key, statistics.median(values), ", ".join("%.1f" % v for v in values)))
    timeouts = sum(1 for r in rows if r.get("timeout"))
    if timeouts:
        print("   %d round(s) timed out" % timeouts)


def drive(base, args, chosen, results):
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
            page.wait_for_function("() => window.__m && window.__m.marks.paintedIdle", timeout=180000)
            page.evaluate(HELPERS_JS)
            page.evaluate("() => window.__n.idle()")
            page.click("#sidebar-tab-library")
            return ctx, page

        def reset(page):
            page.evaluate("() => window.__n.idle()")
            page.evaluate("() => { window.__m.marks = {}; }")

        def pick(page, part, text, timed=False):
            """Type `text` in a list's picker and add the first name offered with Enter (the third Enter of (b) is timed)."""
            box = '.library-search-list[data-list="%s"] .library-search-pick' % part
            page.fill(box, text)
            page.wait_for_selector("#library-search-options-%s [role=option]" % part, timeout=60000)
            reset(page)
            if timed:
                page.evaluate("() => window.__n.timed(() => window.__n.searched(), 120000)")
            page.press(box, "Enter")
            if not timed:
                page.wait_for_function("() => window.__n.searched()", timeout=120000)
                return None
            return page.evaluate("() => window.__p")

        def finish(page, r, what):
            r.update(page.evaluate("() => window.__n.lastIds()"))
            r["photos"] = page.evaluate("() => window.__n.total()")
            asked = page.evaluate("() => JSON.parse(new URLSearchParams(location.search).get('value'))")
            r["asked_as_meant"] = what(asked)
            return r

        if "a" in wanted:
            rows = []
            for _ in range(args.rounds):
                ctx, page = new_page(root + "?view=all")
                page.fill("#library-search-words", chosen["word"])
                reset(page)
                page.evaluate("() => window.__n.timed(() => window.__n.searched(), 120000)")
                page.press("#library-search-words", "Enter")
                r = finish(page, page.evaluate("() => window.__p"), lambda v: v == {"words": chosen["word"]})
                rows.append(r)
                ctx.close()
            results["word"] = rows
            show("(a) a common caption word (%d photos hold it): Enter to the first window painted and idle" % chosen["word_photos"], rows)

        if "b" in wanted:
            rows = []
            for _ in range(args.rounds):
                ctx, page = new_page(root + "?view=all")
                page.click("#btn-library-search-filters")
                pick(page, "all_of", chosen["tags"][0])
                pick(page, "all_of", chosen["tags"][1])
                r = pick(page, "all_of", chosen["tags"][2], timed=True)
                r = finish(page, r, lambda v: [m["value"] for m in v.get("all_of", [])] == chosen["tags"])
                rows.append(r)
                ctx.close()
            results["all_of_3_tags"] = rows
            show("(b) All of three tags (%s photos each): the third Enter to the first window painted and idle"
                 % " / ".join(str(n) for n in chosen["tag_photos"]), rows)

        if "c" in wanted:
            rows = []
            for _ in range(args.rounds):
                ctx, page = new_page(root + "?view=all")
                page.click("#btn-library-search-filters")
                pick(page, "any_of", chosen["people"][0])
                pick(page, "any_of", chosen["people"][1])
                r = pick(page, "none_of", chosen["tags"][0], timed=True)
                r = finish(page, r, lambda v: [m["value"] for m in v.get("any_of", [])] == chosen["people"]
                           and [m["value"] for m in v.get("none_of", [])] == chosen["tags"][:1])
                rows.append(r)
                ctx.close()
            results["any_of_2_people_less_a_tag"] = rows
            show("(c) Any of two people, None of a tag: the last Enter to the first window painted and idle", rows)

        if "d" in wanted:
            rows = []
            for _ in range(args.rounds):
                ctx, page = new_page(root + "?view=year&value=%s" % chosen["year"])
                page.check("#library-search-within")
                page.fill("#library-search-words", chosen["word"])
                reset(page)
                page.evaluate("() => window.__n.timed(() => window.__n.searched(), 120000)")
                page.press("#library-search-words", "Enter")
                r = finish(page, page.evaluate("() => window.__p"),
                           lambda v: v.get("words") == chosen["word"] and v.get("all_of") == [{"kind": "year", "value": str(chosen["year"])}])
                rows.append(r)
                ctx.close()
            results["word_within_a_year"] = rows
            show("(d) the word within the year with the most photos (%d): Enter to the first window painted and idle"
                 % chosen["year_photos"], rows)

        if "e" in wanted:
            rows = []
            value = json.dumps({"none_of": [{"kind": "keyword", "value": chosen["tags"][0]}]}, separators=(",", ":"))
            for _ in range(args.rounds):
                ctx, page = new_page(root + "?view=search&value=" + urllib.parse.quote(value, safe=""))
                page.wait_for_function("() => window.__n.searched()", timeout=120000)
                page.evaluate("() => window.__n.idle()")
                page.evaluate("() => window.__n.timed(() => document.querySelector('#selection-tags-list .selection-summary-chip'), 120000)")
                page.click("#btn-select-all-thumbnails")
                r = page.evaluate("() => window.__p")
                ask = page.evaluate("""() => { const e = performance.getEntriesByType('resource').find(x => x.name.includes('/selection/tally'));
                    return e ? {ms: e.responseEnd - e.requestStart, wait: e.responseStart - e.requestStart, kb: e.encodedBodySize / 1024} : {}; }""")
                r["tally_request_ms"], r["tally_server_wait_ms"], r["tally_reply_kb"] = ask.get("ms"), ask.get("wait"), ask.get("kb")
                r["photos"] = page.evaluate("() => window.__n.total()")
                r["source_is_the_search"] = page.evaluate("() => /view=search/.test(location.search)")
                rows.append(r)
                ctx.close()
            results["select_all_tally"] = rows
            show("(e) Select all of a search less the largest tag, and its tally: the click to the tags and people painted", rows)
        browser.close()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="do it; without this the plan is printed and nothing is done")
    parser.add_argument("--code-root", default=REPO_ROOT, help="the checkout whose code the sandbox serves (default: this one)")
    parser.add_argument("--source", default=tagpup_config.library_path("photo_index.db"),
                        help="the library to copy; opened read-only and never written")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--only", default=EVERYTHING, help="which of a to e to run, as letters (default: all)")
    parser.add_argument("--out", default="", help="also write the numbers here as JSON (counts and times only)")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    args = parser.parse_args()
    args.only = [letter for letter in args.only.lower() if letter in EVERYTHING]
    return args


def print_plan(args):
    print("measure_search.py would, with --run:")
    print("  copy %s (read-only source) into a sandbox under %s, its roots placed at empty sandbox folders, and migrate the copy"
          % (args.source, tempfile.gettempdir()))
    print("  serve the code of %s from that sandbox on a free port" % args.code_root)
    print("  in headless Chromium, %d fresh browsers each: (a) a common word, Enter; (b) All of three tags; (c) Any of two people"
          " less a tag; (d) a word within a year; (e) Select all of a large search and its tally" % args.rounds)
    print("  run only: %s; print the numbers (counts and times, never a name or a word), then delete the sandbox" % ", ".join(args.only))
    print("Nothing was done.")


def migrate(sandbox, db_path, code_root):
    """Bring the copy up to date with the served code, timed: what the server would do as it starts (migration 24 here)."""
    started = time.time()
    processes.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); from tagpup.store import schema; "
                   "schema.ensure(%r)" % (code_root, db_path)],
                  cwd=sandbox, env=dict(os.environ, TAGPUP_HOME=sandbox), check=True)
    print("  the copy brought up to date by the served code in %.1f s" % (time.time() - started))


def choose(sandbox, db_path):
    done = processes.run([sys.executable, "-c", CHOOSE % (sandbox, db_path)], cwd=sandbox,
                         env=dict(os.environ, TAGPUP_HOME=sandbox), check=True, capture_output=True, text=True)
    chosen = json.loads(done.stdout.strip().splitlines()[-1])
    print("  chosen on the copy: a word %d photos hold; three tags of %s photos, %d photos holding all three; %d people; a year of"
          " %d photos" % (chosen["word_photos"], " / ".join(str(n) for n in chosen["tag_photos"]), chosen["all_three"],
                          len(chosen["people"]), chosen["year_photos"]))
    return chosen


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
        chosen = choose(sandbox, db_path)
        results["chosen"] = {key: value for key, value in chosen.items() if key.endswith("photos")}
        tuner, tagpup = free_port(), free_port()
        server = start_server(sandbox, db_path, tuner, tagpup)
        print("  serving the code of %s on port %d" % (args.code_root, tagpup))
        drive("http://127.0.0.1:%d" % tagpup, args, chosen, results)
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
