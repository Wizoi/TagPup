"""Where this machine keeps each root, and moving one: TagTuner's Roots
(docs/ARCHITECTURE.md, "Roots and machines"; tagpup.services.roots_verify for Verify).

A root's rows say `@pictures/2024/a.jpg`; the machine's map (machine_roots.json) says where
`pictures` is. Moving the library to the share, or back, is therefore one edit of the map and
no row, and this is the one place the app makes it:

- `overview` -- each root: its name, the share's address, where this machine keeps it (the first
  place is where files are READ and WRITTEN; the others are where it was, kept so every path is
  still recognised), whether this machine places it at all, how many rows it holds, and the last
  Verify;
- `run_verify` -- Verify of a root at a location, recorded in the library's `job_runs` so the
  Activity page lists it (a sample while a person waits; a full run is a job, tagpup.jobs.verifying);
- `change_location` -- a dry run first (Verify's sample of the new place), then the edit; and
  `back=True`, Change back, the reverse: the first two places swap.

**Never a row.** The edit is the map's alone (`Machine.set_location` / `change_back`), atomic
and one editor at a time (tagpup.config). The caches that hold this machine's paths are rebuilt by
the store's generations the next time they are asked (the map's change moves the salt), and a
connection that sits idle finds the new map within a second.

**What it refuses, as a sentence, writing nothing**: a root the library does not have; a place that
is not an absolute folder here; a folder that is not there (or whose drive or share is not); one
that would put the root inside, or over, another root of the library, where it was not; a result
that is poor -- more than 5% of the rows looked at are missing, or the place cannot be reached --
unless the request says `override`; and any index, Suggest, sync or verify of the library running or
queued (`busy`, which the caller says, and the library's own running jobs, which are read here):
a run that holds the map as it was is pinned to it (tagpup.store.roots.pinned), but a person
moving files under it is told, not left to find out. Two requests at once: the claim of the
change in `job_runs` lets one in and tells the other; a second Confirm after the first, or a tab
that came second and found the map moved, is told so and nothing is repeated.

**Logged**: each change is a run in `job_runs` -- "moved root pictures from X to Y" -- which the
Activity page shows with the others. A refused one is not: nothing happened.
"""
import logging
import time

from tagpup.core import paths
from tagpup.core.result import Conflict, NotFound, Result
from tagpup.store import file_journal, job_runs, root_rows
from tagpup.store import roots as store_roots
from tagpup.services import roots_verify

logger = logging.getLogger(__name__)

#: The names of the runs recorded in the library's job_runs.
VERIFY_JOB = "verify root %s"
MOVE_JOB = "change location of root %s"

#: What a library older than the schema says when asked to claim a run (tagpup.store.job_runs: "behind").
BEHIND = ("This library is older than this version of TagPup: open it with TagPup or the CLI once so that it "
          "updates, then try again.")

#: What TagTuner can and cannot see of what runs, said wherever a move is refused or offered.
SEES_ONLY_ITS_OWN = ("TagTuner sees only its own runs and a Roots verify: before moving a root, stop Suggest in "
                     "TagPup, any index you started from the command line, and a sync.")

#: How to adopt a root, which is all a library with none can do (the CLI).
ADOPT_HINT = ('TagPup CLI.cmd --db <library> roots adopt --name pictures --address "\\\\server\\share\\Pictures" '
              '--location "D:\\where\\the\\photos\\are"   (a dry run; add --apply to convert)')


def _library_roots(library):
    try:
        return {entry["name"]: entry for entry in store_roots.listing(library.path)}
    except Exception as problem:
        raise NotFound("The library cannot be read: %s" % problem) from None


def require_root(library, name):
    """The root's name as the library spells it; NotFound when the library has no such root."""
    folded = paths.root_name(name)
    if folded not in _library_roots(library):
        raise NotFound("This library has no root called %s." % folded)
    return folded


