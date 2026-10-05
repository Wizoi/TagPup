"""A turn on the graphics card, in the words every layer may use: the line a job waiting
for one shows, how a page or a queue tells that line from another, and the exception a
wait that was cancelled raises.

The turns themselves are taken by tagpup.ml.gpu, one process at a time on this machine
(docs/findings.md, #750); the runtime takes them for a run (tagpup.runtime). A job that
waits shows `waiting_line` in its progress -- the index queue's status, a Suggest run's
-- so the Activity page and the folder's status say who has the card and since when,
and a Cancel can tell a job that is only waiting from one that has begun.
"""
import time

#: How a waiting line begins: what is_waiting looks for.
WAITING = "Waiting for the graphics card"

#: What the waiting line names when the holder said nothing of itself.
SOMEONE = "another TagPup program"


class Cancelled(Exception):
    """A wait for the graphics card was cancelled: nothing was run on it."""


def waiting_line(holder=None):
    """`Waiting for the graphics card (in use by: <what>, since 15:41)...` -- `holder` is
    what the holder says of itself ({"what", "since"}, tagpup.ml.gpu), or None."""
    what = (holder or {}).get("what") or SOMEONE
    since = (holder or {}).get("since")
    try:
        when = ", since %s" % time.strftime("%H:%M", time.localtime(float(since))) if since else ""
    except (TypeError, ValueError, OverflowError, OSError):
        when = ""
    return "%s (in use by: %s%s)..." % (WAITING, what, when)


def is_waiting(message):
    """Is `message` a waiting line?"""
    return isinstance(message, str) and message.startswith(WAITING)
