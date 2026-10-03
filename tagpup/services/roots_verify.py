"""Verify: how well a place on this machine holds the files a root's rows describe
(docs/ARCHITECTURE.md, "Roots and machines", TagTuner's Roots).

Asked of a root and a CANDIDATE location -- a place the machine's map may not say yet, the one
the owner is about to point the root at, or the one it has now -- it reads the root's rows as the
library holds them (`@pictures/2024/a.jpg`), turns each into the path it would have at the
candidate by a Roots built here (the library's roots with the candidate in the root's place; the
machine's own map is never touched), and looks at the disk. For each row:

- **matches**: the file is there with the size and modified time the row recorded
  (`store.photos.describes`, what sync trusts a row by);
- **differs**: the file is there and is not what the row recorded -- changed since it was indexed,
  or a copy whose times are not the original's; sync settles it, and it is never "missing";
- **missing**: the folder is there (or the location is) and the file is not;
- **unreadable**: the disk refused the folder or the file's stamp.

And, in a full run, how many photos lie at the location that no row has, the rows held under no
root (grouped by folder), and the rows stored by this machine's spelling that lie where the
candidate would put the root (they would be missed by every lookup: a second set of rows on the
next index).

**A location that cannot be reached is not "all missing".** A drive that is not connected, a
share that is away, a folder that is not there, a share that stops answering half-way: each is
said as it is ("this location cannot be reached"), and the rows not looked at are not counted at
all. Every look at the disk -- the location itself, each folder -- is made on a thread of its own
and waited for `LIST_SECONDS` at most; a share that did not answer is "away" for `AWAY_SECONDS`
(by its drive or server and share), so a second look, a second click, does not start another
blocked thread. A sample is also bounded in time (`SAMPLE_BUDGET`); a full run is a job
(tagpup.jobs.verifying) that reports progress and can be cancelled.

A sample takes `SAMPLE` rows, chosen to cover every folder the root's rows are in (at least one
from each, the rest in proportion), the same rows for the same library; each folder is listed
once and the chosen rows are looked for in the listing. A full run walks the location, listing
each folder once, so the rows and the photos no row has are counted by the one walk.

**Read-only.** It writes no row, no file and not the map. The library is read through
`store.root_rows`, with its own connection; `machine` is only asked where the other roots are.
"""
import logging
import os
import random
import threading
import time

from tagpup.core import paths
from tagpup.core.result import NotFound, Refused
from tagpup.files import images
from tagpup.store import photos as store_photos
from tagpup.store import root_rows
from tagpup.store import roots as store_roots

logger = logging.getLogger(__name__)

#: Rows a sample takes.
SAMPLE = 2000

#: How long one look at the disk -- a folder, the location itself -- is waited for, in seconds,
#: and how long a share that did not answer in time is taken as away.
LIST_SECONDS = 15.0
AWAY_SECONDS = 30.0

#: How long a sample may take altogether, before it says what it has and stops.
SAMPLE_BUDGET = 25.0

#: More of the checked rows than this missing is a poor result: the place is probably another
#: folder, or another copy.
POOR_MISSING = 0.05

_away = {}
_blocked = {}
_guard = threading.Lock()


# ---- Looking at the disk, never for long -----------------------------------------------------

def _anchor_of(location):
    """The key of what a location is on -- its drive, or its server and share -- which is what is
    away when it is."""
    spelled = paths.stored(location)
    drive = os.path.splitdrive(spelled)[0]
    return paths.key(drive) if drive else os.sep