def last_verify(library, name):
    """What the last Verify of root `name` found, as counts and when, or None: {"when", "mode",
    "location", "outcome", "checked", "matches", "differs", "missing", "unreadable", "rows",
    "not_in_library"} (a key absent when the run did not count it)."""
    found = job_runs.runs(library.path, VERIFY_JOB % name, 1)
    if not found:
        return None
    run = found[0]
    keep = ("mode", "location", "checked", "matches", "differs", "unread", "missing", "unreadable", "rows", "not_in_library",
            "partial", "marked", "marks_match", "marks_differ", "marks_unmarked")
    return dict({key: run.changed[key] for key in keep if key in run.changed},
                when=run.finished or run.started, outcome=run.outcome)


def _writes_to(places):
    if not places:
        return None
    return "Tags and renames are written to files at %s." % places[0]


def sharing(library, name, others):
    """The libraries among `others` that also hold a root called `name`: the machine's map is one for
    all of them, so moving the root moves it for each. Libraries that cannot be read are left out."""
    found = []
    for other in others:
        if paths.key(other.path) == paths.key(library.path):
            continue
        try:
            if any(entry["name"] == name for entry in store_roots.listing(other.path)):
                found.append(other)
        except Exception as problem:
            logger.info("Could not read the roots of %s: %s", other.name, problem)
    return found


def overview(library, machine, others=()):
    """Each root of the library with where this machine keeps it: {"roots": [{"name", "address",
    "added", "places", "active", "previous", "writes_to", "writes_to_place", "mapped", "rows",
    "last_verify", "shared_with"}], "map", "adopt_hint", "problem"}. `writes_to_place` is the folder files are written to (the page
    compares it with `active` as paths; the server owns which it is). `shared_with` is the names of the libraries among
    `others` that hold a root of that name (the map is one for them all). `problem` is the sentence
    when the map cannot be read; the roots are then listed without places. Reads only."""
    found = _library_roots(library)
    answer = {"library": library.name, "map": machine.path(), "roots": [], "adopt_hint": ADOPT_HINT, "problem": None}
    try:
        placed = machine.roots()
    except ValueError as problem:
        placed = {}
        answer["problem"] = str(problem)
    for name, entry in sorted(found.items()):
        places = list(placed.get(name, ()))
        try:
            rows = root_rows.count(library.path, name) if places else None
        except ValueError:
            rows = None
        answer["roots"].append({
            "name": name, "address": entry["address"], "added": entry["added"], "places": places,
            "active": places[0] if places else None, "previous": places[1] if len(places) > 1 else None,
            "writes_to": _writes_to(places), "writes_to_place": places[0] if places else None, "mapped": bool(places), "rows": rows,
            "last_verify": last_verify(library, name),
            "shared_with": [other.name for other in sharing(library, name, others)]})
    return answer


def _places(machine, name):
    return list(machine.roots().get(name, ()))


def _running_here(library):
    """The sentences for what is running in the library according to its own job runs -- any
    process's -- and not ended: a sync, a verify, a scheduled job. A run whose process has ended
    is not counted (it is abandoned when the next one claims)."""
    said = []
    for (job, _for), (last, _ended) in job_runs.latest(library.path).items():
        if last is None or last.outcome != job_runs.RUNNING or job.startswith(MOVE_JOB % ""):
            continue
        if last.owner and not file_journal.owner_alive(last.owner):
            continue
        said.append("a verify of root %s is running" % job[len(VERIFY_JOB % ""):] if job.startswith(VERIFY_JOB % "")
                    else "the %s job is running" % job)
    return said


def begin_verify(library, name, now=time.time):
    """Claim the run of a Verify of root `name` in the library's job_runs: a second Verify of the
    root, in this process or another, is told one is under way (Conflict). The claim, to give
    run_verify."""
    name = paths.root_name(name)
    claim = job_runs.claim(library.path, VERIFY_JOB % name, library.name, now())
    if not claim:
        if claim.why == "behind":
            raise Conflict(BEHIND)
        raise Conflict("A verify of %s is under way already; wait for it, or cancel it." % name)
    return claim


