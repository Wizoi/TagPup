"""One Flask app for each page, both served from one process by Waitress.

`create_app("tagpup")` and `create_app("tuner")` are two apps rather than one because
the two pages ask for the same paths -- /api/people, /api/photo-file, /api/databases
and five more -- and mean different things by them; one app would have to ask which
port a request came in on at every such route. Instead `by_port` asks once, and hands
the request to the app for its port -- the ports they always had (tagpup_web.PORTS)
-- *(decided 2026-09-24; docs/ARCHITECTURE.md, "Runtime")*.

What every app does alike is here: refuse a request from anywhere but this machine
(tagpup.web.security), name the library the URL names (tagpup.web.libraries), serve
its page's files, and log every request slower than a second and every one that
failed, with its traceback, to the requests log (tagpup.logs). Each app's routes are
its own blueprint; what both serve alike -- the picker, the tag tree, the rules of what
may be set, the library's settings, its history of changes, when its recurring jobs ran,
where each app is, which version answers, and the Activity page over every library
(tagpup.web.activity_routes) -- is a blueprint each registers. Every
request passes the process's gate (tagpup.web.lifecycle), which counts it while in
flight and turns it away while the always-on process moves onto a new version.
"""
import errno
import gzip
import logging
import os
import socket
import time
import urllib.parse

import waitress
from flask import Blueprint, Flask, Response, abort, current_app, g, jsonify, request

from tagpup import config as tagpup_config
from tagpup.logs import REQUESTS
from tagpup.web import (activity_routes, face_routes, history_routes, photo_face_routes, libraries, lifecycle as lifecycles,
                        name_faces_routes, name_review_routes, roots_gate, roots_ingress, rules_routes, security, settings_routes, sync_routes, tagpup_routes,
                        taxonomy_routes, tuner_routes)

logger = logging.getLogger(__name__)
requests_log = logging.getLogger(REQUESTS)

#: Each page's folder, beside the package in the code folder (tagpup.config knows
#: where that is), and the modules both pages share, served to each at common/.
PAGES = {"tagpup": os.path.join("web", "tagpup"), "tuner": os.path.join("web", "tuner")}
COMMON = "common"
ROUTES = {"tagpup": tagpup_routes.routes, "tuner": tuner_routes.routes}