def _bounded(location, call, seconds):
    """("ok", what `call()` returned) | ("error", the OSError it raised) | ("away", None): `call`
    on a thread of its own, waited for `seconds`. A thread that did not answer is left to end
    when the system lets it (it is a daemon), its share away for AWAY_SECONDS, and no other look
    at the same drive or share is started until it is back or that long has passed."""
    anchor = _anchor_of(location)
    with _guard:
        since = _away.get(anchor)
        blocked = _blocked.get(anchor)
    if since is not None and time.monotonic() - since < AWAY_SECONDS:
        return "away", None
    if blocked is not None and blocked.is_alive():
        blocked.join(seconds)
        if blocked.is_alive():
            with _guard:
                _away[anchor] = time.monotonic()
            return "away", None
    box = {}

    def look():
        try:
            box["value"] = call()
        except OSError as problem:
            box["error"] = problem
        except Exception as problem:   # a bug in what was asked is its own message, not a hung thread
            logger.exception("Verify's look at %s failed", location)
            box["error"] = OSError(str(problem))

    thread = threading.Thread(target=look, name="VerifyLook", daemon=True)
    with _guard:
        _blocked[anchor] = thread
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        logger.info("%s did not answer within %s s; it is taken as away for %s s.", location, seconds, AWAY_SECONDS)
        with _guard:
            _away[anchor] = time.monotonic()
        return "away", None
    with _guard:
        _away.pop(anchor, None)
        if _blocked.get(anchor) is thread:
            del _blocked[anchor]
    if "error" in box:
        return "error", box["error"]
    return "ok", box["value"]


def forget_away():
    """Forget which shares were taken as away: what a test, or a Retry, starts from."""
    with _guard:
        _away.clear()


def _probe(location):
    """"ok" when the location is a folder; "folder" when its drive or share is there and it is
    not; "anchor" when the drive or share is not there."""
    spelled = paths.stored(location)
    drive = os.path.splitdrive(spelled)[0]
    top = drive + os.sep if drive else os.sep
    if not os.path.isdir(top):
        return "anchor"
    return "ok" if os.path.isdir(spelled) else "folder"


def reach(location, seconds=None):
    """Can `location` be reached? {"state": "ok" | "away" | "no_drive" | "no_folder" | "unreadable",
    "reachable", "message"}. Never waits longer than `seconds` (LIST_SECONDS)."""
    seconds = LIST_SECONDS if seconds is None else seconds
    state, value = _bounded(location, lambda: _probe(location), seconds)
    spelled = paths.stored(location)
    if state == "away":
        return _reach("away", "This location cannot be reached: %s did not answer within %d seconds, so nothing "
                              "is counted as missing. Try again when it is back." % (spelled, round(seconds)))
    if state == "error":
        return _reach("unreadable", "This location cannot be read: %s (%s)." % (spelled, value))
    if value == "anchor":
        return _reach("no_drive", "This location cannot be reached: %s is not there -- a drive that is not "
                                  "connected, or a share that is away. Nothing is counted as missing." % (
                                      os.path.splitdrive(spelled)[0] or spelled))
    if value == "folder":
        return _reach("no_folder", "This location cannot be reached: the folder %s does not exist." % spelled)
    return _reach("ok", "")


def _reach(state, message):
    return {"state": state, "reachable": state == "ok", "message": message}


def _list_one(folder):
    """({normcased name: (name, mtime, size)} of the files in `folder`, [its subfolders]): one
    listing, the stamps coming with it. A file whose stamp cannot be had has None for both.
    OSError for a folder that is not there or cannot be read."""
    files, folders = {}, []
    with os.scandir(folder) as entries:
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    folders.append(entry.path)
                elif entry.is_file():
                    found = entry.stat()
                    files[paths.name_key(entry.name)] = (entry.name, found.st_mtime, found.st_size)
            except OSError:
                files[paths.name_key(entry.name)] = (entry.name, None, None)
    return files, folders


# ---- The rows ----------------------------------------------------------------------------------

def candidate_roots(library, name, location, machine):
    """(the paths.Roots with `location` as root `name`'s only place and every other root where
    the machine's map has it, the library's roots as {name: address}). Refused, as a sentence, when
    the library has no such root, the map cannot be read, or the combination is one the map would
    refuse. Pure: no disk, no write."""
    try:
        name = paths.root_name(name)
    except paths.RootsError as problem:
        raise Refused(str(problem)) from None
    try:
        found = {entry["name"]: entry["address"] for entry in store_roots.listing(library.path)}
    except Exception as problem:
        raise NotFound("The library cannot be read: %s" % problem) from None
    if name not in found:
        raise NotFound("This library has no root called %s." % name)
    if not paths.is_native_absolute(location):
        raise Refused("%r is not an absolute folder on this machine: a drive and folder (D:\\Photos) or a "
                      "share (\\\\server\\share\\Photos)." % (location,))
    try:
        placed = {each: list(places) for each, places in machine.roots().items() if each != name}
        placed[name] = [paths.stored(location)]
        return paths.Roots.of(found, placed, machine.path()), found
    except ValueError as problem:   # paths.RootsError, config.MachineMapError
        raise Refused(str(problem)) from None


