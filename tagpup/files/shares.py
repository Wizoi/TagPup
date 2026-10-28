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

It owns "back" as well as "away": the look that did not answer in time is left running, and when it ends with an answer
the share is awake -- its mark is cleared at once, whoever waits for it (`wait_back`) is woken, and whoever asked to be
told (`on_back`) is told, on that thread. No thread of its own, no polling: a share that answered late (1.3 s for a 1.0 s
moment, 2026-10-27 and 2026-10-28) is back when its answer comes, not when its away window runs out (#2101).
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

#: {share key: when it was last taken as away (time.monotonic)}.
_away = {}
#: {share key: when it stopped answering, by the wall clock (time.time)}: kept through the marks a share that stays away
#: renews, until it answers (away_since).
_since = {}
#: {share key: the thread still looking at it}: removed by the look itself as it ends.
_reading = {}
#: {share key: an Event set while the share is not taken as away}: what wait_back waits on.
_back = {}
#: {share key: {callback: None}}: told once, when the share answers again (on_back).
_listeners = {}
_guard = threading.Lock()
#: On a ShareLook thread: did its own look learn that the share is away (_share_answers)? Its end is then not "back".
_looking = threading.local()


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
    """Forget which shares were taken as away, and who waits to be told they are back: what a test, or a Retry, starts
    from."""
    with _guard:
        _away.clear()
        _since.clear()
        _reading.clear()
        _listeners.clear()
        for event in _back.values():
            event.set()


def _left_of_away(since, away_seconds):
    """Seconds left of the window a share found away at `since` (time.monotonic) is taken as away; 0 or less when over.
    The one statement of the window, for bounded (still taken as away) and wait_back (how long to wait)."""
    return since + away_seconds - time.monotonic()


def _mark(share):
    """Take `share` as away from now. Under _guard."""
    _away[share] = time.monotonic()
    _since.setdefault(share, time.time())
    _back.setdefault(share, threading.Event()).clear()


def _answered(share):
    """`share` answered: it is not away. Under _guard; gives back the callbacks to tell (_tell), outside it."""
    _away.pop(share, None)
    _since.pop(share, None)
    event = _back.get(share)
    if event is not None:
        event.set()
    return list(_listeners.pop(share, {}))


def _tell(callbacks, share):
    """Tell each of `callbacks` that `share` is back. Never under _guard; one that raises is logged and the rest told."""
    for callback in callbacks:
        try:
            callback(share)
        except Exception:
            logger.exception("Telling that %s is back failed", share)


def bounded(location, call, seconds, away_seconds=None):
    """("ok", what `call()` returned) | ("error", the OSError it raised) | ("away", None): `call` on a
    thread of its own, waited for `seconds`. A share already taken as away answers "away" at once, and
    one whose earlier look still waits is waited for as long as any -- only a wait that timed out
    makes it away. A look that timed out is left to end: when it ends with an answer, the share is back (_answered)."""
    away_seconds = AWAY_SECONDS if away_seconds is None else away_seconds
    share = share_of(location)
    with _guard:
        since = _away.get(share)
        still = _reading.get(share)
    if since is not None and _left_of_away(since, away_seconds) > 0:
        return "away", None
    if still is not None and still.is_alive():
        still.join(seconds)
        with _guard:
            hung = _reading.get(share) is still
            if hung:
                _mark(share)   # hung again: away from now, not from an old mark that ran out
        if hung:
            return "away", None
    box = {}

    def look():
        _looking.learned = False
        try:
            box["value"] = call()
        except OSError as problem:
            box["error"] = problem
        except Exception as problem:   # a bug in what was asked is its own message, not a hung thread
            logger.exception("The look at %s failed", location)
            box["error"] = OSError(str(problem))
        finally:
            with _guard:
                if _reading.get(share) is thread:
                    del _reading[share]
                # The share answered: an away taken before or during this look is over, but not one the look itself
                # learned (a server that said "not found" at once, _share_answers).
                told = [] if _looking.learned else _answered(share)
            _tell(told, share)

    thread = threading.Thread(target=look, name="ShareLook", daemon=True)
    with _guard:
        _reading[share] = thread
    thread.start()
    thread.join(seconds)
    with _guard:
        hung = _reading.get(share) is thread
        if hung:
            _mark(share)
    if hung:
        logger.info("%s did not answer within %s s; its share is taken as away for %s s, or until it answers.",
                    location, seconds, away_seconds)
        return "away", None
    if "error" in box:
        return "error", box["error"]
    return "ok", box["value"]


def away_since(share):
    """When `share` (share_of) stopped answering, as the Activity page shows a time ("2026-10-28 08:33:15"), or None
    when it is not taken as away."""
    with _guard:
        since, began = _away.get(share), _since.get(share)
    if since is None or began is None or _left_of_away(since, AWAY_SECONDS) <= 0:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(began))


