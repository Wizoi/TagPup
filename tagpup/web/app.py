"""One Flask app for each page, both served from one process by Waitress.

`create_app("tagpup")` and `create_app("tuner")` are two apps rather than one because
the two pages ask for the same paths -- /api/people, /api/photo-file, /api/databases
and five more -- and mean different things by them; one app would have to ask which
port a request came in on at every such route. Instead `by_port` asks once, and hands
the request to the app for its port: TagPup's 8090, TagTuner's 8080, as they always
were *(decided 2026-09-24; docs/ARCHITECTURE.md, "Runtime")*.

What every app does alike is here: refuse a request from anywhere but this machine
(tagpup.web.security), name the library the URL names (tagpup.web.libraries), serve
its page's files, and log every request slower than a second and every one that
failed, with its traceback, to the requests log (tagpup.logs). Each app's routes are
its own blueprint.
"""
import logging
import os
import socket
import time

import waitress
from flask import Flask, Response, abort, g, request

from tagpup import config as tagpup_config
from tagpup.logs import REQUESTS
from tagpup.web import libraries, security, tagpup_routes, tuner_routes

logger = logging.getLogger(__name__)
requests_log = logging.getLogger(REQUESTS)

#: Each page's folder, beside the package in the code folder (tagpup.config knows
#: where that is). The folders keep their names until phase 6 moves them under web/.
PAGES = {"tagpup": "gui_tagpup", "tuner": "gui"}
ROUTES = {"tagpup": tagpup_routes.routes, "tuner": tuner_routes.routes}

#: A page's own files and what they are sent as. They are never cached: a page that
#: kept an old app.js after an install argued with its server for a day.
PAGE_FILES = {
    "index.html": "text/html; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
}
NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"}

#: A request that takes this long is logged, with its time.
SLOW_REQUEST_SECONDS = 1.0

#: Threads answering requests. The old servers gave every request a thread of its own;
#: a page loading a folder asks for dozens of thumbnails at once.
THREADS = 16


def create_app(kind, startup=None, pages=None):
    """The Flask app for `kind` ("tagpup" or "tuner"): its page from `pages` (the
    page's folder, by default the one beside the package) and, until #100, `startup`,
    the Library a request naming none is served."""
    if kind not in PAGES:
        raise ValueError("no such app: %r" % (kind,))
    app = Flask("tagpup.web." + kind, static_folder=None)
    app.config["APP_KIND"] = kind
    app.config["STARTUP_LIBRARY"] = startup
    app.config["PAGES"] = pages or os.path.join(tagpup_config.CODE_ROOT, PAGES[kind])
    app.json.sort_keys = False

    app.before_request(security.guard)
    app.before_request(libraries.attach_library)
    app.before_request(_start_clock)
    app.after_request(_log_slow)
    app.teardown_request(_log_failure)
    app.register_blueprint(libraries.picker)
    app.register_blueprint(ROUTES[kind])
    _page_routes(app)
    app.wsgi_app = libraries.LibraryFromUrl(app.wsgi_app, startup)
    return app


def _page_routes(app):
    def send(name):
        path = os.path.join(app.config["PAGES"], name)
        if not os.path.exists(path):
            abort(404, description="File %s not found" % name)
        with open(path, "rb") as handle:
            content = handle.read()
        return Response(content, content_type=PAGE_FILES[name], headers=NO_CACHE)

    app.add_url_rule("/", "index", lambda: send("index.html"))
    app.add_url_rule("/index.html", "index_html", lambda: send("index.html"))
    app.add_url_rule("/style.css", "style", lambda: send("style.css"))
    app.add_url_rule("/app.js", "script", lambda: send("app.js"))


def _start_clock():
    g.request_started = time.perf_counter()


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


def bind(port):
    """A listening socket on `port`, IPv4 and IPv6 alike, that fails loudly if the port
    is in use.

    Waitress sets SO_REUSEADDR on a socket it binds itself. On POSIX that only skips
    TIME_WAIT, which a restarting server wants; on Windows it lets a second socket bind
    a port a live server already holds, and the two then take each other's connections
    -- two test runs did exactly that, and one run's requests reached the other's
    server (scripts/localserver.py). So the sockets are bound here, with
    SO_EXCLUSIVEADDRUSE where there is one, and handed to Waitress bound.

    Dual-stack where it can be had: losing IPv6 costs two seconds a request from a
    browser that tries it first; failing to start costs everything.
    """
    exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    for family, host in ((socket.AF_INET6, "::"), (socket.AF_INET, "")):
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            if exclusive is not None:
                sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                try:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                except (AttributeError, OSError):
                    pass   # a v6-only platform; localhost pays the fallback
            sock.bind((host, port))
            sock.listen(128)
            return sock
        except OSError:
            sock.close()
            if family == socket.AF_INET:
                raise
    raise OSError("could not bind port %d" % port)


def serve(apps, ready=None, threads=THREADS):
    """Serve `apps` (port -> app) from this process until stopped. `ready()` is called
    once the ports are bound and before the first request is answered: what opens the
    browser, since a page opened before that has nothing to reach."""
    sockets = [bind(port) for port in apps]
    logger.info("Serving %s", ", ".join("%s on port %d" % (app.config["APP_KIND"], port) for port, app in apps.items()))
    if ready is not None:
        ready()
    waitress.serve(by_port(apps), sockets=sockets, threads=threads, ident="TagPup", _quiet=True)
