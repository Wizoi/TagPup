"""Time the faces on the open photo (#791) and the hover of a suggested person (#790), the way a person uses them: in a real
Chromium, on a sandbox copy of a library.

The numbers in docs/ARCHITECTURE.md, "Faces on the photo", come from this. On a copy of photo_index, with the photo it picks (one
with exactly three faces, none named, and a person among its keywords) given a real file in the sandbox, in a folder the sandbox's
own machine map places, it measures each a person's action and not an endpoint:

  (a) the click on the face icon -> the three boxes painted and the main thread idle;
  (b) the click on a box -> the panel painted with who the face looks like (the first one asks the server to build the matrix of
      every named face: said so), and the same again on another box;
  (c) hovering a suggestion -> the popup of the person's faces painted (the first hover asks for everyone's faces: said so);
  (d) the click on a suggestion -> the tag written, the face named, the box and the photo's people painted and the main thread idle;
      then the faces table and the photo's people as the library holds them, which is what TagTuner reads.

    .venv/Scripts/python.exe scripts/measure_faces_on_the_photo.py                  # prints the plan, does nothing
    .venv/Scripts/python.exe scripts/measure_faces_on_the_photo.py --run

Everything runs in a sandbox built and deleted by scripts/sandbox.py: the library copied through SQLite's backup API (the original
read-only) under a temporary TAGPUP_HOME, its roots placed at empty sandbox folders, served on a free port. The one photo file the
measurement writes is under a folder the sandbox placed, and the script refuses to write anywhere else. Nothing in data/ is touched.
Only counts and times are printed, never a name, a keyword or a folder.
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
from sandbox import copy_library, environment, free_port, place_roots, remove_sandbox  # noqa: E402

LIBRARY = "measured"

#: What the measurement waits on: the next two frames, then a turn of the event loop -- the page painted and idle.
HELPERS_JS = r"""
window.__f = {
  async idle() {
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    await new Promise(r => setTimeout(r, 0));
  },
  // From now until `ready()` holds, painted and idle: milliseconds.
  async timed(ready, limit = 120000) {
    const t0 = performance.now();
    while (!ready()) {
      if (performance.now() - t0 > limit) return -1;
      await new Promise(r => requestAnimationFrame(r));
    }
    await window.__f.idle();
    return performance.now() - t0;
  },
};
"""

#: Run in the sandbox: pick the photo, and give it a file where the sandbox's map puts it.
PICK = r"""
import json, os, sys
sys.path.insert(0, %(sandbox)r)
from PIL import Image
from tagpup.store import db, roots as store_roots
conn = db.connect(%(db)r)
found = conn.execute('''
    SELECT p.id FROM photos p
    WHERE (SELECT COUNT(*) FROM faces f WHERE f.photo_id = p.id AND f.excluded = 0) = 3
      AND (SELECT COUNT(*) FROM faces f WHERE f.photo_id = p.id AND f.name IS NOT NULL) = 0
      AND (SELECT COUNT(*) FROM photo_people pp WHERE pp.photo_id = p.id AND pp.source = 'keyword') >= 1
    ORDER BY p.id LIMIT 1''').fetchone()
if not found:
    print(json.dumps({"error": "no photo with three unnamed faces and a keyword person"}))
    sys.exit(0)
photo_id = found[0]
native = store_roots.from_row(conn, conn.execute("SELECT path FROM photos WHERE id = ?", (photo_id,)).fetchone()[0])
sandbox = os.path.realpath(%(sandbox)r)
target = os.path.realpath(native)
if os.path.commonpath([sandbox, target]) != sandbox:
    print(json.dumps({"error": "the photo would be written outside the sandbox"}))
    sys.exit(0)
boxes = [json.loads(box) for (box,) in conn.execute("SELECT box FROM faces WHERE photo_id = ? AND excluded = 0 ORDER BY id", (photo_id,))]
width = max([4000] + [int(box[2]) + 50 for box in boxes])
height = max([3000] + [int(box[3]) + 50 for box in boxes])
os.makedirs(os.path.dirname(target), exist_ok=True)
Image.new("RGB", (width, height), (90, 110, 130)).save(target, "JPEG")
keyword = conn.execute("SELECT COUNT(*) FROM photo_people WHERE photo_id = ? AND source = 'keyword'", (photo_id,)).fetchone()[0]
json.dump({"photo_id": photo_id, "folder": os.path.dirname(target), "path": target, "size": [width, height], "keyword_people": keyword,
           "face_ids": [i for (i,) in conn.execute("SELECT id FROM faces WHERE photo_id = ? AND excluded = 0 ORDER BY id", (photo_id,))]},
          open(%(out)r, "w"))
