"""Looking at a place on the disk that may be a network share that is away, without waiting for it
(docs/ARCHITECTURE.md, "Roots and machines"): the one owner of "a share is away".

A folder on a share that does not answer blocks the call that asks, for as long as Windows waits --
a page's request, a Verify's listing. So every look is made on a thread of its own and waited for a
moment (`bounded`); one that did not answer in time leaves its thread to end when the system lets it (a
daemon), takes the share as AWAY for AWAY_SECONDS -- by its drive, or its server and share, whichever of
its folders was asked -- and no other look at it is started, on another thread, while the first still
waits. A share that answered is not away. Two callers used to keep a cache each (the damaged photos'
lists, and Roots' Verify), one of them unable to say away from missing; this is the one. `stamp_of` is the look at one
file that a page of cards, a thumbnail and a bulk edit's chunk make.
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

#: How long a request waits to hear from one file on a network share, in seconds.
SHARE_WAIT = 1.0

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


#: What stamp_of answers for a file on a share that did not answer in time.
UNANSWERED = object()

#: What stamp_of answers for a file that is there and cannot be read (permissions, a lock): not "not there".
CANNOT_READ = object()


def _root_answers(photo_path):
    """Does the drive or share the path is on answer? Windows says "not found" at once for a server that is
    asleep (WinError 53), a share name that is not there (67) and a drive letter that is not connected (3), the
    same words it uses for a file that was deleted: a file is gone only when the place it was on is there."""
    root = os.path.splitdrive(photo_path)[0]
    if not root:
        return True
    return os.path.exists(root + os.sep)


def _folder_state(folder):
    """"there", "gone" or "unanswered" of a folder, asked of the disk now."""
    if os.path.isdir(folder):
        return "there"
    return "gone" if _root_answers(folder) else "unanswered"


def folder_state(folder, seconds=None):
    """"there" when `folder` is a folder; "gone" when it is not and the drive or share it is on answers;
    "unanswered" when that drive or share does not, or, on a network share, did not answer within `seconds`
    (SHARE_WAIT by default), or is taken as away. What a sync asks of a root and of a missing row's folder: a
    folder it cannot look at is not a folder gone (#1356)."""
    folder = paths.stored(folder)
    if not on_a_network_drive(folder):
        return _folder_state(folder)
    state, found = bounded(folder, lambda: _folder_state(folder), SHARE_WAIT if seconds is None else seconds)
    return found if state == "ok" else "unanswered"


def _stat_state(photo_path):
    """(mtime, size); None for a file that is not there; CANNOT_READ for any other OSError, and for a file
    "not found" on a drive or share that does not answer (it is not known to be gone)."""
    try:
        stat = os.stat(photo_path)
    except (FileNotFoundError, NotADirectoryError):
        return None if _root_answers(photo_path) else CANNOT_READ
    except OSError:
        return CANNOT_READ
    return (stat.st_mtime, stat.st_size)


def stamp_of(photo_path):
    """(mtime, size) of the file now; None when it is not there; CANNOT_READ when it is there and cannot be
    read; UNANSWERED when it is on a share that did not answer within SHARE_WAIT (or is away). What a page's
    request asks of one file, without waiting on a share gone away, and without taking a file it cannot read for
    one that is gone. A share found away is answered UNANSWERED at once for AWAY_SECONDS and no second thread is
    started while one still waits, so a page of cards on an away share costs one wait and not one for each."""
    if not on_a_network_drive(photo_path):
        return _stat_state(photo_path)
    state, found = bounded(photo_path, lambda: _stat_state(photo_path), SHARE_WAIT, AWAY_SECONDS)
    return found if state == "ok" else UNANSWERED
