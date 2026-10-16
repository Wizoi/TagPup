"""A sample of a place: does it hold the files a root's rows describe? Change location shows the
answer before it moves a root (docs/ARCHITECTURE.md, "Roots and machines", TagTuner's Roots).

Asked of a root and a CANDIDATE location -- a place the machine's map may not say yet -- it reads
the root's rows as the library holds them (`@pictures/2024/a.jpg`), turns each into the path it
would have at the candidate by a Roots built here (the library's roots with the candidate in the
root's place; the machine's own map is never touched), takes `SAMPLE` of them spread evenly over
the rows in path order (the same rows for the same library) and looks at each file once
(`tagpup.files.shares.stamp_of`, which does not wait on a share that is away). For each:

- **matches**: the file is there with the size and modified time the row recorded
  (`store.photos.describes`, what sync trusts a row by);
- **differs**: the file is there and is not what the row recorded -- changed since it was indexed,
  or a copy whose times are not the original's; sync settles it, and it is never "missing";
- **missing**: the file is not there;
- **never read**: the file is there and the row has no stamp -- Suggest made it for a photo the index
  never read; sync reads it, and it is not "changed since indexed";
- **unreadable**: the disk refused the file's stamp.

And the rows held under no root (grouped by folder), and the rows stored by this machine's spelling that
lie where the candidate would put the root (they would be missed by every lookup: a second set of rows
on the next index).

**A location that cannot be reached is not "all missing".** A drive that is not connected, a
share that is away, a folder that is not there, a share that stops answering half-way: each is
said as it is ("this location cannot be reached"), and the rows not looked at are not counted at
all. The location itself is looked at on a thread of its own and waited for `LIST_SECONDS` at most;
a share that did not answer is "away" for `AWAY_SECONDS`, so a second click does not start another
blocked thread. A sample is also bounded in time (`SAMPLE_BUDGET`).

**Read-only.** It writes no row, no file and not the map. The library is read through
`store.root_rows`, with its own connection; `machine` is only asked where the other roots are.
"""
import logging
import os
import time

from tagpup.core import paths
from tagpup.core.result import NotFound, Refused
from tagpup.files import shares
from tagpup.store import photos as store_photos
from tagpup.store import root_rows
from tagpup.store import roots as store_roots

logger = logging.getLogger(__name__)

#: Rows a sample takes.
SAMPLE = 200

#: How long one look at the location itself is waited for, in seconds, and how long a share that did
#: not answer in time is taken as away.
LIST_SECONDS = 15.0
AWAY_SECONDS = shares.AWAY_SECONDS

#: How long a sample may take altogether, before it says what it has and stops.
SAMPLE_BUDGET = 25.0

#: More of the checked rows than this missing is a poor result: the place is probably another
#: folder, or another copy.
POOR_MISSING = 0.05

#: More of the checked rows than this DIFFERING is a poor result too: a stale copy -- every file there,
#: other times or sizes -- passes the missing test, and the sync that follows a move re-reads those rows
#: from the files at the new place, replacing tags newer in the rows than in those files.
POOR_DIFFERS = 0.5

#: What sync does with the rows that differ, said wherever a count of them is.
SYNC_REPLACES = ("Sync re-reads those rows from the files there, so a tag newer in a row than in its file "
                 "is replaced by the file's.")

# ---- Looking at the disk, never for long -----------------------------------------------------

def _bounded(location, call, seconds):
    """("ok", what `call()` returned) | ("error", the OSError it raised) | ("away", None), within
    `seconds`, a share that did not answer being away for AWAY_SECONDS (tagpup.files.shares)."""
    return shares.bounded(location, call, seconds, AWAY_SECONDS)


