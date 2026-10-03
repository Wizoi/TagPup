"""Looking at a place on the disk that may be a network share that is away, without waiting for it
(docs/ARCHITECTURE.md, "Roots and machines"): the one owner of "a share is away".

A folder on a share that does not answer blocks the call that asks, for as long as Windows waits --
a page's request, a Verify's listing. So every look is made on a thread of its own and waited for a
moment (`bounded`); one that did not answer in time leaves its thread to end when the system lets it (a
daemon), takes the share as AWAY for AWAY_SECONDS -- by its drive, or its server and share, whichever of
its folders was asked -- and no other look at it is started, on another thread, while the first still
waits. A share that answered is not away. Two callers used to keep a cache each (the damaged photos'
lists, and Roots' Verify), one of them unable to say away from missing; this is the one.
"""
import logging
import os
import threading
import time

from tagpup.core import paths
from tagpup.files import recycle_bin

logger = logging.getLogger(__name__)

#: How long a share that did not answer in time is taken as away, in seconds.
AWAY_SECONDS = 30.0

#: {share key: when it did not answer in time (time.monotonic)}.
_away = {}
#: {share key: the thread still looking at it}.
_reading = {}
_guard = threading.Lock()


def share_of(folder):
    """The key of what `folder` is on: its drive, or its server and share -- what is away when it is."""
    spelled = paths.stored(folder)
    return paths.key(os.path.splitdrive(spelled)[0] or folder)


def on_a_share(path):
    """Is `path` spelled as a network share (\\\\server\\share, or //server/share)?"""
    return str(path).startswith(("\\\\", "//"))


def on_a_network_drive(path):
    """Is `path` on a network share: spelled as one (on_a_share), or on a mapped network drive (a drive letter that
    GetDriveType says is remote; tagpup.files.recycle_bin.on_a_network_drive)? Either can stop answering, and a
    look at either is made on a thread that is waited for (bounded)."""
    return on_a_share(path) or recycle_bin.on_a_network_drive(path)


def forget():
    """Forget which shares were taken as away: what a test, or a Retry, starts from."""
    with _guard:
        _away.clear()
        _reading.clear()


def bounded(location, call, seconds, away_seconds=None):
    """("ok", what `call()` returned) | ("error", the OSError it raised) | ("away", None): `call` on a
    thread of its own, waited for `seconds`. A share already taken as away answers "away" at once, and
    one whose earlier look still waits is waited for as long as any -- only a wait that timed out
    makes it away."""
    away_seconds = AWAY_SECONDS if away_seconds is None else away_seconds
    share = share_of(location)
    with _guard:
        since = _away.get(share)
        still = _reading.get(share)
    if since is not None and time.monotonic() - since < away_seconds:
        return "away", None
    if still is not None and still.is_alive():
        still.join(seconds)
        if still.is_alive():
            with _guard:
                _away.setdefault(share, time.monotonic())
            return "away", None
    box = {}

    def look():
        try:
            box["value"] = call()
        except OSError as problem:
            box["error"] = problem
        except Exception as problem:   # a bug in what was asked is its own message, not a hung thread
            logger.exception("The look at %s failed", location)
            box["error"] = OSError(str(problem))

    thread = threading.Thread(target=look, name="ShareLook", daemon=True)
    with _guard:
        _reading[share] = thread
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        logger.info("%s did not answer within %s s; its share is taken as away for %s s.", location, seconds,
                    away_seconds)
        with _guard:
            _away[share] = time.monotonic()
        return "away", None
    with _guard:
        _away.pop(share, None)
        if _reading.get(share) is thread:
            del _reading[share]
    if "error" in box:
        return "error", box["error"]
    return "ok", box["value"]