def _read_rows(library, name, roots):
    """What the library's rows say of root `name`, converted by `roots`: (ours [(folder, name,
    mtime, size)], counts) -- `counts` the rows of other roots, the rows that do not convert, the
    rows kept native that lie where `roots` has a root (native_inside), and the folders of those
    under none (outside)."""
    mark, sep = paths.ROOT_MARK, paths.ROW_SEP
    ours, outside, other, bad, inside = [], [], 0, 0, 0
    for path, mtime, size in root_rows.stamps(library.path):
        if path.startswith(mark):
            if path[1:].split(sep, 1)[0].lower() != name:
                other += 1
                continue
            try:
                native = paths.from_row(path, roots)
            except paths.RootsError:
                bad += 1
                continue
            folder, base = os.path.split(native)
            ours.append((folder, base, mtime, size))
        elif roots.locate(path) is not None:
            inside += 1
        else:
            outside.append(os.path.dirname(path))
    return ours, {"other_roots": other, "not_converting": bad, "native_inside": inside, "outside": outside}


def _group(ours):
    """{folder key: (folder, [(name key, name, mtime, size)])} of the rows."""
    by_folder, keys = {}, {}
    for folder, base, mtime, size in ours:
        key = keys.get(folder)
        if key is None:
            key = keys[folder] = paths.key(folder)
        held = by_folder.get(key)
        if held is None:
            held = by_folder[key] = (folder, [])
        held[1].append((paths.name_key(base), base, mtime, size))
    return by_folder


def sample_of(by_folder, want):
    """{folder key: [the rows chosen]}: `want` rows about, at least one from each folder, the rest
    in proportion to the folder's rows; the same rows for the same library. Every row when `want`
    is at least the number of rows."""
    total = sum(len(rows) for _folder, rows in by_folder.values())
    if want >= total:
        return {key: list(rows) for key, (_folder, rows) in by_folder.items()}
    chosen = {}
    rng = random.Random(total)
    for key in sorted(by_folder):
        rows = by_folder[key][1]
        take = min(len(rows), max(1, round(want * len(rows) / total)))
        chosen[key] = rng.sample(rows, take)
    return chosen


# ---- The run -----------------------------------------------------------------------------------

class _Tally:
    """What a run has counted."""

    def __init__(self):
        self.matches = self.differs = self.missing = self.unreadable = 0
        self.extra = 0
        self.checked = 0
        self.folders = 0
        self.stopped = None

    def judge(self, row, listing):
        """Count `row`, (name key, name, mtime, size), against the folder's `listing`."""
        found = listing.get(row[0])
        self.checked += 1
        if found is None:
            self.missing += 1
        elif found[1] is None or found[2] is None:
            self.unreadable += 1
        elif store_photos.describes(row[2], row[3], (found[1], found[2])):
            self.matches += 1
        else:
            self.differs += 1

    def refused_folder(self, rows, why):
        """Count the rows of a folder the disk said something of: not there, or not readable."""
        self.checked += len(rows)
        if why == "missing":
            self.missing += len(rows)
        else:
            self.unreadable += len(rows)


def _list_bounded(location, folder, seconds):
    """("ok", (files, folders)) | ("missing" | "unreadable", None) | ("away", None)."""
    state, value = _bounded(location, lambda: _list_one(folder), seconds)
    if state == "ok":
        return "ok", value
    if state == "away":
        return "away", None
    if isinstance(value, (FileNotFoundError, NotADirectoryError)):
        return "missing", None
    return "unreadable", None