def forget_away():
    """Forget which shares were taken as away: what a test, or a Retry, starts from."""
    shares.forget()


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
    """What the library's rows say of root `name`, converted by `roots`: (ours [(native path,
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
            ours.append((native, mtime, size))
        elif roots.locate(path) is not None:
            inside += 1
        else:
            outside.append(os.path.dirname(path))
    return ours, {"other_roots": other, "not_converting": bad, "native_inside": inside, "outside": outside}


def _sample(ours, want):
    """`want` of the rows `ours` ((native path, mtime, size)), evenly spread over their path order: the
    same rows for the same library, from every part of it. All of them when there are no more than `want`."""
    ours = sorted(ours, key=lambda row: paths.key(row[0]))
    if len(ours) <= want:
        return ours
    return [ours[number * len(ours) // want] for number in range(want)]


def _look(chosen, deadline):
    """({"matches", "differs", "missing", "unreadable", "unread"} counted over `chosen`, why it stopped
    early: None | "unreachable" | "time"), each file looked at once."""
    count = dict.fromkeys(("matches", "differs", "missing", "unreadable", "unread"), 0)
    looked = 0
    for native, mtime, size in chosen:
        if time.monotonic() >= deadline:
            return count, looked, "time"
        found = shares.stamp_of(native)
        if found is shares.UNANSWERED:
            return count, looked, "unreachable"
        looked += 1
        if found is None:
            count["missing"] += 1
        elif found is shares.CANNOT_READ:
            count["unreadable"] += 1
        elif mtime is None or size is None:
            count["unread"] += 1
        elif store_photos.describes(mtime, size, found):
            count["matches"] += 1
        else:
            count["differs"] += 1
    return count, looked, None


def verify(library, name, location, machine, sample=SAMPLE, seconds=None):
    """Whether `location` holds the files of root `name`'s rows: a sample (`sample` rows).

    Returns a dict -- "root", "location", "reachable", "state", "message" (why not, when it cannot be
    reached), "rows" (the root's rows), "checked", "matches", "differs", "unread", "missing",
    "unreadable", "outside" (the rows under no root, by folder: paths.outside_roots), "outside_rows",
    "native_inside", "stopped" (None | "unreachable" | "time"), "poor" and "poor_why", and "summary", a
    sentence. Refused (Refused, NotFound) for a root the library does not have, a location that
    is not an absolute folder on this machine, a map that cannot be read or that the location
    does not fit. `seconds` is how long the look at the location itself is waited for (LIST_SECONDS)."""
    seconds = LIST_SECONDS if seconds is None else seconds
    name = paths.root_name(name)
    if not isinstance(location, str) or not paths.is_native_absolute(location):
        raise Refused("%r is not an absolute folder on this machine: a drive and folder (D:\\Photos) or a "
                      "share (\\\\server\\share\\Photos)." % (location,))
    location = paths.stored(location)
    roots, _found = candidate_roots(library, name, location, machine)
    ours, counts = _read_rows(library, name, roots)
    answer = {"root": name, "location": location, "rows": len(ours) + counts["not_converting"],
              "checked": 0, "matches": 0, "differs": 0, "unread": 0, "missing": 0,
              "unreadable": counts["not_converting"],
              "native_inside": counts["native_inside"],
              "outside": paths.outside_roots(counts["outside"], roots), "outside_rows": len(counts["outside"]),
              "stopped": None}
    reached = reach(location, seconds)
    answer.update(reachable=reached["reachable"], state=reached["state"], message=reached["message"])
    if not reached["reachable"]:
        answer["stopped"] = "unreachable"
        return _verdict(answer)
    found, looked, stopped = _look(_sample(ours, sample), time.monotonic() + SAMPLE_BUDGET)
    answer.update(found, checked=looked, stopped=stopped)
    answer["unreadable"] += counts["not_converting"]
    if stopped == "unreachable":
        answer.update(reachable=False, state="away", message=(
            "This location stopped answering after %d file(s); the rows not looked at are not counted "
            "as missing." % looked))
    return _verdict(answer)


def _verdict(answer):
    """`answer` with whether the result is poor, why (poor_why), and the summary sentence."""
    why = []
    if not answer["reachable"]:
        why.append(answer["message"])
    checked = answer["checked"]
    if checked and answer["missing"] > POOR_MISSING * checked:
        why.append("%d of the %d rows looked at (%d%%) are not there: this looks like another folder, or another "
                   "copy." % (answer["missing"], checked, round(100 * answer["missing"] / checked)))
    if checked and answer["differs"] > POOR_DIFFERS * checked:
        why.append("%d of the %d rows looked at (%d%%) differ from their files (other size or modified time): "
                   "this looks like an older or edited copy. %s" % (
                       answer["differs"], checked, round(100 * answer["differs"] / checked), SYNC_REPLACES))
    if answer["native_inside"]:
        why.append("%d row(s) are held by this machine's own spelling of a place that would be this root's: "
                   "they would be missed, and indexed again as new photos." % answer["native_inside"])
    answer["poor_why"] = why
    answer["poor"] = bool(why)
    if not answer["reachable"] and not checked:
        answer["summary"] = answer["message"]
        return answer
    parts = ["%d match" % answer["matches"], "%d differ (changed since indexed; sync settles them)" % answer["differs"],
             "%d are missing" % answer["missing"]]
    if answer["unread"]:
        parts.append("%d never read by the index (sync reads them)" % answer["unread"])
    if answer["unreadable"]:
        parts.append("%d could not be read" % answer["unreadable"])
    text = "Looked at a sample of %d of %d rows: %s." % (checked, answer["rows"], ", ".join(parts))
    if answer["differs"]:
        text += " " + SYNC_REPLACES
    if answer["stopped"] == "time":
        text += " It ran out of time."
    elif answer["stopped"] == "unreachable":
        text += " " + answer["message"]
    answer["summary"] = text
    return answer
