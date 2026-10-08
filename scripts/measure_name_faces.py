"""Time "Name faces from tags" (#789) the way a person uses it: in a real Chromium, on a sandbox copy of a library.

The numbers in docs/ARCHITECTURE.md, "Name faces from tags", come from this. On a copy of photo_index it measures each a person's
action and not an endpoint:

  (a) TagTuner: the click on the header's button -> the question painted ("Name N faces in M photos ...?") and the main thread idle;
      the plan is the job's first step, so this is the plan's time, 11 to 26 s when it was a request (#844);
  (b) TagPup: the same click on the folder view's button -> the question (the plan is read again; nothing is cached);
  (c) the click on Yes -> the result painted ("N face names given ...") and idle: the journaled change of every face the plan named;
  (d) with --group, Yes with the grouping ticked -> the result: DBSCAN over every face and the voting, which is the slow part
      (it is not run unless asked: it re-derives every automatic name of the copy and takes a long while).

While the job works a probe asks the server for /api/server twice a second, and the slowest answer is printed: the server must
stay usable while it plans (docs/ARCHITECTURE.md). Everything runs in a sandbox built and deleted by scripts/sandbox.py: the
library copied through SQLite's backup API (the original read-only) under a temporary TAGPUP_HOME, its roots placed at empty
sandbox folders, served on a free port. Nothing in data/ is touched. Only counts and times are printed, never a name.

    .venv/Scripts/python.exe scripts/measure_name_faces.py                  # prints the plan, does nothing
    .venv/Scripts/python.exe scripts/measure_name_faces.py --run [--group]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
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
  // From now until `ready()` holds, painted and idle: milliseconds, or -1 at the limit.
  async timed(ready, limit) {
    const t0 = performance.now();
    while (!ready()) {
      if (performance.now() - t0 > limit) return -1;
      await new Promise(r => setTimeout(r, 50));
    }
    await window.__f.idle();
    return performance.now() - t0;
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


class Probe:
    """Asks the server a cheap question twice a second while a job works, and keeps the slowest answer."""

    def __init__(self, url):
        self.url, self.slowest, self.count, self._stop = url, 0.0, 0, threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            started = time.time()
            try:
                urllib.request.urlopen(self.url, timeout=60).read()
                self.slowest = max(self.slowest, time.time() - started)
                self.count += 1
            except OSError:
                self.slowest = max(self.slowest, 60.0)
            self._stop.wait(0.5)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join(5)


def say(label, ms):
    print("   %-72s %9.0f ms" % (label, ms))


def question_shown(page):
    """From now until the question is on screen, painted and idle."""
    return page.evaluate("""async () => {
        const ready = () => { const q = document.querySelector('#name-faces-modal .name-faces-question');
            return q && /^(Name|No face)/.test(q.textContent); };
        return await window.__f.timed(ready, 600000); }""")


def drive(base_tuner, base_tagpup, args, results):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        page.goto("%s/%s/" % (base_tuner, LIBRARY), wait_until="domcontentloaded", timeout=900000)
        page.wait_for_selector("#btn-name-faces", timeout=120000)
        page.evaluate(HELPERS_JS)
        page.wait_for_timeout(2000)
        with Probe("%s/%s/api/server" % (base_tuner, LIBRARY)) as probe:
            page.evaluate("() => { window.__t0 = performance.now(); document.getElementById('btn-name-faces').click(); }")
            ms = question_shown(page)
            ms = ms + 0 if ms < 0 else page.evaluate("() => performance.now() - window.__t0")
        results["tuner_question_ms"] = ms
        say("(a) TagTuner: click Name faces from tags -> the question painted and idle", ms)
        print("       the server answered %d probes meanwhile, the slowest in %.2f s" % (probe.count, probe.slowest))
        results["tuner_probe_slowest_s"] = probe.slowest
        print("       the question: %s" % page.evaluate("() => document.querySelector('#name-faces-modal .name-faces-question').textContent"))
        print("       the grouping box is ticked: %s" % page.evaluate("() => document.getElementById('name-faces-group').checked"))
        results["question"] = page.evaluate("() => document.querySelector('#name-faces-modal .name-faces-question').textContent")

        # No: nothing is written, and the second page asks again.
        page.get_by_role("button", name="No").click()
        page.wait_for_function("() => /nothing was changed/i.test(document.querySelector('#name-faces-modal .name-faces-body').textContent)",
                               timeout=60000)

        page2 = ctx.new_page()
        page2.goto("%s/%s/" % (base_tagpup, LIBRARY), wait_until="domcontentloaded", timeout=900000)
        page2.wait_for_selector("#btn-name-faces", state="attached", timeout=120000)
        page2.evaluate(HELPERS_JS)
        page2.wait_for_timeout(2000)
        page2.evaluate("() => { window.__t0 = performance.now(); document.getElementById('btn-name-faces').click(); }")
        ms = question_shown(page2)
        ms = ms if ms < 0 else page2.evaluate("() => performance.now() - window.__t0")
        results["tagpup_question_ms"] = ms
        say("(b) TagPup: click Name faces from tags -> the question painted and idle", ms)

        if args.group:
            page2.evaluate("() => { const b = document.getElementById('name-faces-group'); b.checked = true;"
                           " b.dispatchEvent(new Event('change', { bubbles: true })); }")
        with Probe("%s/%s/api/server" % (base_tagpup, LIBRARY)) as probe:
            page2.evaluate("() => { window.__t0 = performance.now(); }")
            page2.get_by_role("button", name="Yes").click()
            done = page2.evaluate("""async (limit) => {
                const ready = () => { const r = document.querySelector('#name-faces-modal .name-faces-result');
                    return r && r.textContent.length > 0; };
                return await window.__f.timed(ready, limit); }""", 10800000 if args.group else 600000)
            ms = done if done < 0 else page2.evaluate("() => performance.now() - window.__t0")
        key = "group_ms" if args.group else "yes_ms"
        results[key] = ms
        say("(%s) TagPup: click Yes%s -> the result painted and idle" % ("d" if args.group else "c", " with the grouping ticked" if args.group else ""), ms)
        print("       the server answered %d probes meanwhile, the slowest in %.2f s" % (probe.count, probe.slowest))
        results[key.replace("_ms", "_probe_slowest_s")] = probe.slowest
        results["result"] = page2.evaluate("() => document.querySelector('#name-faces-modal .name-faces-body').textContent")
        print("       the result: %s" % results["result"])
        browser.close()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="really do it (without it, only the plan is printed)")
    parser.add_argument("--group", action="store_true", help="also time the grouping (Yes with its box ticked): it takes a long while")
    parser.add_argument("--source", default=tagpup_config.library_path("photo_index.db"),
                        help="the library to copy; opened read-only and never written")
    parser.add_argument("--code-root", default=REPO_ROOT, help="the code to serve (a git archive of another commit)")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--out", help="write the numbers here as JSON")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.run:
        print("Would copy %s (read-only) into a sandbox, serve the code of %s on free ports, and time in Chromium: the click on Name faces "
              "from tags -> the question (TagTuner, then TagPup), then Yes%s. Nothing was done."
              % (os.path.basename(args.source), args.code_root, " with the grouping ticked" if args.group else ""))
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
        tuner, tagpup = free_port(), free_port()
        server = start_server(sandbox, db_path, tuner, tagpup)
        print("  serving the code of %s on ports %d (TagTuner) and %d (TagPup)" % (args.code_root, tuner, tagpup))
        drive("http://127.0.0.1:%d" % tuner, "http://127.0.0.1:%d" % tagpup, args, results)
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