def run_verify(library, name, location, machine, full=False, cancel=None, progress=None, budget=None,
               seconds=None, now=time.time, claim=None):
    """roots_verify.verify, recorded as a run of the library (job_runs) the Activity page lists:
    claimed first (`claim`, from begin_verify, or here) and ended with its counts, `done` when it
    looked at what it was asked to, `failed` when the location could not be reached or it ran out
    of time. Returns verify's dict."""
    name = paths.root_name(name)
    claim = claim or begin_verify(library, name, now)
    answer = None
    try:
        answer = roots_verify.verify(library, name, location, machine, full=full, cancel=cancel, progress=progress,
                                     budget=budget, seconds=seconds)
    except BaseException as problem:
        job_runs.finish(library.path, claim.run_id, job_runs.FAILED, now(), {"what": "verify of root %s refused" % name},
                        str(problem))
        raise
    counts = {key: answer[key] for key in ("rows", "checked", "matches", "differs", "unread", "missing", "unreadable",
                                           "folders") if key in answer}
    if answer["not_in_library"] is not None:
        counts["not_in_library"] = answer["not_in_library"]
    if answer.get("markers"):
        marks = answer["markers"]
        counts.update(marked=marks["checked"], marks_match=marks["match"], marks_differ=marks["differs"],
                      marks_unmarked=marks["unmarked"])
    counts.update(mode=answer["mode"], location=answer["location"], partial=int(answer["partial"]),
                  what="verified root %s at %s (%s): %s" % (name, answer["location"],
                                                            "all" if full else "sample", answer["summary"]))
    failed = answer["stopped"] in ("unreachable", "time")
    job_runs.finish(library.path, claim.run_id, job_runs.FAILED if failed else job_runs.DONE, now(), counts,
                    answer["message"] or answer["summary"] if failed else None)
    return answer


def _relation(path, other):
    if paths.key(path) == paths.key(other):
        return "same"
    if paths.is_under(path, other):
        return "inside"
    if paths.is_under(other, path):
        return "over"
    return None


def _overlap(library_names, name, current, candidate, machine):
    """A sentence when `candidate` would put root `name` inside, or over, another root of the
    library where the current place is not -- the same files would then be reachable under two
    roots, and a path would be held by the one and looked up by the other -- else None."""
    for other, places in machine.roots().items():
        if other == name or other not in library_names:
            continue
        for place in places:
            now_ = _relation(current, place) if current else None
            then = _relation(candidate, place)
            if then is not None and then != now_:
                return ("%s is %s %s, a place of the root %s: the same photos would be reachable under two roots, "
                        "and the library would look for each in the wrong one." % (
                            candidate, {"same": "the same place as", "inside": "inside", "over": "over"}[then],
                            place, other))
    return None


