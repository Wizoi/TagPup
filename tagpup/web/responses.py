"""How a route answers: JSON, a JSON error, an image, or a photo's own bytes.

The old handlers had send_json, send_json_error and send_error each, and the two
apps' photo route differed in two settings; scripts/localserver.py held the one copy
of the photo route for both. The shapes are kept exactly: the pages read them.
"""
import logging

from flask import Response, abort, jsonify

from tagpup.core.result import CHANGED_ON_DISK, SHARE_AWAY, NotFound, Refused
from tagpup.services import photos as photo_actions

logger = logging.getLogger(__name__)


def error(status, message, **extra):
    """The JSON error both pages read: {"success": false, "error": message}, with
    `status`. TagTuner's page reads `data.success` on every reply, refusals included,
    and TagPup's treats the key missing as false; one shape, so neither has to know.
    `extra` goes in beside them (a bulk write's `written`)."""
    return jsonify({"success": False, "error": message, **extra}), status


#: How a refused Result is answered, by its kind (Result.refused_kind): (status, what the reply says beside the error).
#: 409 when the file is no longer the one the page built its record from, `changed_on_disk` telling the page to read it
#: again; 503 when a folder's network share did not answer (try again later); any other, REFUSED, 400.
STATUS = {CHANGED_ON_DISK: (409, {"changed_on_disk": True}), SHARE_AWAY: (503, {})}

#: Seconds a 503 tells the page to wait before it asks again (Retry-After): a file that cannot be reached just now
#: (unavailable), and a server or a library being brought up to date (tagpup.web.lifecycle, tagpup.web.libraries).
RETRY_AFTER_SECONDS = 2


def unavailable(why):
    """The reply for a read that cannot be answered just now (tagpup.core.result.Unavailable: the file's share is away,
    or the file refuses to be read): its sentence, answered as a share away is (STATUS, 503), with Retry-After."""
    reply = error(STATUS[SHARE_AWAY][0], str(why))
    reply[0].headers["Retry-After"] = str(RETRY_AFTER_SECONDS)
    return reply


def refused(result, **extra):
    """A refused Result's JSON error, as its kind is answered (STATUS)."""
    status, said = STATUS.get(result.refused_kind, (400, {}))
    return error(status, result.refused, **said, **extra)


def folder_refused(folder):
    """None when `folder`, as a request sent it, is a folder now; else the reply that refuses it: 503 with the share's
    sentence when its network share did not answer, 400 when it is not there (tagpup.services.photos.folder_refusal).
    The one check of the routes that act on a folder."""
    refusal = photo_actions.folder_refusal(folder)
    return refused(refusal) if refusal else None


def image(content, content_type, cache_seconds=None):
    """An image's bytes. `cache_seconds` lets the browser keep it that long."""
    response = Response(content, mimetype=content_type)
    if cache_seconds:
        response.headers["Cache-Control"] = "max-age=%d" % cache_seconds
    return response


def ranged(asked, size, content_type, send, span):
    """The answer to a request for a file's bytes that may name a range (a video's, /api/video-file). `asked` is the
    request's Range as werkzeug reads it (request.range: None for none, and for one that does not parse); `size` the
    file's; `send(start, length)` gives the bytes, a piece at a time, streamed as they come.

    No range: 200 with the whole file. A range that cannot be answered -- several, one starting past the end, a suffix
    longer than the file, any range of a file of no bytes -- 416 with `Content-Range: bytes */size`. Else 206 with the
    bytes from its start, at most `span` of them: a browser playing a video asks again from where an answer ended."""
    found = None
    if asked is not None:
        found = asked.range_for_length(size)
        if found is None:
            refused = Response(b"", status=416)
            refused.headers["Content-Range"] = "bytes */%d" % size
            refused.headers["Accept-Ranges"] = "bytes"
            return refused
    start, stop = found if found else (0, size)
    if found:
        stop = min(stop, start + span)
    reply = Response(send(start, stop - start), status=206 if found else 200, mimetype=content_type,
                     direct_passthrough=True)
    if found:
        reply.headers["Content-Range"] = "bytes %d-%d/%d" % (start, stop - 1, size)
    reply.headers["Content-Length"] = str(stop - start)
    reply.headers["Accept-Ranges"] = "bytes"
    return reply


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
