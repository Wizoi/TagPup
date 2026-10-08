"""Watch the face and the photo's person tag stay in step (#859, #860, #861), the way a person works: in a real Chromium, on a
sandbox copy of a library.

On a copy of photo_index, with the photo it picks (exactly three faces, one of them named, another with a suggestion) given a
real file in the sandbox, it does what the owner did and says what the page, the library and the FILE hold after each:

  (a) the Detected Faces strip's card of an unnamed face with a suggestion -> the face named, the person on the photo (tag
      first), the box painted named and the strip's card with it, idle; then the library and the file;
  (b) the page loaded again -> the same boxes, the same strip;
  (c) the box's panel, Not this person -> the face unnamed, the person off the photo and out of its file;
  (d) the full-window zoom: the click on the photo -> every box drawn over the zoomed picture; a click on a box -> the panel;
      Escape -> the panel goes, the zoom stays; Escape -> the zoom goes;
  (e) TagTuner's own routes, through the other app's port: Match writes the tag and names the face, Unmatch takes both off,
      Not important on a named face takes the person off the photo, AutoMatch All names and tags;
  and what the faces' read (/api/photo-faces) costs, since every write of the open photo's tags draws its faces again.

    .venv/Scripts/python.exe scripts/measure_faces_in_step.py                  # prints the plan, does nothing
    .venv/Scripts/python.exe scripts/measure_faces_in_step.py --run

Everything runs in a sandbox built and deleted by scripts/sandbox.py: the library copied through SQLite's backup API (the original
read-only) under a temporary TAGPUP_HOME, its roots placed at empty sandbox folders, served on a free port. The one photo file it
writes is under a folder the sandbox placed, and the script refuses to write anywhere else. Nothing in data/ is touched. Only
counts, yes/no and times are printed, never a name, a keyword or a folder.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

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
from tagpup.core.library import Library
from tagpup.services import faces as faces_service
from tagpup.store import db, roots as store_roots
conn = db.connect(%(db)r)
library = Library(%(db)r)
candidates = conn.execute('''
    SELECT p.id, p.path FROM photos p
    WHERE (SELECT COUNT(*) FROM faces f WHERE f.photo_id = p.id) = 3
      AND (SELECT COUNT(*) FROM faces f WHERE f.photo_id = p.id AND f.excluded = 0) = 3
      AND (SELECT COUNT(*) FROM faces f WHERE f.photo_id = p.id AND f.name IS NOT NULL) = 1
    ORDER BY p.id LIMIT 400''').fetchall()
picked = None
tried = 0
for photo_id, row_path in candidates:
    native = store_roots.from_row(conn, row_path)
    tried += 1
    panel = faces_service.panel(library, native)
    unnamed = [f for f in panel["faces"] if not f["name"] and f["suggestion"]]
    drift = conn.execute("SELECT COUNT(*) FROM photo_people WHERE photo_id = ? AND source = 'face'", (photo_id,)).fetchone()[0]
    if unnamed and (drift or picked is None):
        picked = (photo_id, native, drift, unnamed[0]["id"], unnamed[0]["suggestion"])
        if drift:
            break
    if picked and tried >= 60:
        break
if not picked:
    print(json.dumps({"error": "no photo with three faces, one named, and another with a suggestion (tried %%d)" %% tried}))
    sys.exit(0)
photo_id, native, drift, suggested_face, suggested_name = picked
sandbox = os.path.realpath(%(sandbox)r)
target = os.path.realpath(native)
if os.path.commonpath([sandbox, target]) != sandbox:
    print(json.dumps({"error": "the photo would be written outside the sandbox"}))
    sys.exit(0)
boxes = [json.loads(box) for (box,) in conn.execute("SELECT box FROM faces WHERE photo_id = ? ORDER BY id", (photo_id,))]
width = max([4000] + [int(box[2]) + 50 for box in boxes])
height = max([3000] + [int(box[3]) + 50 for box in boxes])
os.makedirs(os.path.dirname(target), exist_ok=True)
# A kept sandbox holds the photos earlier runs gave files: only this one is in its folder now (the folder is the sandbox's).
for name in os.listdir(os.path.dirname(target)):
    if os.path.join(os.path.dirname(target), name) != target and name.lower().endswith(".jpg"):
        os.remove(os.path.join(os.path.dirname(target), name))
Image.new("RGB", (width, height), (90, 110, 130)).save(target, "JPEG")
json.dump({"photo_id": photo_id, "folder": os.path.dirname(target), "path": target, "size": [width, height],
           "named_by_face_alone": bool(drift), "tried": tried, "suggested_face": suggested_face, "suggested_name": suggested_name,
           "face_ids": [i for (i,) in conn.execute("SELECT id FROM faces WHERE photo_id = ? ORDER BY id", (photo_id,))]},
          open(%(out)r, "w"))
print(json.dumps({"ok": True}))
"""

