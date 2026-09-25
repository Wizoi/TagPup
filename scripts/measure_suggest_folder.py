"""Time "open a folder, click Start Photo Analysis, wait for suggestions" in TagPup.

The report this answers: a new folder of three photos took a very long time to get
from choosing it to having suggestions on screen. So the unit here is that click
sequence, performed in a browser, against a server started the way the app starts it.

Everything runs in a sandbox, built and deleted the same way as
measure_identify_faces.py: the library is copied through SQLite's backup API (the
original opened read-only), the code is snapshotted with its own config.ini, and the
server is a separate process on a free port. The photos are copied too, into folders
the library has never seen -- so nothing is cached for them, which is the situation
the report describes.

Two timings are taken in one sandbox, each on its own fresh copy of the photos:

* **cold** -- the server has just been launched and the folder is chosen at once,
  the way a person opens the app and gets on with it;
* **warm** -- the same, after the server has finished loading its models.

    .venv/Scripts/python.exe scripts/measure_suggest_folder.py --photos "C:/some/folder"

Headless Chromium runs faster than a real session, and the library copy has just
been written so much of it is in the OS file cache. Both make the absolute numbers
low. Use it for comparison.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from .code_snapshot import REPO_ROOT, copy_code
    from .measure_identify_faces import free_port, remove_sandbox
except ImportError:  # imported as a top-level module
    from code_snapshot import REPO_ROOT, copy_code
    from measure_identify_faces import free_port, remove_sandbox

import _root  # noqa: E402,F401
from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup.files import images  # noqa: E402
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core import processes  # noqa: E402



def build_sandbox(source_db, photos, sandbox, copies, code_root=REPO_ROOT):
    """Code, config, library and taxonomy, plus `copies` fresh copies of the photos."""
    os.makedirs(os.path.join(sandbox, "data"), exist_ok=True)
    copy_code(sandbox, code_root, launchers=True)

    # The real config, for its model and candidate settings -- suggestions made with a
    # different model would be measuring something else -- with every path pointed
    # into the sandbox.
    config = tagpup_config.read_file()
    if not config.has_section("paths"):
        config.add_section("paths")
    data_dir = os.path.join(sandbox, "data")
    config.set("paths", "data_dir", data_dir)
    tagpup_config.write_file(config, folder=sandbox)

    target = os.path.join(sandbox, "data", "measured.db")
    started = time.time()
    source = tagpup_db.connect(tagpup_db.readonly_uri(source_db), uri=True)
    destination = tagpup_db.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    print("  copied %.1f GB in %.1fs" % (os.path.getsize(target) / 1e9, time.time() - started))

    folders = []
    leaf = os.path.basename(os.path.normpath(photos))
    for n in range(copies):
        folder = os.path.join(sandbox, "photos", "copy%d" % (n + 1), leaf)
        os.makedirs(folder)
        for name in os.listdir(photos):
            if images.is_photo(name):
                shutil.copy2(os.path.join(photos, name), folder)
        folders.append(folder)
    return target, folders


def start_server(sandbox, db_path, port):
    """The TagPup server as its own process: the sandbox's tagpup_web.py, which builds
    the runtime -- Suggest's models -- and warms them as the apps people start get, logging to the
    sandbox's data/logs (tagpup.logs)."""
    log_path = os.path.join(sandbox, "data", "logs", "tagpup_web.log")
    # Its home is the sandbox, whatever TAGPUP_HOME this was run with.
    process = processes.start([sys.executable, os.path.join(sandbox, "tagpup_web.py"), "--db", db_path,
                               "--tagpup-port", str(port), "--tuner-port", str(free_port())],
                              cwd=sandbox, env=dict(os.environ, TAGPUP_HOME=sandbox),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return process, log_path


def wait_for_port(port, process, timeout=120):
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            if process.poll() is not None:
                raise RuntimeError("the sandbox server exited before it was ready") from None
            time.sleep(0.1)
    raise RuntimeError("the sandbox server never came up on port %d" % port)


def wait_for_log(log_path, pattern, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8", errors="replace") as handle:
                if re.search(pattern, handle.read()):
                    return True
        time.sleep(0.5)
    return False


def open_folder_and_suggest(page, url, folder, timeout):
    """The click sequence. Returns (folder->cards, click->suggestions usable)."""
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("#folder-path-input")

    started = time.time()
    page.fill("#folder-path-input", folder)
    page.press("#folder-path-input", "Enter")
    page.wait_for_selector("#photo-list .photo-item-file", timeout=timeout * 1000)
    page.wait_for_function(
        "() => !document.querySelector('#btn-suggest-tags').disabled",
        timeout=timeout * 1000)
    opened = time.time() - started

    started = time.time()
    page.click("#btn-suggest-tags")
    # Done means the page has every photo's suggestions: the button disables itself
    # once nothing is unprocessed, the progress bar is gone, and the status says so.
    page.wait_for_function(
        """() => document.querySelector('#btn-suggest-tags').disabled
                 && document.querySelector('#suggest-progress-container').classList.contains('hidden')
                 && document.querySelector('#status-text').textContent === 'Ready'""",
        timeout=timeout * 1000, polling=100)
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => r(1)))")
    return opened, time.time() - started