def change_location(library, name, location, machine, apply=False, override=False, expected=None, busy=None,
                    back=False, now=time.time, budget=roots_verify.SAMPLE_BUDGET, seconds=None, others=()):
    """Move root `name` to `location` on this machine -- or, with `back`, to the place it was before
    (`location` is then ignored). A dry run unless `apply`: details["verify"] says what a sample of
    the place holds, details["would_refuse"] why it would be refused. `override` accepts a poor
    result. `expected` is the place the caller saw the root at: the map having moved since
    refuses (another tab, a hand edit). `busy()` returns the sentences for what the caller knows is
    running or queued for the library (and for those among `others` that hold the root too, whose runs
    the map's move would reach just the same: the map is the machine's, one for all of them). A Result: `changed` 1 when the map was written, 0 when
    the root was at that place already (details["unchanged"]); `refused` with the sentence when it
    was not, nothing written."""
    result = Result(details={"dry_run": not apply, "root": name, "back": back})
    try:
        name = paths.root_name(name)
        found = _library_roots(library)
        result.details["shared_with"] = [other.name for other in sharing(library, name, others)]
        if name not in found:
            raise NotFound("This library has no root called %s." % name)
        if machine.set_location is None:
            result.refuse("This program does not move roots.")
            return result
        places = _places(machine, name)
    except NotFound:
        raise
    except ValueError as problem:
        result.refuse(str(problem))
        return result
    result.details.update(root=name, places=places, current=places[0] if places else None)
    if back:
        if len(places) < 2:
            result.refuse("%s has no previous place on this machine to go back to." % name)
            return result
        location = places[1]
    if not isinstance(location, str) or not paths.is_native_absolute(location):
        result.refuse("%r is not an absolute folder on this machine: a drive and folder (D:\\Photos) or a share "
                      "(\\\\server\\share\\Photos)." % (location,))
        return result
    location = paths.stored(location)
    result.details["location"] = location
    if places and paths.key(places[0]) == paths.key(location):
        result.details["unchanged"] = True
        result.details["message"] = "%s is at %s already; nothing to change." % (name, location)
        return result

    reached = roots_verify.reach(location, seconds)
    if reached["state"] in ("no_drive", "no_folder", "unreadable"):
        result.details["reach"] = reached
        if back:
            result.refuse("The previous place, %s, is not there any more. Nothing was changed. Choose a place with "
                          "Change location." % location)
        else:
            result.refuse("Not changed. " + reached["message"])
        return result
    try:
        checked = roots_verify.verify(library, name, location, machine, budget=budget, seconds=seconds)
    except (NotFound, ValueError) as problem:
        result.refuse(str(problem))
        return result
    result.details["verify"] = checked
    try:
        clash = _overlap(found, name, places[0] if places else None, location, machine)
    except ValueError as problem:
        result.refuse(str(problem))
        return result
    if clash:
        result.refuse("Not changed. " + clash)
        return result
    if checked["poor"] and not override:
        why = " ".join(checked["poor_why"])
        result.details["would_refuse"] = why
        result.refuse("Not changed: %s Say override to change it anyway." % why)
        return result
    result.details["writes_to"] = _writes_to([location])
    if not apply:
        return result

    running = list(busy() if busy is not None else []) + _running_here(library)
    # The map is one for every library that holds the root: a run in one of them is held up too.
    for other in sharing(library, name, others):
        running += ["in %s, which uses this root too, %s" % (other.name, said) for said in _running_here(other)]
    if running:
        result.details["conflict"] = True
        result.refuse("Not changed: %s. Wait for it to finish or cancel it, then try again. %s" % (
            "; ".join(running), SEES_ONLY_ITS_OWN))
        return result
    job = MOVE_JOB % name
    started = now()
    claim = job_runs.claim(library.path, job, library.name, started)
    if not claim:
        result.details["conflict"] = True
        result.refuse("Not changed: " + (BEHIND if claim.why == "behind" else
                                         "another change of %s is under way; try again in a moment." % name))
        return result
    try:
        if back:
            written = machine.change_back(name, expected=expected)
        else:
            written = machine.set_location(name, location, expected=expected, must_exist=False)
    except ValueError as problem:
        job_runs.finish(library.path, claim.run_id, job_runs.FAILED, now(),
                        {"what": "change of location of root %s refused" % name}, str(problem))
        result.details["conflict"] = "changed since you looked" in str(problem)
        result.refuse(str(problem))
        return result
    except BaseException as problem:
        job_runs.finish(library.path, claim.run_id, job_runs.FAILED, now(), {}, "%s: %s" % (type(problem).__name__, problem))
        raise
    result.attempted = 1
    result.details["places"] = written["places"]
    if not written["changed"]:
        job_runs.finish(library.path, claim.run_id, job_runs.DONE, now(),
                        {"what": "change of location of root %s: it was at %s already" % (name, location)})
        result.details["unchanged"] = True
        result.details["message"] = "%s is at %s already; nothing to change." % (name, location)
        return result
    before = written["previous"][0] if written["previous"] else None
    what = "%s root %s %s%s" % ("moved back" if back else "moved", name, "to " + written["places"][0],
                                " (was %s)" % before if before else "")
    job_runs.finish(library.path, claim.run_id, job_runs.DONE, now(),
                    {"what": what, "rows": checked["rows"], "checked": checked["checked"], "missing": checked["missing"],
                     "override": int(bool(override and checked["poor"]))})
    result.changed = 1
    logger.info("%s in %s", what, library.name)
    result.details["message"] = what
    return result
