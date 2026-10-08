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
- **never read**: the file is there and the row has no stamp -- Suggest made it for a photo the index
  never read; sync reads it, and it is not "changed since indexed";
- **unreadable**: the disk refused the folder or the file's stamp.

**Markers.** A library that has marked folders (tagpup.services.folder_ids; `folder_ids` rows) also has the
marker read (`.tagpup`, tagpup.files.folder_marker) in each of the root's marked folders AT THE CANDIDATE, the
row's `@name/rel` turned into a path by the same Roots built here, and counted: **matches** (the marker holds
this library's entry for the id the row has), **differs** (it holds this library's entry for another id, or only
other libraries' -- likely a different folder at that path), **unmarked** (no marker: the library predates
marking, or the folder is new), **malformed** (a file of that name that is not a marker, hand-edited: counted and
never a reason to refuse), **unreadable** (the disk refused) and **not there** (the folder is not at the
candidate). A sample reads at most MARKER_SAMPLE of them, each folder once, within MARKER_BUDGET; a full run all
of them, with progress and cancel. Each read is made on the bounded thread as a listing is, so a share that does
not answer stops it as "unreachable". A library with no `folder_ids` rows for the root (not marked, or behind
migration 26) has `markers` None and nothing is said. Markers that DIFFER make the result poor (so Change
location refuses it unless the owner overrides); ones that are absent, malformed or unreadable never do.

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
import functools
import logging
import os
import random
import time

from tagpup.core import paths
from tagpup.core.result import NotFound, Refused
from tagpup.files import folder_marker, images, shares
from tagpup.store import folder_ids as store_folder_ids
from tagpup.store import photos as store_photos
from tagpup.store import root_rows
from tagpup.store import roots as store_roots

logger = logging.getLogger(__name__)

#: Rows a sample takes.
SAMPLE = 2000

#: How long one look at the disk -- a folder, the location itself -- is waited for, in seconds,
#: and how long a share that did not answer in time is taken as away.
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

#: Marked folders a sample reads the marker of, and how long it may spend on them.
MARKER_SAMPLE = 300
MARKER_BUDGET = 10.0

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


# ---- The markers --------------------------------------------------------------------------------

def _marked_folders(library, name, roots):
    """(the library's identifier, [(folder id, folder at the candidate)] of root `name`'s marked folders, the
    rows that do not convert). The identifier is None, and the list empty, for a library that has marked
    nothing or is behind migration 26: nothing to say then."""
    library_id, held = store_folder_ids.held(library.path)
    if not library_id:
        return None, [], 0
    mark, sep = paths.ROOT_MARK, paths.ROW_SEP
    ours, bad, seen = [], 0, set()
    for folder_id, path in held:
        if not path.startswith(mark) or path[1:].split(sep, 1)[0].lower() != name:
            continue
        try:
            folder = paths.from_row(path, roots)
        except paths.RootsError:
            bad += 1
            continue
        key = paths.key(folder)
        if key not in seen:
            seen.add(key)
            ours.append((folder_id, folder))
    return library_id, ours, bad


def _marker_state(folder, folder_id, library_id):
    """What the marker of `folder` says of `folder_id`: "match" | "differs" | "unmarked" | "malformed" |
    "unreadable" | "not_there". One stat and one read; OSError (a folder the disk refuses) escapes."""
    try:
        os.stat(paths.stored(folder))
    except (FileNotFoundError, NotADirectoryError):
        return "not_there"
    marker = folder_marker.read(folder)
    if marker.state == folder_marker.ABSENT:
        return "unmarked"
    if marker.state == folder_marker.MALFORMED:
        return "malformed"
    if marker.state == folder_marker.UNREADABLE:
        return "unreadable"
    return "match" if marker.entry_of(library_id) == folder_id else "differs"


class _Marks:
    """What the reading of markers has counted."""
    STATES = ("match", "differs", "unmarked", "malformed", "unreadable", "not_there")

    def __init__(self, rows, unreadable=0):
        self.rows = rows
        self.counts = dict.fromkeys(self.STATES, 0)
        self.counts["unreadable"] = unreadable
        self.checked = 0
        self.partial = False

    def line(self):
        """One line for a person; never a folder's name."""
        counts = self.counts
        text = "%d of %d marked folders match; %d differ; %d not marked" % (
            counts["match"], self.checked, counts["differs"], counts["unmarked"])
        for key, said in (("malformed", "%d with a marker that is not readable as one"),
                          ("unreadable", "%d could not be read"), ("not_there", "%d not there")):
            if counts[key]:
                text += "; " + said % counts[key]
        return text

    def answer(self):
        return dict(self.counts, rows=self.rows, checked=self.checked, partial=self.partial, line=self.line())