#: Run in the sandbox: what the library and the FILE hold of the photo's faces and people, as counts and yes/no.
CHECK = r"""
import json, sys
sys.path.insert(0, %(sandbox)r)
from tagpup import runtime
from tagpup.core import vocabulary
from tagpup.core.library import Library
from tagpup.files.exiftool_session import ExifToolSession
from tagpup.store import db, taxonomy
conn = db.connect(db.readonly_uri(%(db)r), uri=True)
known = taxonomy.read_people_vocabulary(conn)
faces = conn.execute("SELECT id, name IS NOT NULL, name_source, excluded FROM faces WHERE photo_id = ? ORDER BY id", (%(photo)d,)).fetchall()
people = conn.execute("SELECT source FROM photo_people WHERE photo_id = ? ORDER BY position", (%(photo)d,)).fetchall()
with ExifToolSession(executable=runtime.exiftool(Library(%(db)r))) as et:
    held = et.get_tags([%(path)r], tags=["XMP:Subject"])[0].get("XMP:Subject") or []
held = [held] if isinstance(held, str) else held
persons = [t for t in held if vocabulary.extract_people({}, [t], known)]
print(json.dumps({"faces_named": sum(1 for f in faces if f[1]), "faces": len(faces), "excluded": sum(f[3] for f in faces),
                  "people_listed": len(people), "people_from_a_face_alone": sum(1 for p in people if p[0] == "face"),
                  "tags_in_the_file": len(held), "people_tags_in_the_file": len(persons)}))
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


def say(label, value):
    print("   %-78s %s" % (label, value))


def held(sandbox, db_path, picked):
    done = processes.run([sys.executable, "-c", CHECK % {"sandbox": sandbox, "db": db_path, "photo": picked["photo_id"],
                                                         "path": picked["path"]}],
                         cwd=sandbox, env=environment(sandbox), check=True, capture_output=True, text=True)
    return json.loads(done.stdout.strip().splitlines()[-1])


def post(port, route, body):
    request = urllib.request.Request("http://127.0.0.1:%d/%s/api/%s" % (port, LIBRARY, route), data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as reply:
            return reply.status, json.loads(reply.read() or b"{}")
    except urllib.error.HTTPError as problem:
        return problem.code, json.loads(problem.read() or b"{}")


def page_state(page):
    return page.evaluate("""() => ({
        boxes: document.querySelectorAll('#face-layer .face-box').length,
        named: document.querySelectorAll('#face-layer .face-box.is-named').length,
        cards: document.querySelectorAll('.face-card').length,
        cards_named: [...document.querySelectorAll('.face-card')].filter(c => !c.classList.contains('unmatched') && !c.classList.contains('excluded')).length,
        no_people_tags: document.getElementById('detail-people').textContent.includes('No people tags'),
        people_pills: document.querySelectorAll('#detail-people .tag-pill').length,
        keyword_pills: document.querySelectorAll('#detail-tags .tag-pill').length,
        summary: document.getElementById('faces-summary').textContent })""")


def open_photo(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=900000)
    page.wait_for_function("() => document.getElementById('sidebar-switch')", timeout=120000)
    page.evaluate(HELPERS_JS)
    # The vocabulary the people pills are drawn by arrives after the page; a photo opened before it shows its people as keywords.
    page.wait_for_function("() => document.querySelectorAll('#people-datalist option').length > 0", timeout=120000)
    page.wait_for_timeout(2000)
    page.wait_for_selector("li.photo-item-file", timeout=120000)
    page.locator("li.photo-item-file").first.click()
    page.wait_for_selector("#face-layer .face-boxes-toggle", timeout=120000)
    page.wait_for_function("() => document.getElementById('main-image').complete && document.getElementById('main-image').naturalWidth > 0", timeout=60000)
    page.wait_for_selector(".face-card", timeout=60000)
    page.wait_for_timeout(1500)       # the vocabulary the pills are drawn by arrives after the photo
    page.evaluate("() => window.__f.idle()")


def drive(base, tuner_port, picked, args, results, sandbox, db_path):
    from playwright.sync_api import sync_playwright
    root = "%s/%s/" % (base, LIBRARY)
    url = root + "?path=" + urllib.parse.quote(picked["folder"], safe="")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        open_photo(page, url)
        before = page_state(page)
        results["before"] = before
        print("\nthe photo (tried %d candidates): %d faces, %d named; people tags: %s; the library lists its people from a face alone: %s"
              % (picked["tried"], before["boxes"] or before["cards"], before["cards_named"],
                 "none" if before["no_people_tags"] else "some", picked["named_by_face_alone"]))
        say("the library and the file before", held(sandbox, db_path, picked))

        # What a read of the faces costs: every write of the open photo's tags asks again.
        reads = page.evaluate("""async () => {
            const url = '/%s/api/photo-faces?path=' + encodeURIComponent(document.querySelector('li.photo-item-file').dataset.path);
            const out = [];
            for (let i = 0; i < 4; i++) { const t0 = performance.now(); const r = await fetch(url); await r.json(); out.push(performance.now() - t0); }
            return out; }""" % LIBRARY)
        results["faces_read_ms"] = reads
        say("(read) /api/photo-faces, four in a row (the first is cold)", ", ".join("%.0f ms" % ms for ms in reads))

        page.evaluate("() => document.querySelector('#face-layer .face-boxes-toggle').click()")
        page.evaluate("() => window.__f.idle()")

        # (a) the strip's card.
        result = page.evaluate("""async (faceId) => {
            const crop = document.querySelector('.face-card img[src*="id=' + faceId + '"]');
            const card = crop && crop.closest('.face-card.face-card-actionable');
            if (!card) return { ms: -2, strip_ms: -2 };
            const named0 = document.querySelectorAll('#face-layer .face-box.is-named').length;
            const cards0 = [...document.querySelectorAll('.face-card')].filter(c => !c.classList.contains('unmatched') && !c.classList.contains('excluded')).length;
            const t0 = performance.now(); card.click();
            const boxes = await window.__f.timed(() => document.querySelectorAll('#face-layer .face-box.is-named').length === named0 + 1
                && !document.getElementById('detail-people').textContent.includes('No people tags'));
            const boxesAt = performance.now() - t0;
            const strip = await window.__f.timed(() => [...document.querySelectorAll('.face-card')].filter(c => !c.classList.contains('unmatched') && !c.classList.contains('excluded')).length === cards0 + 1);
            return { ms: boxesAt, strip_ms: performance.now() - t0, named: document.querySelectorAll('#face-layer .face-box.is-named').length }; }""",
                          picked["suggested_face"])
        results["strip_card_ms"] = result["ms"]
        say("(a) click a strip card -> face named, person on the photo, box painted named and idle", "%.0f ms" % result["ms"])
        say("    ... and the strip drawn again from the library's answer, idle", "%.0f ms" % result["strip_ms"])
        after = page_state(page)
        say("    the page after: boxes named / cards named / people pills", "%d / %d / %d" % (after["named"], after["cards_named"], after["people_pills"]))
        after_a = held(sandbox, db_path, picked)
        say("    the library and the file after", after_a)
        results["after_strip"] = {"page": after, "held": after_a}

        # (b) reload.
        open_photo(page, url)
        page.evaluate("() => document.querySelector('#face-layer .face-boxes-toggle').click()")
        again = page_state(page)
        say("(b) the page loaded again: boxes named / cards named / people pills / keyword pills", "%d / %d / %d / %d" % (
            again["named"], again["cards_named"], again["people_pills"], again["keyword_pills"]))
        results["after_reload"] = again

        # (c) Not this person.
        out = page.evaluate("""async (faceId) => {
            const target = document.querySelector('#face-layer .face-box[data-face-id="' + faceId + '"]');
            target.click();
            await window.__f.timed(() => document.querySelector('#face-layer .face-panel'));
            const off = [...document.querySelectorAll('#face-layer .face-panel button')].find(b => b.textContent === 'Not this person');
            const before = document.querySelectorAll('#face-layer .face-box.is-named').length;
            const t0 = performance.now(); off.click();
            const ms = await window.__f.timed(() => document.querySelectorAll('#face-layer .face-box.is-named').length === before - 1);
            return { ms: performance.now() - t0, named: document.querySelectorAll('#face-layer .face-box.is-named').length }; }""",
                          picked["suggested_face"])
        results["not_this_person_ms"] = out["ms"]
        say("(c) Not this person -> face unnamed, person taken off the photo, box painted and idle", "%.0f ms" % out["ms"])
        page.wait_for_timeout(300)
        after_c = held(sandbox, db_path, picked)
        say("    the library and the file after (the first face stays named)", after_c)
        results["after_unname"] = after_c

        # (d) the zoom.
        z = page.evaluate("""async () => {
            const t0 = performance.now(); document.getElementById('main-image').click();
            const ms = await window.__f.timed(() => document.querySelectorAll('.image-zoom-layer .face-box').length === 3);
            const inside = [...document.querySelectorAll('.image-zoom-layer .face-box')].every(b => {
                const r = b.getBoundingClientRect(); return r.width > 4 && r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight; });
            return { ms, boxes: document.querySelectorAll('.image-zoom-layer .face-box').length, inside,
                     behind: document.querySelectorAll('#face-layer .face-box').length }; }""")
        results["zoom"] = z
        say("(d) click the photo -> its boxes drawn over the zoomed picture, painted and idle", "%.0f ms (boxes %d, all inside the window: %s, copies behind: %d)"
            % (z["ms"], z["boxes"], z["inside"], z["behind"]))
        p = page.evaluate("""async () => {
            const t0 = performance.now(); document.querySelectorAll('.image-zoom-layer .face-box')[0].click();
            const ms = await window.__f.timed(() => document.querySelector('.image-zoom-layer .face-panel'));
            return { ms, open: !document.getElementById('image-zoom').classList.contains('hidden') }; }""")
        say("    click a box in the zoom -> the panel painted and idle", "%.0f ms (zoom still open: %s)" % (p["ms"], p["open"]))
        page.keyboard.press("Escape")
        page.evaluate("() => window.__f.idle()")
        e1 = page.evaluate("() => ({ panel: !!document.querySelector('.image-zoom-layer .face-panel'), open: !document.getElementById('image-zoom').classList.contains('hidden') })")
        page.keyboard.press("Escape")
        page.evaluate("() => window.__f.idle()")
        e2 = page.evaluate("() => ({ open: !document.getElementById('image-zoom').classList.contains('hidden'), boxes: document.querySelectorAll('#face-layer .face-box').length })")
        say("    Escape -> the panel goes (panel left: %s), the zoom stays (open: %s)" % (e1["panel"], e1["open"]), "")
        say("    Escape again -> the zoom goes (open: %s), the boxes are back on the photo (%d)" % (e2["open"], e2["boxes"]), "")
        results["zoom_escape"] = [e1, e2]
        browser.close()

    # (e) TagTuner's routes.
    ids = picked["face_ids"]
    print("\nTagTuner's own routes (the other app's port), on the same photo:")
    status, body = post(tuner_port, "photo/unmatch-all", {"photo_path": picked["path"]})
    say("Unmatch All -> status %d, tags taken off %s; the library and the file after" % (status, body.get("tags_removed")),
        held(sandbox, db_path, picked))
    status, body = post(tuner_port, "face/match", {"face_id": picked["suggested_face"], "person_name": picked["suggested_name"]})
    say("Match (the strip's) -> status %d, files written %s; the library and the file after" % (status, body.get("tags_written")),
        held(sandbox, db_path, picked))
    status, body = post(tuner_port, "face/unmatch", {"face_id": picked["suggested_face"]})
    say("Unmatch -> status %d, tags taken off %s; after" % (status, body.get("tags_removed")), held(sandbox, db_path, picked))
    post(tuner_port, "face/match", {"face_id": picked["suggested_face"], "person_name": picked["suggested_name"]})
    status, body = post(tuner_port, "faces/exclude", {"face_ids": [picked["suggested_face"]]})
    say("Not important on the face just named -> status %d, tags taken off %s; after" % (status, body.get("tags_removed")),
        held(sandbox, db_path, picked))
    status, body = post(tuner_port, "faces/restore", {"face_ids": [picked["suggested_face"]]})
    say("Restore -> status %d, restored %s" % (status, body.get("restored")), "")
    status, body = post(tuner_port, "photo/automatch", {"photo_path": picked["path"]})
    say("AutoMatch All -> status %d, faces named %s, files written %s; after" % (status, body.get("matched_count"), body.get("tags_written")),
        held(sandbox, db_path, picked))
    results["tuner"] = {"last": status}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="really do it (without it, only the plan is printed)")
    parser.add_argument("--source", default=tagpup_config.library_path("photo_index.db"),
                        help="the library to copy; opened read-only and never written")
    parser.add_argument("--code-root", default=REPO_ROOT, help="the code to serve (a git archive of another commit)")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--out", help="write the numbers here as JSON")
    parser.add_argument("--reuse", help="a sandbox this script made and kept (--keep): its code and its library as the last run left"
                                        " them, served again with a photo picked anew")
    parser.add_argument("--keep", action="store_true", help="do not delete the sandbox at the end (a run with --reuse DIR deletes it)")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.run:
        print("Would copy %s (read-only) into a sandbox, serve the code of %s on a free port, give one photo with three faces (one"
              " named) a file in the sandbox, and watch in Chromium the strip's card, a reload, Not this person and the zoom, then"
              " TagTuner's routes. Nothing was done." % (os.path.basename(args.source), args.code_root))
        return
    if not os.path.exists(args.source):
        sys.exit("%s does not exist (is TAGPUP_HOME set? or pass --source)" % args.source)
    sandbox = args.reuse or tempfile.mkdtemp(prefix="tagpup_in_step_")
    server = None
    results = {"code_root": args.code_root}
    try:
        print("sandbox : %s" % sandbox)
        db_path = os.path.join(sandbox, "data", LIBRARY + ".db")
        if not args.reuse:
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
        print("  serving the code of %s on ports %d (TagPup) and %d (TagTuner)" % (args.code_root, tagpup, tuner))
        drive("http://127.0.0.1:%d" % tagpup, tuner, picked, args, results, sandbox, db_path)
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
        if args.keep:
            print("sandbox kept: %s (run again with --reuse; the second run deletes it)" % sandbox)
        else:
            print("sandbox deleted: %s" % remove_sandbox(sandbox))


if __name__ == "__main__":
    main()
