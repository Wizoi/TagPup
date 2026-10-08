"""See what the pages' drop-down lists look like when they are open: the library picker's white strip (#782).

A select's list is drawn by an operating-system popup that a headless browser never paints and a screenshot of
the page never holds. So this opens each page in a headed Chromium of its own, in a sandbox, opens the library
picker the way a person does (a click), and has Windows paint the popup window (PrintWindow: the picture is the
list alone, wherever it is on the desktop). The picture is the evidence; the number is the rows of it that are
no option's (a hairline of the select's own colour under the last option; white), counted on the picture, at
each display scale asked for (`--scale 1,1.25,1.5`: a list is a whole number of device pixels only at some).
With the page's styles in `--code-root` (a `git archive` of an older commit shows the older list). Measured
2026-10-07 on Chrome 154 at 100% to 225%: before the options took the select's colour, 1-2 rows at 125% to 200%;
after, one row at 125%, 150%, 175% and 225%, which is the popup window's own (Chromium rounds its height up,
and no style of the page moves it), and none at 100% and 200%.

    .venv/Scripts/python.exe scripts/measure_select_list.py                  # prints the plan, does nothing
    .venv/Scripts/python.exe scripts/measure_select_list.py --run --out <folder for the pictures>

The sandbox (scripts/sandbox.py) holds three empty libraries, on a free port; nothing in data/ is read or
written. A Chromium window is on the desktop for a few seconds per page. Windows only.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup.core import processes  # noqa: E402
from code_snapshot import REPO_ROOT, copy_code  # noqa: E402
from sandbox import enter, environment, free_port, remove_sandbox  # noqa: E402

LIBRARIES = ("kr-track.db", "photo_index.db", "renton_parkrun.db")
PAGES = ("tagpup", "tuner")
LIGHT = 200      # a channel above this on all three is a light pixel
MIN_OPTION = 8   # an option is at least this many device pixels tall


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


def chrome_windows():
    """{handle: (width, height)} of every visible Chromium window on the desktop (the owner's own too)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = {}
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(handle, _):
        if user32.IsWindowVisible(handle):
            name = ctypes.create_unicode_buffer(100)
            user32.GetClassNameW(handle, name, 100)
            rect = wintypes.RECT()
            user32.GetWindowRect(handle, ctypes.byref(rect))
            if name.value == "Chrome_WidgetWin_1" and rect.left > -30000:
                found[handle] = (rect.right - rect.left, rect.bottom - rect.top)
        return True
    user32.EnumWindows(callback(each), 0)
    return found


def picture_of(handle, width, height):
    """What Windows paints in one window (PrintWindow, the full content), as a PIL image."""
    import ctypes
    from ctypes import wintypes
    from PIL import Image
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

    class Header(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG), ("planes", wintypes.WORD),
                    ("bits", wintypes.WORD), ("compression", wintypes.DWORD), ("image", wintypes.DWORD),
                    ("x", wintypes.LONG), ("y", wintypes.LONG), ("used", wintypes.DWORD), ("important", wintypes.DWORD)]
    source = user32.GetWindowDC(handle)
    memory = gdi32.CreateCompatibleDC(source)
    bitmap = gdi32.CreateCompatibleBitmap(source, width, height)
    gdi32.SelectObject(memory, bitmap)
    user32.PrintWindow(handle, memory, 2)
    header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory)
    user32.ReleaseDC(handle, source)
    return Image.frombuffer("RGBA", (width, height), pixels, "raw", "BGRA", 0, 1).convert("RGB")


def strip_rows(picture):
    """(rows of the picture that are no option's, rows): the list's frame is one row at the top and one under
    the last option; every other run of rows of one colour that is shorter than an option -- a hairline of the
    select's own colour, or the window's white below the frame -- or is white, is a strip. The colour is read
    down the left margin, where there is no text. 0 for a list of options and nothing else."""
    width, height = picture.size
    colours = [picture.getpixel((2, y)) for y in range(height)]
    runs, start = [], 0
    for y in range(1, height + 1):
        if y == height or colours[y] != colours[start]:
            runs.append((y - start, colours[start]))
            start = y
    frame = runs[0][1]
    runs = runs[1:]
    for index in range(len(runs) - 1, -1, -1):      # the bottom frame, once
        if runs[index][1] == frame and runs[index][0] < MIN_OPTION:
            del runs[index]
            break
    strip = sum(length for length, (r, g, b) in runs if length < MIN_OPTION or min(r, g, b) > LIGHT)
    return strip, height