#: A page's own files and what they are sent as. They are never cached: a page that
#: kept an old script after an install argued with its server for a day.
PAGE_FILES = {
    "index.html": "text/html; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}
SCRIPT_TYPE = "application/javascript; charset=utf-8"
STYLE_TYPE = PAGE_FILES["style.css"]

#: What a module's name may be: a page's modules and the shared ones are all named so,
#: and nothing else in their folders -- nor anything above them -- can be asked for.
#: The router's pattern, so the two cannot drift (#165).
MODULE_NAME = libraries.MODULE_NAME
NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"}

#: A request that takes this long is logged, with its time.
SLOW_REQUEST_SECONDS = 1.0

#: Threads answering requests. The old servers gave every request a thread of its own;
#: a page loading a folder asks for dozens of thumbnails at once.
THREADS = 16


def create_app(kind, startup=None, pages=None, runtime=None, ports=None, lifecycle=None):
    """The Flask app for `kind` ("tagpup" or "tuner"): its page from `pages` (the
    page's folder, by default the one beside the package), `startup`, the Library a
    request naming none is served, and `runtime`, the process's models
    (tagpup.runtime.Runtime), which the routes take from app.config["RUNTIME"]. An app
    made without one answers everything but Suggest. `ports` is {kind: port} for the
    apps the process serves, which the launcher knows (tagpup_web.PORTS, or the ports
    it was told): /api/apps tells a page where the other app is, and an app made
    without them knows of none. `lifecycle` is the process's gate and version
    (tagpup.web.lifecycle.Lifecycle), one for both apps; an app made without one has
    its own."""
    if kind not in PAGES:
        raise ValueError("no such app: %r" % (kind,))
    app = Flask("tagpup.web." + kind, static_folder=None)
    app.config["APP_KIND"] = kind
    app.config["STARTUP_LIBRARY"] = startup
    app.config["RUNTIME"] = runtime
    if runtime is not None and getattr(runtime, "idle", None) is not None:
        # What the pages keep only while used, let go on the runtime's idle timer.
        tagpup_routes.idle_caches(runtime.idle)
        tuner_routes.idle_caches(runtime.idle)
    app.config["PORTS"] = dict(ports or {})
    app.config["LIFECYCLE"] = lifecycle = lifecycle or lifecycles.Lifecycle(version=tagpup_config.code_version())
    app.config["PAGES"] = pages or os.path.join(tagpup_config.CODE_ROOT, PAGES[kind])
    app.config["COMMON"] = os.path.join(os.path.dirname(app.config["PAGES"]), COMMON)
    app.json.sort_keys = False

    app.before_request(security.guard)
    app.before_request(libraries.attach_library)
    app.before_request(name_faces_routes.refuse_writes_while_naming)
    app.before_request(roots_gate.guard)
    app.before_request(roots_ingress.guard)
    app.after_request(roots_ingress.mark)
    app.before_request(_start_clock)
    app.after_request(_never_cache_json)
    app.after_request(_gzip_big_json)
    app.after_request(_log_slow)
    app.teardown_request(_log_failure)
    app.register_blueprint(libraries.picker)
    app.register_blueprint(rules_routes.routes)
    app.register_blueprint(taxonomy_routes.routes)
    app.register_blueprint(settings_routes.routes)
    app.register_blueprint(history_routes.routes)
    app.register_blueprint(sync_routes.routes)
    app.register_blueprint(lifecycles.routes)
    app.register_blueprint(activity_routes.routes)
    app.register_blueprint(face_routes.routes)
    app.register_blueprint(name_faces_routes.routes)
    app.register_blueprint(name_review_routes.routes)
    app.register_blueprint(apps)
    app.register_blueprint(ROUTES[kind])
    if kind == "tagpup":
        app.register_blueprint(photo_face_routes.routes)
    _page_routes(app)
    # The gate outside the library's middleware: a request turned away while the server
    # drains opens no library.
    app.wsgi_app = lifecycle.wrap(libraries.LibraryFromUrl(app.wsgi_app, startup))
    return app


# ---- Where each app is, which both serve alike ------------------------------------------

apps = Blueprint("apps", __name__)


@apps.get("/api/apps")
def app_urls():
    """Each app's page for the library this request names, on this machine: the gear's
    link to the other app (web/common/gear.js). A page never spells a port; the ports
    are the process's (create_app's `ports`), and an app told none names no page."""
    host = urllib.parse.urlsplit(request.host_url).hostname or "localhost"
    if ":" in host:
        host = "[%s]" % host   # an IPv6 address, as a URL spells it
    library = urllib.parse.quote(request.script_root)   # "/<name>", or "" for none
    urls = {kind: "%s://%s:%d%s/" % (request.scheme, host, port, library)
            for kind, port in current_app.config["PORTS"].items()}
    return jsonify({"this": current_app.config["APP_KIND"], "apps": urls})


def _page_routes(app):
    """The page's index.html and style.css, its modules (/<name>.js) and the shared
    ones (/common/<name>.js), and the shared modules' stylesheets (/common/<name>.css:
    the gear's and the tag editor's). The page asks for them relative to itself, so
    each is asked for under the library's URL like every other request."""
    def send(folder, name, content_type):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            abort(404, description="File %s not found" % name)
        with open(path, "rb") as handle:
            content = handle.read()
        return Response(content, content_type=content_type, headers=NO_CACHE)

    def module(folder, name):
        if not MODULE_NAME.match(name):
            abort(404, description="File %s.js not found" % name)
        return send(folder, name + ".js", SCRIPT_TYPE)

    def stylesheet(folder, name):
        if not MODULE_NAME.match(name):
            abort(404, description="File %s.css not found" % name)
        return send(folder, name + ".css", STYLE_TYPE)

    page = app.config["PAGES"]
    app.add_url_rule("/", "index", lambda: send(page, "index.html", PAGE_FILES["index.html"]))
    app.add_url_rule("/index.html", "index_html", lambda: send(page, "index.html", PAGE_FILES["index.html"]))
    app.add_url_rule("/style.css", "style", lambda: send(page, "style.css", PAGE_FILES["style.css"]))
    app.add_url_rule("/<name>.js", "script", lambda name: module(page, name))
    app.add_url_rule("/%s/<name>.js" % COMMON, "common_script", lambda name: module(app.config["COMMON"], name))
    app.add_url_rule("/%s/<name>.css" % COMMON, "common_style", lambda name: stylesheet(app.config["COMMON"], name))


def _start_clock():
    g.request_started = time.perf_counter()


def _never_cache_json(response):
    """A JSON reply is never kept by the browser, as both old servers sent them: a page
    that kept an old answer argued with its server."""
    if response.mimetype == "application/json":
        response.headers.update(NO_CACHE)
    return response


#: A JSON reply of at least this many bytes is gzipped for a client that accepts it: the navigator's folders (792 KB for
#: 2,746 folders) and a view's order (236 KB for 41,000 ids) are long lists of repeating text, which shrink to a fifth or less.
GZIP_FROM = 100_000


def _accepts_gzip(header):
    """Does an Accept-Encoding header accept gzip? `gzip;q=0` and `identity;q=1, gzip;q=0` say no (RFC 9110)."""
    for item in header.lower().split(","):
        name, _, params = item.partition(";")
        if name.strip() not in ("gzip", "x-gzip"):
            continue
        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.partition("=")
            if key.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        return quality > 0
    return False


def _gzip_big_json(response):
    """Compress a long JSON reply when the client sent Accept-Encoding: gzip (findings #572). The page's fetch undoes it
    unseen. A reply that is streamed, already encoded, or not a plain 200 is left as it is."""
    if (response.status_code != 200 or response.mimetype != "application/json" or response.direct_passthrough
            or "Content-Encoding" in response.headers or not _accepts_gzip(request.headers.get("Accept-Encoding", ""))):
        return response
    data = response.get_data()
    if len(data) < GZIP_FROM:
        return response
    response.set_data(gzip.compress(data, compresslevel=5))
    response.headers["Content-Encoding"] = "gzip"
    response.headers.add("Vary", "Accept-Encoding")
    return response


def _as_sent():
    """The request as the browser sent it, library and all: the middleware has taken
    the library off the path by now."""
    return "%s %s" % (request.method, request.script_root + request.full_path.rstrip("?"))


def _log_slow(response):
    started = getattr(g, "request_started", None)
    if started is not None:
        took = time.perf_counter() - started
        if took >= SLOW_REQUEST_SECONDS:
            requests_log.warning("slow: %s took %.2fs", _as_sent(), took)
    return response


def _log_failure(error):
    """A request that raised, logged with its traceback: the console it went to before
    is gone when the window closes."""
    if error is not None:
        requests_log.error("a request failed: %s", _as_sent(), exc_info=error)


# ---- One process, both ports ---------------------------------------------------------

def by_port(apps):
    """A WSGI app answering each request with the app for the port it arrived on:
    `apps` maps port to app."""
    def dispatch(environ, start_response):
        app = apps.get(int(environ.get("SERVER_PORT") or 0))
        if app is None:
            start_response("404 NOT FOUND", [("Content-Type", "text/plain; charset=utf-8")])
            return [b"No app answers on this port"]
        return app(environ, start_response)
    return dispatch


#: Where the server listens. LOCAL, this PC only, is the default *(owner, 2026-09-26)*: the
#: apps hold a private photo library and have no logins. LAN, every interface, is for
#: phase 10, when other people on the home network get logins of their own; nothing
#: chooses it but `tagpup_web.py --listen lan`.
LOCAL, LAN = "local", "lan"
LISTEN = (LOCAL, LAN)

#: The errors that mean this machine has no IPv6 loopback, not that the port is taken:
#: the server then listens on 127.0.0.1 alone.
_NO_IPV6 = {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT, getattr(errno, "WSAEADDRNOTAVAIL", -1),
            getattr(errno, "WSAEAFNOSUPPORT", -1), 10049, 10047}


def _listening(family, host, port, dual_stack=False):
    """One listening socket on (host, port), bound exclusively where Windows offers it."""
    exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if exclusive is not None:
            sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            try:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0 if dual_stack else 1)
            except (AttributeError, OSError):
                pass   # a v6-only platform; localhost pays the fallback
        sock.bind((host, port))
        sock.listen(128)
        return sock
    except BaseException:
        sock.close()
        raise


