"""Where a caller's paths are resolved through the first place of a root: once, at the ingress
(docs/ARCHITECTURE.md, "Roots and machines"; findings #465, #480-#482).

After a move the map keeps the previous place recognised, so a page that still holds a path spelled by it,
a bookmark, or a folder typed under it, names the same row as the first place's. Nothing that serves a request
may take such a path as written: a cache keyed by it (TagPup's folder scans) would be a second entry for
one folder, still holding the old tags after a write at the first place; photo-file would serve the old copy
(after a rotate, "nothing happened"); open and show-in-explorer would open the old file; an isdir pre-check
would refuse a folder that exists at the first place once the old place is gone.

So this is the one place: a before_request, after the library is named and the gate has passed, that turns each
path-bearing parameter of an /api/ request -- in the query or the JSON body, whatever the route -- into the first
place's spelling (`tagpup.services.roots.canonicaliser`), before any route, cache or pre-check reads it. The
routes then read what they always read. A path under no root, and a library with no roots, pass untouched.
The services keep their own decorator (`canonical_args`) for what is not a request: the CLI, the MCP tools, jobs.

A folder to be ADDED (`/api/folder/add`, `/api/folder/index-start`) that is spelled by a previous place and does
not exist at the first place is refused here with a sentence naming both places and the root: not "invalid" with
a path the owner never typed.

Not canonicalised: `/api/roots` (its `location` is a place being CHOSEN, and `from` a place the page saw).
"""
import os
import threading
import time

from flask import g, request
from werkzeug.datastructures import MultiDict

from tagpup import config as tagpup_config
from tagpup.services import roots as roots_service
from tagpup.web import responses, state

#: The parameters that hold a path or a folder, or a list of them, whichever route reads them.
KEYS = ("path", "paths", "folder", "folder_path", "folder_paths", "photo_path", "photo_paths")

#: Routes that open or add a folder, which a place that is gone is refused for by name, not as an invalid path
#: the owner never typed.
ADDING = ("/api/folder/add", "/api/folder/index-start", "/api/folder/scan", "/api/folder/membership",
          "/api/folder/subfolders")

#: Not rewritten: roots, whose `location` is a place being chosen, and the folder box's autocomplete, whose
#: `path` is half-typed text (the rewrite would drop its trailing separator).
LEFT_ALONE = ("/api/roots", "/api/autocomplete-folder")

#: The header a response carries when a request's paths were rewritten: the page's own are an old place's, and
#: web/common/api.js tells the owner and reloads once.
MOVED_HEADER = "X-TagPup-Roots-Moved"

#: How long one look at a library's roots serves: {library key: (map stamp, when, canonicaliser)}.
FRESH = 1.0
_looks = {}
_guard = threading.Lock()


def _stamp():
    try:
        found = os.stat(tagpup_config.machine_roots_path())
    except OSError:
        return None
    return (found.st_mtime_ns, found.st_size)


def _canonicaliser(library):
    """roots_service.canonicaliser, looked at once per library per second at most (and again the moment the map
    file changes), so a request carrying a path costs no connection of its own."""
    stamp, now = _stamp(), time.monotonic()
    with _guard:
        held = _looks.get(library.key)
    if held is not None and held[0] == stamp and now - held[1] < FRESH:
        return held[2]
    made = roots_service.canonicaliser(library)
    with _guard:
        _looks[library.key] = (stamp, now, made)
    return made


def forget(library):
    """The library's roots or map changed here: look again at the next request."""
    with _guard:
        _looks.pop(library.key, None)


def _carries_a_path(body):
    return any(key in request.args for key in KEYS) or (isinstance(body, dict) and any(key in body for key in KEYS))


def mark(response):
    """after_request: say that this request's paths were an old place's (MOVED_HEADER: the root's name)."""
    named = g.get("roots_moved")
    if named:
        response.headers[MOVED_HEADER] = named
    return response


def _each(value, make):
    if isinstance(value, str):
        return make(value)
    if isinstance(value, list):
        return [make(each) if isinstance(each, str) else each for each in value]
    return value


def guard():
    """before_request: resolve the request's paths through the first place, or refuse a folder to add that
    is gone from it."""
    library = state.current()
    if library is None or not request.path.startswith("/api/") or request.path.startswith(LEFT_ALONE):
        return None
    body = request.get_json(silent=True) if request.is_json else None
    if not _carries_a_path(body):
        return None
    canonical = _canonicaliser(library)
    if canonical is None:
        return None
    moved = []

    def make(value):
        found = canonical(value)
        if found != value:
            moved.append((value, found))
        return found

    if any(key in request.args for key in KEYS):
        resolved = MultiDict()
        for key, value in request.args.items(multi=True):
            resolved.add(key, make(value) if key in KEYS else value)
        request.__dict__["args"] = resolved          # request.args is a cached property
    if isinstance(body, dict) and any(key in body for key in KEYS):
        body = {key: (_each(value, make) if key in KEYS else value) for key, value in body.items()}
        request._cached_json = (body, body)
    if moved:
        found = canonical.roots.locate(moved[0][0])
        g.roots_moved = found[0] if found else "?"
    if moved and request.path in ADDING:
        for original, found in moved:
            if not os.path.isdir(found):
                return responses.error(400, roots_service.old_place_sentence(library, original, found))
    return None
