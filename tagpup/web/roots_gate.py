"""A library whose root this machine does not place is told of at once, in words, and not by the
first photo that fails to open (docs/ARCHITECTURE.md, "Roots and machines").

A library that holds a root says its photos as `@pictures/2024/a.jpg`; this machine's map
(machine_roots.json) says where `pictures` is. A machine whose map is missing, unreadable or lacks
the root cannot name one photo's file: the store refuses every path of such a library
(paths.UnmappedRoot) -- it is never read as an empty one -- and a page that ran into that at its
first photo request showed a traceback's last line. So each /api/ request that needs the photos'
paths asks `tagpup.services.roots.problem` first, and is answered 409 with the message: it names
machine_roots.json, where it is and the line to add, and carries `roots_problem` so the page can show
it as a banner (web/common/roots-banner.js, which api.js feeds with the X-TagPup-Roots-Problem
header). What does not read a photo's path is let through, so the page can load and the Roots dialog
can open: the library picker, the rules, the version, the Activity page, the history, the folder picker (the
Roots dialog's Browse names the place a root is to be) and Roots itself.

One look is an open of the library and a stat of the map; it is kept for a second per library, as a
connection keeps its map (tagpup.store.roots), and forgotten when the map is changed here.
"""
import logging
import threading
import time

from flask import make_response, request

from tagpup.services import roots as roots_service
from tagpup.web import responses, state

logger = logging.getLogger(__name__)

#: The header a refusal carries, which web/common/api.js turns into the page's banner.
HEADER = "X-TagPup-Roots-Problem"

#: What an /api/ path may be asked while the library's roots are not placed.
LET_THROUGH = ("/api/roots", "/api/databases", "/api/server", "/api/rules", "/api/apps", "/api/activity",
               "/api/history", "/api/jobs", "/api/browse-folder")

#: How long a look is trusted, in seconds.
FRESH = 1.0

_seen = {}
_guard = threading.Lock()


def problem_of(library):
    """The sentence for why `library`'s photos cannot be read on this machine, or None
    (tagpup.services.roots.problem), asked at most once a second."""
    now = time.monotonic()
    with _guard:
        found = _seen.get(library.key)
    if found is not None and now - found[0] < FRESH:
        return found[1]
    try:
        said = roots_service.problem(library)
    except Exception as why:   # a library that cannot be opened is the route's to say, not this gate's
        logger.warning("Could not ask whether %s's roots are placed: %s", library.name, why)
        said = None
    with _guard:
        _seen[library.key] = (now, said)
    return said


def forget(library):
    """The map was changed: look again at the next request."""
    with _guard:
        _seen.pop(library.key, None)


def guard():
    """before_request: 409 with the problem's message for an /api/ request on a library whose
    roots this machine does not place, unless it is one that reads no photo's path."""
    library = state.current()
    if library is None or not request.path.startswith("/api/") or request.path.startswith(LET_THROUGH):
        return None
    said = problem_of(library)
    if said is None:
        return None
    reply = make_response(responses.error(409, said, roots_problem=True))
    reply.headers[HEADER] = "1"
    return reply
