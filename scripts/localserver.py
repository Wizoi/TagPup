"""The HTTP socket both servers listen on, who they will answer, and what they log.

Both TagPup and TagTuner had their own copy of a two-line `ThreadedHTTPServer` and a
word-for-word copy of the Host and Origin check. They also had the same two bugs, and
the first one cost two seconds on every click.

**Listen on IPv6 as well as IPv4.** `http://localhost:8080/` is the address in the
browser's bar, and on Windows `localhost` resolves to `::1` before `127.0.0.1`. The
servers bound an IPv4 socket only, so the browser's first connection attempt went to an
address nothing was listening on and sat there until Windows gave up -- measured at
2.05s, on every single request, before a byte of work was done. Loading a grid of
24,000 face crops pays that on every new connection.

A dual-stack socket answers both, so whichever address `localhost` resolves to first is
the right one.

**Parse a bracketed host properly.** The check did `host.split(":")[0]`, which turns
`[::1]:8080` into `[`. So the IPv6 spelling was in the allow-list and could never match
it -- harmless while nothing listened on IPv6, and a locked door the moment something
did.
"""
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.parse
from socketserver import ThreadingTCPServer

import _root  # noqa: F401
from tagpup import config as tagpup_config
from tagpup.core import library as libraries
from tagpup.core.result import NotFound, Refused
from tagpup.logs import REQUESTS
from tagpup.services import photos as photo_actions

logger = logging.getLogger(__name__)
requests_log = logging.getLogger(REQUESTS)

#: A request slower than this is logged with how long it took.
SLOW_REQUEST_SECONDS = 1.0

#: The only hosts these servers will answer to. They hold a private photo library and
#: bind a port on the machine, so the Host check is what keeps a page on the internet
#: from driving them through the browser (DNS rebinding), and the Origin check is what
#: keeps one from reading the answers (CSRF).
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class ThreadedHTTPServer(ThreadingTCPServer):
    """A threaded server that answers on IPv4 and IPv6 alike.

    Falls back to IPv4 where a dual-stack socket cannot be had -- a machine with IPv6
    switched off, or a platform that refuses to clear IPV6_V6ONLY. Losing IPv6 costs
    two seconds a request; failing to start costs everything.
    """

    # SO_REUSEADDR means different things on the two platforms. On POSIX it only
    # skips TIME_WAIT, which a restarting server wants. On Windows it lets a second
    # socket bind a port a live server already holds -- and the two then take each
    # other's connections. Two test runs did exactly that: one run's requests reached
    # the other run's server, where a mocked folder picker was real, and opened it on
    # the desktop of the person using the app. Windows gets SO_EXCLUSIVEADDRUSE instead,
    # so a port in use is refused, loudly.
    allow_reuse_address = os.name != "nt"
    address_family = socket.AF_INET6

    def __init__(self, server_address, handler, bind_and_activate=True):
        host, port = server_address[0], server_address[1]
        try:
            super().__init__((host or "::", port), handler, bind_and_activate)
        except OSError:
            self.address_family = socket.AF_INET
            super().__init__((host or "", port), handler, bind_and_activate)

    def server_bind(self):
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        if self.address_family == socket.AF_INET6:
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except (AttributeError, OSError):
                # Some platforms are v6-only and will not be talked out of it. The
                # server still works; `localhost` just pays the fallback again.
                pass
        super().server_bind()

    def handle_error(self, request, client_address):
        """A request that raised, logged with its traceback.

        socketserver printed it to stderr, which is a console nobody may be watching
        and which is gone when the window closes. A browser dropping a request it no
        longer wants -- thumbnails scrolled past -- is not a failure of this server.
        """
        error = sys.exc_info()[1]
        who = client_address[0] if client_address else "?"
        if who.startswith("::ffff:"):
            who = who[len("::ffff:"):]   # an IPv4 client, as the dual-stack socket spells it
        if isinstance(error, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            requests_log.debug("%s closed the connection before the answer was sent", who)
            return
        requests_log.exception("a request from %s failed", who)


class RequestLog:
    """Mixed into a request handler: every request slower than a second is logged.

    Timed from the moment the request line is parsed, so time spent waiting for a
    request to arrive is never counted as time spent answering it.
    """

    slow_request_seconds = SLOW_REQUEST_SECONDS

    def parse_request(self):
        self._request_started = time.perf_counter()
        return super().parse_request()

    def handle_one_request(self):
        self._request_started = None
        try:
            super().handle_one_request()
        finally:
            started = self._request_started
            if started is not None:
                took = time.perf_counter() - started
                if took >= self.slow_request_seconds:
                    requests_log.warning("slow: %s took %.2fs", self.requestline, took)


def hostname_of(authority):
    """The host out of a `host[:port]` authority, brackets and all handled.

    `[::1]:8080` -> `::1`, `localhost:8080` -> `localhost`. Returns it lowercased,
    since hostnames are not case-sensitive and the allow-list is written in lower case.
    """
    authority = (authority or "").strip()
    if authority.startswith("["):
        end = authority.find("]")
        if end != -1:
            return authority[1:end].lower()
    return authority.split(":")[0].lower()


def is_local_request(handler):
    """Is this request from a page on this machine, served by this server?

    Returns True to proceed. On refusal it sends the error itself and returns False,
    so a caller reads as `if not is_local_request(self): return`.
    """
    if hostname_of(handler.headers.get("Host", "")) not in LOCAL_HOSTS:
        handler.send_error(403, "Forbidden: Invalid Host Header")
        return False

    origin = handler.headers.get("Origin")
    if origin:
        netloc = urllib.parse.urlparse(origin).netloc
        if hostname_of(netloc) not in LOCAL_HOSTS:
            handler.send_error(403, "Forbidden: Cross-Origin Requests Denied")
            return False
    return True


# ---- What both apps serve alike ------------------------------------------------------

#: Start a process without a console window of its own. Windows only.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_FOLDER_DIALOG = (
    "import tkinter as tk; "
    "from tkinter import filedialog; "
    "root = tk.Tk(); "
    "root.withdraw(); "
    "root.lift(); "
    "root.focus_force(); "
    "root.attributes('-topmost', True); "
    "print(filedialog.askdirectory(title='Select Image Folder'))"
)


