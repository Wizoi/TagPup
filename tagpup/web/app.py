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
may be set, the library's settings, its history of changes, and where each app is -- is
a blueprint each registers.
"""
import logging
import os
import re
import socket
import time
import urllib.parse

import waitress
from flask import Blueprint, Flask, Response, abort, current_app, g, jsonify, request

from tagpup import config as tagpup_config
from tagpup.logs import REQUESTS
from tagpup.web import (history_routes, libraries, rules_routes, security, settings_routes, tagpup_routes,
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
MODULE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"}

#: A request that takes this long is logged, with its time.
SLOW_REQUEST_SECONDS = 1.0

#: Threads answering requests. The old servers gave every request a thread of its own;
#: a page loading a folder asks for dozens of thumbnails at once.
THREADS = 16


def create_app(kind, startup=None, pages=None, runtime=None, ports=None):
    """The Flask app for `kind` ("tagpup" or "tuner"): its page from `pages` (the
    page's folder, by default the one beside the package), `startup`, the Library a
    request naming none is served, and `runtime`, the process's models
    (tagpup.runtime.Runtime), which the routes take from app.config["RUNTIME"]. An app
    made without one answers everything but Suggest. `ports` is {kind: port} for the
    apps the process serves, which the launcher knows (tagpup_web.PORTS, or the ports
    it was told): /api/apps tells a page where the other app is, and an app made
    without them knows of none."""
    if kind not in PAGES:
        raise ValueError("no such app: %r" % (kind,))
    app = Flask("tagpup.web." + kind, static_folder=None)
    app.config["APP_KIND"] = kind
    app.config["STARTUP_LIBRARY"] = startup
    app.config["RUNTIME"] = runtime
    app.config["PORTS"] = dict(ports or {})
    app.config["PAGES"] = pages or os.path.join(tagpup_config.CODE_ROOT, PAGES[kind])
    app.config["COMMON"] = os.path.join(os.path.dirname(app.config["PAGES"]), COMMON)
    app.json.sort_keys = False

    app.before_request(security.guard)
    app.before_request(libraries.attach_library)
    app.before_request(_start_clock)
    app.after_request(_never_cache_json)
    app.after_request(_log_slow)
    app.teardown_request(_log_failure)
    app.register_blueprint(libraries.picker)
    app.register_blueprint(rules_routes.routes)
    app.register_blueprint(taxonomy_routes.routes)
    app.register_blueprint(settings_routes.routes)
    app.register_blueprint(history_routes.routes)
    app.register_blueprint(apps)
    app.register_blueprint(ROUTES[kind])
    _page_routes(app)
    app.wsgi_app = libraries.LibraryFromUrl(app.wsgi_app, startup)
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