print(json.dumps({"ok": True}))
"""

#: Run in the sandbox after the naming: what the library holds of the photo, which is what TagTuner reads.
CHECK = r"""
import json, sys
sys.path.insert(0, %(sandbox)r)
from tagpup.store import db
conn = db.connect(db.readonly_uri(%(db)r), uri=True)
faces = conn.execute("SELECT id, name IS NOT NULL, name_source, tag_id IS NOT NULL FROM faces WHERE photo_id = ? ORDER BY id", (%(photo)d,)).fetchall()
people = conn.execute("SELECT source, tag_id IS NOT NULL FROM photo_people WHERE photo_id = ? ORDER BY position", (%(photo)d,)).fetchall()
print(json.dumps({"faces": faces, "people": people}))
"""


def start_server(sandbox, db_path, tuner_port, tagpup_port):
    import socket
    process = processes.start(
        [sys.executable, os.path.join(sandbox, "tagpup_web.py"), "--db", db_path,
         "--tuner-port", str(tuner_port), "--tagpup-port", str(tagpup_port)],
        cwd=sandbox, env=environment(sandbox, TAGPUP_NO_JOBS="1"),
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


def say(label, ms):
    print("   %-62s %8.0f ms" % (label, ms))


def drive(base, picked, args, results, sandbox, db_path):
    from playwright.sync_api import sync_playwright
    root = "%s/%s/" % (base, LIBRARY)
    url = root + "?path=" + urllib.parse.quote(picked["folder"], safe="")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        # The faces' crops are answered by the server; the photo is the real (blank) file it was given.
        page.goto(url, wait_until="domcontentloaded", timeout=900000)
        page.wait_for_function("() => document.getElementById('sidebar-switch')", timeout=120000)
        page.evaluate(HELPERS_JS)
        page.wait_for_selector("li.photo-item-file", timeout=120000)
        page.locator("li.photo-item-file").first.click()
        page.wait_for_selector("#face-layer .face-boxes-toggle", timeout=120000)
        page.wait_for_function("() => document.getElementById('main-image').complete && document.getElementById('main-image').naturalWidth > 0", timeout=60000)
        page.evaluate("() => window.__f.idle()")
        results["faces_found"] = page.evaluate("() => Number(document.querySelector('#face-layer .face-boxes-toggle').textContent.replace(/[^0-9]/g, ''))")
        print("\nthe photo has %d faces; its file is %dx%d px, shown %s px wide" % (
            results["faces_found"], picked["size"][0], picked["size"][1],
            page.evaluate("() => document.getElementById('main-image').clientWidth")))

        t = page.evaluate("""async () => {
            const icon = document.querySelector('#face-layer .face-boxes-toggle');
            const t0 = performance.now(); icon.click();
            await window.__f.timed(() => document.querySelectorAll('#face-layer .face-box').length === 3);
            return performance.now() - t0; }""")
        results["boxes_ms"] = t
        say("(a) click the face icon -> 3 boxes painted and idle", t)
        wide = page.evaluate("""() => { const i = document.getElementById('main-image'); const l = document.getElementById('face-layer');
            const boxes = [...document.querySelectorAll('#face-layer .face-box')].map(b => b.getBoundingClientRect());
            const layer = l.getBoundingClientRect();
            return boxes.every(b => b.width > 4 && b.left >= layer.left - 1 && b.right <= layer.right + 1 && b.top >= layer.top - 1 && b.bottom <= layer.bottom + 1); }""")
        results["boxes_inside_the_picture"] = wide
        print("   every box lies inside the picture: %s" % wide)

        rows = []
        for n in range(3):
            ms = page.evaluate("""async (n) => {
                const t0 = performance.now(); document.querySelectorAll('#face-layer .face-box')[n].click();
                const ok = () => { const p = document.querySelector('#face-layer .face-panel');
                    return p && p.querySelector('.face-panel-suggestions') && !p.querySelector('.face-panel-suggestions').textContent.includes('Looking'); };
                await window.__f.timed(ok); return performance.now() - t0; }""", n)
            rows.append(ms)
            say("(b) click box %d -> panel with who it looks like painted and idle%s" % (n + 1, " (the first builds the matrix of named faces)" if n == 0 else ""), ms)
        results["panel_ms"] = rows
        offered = page.evaluate("() => document.querySelectorAll('#face-layer .face-panel .face-panel-suggestion').length")
        print("   the last panel offers %d suggestion(s)" % offered)

        # The first hover asks for everyone's faces.
        hovers = []
        for n in range(2):
            ms = page.evaluate("""async () => {
                const button = document.querySelector('#face-layer .face-panel .face-panel-suggestion');
                if (!button) return -2;
                button.dispatchEvent(new MouseEvent('mouseenter'));
                const t0 = performance.now();
                await window.__f.timed(() => { const p = document.getElementById('person-faces-popup'); return p && !p.classList.contains('hidden'); }, 60000);
                const took = performance.now() - t0;
                button.dispatchEvent(new MouseEvent('mouseleave'));
                return took; }""")
            hovers.append(ms)
            say("(c) hover a suggestion -> the popup of their faces painted%s" % (" (the first asks for everyone's faces)" if n == 0 else ""), ms)
        results["hover_ms"] = hovers

        result = page.evaluate("""async () => {
            const button = document.querySelector('#face-layer .face-panel .face-panel-suggestion');
            if (!button) return { ms: -2 };
            const label = button.querySelector('.face-panel-suggestion-name').textContent;
            const t0 = performance.now(); button.click();
            const named = () => [...document.querySelectorAll('#face-layer .face-box')].some(b => b.classList.contains('is-named'))
                && document.getElementById('detail-people').textContent.includes(label);
            const ms = await window.__f.timed(named);
            return { ms: performance.now() - t0, tagged: document.getElementById('detail-people').textContent.includes(label) }; }""")
        say("(d) click a suggestion -> tag written, face named, boxes and people painted and idle", result["ms"])
        results["naming_ms"] = result["ms"]
        page.wait_for_timeout(500)
        browser.close()
    done = processes.run([sys.executable, "-c", CHECK % {"sandbox": sandbox, "db": db_path, "photo": picked["photo_id"]}],
                         cwd=sandbox, env=environment(sandbox), check=True, capture_output=True, text=True)
    held = json.loads(done.stdout.strip().splitlines()[-1])
    named = [f for f in held["faces"] if f[1]]
    print("\nin the library afterwards (what TagTuner reads): %d of %d faces named (%s), the photo lists %d people (%s)" % (
        len(named), len(held["faces"]), ", ".join("source %s, with its id: %s" % (f[2], bool(f[3])) for f in named),
        len(held["people"]), ", ".join(p[0] for p in held["people"])))
    results["held"] = held


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="really do it (without it, only the plan is printed)")
    parser.add_argument("--source", default=tagpup_config.library_path("photo_index.db"),
                        help="the library to copy; opened read-only and never written")
    parser.add_argument("--code-root", default=REPO_ROOT, help="the code to serve (a git archive of another commit)")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--out", help="write the numbers here as JSON")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.run:
        print("Would copy %s (read-only) into a sandbox, serve the code of %s on a free port, give one photo with three unnamed faces"
              " a file in the sandbox, and time in Chromium: the face icon, the boxes, a box's panel, a hover and a naming. Nothing was done."
              % (os.path.basename(args.source), args.code_root))
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
        processes.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); from tagpup.store import schema; schema.ensure(%r)"
                       % (sandbox, db_path)], cwd=sandbox, env=environment(sandbox), check=True)
        out = os.path.join(sandbox, "picked.json")
        done = processes.run([sys.executable, "-c", PICK % {"sandbox": sandbox, "db": db_path, "out": out}], cwd=sandbox,
                             env=environment(sandbox), check=True, capture_output=True, text=True)
        said = json.loads(done.stdout.strip().splitlines()[-1])
        if said.get("error"):
            sys.exit(said["error"])
        with open(out) as handle:
            picked = json.load(handle)
        tuner, tagpup = free_port(), free_port()
        server = start_server(sandbox, db_path, tuner, tagpup)
        print("  serving the code of %s on port %d" % (args.code_root, tagpup))
        drive("http://127.0.0.1:%d" % tagpup, picked, args, results, sandbox, db_path)
        print("\nmedians: boxes %.0f ms, panel %.0f ms, naming %.0f ms" % (
            results["boxes_ms"], statistics.median(results["panel_ms"]), results["naming_ms"]))
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