def ask_for_folder():
    """The folder picked in a folder dialog on this machine's desktop, or "" if it was
    cancelled. Browse, in either app.

    The dialog runs in an interpreter of its own, so no window or Tk state lives in the
    server.
    """
    picked = subprocess.run([sys.executable, "-c", _FOLDER_DIALOG], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, creationflags=CREATE_NO_WINDOW)
    return picked.stdout.strip()


def list_libraries(handler):
    """GET /api/databases: the libraries the picker offers, and the one chosen last. A
    server started on a test library offers only test libraries (tagpup.core.library)."""
    settings = tagpup_config.load()
    data_dir = tagpup_config.data_dir(settings)
    files = os.listdir(data_dir) if os.path.exists(data_dir) else []
    handler.send_json({
        "databases": sorted(libraries.picker_names(files, _test_mode(handler))),
        "selected": libraries.picker_name(tagpup_config.default_db(settings)),
    })


def select_library(handler, db_name):
    """POST /api/databases/select: remember the library chosen, so the apps open it next."""
    if not db_name:
        handler.send_json_error(400, "Invalid database name")
        return
    try:
        tagpup_config.remember_library(libraries.file_name_for(db_name))
        handler.send_json({"success": True})
    except Exception as e:
        handler.send_json_error(500, f"Error saving default database: {e}")


def create_library(handler, db_name, create):
    """POST /api/databases/create: make the library `db_name` with `create(db_path)`, unless
    it is there already, and remember it as the one chosen."""
    if not db_name:
        handler.send_json_error(400, "Invalid database name")
        return
    db_name = libraries.file_name_for(db_name)
    problem = libraries.problem_with_new_name(db_name)
    if problem:
        handler.send_json_error(400, problem)
        return
    file_name = libraries.for_mode(db_name, _test_mode(handler))
    db_path = tagpup_config.library_path(file_name).replace("\\", "/")  # not a path: a database file
    try:
        if not os.path.exists(db_path):
            create(db_path)
        tagpup_config.remember_library(db_name)
        handler.send_json({"success": True, "db_name": os.path.splitext(db_name)[0]})
    except Exception as e:
        handler.send_json_error(500, f"Error creating database: {e}")


def _test_mode(handler):
    """Was this server started on a test library?"""
    return libraries.is_test_library(type(handler).db_path)


def send_image(handler, content, content_type, cache_seconds=None):
    """Answer with an image. `cache_seconds` lets the browser keep it that long."""
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(content)))
    if cache_seconds:
        handler.send_header("Cache-Control", "max-age=%d" % cache_seconds)
    handler.end_headers()
    handler.wfile.write(content)


def serve_photo_file(handler, wanted, size, upright, cache_seconds=None):
    """GET /api/photo-file?path=<photo>[&size=<pixels>]: a photo for an <img>, or a smaller
    copy of it (tagpup.services.photos.page_copy). `wanted` and `size` are the request's
    `path` and `size` as parse_qs gives them: a list, or None. Errors go out as plain
    error pages.

    The two apps differ in `upright` and `cache_seconds`. TagPup turns a copy upright
    and lets the browser keep it for a day: its pages put the file's mtime in the URL,
    so a rotated photo is asked for again. TagTuner draws face boxes over its photos in
    the stored pixels' coordinates (docs/findings.md, #1), so it asks for them as
    stored, and keeps nothing.
    """
    if not wanted:
        handler.send_error(400, "Missing 'path' parameter")
        return
    photo_path = urllib.parse.unquote(wanted[0])   # a second decoding: docs/findings.md, #32
    try:
        max_size = int(size[0]) if size else None
    except ValueError:
        max_size = None   # a size that is not a number gets the photo itself, as it always did
    try:
        content, content_type = photo_actions.page_copy(photo_path, max_size, upright)
    except Refused as refused:
        handler.send_error(400, str(refused))
    except NotFound as missing:
        handler.send_error(404, str(missing))
    except Exception as e:
        logger.error("Error serving %s: %s", photo_path, e)
        handler.send_error(500, "Error serving file: %s" % e)
    else:
        send_image(handler, content, content_type, cache_seconds)