def _sample_run(location, chosen, by_folder, tally, cancel, progress, deadline, total, seconds):
    # In an order spread over the root, not alphabetical: a sample that runs out of time on a slow
    # share has then looked at folders from everywhere, not at the first few years only.
    order = sorted(chosen)
    random.Random(len(order)).shuffle(order)
    for key in order:
        if cancel():
            tally.stopped = "cancelled"
            return
        if deadline is not None and time.monotonic() > deadline:
            tally.stopped = "time"
            return
        state, value = _list_bounded(location, by_folder[key][0], seconds)
        if state == "away":
            tally.stopped = "unreachable"
            return
        if state != "ok":
            tally.refused_folder(chosen[key], state)
        else:
            for row in chosen[key]:
                tally.judge(row, value[0])
        tally.folders += 1
        progress(tally.checked, total, tally.folders)


def _full_run(location, by_folder, tally, cancel, progress, total, seconds):
    """Walk the location, one listing of each folder: rows judged where they are, photos no row
    has counted. Then a folder of rows the walk did not come to is listed directly (a link, a
    folder the walk was not let into) before its rows are called missing."""
    rows_of = {key: {row[0] for row in rows} for key, (_folder, rows) in by_folder.items()}
    done = set()
    waiting = [paths.stored(location)]
    while waiting:
        if cancel():
            tally.stopped = "cancelled"
            return
        folder = waiting.pop()
        key = paths.key(folder)
        state, value = _list_bounded(location, folder, seconds)
        if state == "away":
            tally.stopped = "unreachable"
            return
        tally.folders += 1
        done.add(key)
        held = by_folder.get(key)
        if state != "ok":
            if held is not None:
                tally.refused_folder(held[1], state)
            continue
        files, folders = value
        if held is not None:
            for row in held[1]:
                tally.judge(row, files)
        known = rows_of.get(key, ())
        tally.extra += sum(1 for name_key, (name, _m, _s) in files.items()
                           if name_key not in known and images.is_photo(name))
        waiting.extend(sorted(folders, reverse=True))
        progress(tally.checked, total, tally.folders)
    for key in sorted(by_folder):
        if key in done:
            continue
        if cancel():
            tally.stopped = "cancelled"
            return
        folder, rows = by_folder[key]
        state, value = _list_bounded(location, folder, seconds)
        if state == "away":
            tally.stopped = "unreachable"
            return
        if state != "ok":
            tally.refused_folder(rows, state)
        else:
            for row in rows:
                tally.judge(row, value[0])
        tally.folders += 1
        progress(tally.checked, total, tally.folders)


