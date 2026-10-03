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

from flask import request
from werkzeug.datastructures import MultiDict

from tagpup.services import roots as roots_service
from tagpup.web import responses, state

#: The parameters that hold a path or a folder, or a list of them, whichever route reads them.
KEYS = ("path", "paths", "folder", "folder_path", "folder_paths", "photo_path", "photo_paths")

#: Routes that add a folder, which a place that is gone is refused for (by name, not as "invalid").
ADDING = ("/api/folder/add", "/api/folder/index-start")


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
    if library is None or not request.path.startswith("/api/") or request.path.startswith("/api/roots"):
        return None
    canonical = roots_service.canonicaliser(library)
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
    body = request.get_json(silent=True) if request.is_json else None
    if isinstance(body, dict) and any(key in body for key in KEYS):
        body = {key: (_each(value, make) if key in KEYS else value) for key, value in body.items()}
        request._cached_json = (body, body)
    if moved and request.path in ADDING:
        for original, found in moved:
            if not os.path.isdir(found):
                return responses.error(400, roots_service.old_place_sentence(library, original, found))
    return None
