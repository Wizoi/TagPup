"""How a route answers: JSON, a JSON error, an image, or a photo's own bytes.

The old handlers had send_json, send_json_error and send_error each, and the two
apps' photo route differed in two settings; scripts/localserver.py held the one copy
of the photo route for both. The shapes are kept exactly: the pages read them.
"""
import logging

from flask import Response, abort, jsonify

from tagpup.core.result import CHANGED_ON_DISK, DAMAGED_PHOTOS, NOT_IN_LIBRARY, NotFound, Refused
from tagpup.services import photos as photo_actions

logger = logging.getLogger(__name__)


def error(status, message, **extra):
    """The JSON error both pages read: {"success": false, "error": message}, with
    `status`. TagTuner's page reads `data.success` on every reply, refusals included,
    and TagPup's treats the key missing as false; one shape, so neither has to know.
    `extra` goes in beside them (a bulk write's `written`)."""
    return jsonify({"success": False, "error": message, **extra}), status


def refused(result, **extra):
    """A refused Result's JSON error: 409 when the library does not hold the folder of a
    photo it was asked to write (tagpup.services.libraries.refuse_writes), else 400."""
    if result.details.get(CHANGED_ON_DISK):
        return error(409, result.refused, changed_on_disk=True, **extra)
    conflict = result.details.get(NOT_IN_LIBRARY) or result.details.get(DAMAGED_PHOTOS)
    return error(409 if conflict else 400, result.refused, **extra)


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
    photo_path = wanted   # as Flask decoded it, once (docs/findings.md, #32)
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