def print_log(log_path, since):
    """The server's own account of the run, relative to when it was launched."""
    interesting = re.compile(
        r"warm|Warm|Loaded \d+ index|CLIP model|Face models|Precomputing|era-aware|"
        r"suggestions_thread|Recorded|ExifTool|NOT saved|Error|error", re.I)
    base = time.strftime("%H:%M:%S", time.localtime(since))
    h0 = sum(int(x) * m for x, m in zip(base.split(":"), (3600, 60, 1)))
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = re.match(r"(\d\d):(\d\d):(\d\d)\.(\d{3}) (.*)", line)
            if match and interesting.search(line):
                h, m, s, ms, rest = match.groups()
                t = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000 - h0
                print("  %+7.1fs  %s" % (t, rest[:150]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=tagpup_config.library_path("photo_index.db"),
                        help="the library to copy; opened read-only and never written")
    parser.add_argument("--photos", required=True, help="the folder to open")
    parser.add_argument("--timeout", type=int, default=900, help="seconds")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--code", default=REPO_ROOT,
                        help="where to snapshot scripts/ and the pages from, to measure "
                             "a change before it lands in the repo")
    parser.add_argument("--keep", action="store_true",
                        help="leave the sandbox in place to look at afterwards")
    args = parser.parse_args()

    sandbox = tempfile.mkdtemp(prefix="tagpup_measure_")
    port = free_port()
    server = None
    try:
        print("sandbox : %s" % sandbox)
        print("source  : %s (read-only)" % args.source)
        print("code    : %s" % args.code)
        db_path, folders = build_sandbox(os.path.abspath(args.source), args.photos,
                                         sandbox, 2, args.code)

        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not args.headed)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})

            launched = time.time()
            server, log_path = start_server(sandbox, db_path, port)
            wait_for_port(port, server)
            url = "http://127.0.0.1:%d/measured/" % port
            print("  serving on port %d after %.1fs" % (port, time.time() - launched))

            opened, suggested = open_folder_and_suggest(page, url, folders[0], args.timeout)
            cold_total = time.time() - launched
            print("\ncold (folder chosen right after launch)")
            print("  choose folder -> photos listed : %7.2fs" % opened)
            print("  click -> suggestions usable    : %7.2fs" % suggested)
            print("  launch -> suggestions usable   : %7.2fs" % cold_total)

            if not wait_for_log(log_path, r"Face model warmup completed|Error warming", 600):
                print("  (warm-up never reported finishing)")
            opened, suggested = open_folder_and_suggest(page, url, folders[1], args.timeout)
            print("\nwarm (models already loaded)")
            print("  choose folder -> photos listed : %7.2fs" % opened)
            print("  click -> suggestions usable    : %7.2fs" % suggested)
            browser.close()

        print("\nserver log")
        print_log(log_path, launched)
    finally:
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        if args.keep:
            print("\nsandbox left at %s" % sandbox)
        else:
            remove_sandbox(sandbox)


if __name__ == "__main__":
    main()