def _marker_run(location, folders, library_id, marks, cancel, progress, deadline, seconds, tally):
    """Read the marker of each of `folders` [(folder id, folder)] once, on the bounded thread; stops for a cancel
    or a share that does not answer (tally.stopped), or the deadline (marks.partial)."""
    for folder_id, folder in folders:
        if cancel():
            tally.stopped = "cancelled"
            return
        if deadline is not None and time.monotonic() >= deadline:
            marks.partial = True
            return
        state, value = _bounded(location, functools.partial(_marker_state, folder, folder_id, library_id), seconds)
        if state == "away":
            tally.stopped = "unreachable"
            return
        marks.counts["unreadable" if state == "error" else value] += 1
        marks.checked += 1
        progress(tally.checked, marks.rows, tally.folders + marks.checked)


def _markers(library, name, roots, location, full, tally, cancel, progress, budget, seconds):
    """The markers of the root's marked folders at `location`, as `_Marks.answer()`; None when the library has no
    marked folder of the root."""
    library_id, folders, bad = _marked_folders(library, name, roots)
    if not folders and not bad:
        return None
    marks = _Marks(len(folders) + bad, unreadable=bad)
    marks.checked = bad
    if not full and len(folders) > MARKER_SAMPLE:
        chosen = sorted(folders, key=lambda each: paths.key(each[1]))
        random.Random(len(chosen)).shuffle(chosen)
        folders = chosen[:MARKER_SAMPLE]
    deadline = None if budget is None or full else time.monotonic() + min(budget, MARKER_BUDGET)
    _marker_run(location, folders, library_id, marks, cancel, progress, deadline, seconds, tally)
    return marks.answer()


# ---- The run -----------------------------------------------------------------------------------

class _Tally:
    """What a run has counted."""

    def __init__(self):
        self.matches = self.differs = self.missing = self.unreadable = self.unread = 0
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
        elif row[2] is None or row[3] is None:
            self.unread += 1
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
    "markers" (None, or the counts of the root's marked folders' markers: "rows", "checked", "match", "differs", "unmarked",
    "malformed", "unreadable", "not_there", "partial" and "line", a sentence; see the module), "stopped" (None | "cancelled" | "unreachable" | "time"), "poor" and "poor_why", and "summary", a
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
              "checked": 0, "matches": 0, "differs": 0, "unread": 0, "missing": 0,
              "unreadable": counts["not_converting"],
              "folders": 0, "not_in_library": None, "markers": None, "other_rows": counts["other_roots"],
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
    if not tally.stopped:
        answer["markers"] = _markers(library, name, roots, location, full, tally, cancel, progress, budget, seconds)
    answer.update(checked=tally.checked, matches=tally.matches, differs=tally.differs, unread=tally.unread, missing=tally.missing,
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


#: What sync does with a row whose file differs, said wherever a count of them is.
SYNC_REPLACES = ("Sync re-reads those rows from the files there, so a tag newer in a row than in its file "
                 "is replaced by the file's.")


def _conclude(answer, total):
    """The verdict: whether the result is poor, why, and the summary sentence."""
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
    marks = answer.get("markers")
    if marks and marks["differs"]:
        why.append("%d of the %d marked folders looked at carry the marker of a different folder: this looks like "
                   "another folder at that place." % (marks["differs"], marks["checked"]))
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
    if answer["unread"]:
        parts.append("%d never read by the index (sync reads them)" % answer["unread"])
    if answer["unreadable"]:
        parts.append("%d could not be read" % answer["unreadable"])
    text = "Looked at %s: %s." % (kind, ", ".join(parts))
    if answer["differs"]:
        text += " " + SYNC_REPLACES
    if answer["not_in_library"]:
        text += " %d photo(s) at the location have no row." % answer["not_in_library"]
    if answer.get("markers"):
        text += " " + answer["markers"]["line"] + "."
    if answer["stopped"] == "cancelled":
        text += " Cancelled before it finished."
    elif answer["stopped"] == "time":
        text += " It ran out of time; run All for the rest."
    elif answer["stopped"] == "unreachable":
        text += " " + answer["message"]
    return text
