"""How a route answers: JSON, a JSON error, an image, or a photo's own bytes.

The old handlers had send_json, send_json_error and send_error each, and the two
apps' photo route differed in two settings; scripts/localserver.py held the one copy
of the photo route for both. The shapes are kept exactly: the pages read them.
"""
import logging
import urllib.parse

from flask import Response, abort, jsonify

from tagpup.core.result import NotFound, Refused
from tagpup.services import photos as photo_actions

logger = logging.getLogger(__name__)


def error(status, message):
    """The JSON error the pages read: {"error": message}, with `status`."""
    return jsonify({"error": message}), status


def image(content, content_type, cache_seconds=None):
    """An image's bytes. `cache_seconds` lets the browser keep it that long."""
    response = Response(content, mimetype=content_type)
    if cache_seconds:
        response.headers["Cache-Control"] = "max-age=%d" % cache_seconds
    return response


def photo(wanted, size, upright, cache_seconds=None):
    """GET /api/photo-file?path=<photo>[&size=<pixels>]: a photo for an <img>, or a
    smaller copy of it (tagpup.services.photos.page_copy). `wanted` and `size` are the
    request's `path` and `size`, or None. Errors go out as plain error pages.

    The two apps differ in `upright` and `cache_seconds`. TagPup turns a copy upright
    and lets the browser keep it for a day: its pages put the file's mtime in the URL,
    so a rotated photo is asked for again. TagTuner draws face boxes over its photos in
    the stored pixels' coordinates (docs/findings.md, #1), so it asks for them as
    stored, and keeps nothing.
    """
    if not wanted:
        abort(400, description="Missing 'path' parameter")
    photo_path = urllib.parse.unquote(wanted)   # a second decoding: docs/findings.md, #32
    try:
        max_size = int(size) if size else None
    except ValueError:
        max_size = None   # a size that is not a number gets the photo itself, as it always did
    try:
        content, content_type = photo_actions.page_copy(photo_path, max_size, upright)
    except Refused as refused:
        abort(400, description=str(refused))
    except NotFound as missing:
        abort(404, description=str(missing))
    except Exception as e:
        logger.error("Error serving %s: %s", photo_path, e)
        abort(500, description="Error serving file: %s" % e)
    return image(content, content_type, cache_seconds)