def photograph(ports, page_name, out, channel=None, scale=None):
    import ctypes
    from playwright.sync_api import sync_playwright
    ctypes.windll.user32.SetProcessDPIAware()
    port = {"tagpup": ports[1], "tuner": ports[0]}[page_name]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, channel=channel,
                                     args=["--force-device-scale-factor=%s" % scale] if scale else [])
        try:
            page = browser.new_context(viewport={"width": 1300, "height": 800}).new_page()
            page.goto("http://127.0.0.1:%d/photo_index/" % port, wait_until="domcontentloaded")
            page.wait_for_selector("#db-select option", state="attached", timeout=60000)
            page.wait_for_timeout(1500)
            info = page.evaluate("""() => { const s = document.getElementById('db-select'), c = getComputedStyle(s),
                o = getComputedStyle(s.options[0]);
                return {options: s.options.length, size: s.size, colorScheme: c.colorScheme, background: c.backgroundColor,
                        optionBackground: o.backgroundColor, height: c.height, font: c.fontFamily + ' ' + c.fontSize,
                        optionFont: o.fontFamily + ' ' + o.fontSize}; }""")
            page.bring_to_front()
            before = chrome_windows()
            page.locator("#db-select").click()
            page.wait_for_timeout(800)
            # the list is the new window of a list's size, not the browser's own
            opened = {h: size for h, size in chrome_windows().items() if h not in before and size[0] < 1000 and size[1] < 1000}
            if not opened:
                raise RuntimeError("no drop-down list opened")
            handle, (width, height) = max(opened.items(), key=lambda item: item[1][1])
            picture = picture_of(handle, width, height)
            path = os.path.join(out, "select-%s-%s-%s.png" % (page_name, channel or "chromium", scale or "auto"))
            picture.save(path)
            light, rows = strip_rows(picture)
        finally:
            browser.close()
    return info, light, rows, path


def listbox(ports, page_name, out, width=1300):
    """The library picker's own list (web/common/library-picker.js, #909) in a headless browser: it is page DOM, so the
    browser paints it and nothing is borrowed from the desktop. Returns (blank, rows, path): `blank` is how many pixels
    lie between the last option's bottom and the list's bottom edge, which is the list's padding and border (5) and no more."""
    from playwright.sync_api import sync_playwright
    port = {"tagpup": ports[1], "tuner": ports[0]}[page_name]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_context(viewport={"width": width, "height": 800}).new_page()
            page.goto("http://127.0.0.1:%d/photo_index/" % port, wait_until="domcontentloaded")
            page.wait_for_selector(".lib-picker-option", state="attached", timeout=60000)
            page.locator(".lib-picker-button").click()
            page.wait_for_selector(".lib-picker-list:not(.hidden)")
            box = page.evaluate("""() => { const l = document.querySelector('.lib-picker-list'), o = l.querySelectorAll('.lib-picker-option'),
                r = l.getBoundingClientRect(), last = o[o.length - 1].getBoundingClientRect(), b = document.querySelector('.lib-picker-button').getBoundingClientRect();
                return {blank: Math.round(r.bottom - last.bottom), rows: o.length, right: r.right, window: innerWidth,
                        shift: Math.round(b.height), scroll: l.scrollHeight - l.clientHeight}; }""")
            path = os.path.join(out, "picker-%s-%d.png" % (page_name, width))
            page.locator(".lib-picker-list").screenshot(path=path)
        finally:
            browser.close()
    return box, path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--code-root", default=REPO_ROOT)
    parser.add_argument("--channel", default=None, help="the browser: chrome, msedge, or (default) Playwright's own Chromium")
    parser.add_argument("--scale", default=None, help="force the browser's device scale factor: 1,1.25,1.5 (a list: each in turn)")
    parser.add_argument("--listbox", action="store_true", help="measure the page's own library list (#909), headless, not the native popup")
    parser.add_argument("--out", default=tempfile.gettempdir(), help="where the pictures go")
    args = parser.parse_args()
    if not args.run:
        print("measure_select_list.py would, with --run: build a sandbox of three empty libraries serving the code of %s," % args.code_root)
        print("open the library picker of each page in a headed Chromium, photograph the open list, count the rows of the list that are no option's, delete the sandbox.")
        print("Nothing was done.")
        return
    sandbox = tempfile.mkdtemp(prefix="tagpup_select_")
    server = None
    try:
        copy_code(sandbox, code_root=args.code_root, launchers=True)
        os.makedirs(os.path.join(sandbox, "data"))
        enter(sandbox)
        from tagpup.services import libraries as library_actions
        for name in LIBRARIES:
            library_actions.create(os.path.join(sandbox, "data", name))
        ports = (free_port(), free_port())
        server = start_server(sandbox, os.path.join(sandbox, "data", "photo_index.db"), ports[0], ports[1])
        if args.listbox:
            for page_name in PAGES:
                for width in (1300, 360):
                    box, path = listbox(ports, page_name, args.out, width)
                    print("%-7s width %-5d list: %s   picture: %s" % (page_name, width, box, path))
            return
        for scale in (args.scale.split(",") if args.scale else [None]):
            for page_name in PAGES:
                info, light, rows, path = photograph(ports, page_name, args.out, args.channel, scale)
                print("%-7s scale %-5s strip rows in the list: %d of %d   picture: %s\n        %s"
                      % (page_name, scale or "auto", light, rows, path, info))
    finally:
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        print("sandbox deleted: %s" % remove_sandbox(sandbox))


if __name__ == "__main__":
    main()