def on_back(share, callback):
    """Have `callback(share)` called once, on the thread of the look that hears `share` (share_of) answer again: True
    when it is taken as away now and so will be told; False, and nothing kept, when it is not (ask it). Asked again with
    the same callback while away, it is kept once. What the watcher asks for a root that does not answer."""
    with _guard:
        if share not in _away:
            return False
        _listeners.setdefault(share, {})[callback] = None
        return True


#: What stamp_of answers for a file on a share that did not answer in time.
UNANSWERED = object()

#: What stamp_of answers for a file that is there and cannot be read (permissions, a lock): not "not there".
CANNOT_READ = object()


def _share_answers(photo_path):
    """Does the network share the path is on answer? Windows says "not found" at once for a server that is asleep
    (WinError 53), a share name that is not there (67) and a mapped drive that is not connected, the same words it
    uses for a file that was deleted: a file on a share is gone only when the share is there. Asked of a network path
    alone: a local drive letter that is not connected is a drive gone, and what was on it gone (owner, 2026-10-15: no
    USB drives; #1387). The one place that learns a share is away from a quick "not found": one that does not answer
    is taken as away for AWAY_SECONDS from now, as one that did not answer in time is, so the next look -- at a file
    (stamp_of) or a folder (folder_state) -- is answered at once without asking it (#1543)."""
    root = os.path.splitdrive(photo_path)[0]
    if not root:
        return True
    if os.path.exists(root + os.sep):
        return True
    share = share_of(root)
    with _guard:
        _mark(share)   # a look answered from the memo never gets here (bounded), so the window runs from this look
    _looking.learned = True
    logger.info("%s said it is not there; its share is taken as away for %s s.", root, AWAY_SECONDS)
    return False


def _folder_state(folder, on_a_share):
    """"there", "gone" or "unanswered" (a share's only) of a folder, asked of the disk now."""
    if os.path.isdir(folder):
        return "there"
    return "unanswered" if on_a_share and not _share_answers(folder) else "gone"


def folder_state(folder, seconds=None):
    """"there" when `folder` is a folder; "unanswered" when it is on a network share (UNC, or a mapped network drive)
    that does not answer, did not answer within `seconds` (SHARE_WAIT by default), or is taken as away; otherwise
    "gone" -- a local drive letter that is not connected included. What a sync asks of a root and of a missing row's
    folder: a folder on a share it cannot look at is not a folder gone (#1356); only a share (#1387)."""
    folder = paths.stored(folder)
    if not on_a_network_drive(folder):
        return _folder_state(folder, False)
    state, found = bounded(folder, lambda: _folder_state(folder, True), SHARE_WAIT if seconds is None else seconds)
    return found if state == "ok" else "unanswered"


def _stat_state(photo_path, on_a_share):
    """(mtime, size); None for a file that is not there; CANNOT_READ for any other OSError; UNANSWERED for a file "not
    found" on a network share that does not answer (a server asleep, WinError 53: not known to be gone, and the share
    is away, not the file unreadable; #1543)."""
    try:
        stat = os.stat(photo_path)
    except (FileNotFoundError, NotADirectoryError):
        return UNANSWERED if on_a_share and not _share_answers(photo_path) else None
    except OSError:
        return CANNOT_READ
    return (stat.st_mtime, stat.st_size)