def verify(library, name, location, machine, full=False, sample=SAMPLE, cancel=None, progress=None,
           budget=None, seconds=None):
    """How well `location` holds the files of root `name`'s rows: a sample (`sample` rows, one of
    them from every folder at least), or with `full` every row and the photos no row has.

    Returns a dict -- "root", "location", "mode" ("sample" | "all"), "reachable", "state",
    "message" (why not, when it cannot be reached), "rows" (the root's rows), "checked",
    "matches", "differs", "missing", "unreadable", "folders" (looked at), "not_in_library" (None
    unless full), "other_rows" (held under no root, or under another), "outside" (the rows under no
    root, by folder: paths.outside_roots), "native_inside", "not_converting", "partial",
    "stopped" (None | "cancelled" | "unreachable" | "time"), "poor" and "poor_why", and "summary", a
    sentence. Refused (Refused, NotFound) for a root the library does not have, a location that
    is not an absolute folder on this machine, a map that cannot be read or that the location
    does not fit.

    `cancel()` is asked between folders; `progress(checked, rows, folders)` is called as it goes,
    on this thread. `budget` seconds, when given, end a sample with what it has (SAMPLE_BUDGET is
    the web route's); `seconds` is how long one look at the disk is waited for (LIST_SECONDS)."""
    cancel = cancel or (lambda: False)
    progress = progress or (lambda checked, total, folders: None)
    seconds = LIST_SECONDS if seconds is None else seconds
    name = paths.root_name(name)
    if not isinstance(location, str) or not paths.is_native_absolute(location):
        raise Refused("%r is not an absolute folder on this machine: a drive and folder (D:\\Photos) or a "
                      "share (\\\\server\\share\\Photos)." % (location,))
    location = paths.stored(location)
    roots, _found = candidate_roots(library, name, location, machine)
    ours, counts = _read_rows(library, name, roots)
    by_folder = _group(ours)
    total = len(ours) + counts["not_converting"]
    answer = {"root": name, "location": location, "mode": "all" if full else "sample", "rows": total,
              "checked": 0, "matches": 0, "differs": 0, "missing": 0, "unreadable": counts["not_converting"],
              "folders": 0, "not_in_library": None, "other_rows": counts["other_roots"],
              "native_inside": counts["native_inside"], "not_converting": counts["not_converting"],
              "outside": paths.outside_roots(counts["outside"], roots), "outside_rows": len(counts["outside"]),
              "partial": False, "stopped": None, "poor": False, "poor_why": []}
    reached = reach(location, seconds)
    answer.update(reachable=reached["reachable"], state=reached["state"], message=reached["message"])
    if not reached["reachable"]:
        answer["partial"] = True
        answer["stopped"] = "unreachable"
        return _conclude(answer, total)
    tally = _Tally()
    if full:
        _full_run(location, by_folder, tally, cancel, progress, total, seconds)
    else:
        chosen = sample_of(by_folder, sample)
        deadline = None if budget is None else time.monotonic() + budget
        _sample_run(location, chosen, by_folder, tally, cancel, progress, deadline, total, seconds)
    answer.update(checked=tally.checked, matches=tally.matches, differs=tally.differs, missing=tally.missing,
                  unreadable=tally.unreadable + counts["not_converting"], folders=tally.folders,
                  stopped=tally.stopped)
    if full and not tally.stopped:
        answer["not_in_library"] = tally.extra
    answer["partial"] = bool(tally.stopped)
    if tally.stopped == "unreachable":
        answer.update(reachable=False, state="away", message=(
            "This location stopped answering after %d folder(s); the rows not looked at are not counted "
            "as missing." % tally.folders))
    return _conclude(answer, total)


def _conclude(answer, total):
    """The verdict: whether the result is poor, why, and the summary sentence."""
    why = []
    if not answer["reachable"]:
        why.append(answer["message"])
    checked = answer["checked"]
    if checked and answer["missing"] > POOR_MISSING * checked:
        why.append("%d of the %d rows looked at (%d%%) are not there: this looks like another folder, or another "
                   "copy." % (answer["missing"], checked, round(100 * answer["missing"] / checked)))
    if answer["native_inside"]:
        why.append("%d row(s) are held by this machine's own spelling of a place that would be this root's: "
                   "they would be missed, and indexed again as new photos." % answer["native_inside"])
    answer["poor_why"] = why
    answer["poor"] = bool(why)
    answer["summary"] = _summary(answer, total)
    return answer


def _summary(answer, total):
    if not answer["reachable"] and not answer["checked"]:
        return answer["message"]
    if answer["mode"] == "all":
        kind = "%d of %d rows" % (answer["checked"], total)
    else:
        kind = "a sample of %d of %d rows covering %d folder(s)" % (answer["checked"], total, answer["folders"])
    parts = ["%d match" % answer["matches"], "%d differ (changed since indexed; sync settles them)" % answer["differs"],
             "%d are missing" % answer["missing"]]
    if answer["unreadable"]:
        parts.append("%d could not be read" % answer["unreadable"])
    text = "Looked at %s: %s." % (kind, ", ".join(parts))
    if answer["not_in_library"]:
        text += " %d photo(s) at the location have no row." % answer["not_in_library"]
    if answer["stopped"] == "cancelled":
        text += " Cancelled before it finished."
    elif answer["stopped"] == "time":
        text += " It ran out of time; run All for the rest."
    elif answer["stopped"] == "unreachable":
        text += " " + answer["message"]
    return text