def _no_ipv6(error):
    return error.errno in _NO_IPV6 or getattr(error, "winerror", None) in _NO_IPV6


def bind(port, listen=LOCAL):
    """The listening sockets for `port` -- a list -- that fail loudly if the port is in use.

    Waitress sets SO_REUSEADDR on a socket it binds itself. On POSIX that only skips
    TIME_WAIT, which a restarting server wants; on Windows it lets a second socket bind
    a port a live server already holds, and the two then take each other's connections
    -- two test runs did exactly that, and one run's requests reached the other's
    server (scripts/localserver.py). So the sockets are bound here, with
    SO_EXCLUSIVEADDRUSE where there is one, and handed to Waitress bound.

    LOCAL (the default): 127.0.0.1 and ::1, one socket each, so `localhost` answers at
    once whichever address the browser tries first -- losing IPv6 cost two seconds a
    request from a browser that tries it first (tests/test_local_server_socket.py) --
    and nothing off this machine can connect at all. A machine without an IPv6 loopback
    gets 127.0.0.1 alone; a port another holds on either address is refused.

    LAN: every interface, dual-stack where it can be had (phase 10; off by default).
    """
    if listen == LAN:
        try:
            return [_listening(socket.AF_INET6, "::", port, dual_stack=True)]
        except OSError:
            return [_listening(socket.AF_INET, "", port)]
    if listen != LOCAL:
        raise ValueError("listen is %s, not %r" % (" or ".join(LISTEN), listen))
    for _attempt in range(5 if port == 0 else 1):
        v4 = _listening(socket.AF_INET, "127.0.0.1", port)
        chosen = v4.getsockname()[1]
        if not socket.has_ipv6:
            return [v4]
        try:
            return [v4, _listening(socket.AF_INET6, "::1", chosen)]
        except OSError as e:
            if _no_ipv6(e):
                logger.warning("No IPv6 loopback on this machine (%s): listening on 127.0.0.1 only.", e)
                return [v4]
            v4.close()
            if port != 0:
                raise
            # A free port chosen for IPv4 that another holds on ::1: choose again.
    raise OSError("could not find a port free on both 127.0.0.1 and ::1")


def bind_all(ports, listen=LOCAL):
    """The listening sockets for every port of `ports`, in order: all of them, or none --
    a port another holds closes the ones already bound, and raises. What a launcher binds
    before it does anything else, so two launches at once do not both start a server
    (tagpup.launcher)."""
    sockets = []
    try:
        for port in ports:
            sockets += bind(port, listen)
    except BaseException:
        for sock in sockets:
            sock.close()
        raise
    return sockets


def serve(apps, ready=None, threads=THREADS, listen=LOCAL, sockets=None):
    """Serve `apps` (port -> app) from this process until stopped, on this machine's
    loopback addresses unless `listen` is LAN (bind), or on `sockets` already bound
    (bind_all). `ready()` is called once the ports are bound and before the first request
    is answered: what opens the browser, since a page opened before that has nothing to
    reach."""
    if sockets is None:
        sockets = bind_all(list(apps), listen)
    logger.info("Serving %s, %s", ", ".join("%s on port %d" % (app.config["APP_KIND"], port)
                                            for port, app in apps.items()),
                "on this PC only" if listen == LOCAL else "on every interface")
    if ready is not None:
        ready()
    waitress.serve(by_port(apps), sockets=sockets, threads=threads, ident="TagPup", _quiet=True)
