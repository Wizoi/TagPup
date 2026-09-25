"""Which library a request is for, and the picker that lists, chooses and makes them.

The first part of a URL names the library -- /kr-track/api/tags -- and the rest is the
request; the pages fetch relative to /<name>/, so a library's URL is where its page
lives. `LibraryFromUrl` takes the name off the path the way the old servers did, and
hands the route a Library (tagpup.web.state.current). Both old servers had their own
copy of this, word for word (docs/ARCHITECTURE.md, "Runtime").

A named library must exist. Requests used to go ahead against data/<name>.db either
way, and the first handler to open it created it, so a typo in the address bar made
an empty library that then sat in the list. Create makes libraries.

`startup` is the library the server was started on, which a request naming none goes
to, and a bare page request is redirected to. It goes with #100: a library is then
reached by its URL alone, and the picker is what a page without one shows.
"""
import logging
import os
import re

from flask import Blueprint, current_app, g, jsonify, request

from tagpup import config as tagpup_config
from tagpup.core import library as libraries
from tagpup.core.library import Library
from tagpup.services import libraries as library_actions
from tagpup.web import responses

logger = logging.getLogger(__name__)

#: A URL's first part that is a page's own file, or nothing, rather than a library.
PAGE_FILES = frozenset({"index.html", "style.css", "app.js", "favicon.ico", ""})
PAGE_SUFFIXES = (".css", ".js", ".html", ".png", ".jpg", ".jpeg", ".ico")

#: The page paths a request without a library is sent to the startup library for.
PAGE_PATHS = ("/", "/index.html", "/style.css", "/app.js")


def names_a_library(first):
    """Does the first part of a URL name a library, rather than a route or a page file
    (tagpup.core.library.ROUTES, which a library may not be called)?"""
    return first not in libraries.ROUTES and first not in PAGE_FILES and not first.endswith(PAGE_SUFFIXES)


class LibraryFromUrl:
    """WSGI middleware: /<name>/<rest> becomes <rest> for the app, with the Library in
    `environ["tagpup.library"]` and /<name> on SCRIPT_NAME, so url_for still builds the
    page's URLs. A name that is no library is answered 404 here."""

    def __init__(self, app, startup=None):
        self.app = app
        self.startup = startup

    def _startup_file(self):
        return os.path.basename(self.startup.path) if self.startup is not None else None

    def resolve(self, name):
        """The Library `name` names, or None. Test mode follows the startup library: a
        server started on a test library serves test libraries."""
        file_name = libraries.for_mode(name + ".db", libraries.is_test_library(self._startup_file() or ""))
        if file_name == self._startup_file():
            # The startup library is the file the server was started on. Looking it up
            # by name in the data folder found the same file only while it lived there.
            library = self.startup
        else:
            library = Library(tagpup_config.library_path(file_name))
            if not os.path.exists(library.path):
                return None
        # A library first reached by its URL was never opened through the index, and
        # without its generations every cache on it goes stale. When that cannot happen
        # just now, the library is served as it is and the next request tries again:
        # failing the request failed every page and file (docs/findings.md, #57).
        try:
            library_actions.bring_up_to_date(library.path)
        except Exception as e:
            logger.warning("Could not bring %s up to date: %s", library.path, e)
        return library

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO") or "/"
        environ["tagpup.library"] = None
        match = re.match(r"^/([^/]+)(/.*)?$", path)
        if match and names_a_library(match.group(1)):
            name = match.group(1)
            library = self.resolve(name)
            if library is None:
                start_response("404 NOT FOUND", [("Content-Type", "text/plain; charset=utf-8")])
                return [("There is no library called %s" % name).encode("utf-8")]
            environ["tagpup.library"] = library
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + "/" + name
            environ["PATH_INFO"] = match.group(2) or "/"
        elif self.startup is not None:
            if path in PAGE_PATHS:
                location = "/%s%s" % (libraries.picker_name(self._startup_file()), path)
                if environ.get("QUERY_STRING"):
                    location += "?" + environ["QUERY_STRING"]
                start_response("302 FOUND", [("Location", location)])
                return [b""]
            environ["tagpup.library"] = self.startup
        return self.app(environ, start_response)


def attach_library():
    """before_request: the request's Library, from the middleware, for tagpup.web.state."""
    g.library = request.environ.get("tagpup.library")


def _test_mode():
    """Was this server started on a test library?"""
    startup = current_app.config.get("STARTUP_LIBRARY")
    return startup is not None and libraries.is_test_library(os.path.basename(startup.path))


# ---- The picker, which both apps serve alike --------------------------------------------

picker = Blueprint("libraries", __name__)


@picker.get("/api/databases")
def list_libraries():
    """The libraries the picker offers. A server started on a test library offers only
    test libraries (tagpup.core.library). Which one was chosen last is the browser's
    to remember (docs/findings.md, #100): the server kept it in config.ini, and wrote
    that file on every choice."""
    data_dir = tagpup_config.data_dir()
    files = os.listdir(data_dir) if os.path.exists(data_dir) else []
    return jsonify({"databases": sorted(libraries.picker_names(files, _test_mode()))})


@picker.post("/api/databases/create")
def create_library():
    """Make the library named, unless it is there already."""
    body = request.get_json(silent=True) or {}
    db_name = body.get("db_name")
    if not db_name:
        return responses.error(400, "Invalid database name")
    db_name = libraries.file_name_for(db_name)
    problem = libraries.problem_with_new_name(db_name)
    if problem:
        return responses.error(400, problem)
    db_path = tagpup_config.library_path(libraries.for_mode(db_name, _test_mode()))
    try:
        if not os.path.exists(db_path):
            library_actions.create(db_path)
    except Exception as e:
        return responses.error(500, "Error creating database: %s" % e)
    return jsonify({"success": True, "db_name": os.path.splitext(db_name)[0]})