def stamp_of(photo_path):
    """(mtime, size) of the file now; None when it is not there (a local drive letter that is not connected
    included, #1387); CANNOT_READ when it is there and cannot be read; UNANSWERED when it is on a share that did not
    answer within SHARE_WAIT (or is away). What a page's request asks of one file, without waiting on a share gone
    away, and without taking a file it cannot read for one that is gone. A share found away is answered UNANSWERED at
    once for AWAY_SECONDS and no second thread is started while one still waits, so a page of cards on an away share
    costs one wait and not one for each."""
    if not on_a_network_drive(photo_path):
        return _stat_state(photo_path, False)
    state, found = bounded(photo_path, lambda: _stat_state(photo_path, True), SHARE_WAIT, AWAY_SECONDS)
    return found if state == "ok" else UNANSWERED


#: How often a patient wait for a share asks whether it is to stop, in seconds.
STOP_EVERY = 0.5


def wait_back(share, seconds, stop=None):
    """Wait, at most `seconds`, until `share` (share_of) is no longer taken as away: the look that did not answer in time
    answered after all (bounded: the share is awake, and the wait ends at that moment), or the window it is taken as away
    for ran out. True when it is not taken as away (ask it again: a fresh look), False when it still is, or when `stop()`,
    asked every STOP_EVERY seconds, said stop. A share found away by a quick "not found" has no look to end: its window,
    or `seconds`, is waited out. The one patient wait: a walk's (Walk, patient) and a sync's root."""
    deadline = time.monotonic() + seconds
    while True:
        with _guard:
            since, event = _away.get(share), _back.get(share)
        left_away = _left_of_away(since, AWAY_SECONDS) if since is not None else 0
        if left_away <= 0:
            return True
        left = deadline - time.monotonic()
        if left <= 0 or (stop is not None and stop()):
            return False
        event.wait(min(left, left_away, STOP_EVERY))


class Walk:
    """One pass over many photos or folders (a warm, a repair, a bake, a sync's roots): each share found away is asked
    ONCE, its photos counted as skipped, not asked again and never counted as unreadable. The one owner of this rule; the
    passes used to write it each (#1543). `away` names the shares found away (share_of: a drive, or \\\\server\\share), in
    the order found; `skipped` counts the looks it answered unasked or not answered (photos, for stamp_of).

    A `patient` walk (a bake's, a sync's: a run over a library's files, not a page) takes no share as away on one slow
    answer: a share asleep is slower than SHARE_WAIT to give its first answer (1.3 s, measured 2026-10-27), and an
    impatient walk then counted every photo on it unreachable in seconds. It waits for the share to be back (wait_back, at
    most `wait` seconds, `stop()` asked as it waits) and asks the same photo or folder once more; only a share that does
    not answer that either is found away. Once per share, so a share that stays away costs one wait and one look, not one
    for each photo."""

    def __init__(self, patient=False, stop=None, wait=None):
        self.away = []
        self.skipped = 0
        self._shares = set()
        self.patient, self.stop = patient, stop
        self.wait = AWAY_SECONDS if wait is None else wait
        self._waited = set()

    def _ask(self, path, ask, unanswered):
        """`ask()`, the look at `path`, except `unanswered` at once, unasked, for a share this walk found away; asked once
        more after the share is back, for a patient walk."""
        share = share_of(path) if on_a_network_drive(path) else None
        if share is not None and share in self._shares:
            self.skipped += 1
            return unanswered
        found = ask()
        if found == unanswered and self.patient and share not in self._waited:
            self._waited.add(share)
            logger.info("%s did not answer; waiting for it to be back before asking it once more.", share)
            if wait_back(share, self.wait, self.stop):
                found = ask()
        if found == unanswered:
            self._shares.add(share)
            self.away.append(share)
            self.skipped += 1
        return found

    def folder_state(self, folder, seconds=None):
        """`folder_state(folder, seconds)`, except "unanswered" at once, unasked, for a share this walk found away."""
        return self._ask(folder, lambda: folder_state(folder, seconds), "unanswered")

    def stamp_of(self, photo_path):
        """`stamp_of(photo_path)`, except UNANSWERED at once, unasked, for a share this walk found away."""
        return self._ask(photo_path, lambda: stamp_of(photo_path), UNANSWERED)
