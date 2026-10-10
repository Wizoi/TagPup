"""A library's roots: listing them, and the explicit command that adopts one
(docs/ARCHITECTURE.md, "Roots and machines").

A library holds a photo's path as a root's name and the path under it, so that the library
does not say where a machine keeps its photos; each machine says where it keeps each root
(machine_roots.json, tagpup.config). Opening a library never converts it. `adopt` does, when
the owner says so, and is a dry run unless told to apply:

- The dry run counts, for each table, the rows it would convert, the rows under no root
  (grouped by folder: they keep their native path and are never guessed at), the rows
  whose conversion raises or would not convert back, the settings it would rewrite, and why
  it would be refused. Nothing is written, nothing migrated.
- `apply` writes the machine's map if it lacks the root (atomically, one editor at a time,
  only then), then, in one transaction under the library's write lock, takes a new backup of
  the library, converts every table (and re-roots the rows of an outer root that lie under a
  root nested inside it),
  re-verifies before it commits (row counts equal, every rooted row converts back, the map
  places the root where the rows were converted by), and records one journaled change --
  `roots adopt: <name>` -- that History lists and `undo` reverses, converting back by the
  machine's map.

It refuses, and writes nothing to the library: a root of that name already, a location that is
not there or that no row lies under, a location the map places the root elsewhere than, a
conversion that is not reversible for some row, two rows that would become one, another
process holding the library's write lock, an unfinished change of photo files.

The machine's map is the entry point's to hand in (`Machine`): this layer does not import
tagpup.config.
"""
import contextlib
import functools
import inspect
import os
from dataclasses import dataclass
from typing import Callable

from tagpup.core import paths
from tagpup.core.result import NotFound, Refused, Result
from tagpup.store import adoption, db, schema
from tagpup.store import roots as store_roots


@dataclass(frozen=True)
class Machine:
    """What an entry point hands in of this machine: `roots()` the map as {name: (locations)},
    `add(name, location)` writes the root into it (True if written, False if it already places
    the root there), `path()` where the file is. tagpup.config's machine_roots,
    add_machine_root and machine_roots_path. `set_location(name, location, expected=, must_exist=)`
    and `change_back(name, expected=)` move a root it already places (tagpup.config; TagTuner's
    Roots, tagpup.services.roots_location); none where the entry point does not move roots."""
    roots: Callable
    add: Callable
    path: Callable
    set_location: Callable = None
    change_back: Callable = None


#: The library's roots were changed, by another process, while a run held them: what a pinned run
#: raises. Re-exported for the entry points, which stop cleanly on it.
RootsChanged = store_roots.RootsChanged

#: The exit code of a process that stopped for it (EX_TEMPFAIL: try again), which the process that
#: started it -- the server's index queue -- turns into a sentence.
EXIT_ROOTS_CHANGED = 75

#: What the owner is told when a run stops for it.
STOPPED = ("The library's roots were changed by another process while this ran, so it stopped: what it would "
           "write from here would be spelled by roots the library no longer has. Nothing was written wrongly; "
           "start it again.")


class Unplaced(Refused):
    """A run asked for the library's roots and this machine does not place one (or its map cannot
    be read): the message names machine_roots.json and the line to add."""


@contextlib.contextmanager
def pinned(library):
    """Hold the library's Roots, and this machine's map as it is now, for a whole run -- an index
    run, a sync pass, a change of photo files: every path of it is converted by one map however
    often the map is edited meanwhile (a Change location is refused while a run is under way, and a
    connection that opened later would otherwise convert by the new map half-way). A change of the
    library's own roots by another process stops the run: RootsChanged, which the entry point
    answers with STOPPED.

    A library that is not there, or has no roots, holds nothing. Unplaced (a Refused, the sentence
    naming machine_roots.json) when the library holds a root this machine does not place: the run
    could not name one photo's file."""
    if not os.path.exists(library.path):
        yield None
        return
    stack = contextlib.ExitStack()
    try:
        roots = stack.enter_context(store_roots.pinned(library.path))
    except ValueError as why:   # paths.RootsError, config.MachineMapError
        raise Unplaced(str(why)) from None
    with stack:
        if roots is not None and roots.unmapped:
            raise Unplaced(roots.what_to_add(roots.unmapped[0]))
        yield roots


def stopped(why):
    """The sentence for a run that did not run, or stopped: `why` a RootsChanged or an Unplaced."""
    return STOPPED if isinstance(why, RootsChanged) else str(why)


def _roots_of(library):
    """The Roots a path of the library is canonicalised by, None when it has none or this machine
    cannot say (the gate and `problem` tell the owner; a path is then left as given)."""
    try:
        roots = store_roots.roots_for(library)
    except Exception:
        return None
    return None if roots.identity else roots


def canonical(library, path):
    """`path` spelled by the first place of its root (paths.canonical): THE boundary an old
    place's spelling is resolved at, so every file operation of a service is at the place the
    owner was told writes go to, and the page's next answer names it."""
    return paths.canonical(path, _roots_of(library)) if path else path


def canonicaliser(library):
    """A function that spells a path by the first place of its root (paths.canonical), made once from
    the library's roots -- for an ingress that resolves many -- or None for a library that has none
    or whose roots this machine cannot place (the gate tells the owner)."""
    roots = _roots_of(library)
    if roots is None:
        return None

    def canonical_of(path):
        return paths.canonical(path, roots)

    canonical_of.roots = roots
    return canonical_of


def old_place_sentence(library, original, canonical_path):
    """Why a folder typed in an old place's spelling cannot be used: it does not exist at the first place.
    Names the root, the previous place `original` is under and the first place `canonical_path` is."""
    roots = _roots_of(library)
    found = roots.locate(original) if roots is not None else None
    if found is None:
        return "%s is not a folder." % original
    name = found[0]
    previous = next((place for place in roots.locations.get(name, ())[1:]
                     if paths.same(original, place) or paths.is_under(original, place)), "its previous place")
    first = roots.locations[name][0]
    return ("%s is under %s, the previous place of root %s, and does not exist at its current place (%s) as %s. "
            "Nothing was done. Use the folder at the current place." % (original, previous, name, first, canonical_path))


def _one(each, roots):
    """A path, or a (path, ...) tuple whose first is one (a suggestion's write)."""
    if isinstance(each, str):
        return paths.canonical(each, roots)
    if isinstance(each, (tuple, list)) and each and isinstance(each[0], str):
        return type(each)([paths.canonical(each[0], roots), *each[1:]])
    return each


def canonical_args(*names, both=()):
    """Decorator for a service whose first argument is the library: the arguments `names` -- a
    path, a list of paths, or a dict keyed by paths -- are resolved by the first place of their
    root before the service sees them; those in `both` are dicts of path to path (renames), whose
    values are resolved too. A library with no roots is not touched, and costs one read of its
    roots table."""
    def decorate(function):
        signature = inspect.signature(function)

        @functools.wraps(function)
        def run(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            roots = _roots_of(bound.arguments.get(next(iter(signature.parameters))))
            if roots is not None:
                for name in (*names, *both):
                    given = bound.arguments.get(name)
                    if isinstance(given, str):
                        bound.arguments[name] = paths.canonical(given, roots)
                    elif isinstance(given, dict):
                        both_ways = name in both
                        bound.arguments[name] = {
                            paths.canonical(key_, roots): (paths.canonical(value, roots)
                                                           if both_ways and isinstance(value, str) else value)
                            for key_, value in given.items()}
                    elif isinstance(given, (list, tuple, set)):
                        bound.arguments[name] = [_one(each, roots) for each in given]
            return function(*bound.args, **bound.kwargs)
        return run
    return decorate


def listing(library, machine=None):
    """The library's roots: [{"name", "address", "added", "locations", "mapped"}], where this
    machine keeps each (`machine`, the map; none when there is none to ask). Reads only."""
    found = store_roots.listing(library.path) if _there(library) else []
    placed = machine.roots() if machine is not None else {}
    return {"roots": [dict(entry, locations=list(placed.get(entry["name"], ())),
                           mapped=entry["name"] in placed) for entry in found],
            "map": machine.path() if machine is not None else None}


def _there(library):
    if not os.path.exists(library.path):
        raise NotFound("There is no library at %s." % library.path)
    return True


def _places(name, location, machine, result):
    """(the places this machine keeps `name` at, whether the map lacks it) -- or None, with
    the result refused, when what the owner named cannot be."""
    try:
        placed = machine.roots()
    except ValueError as problem:
        result.refuse(str(problem))
        return None
    if not paths.is_native_absolute(location):
        result.refuse("The location %r is not an absolute folder on this machine." % location)
        return None
    if name in placed:
        if not any(paths.key(location) == paths.key(place) for place in placed[name]):
            result.refuse("The map (%s) already places %s at %s, not at %s: it is not changed here. Name that place, "
                          "or edit the file to list the new one beside it." % (
                              machine.path(), name, ", ".join(placed[name]), location))
            return None
        return list(placed[name]), False
    return [location], True


def adopt(library, name, address, location, machine, apply=False):
    """Adopt root `name` -- `address` the share's own address, `location` where this machine
    keeps it -- for the library, converting its paths. A dry run unless `apply`
    (details["rehearsal"]); applied, `changed` is the rows converted, details["change"] the
    journal's change (undoable) and details["backup"] the copy taken first. Refused (a Result
    with `refused`), with nothing written to the library, for any reason in the module's
    description."""
    result = Result(details={"dry_run": not apply})
    try:
        name = paths.root_name(name)
    except paths.RootsError as problem:
        result.refuse(str(problem))
        return result
    try:
        _there(library)
    except NotFound as problem:
        result.refuse(str(problem))
        return result
    found = _places(name, location, machine, result)
    if found is None:
        return result
    places, map_lacks = found
    try:
        report = adoption.rehearse(library.path, name, address or "", places)
    except (adoption.Refused, paths.RootsError, ValueError) as problem:
        result.refuse(str(problem))
        return result
    result.details["rehearsal"] = report
    result.details["map"] = {"file": machine.path(), "would_write": map_lacks}
    result.attempted = report["tables"].get("photos", {}).get("rows", 0)
    if report["refused"]:
        result.refuse("; ".join(report["refused"]))
        return result
    if not apply:
        return result
    return _apply(library, name, address or "", location, places, map_lacks, machine, result)


def repair_addresses(library, apply=False):
    """Give each root whose stored address lost a leading backslash (a share's address
    written with one, #914) its two, as one journaled change `undo` reverses. A dry run unless
    `apply`: details["repairs"] is [{"name", "was", "now"}] either way, `changed` the roots
    written. Refused, nothing written, when the repaired addresses would nest or another process
    holds the write lock. Needs no backup: it changes one text column and the journal holds the
    way back. A run in another process that holds the library's roots (an index, a sync, in any app)
    stops with RootsChanged when it commits, so it is to be run with TagPup and TagTuner stopped."""
    result = Result(details={"dry_run": not apply})
    try:
        _there(library)
    except NotFound as problem:
        result.refuse(str(problem))
        return result
    todo = adoption.address_repairs(library.path)
    result.attempted = len(todo)
    result.details["repairs"] = [{"name": name, "was": was, "now": now} for name, was, now in todo]
    if not apply or not todo:
        return result
    try:
        done = adoption.repair_addresses(library.path)
    except (adoption.Refused, paths.RootsError, ValueError) as problem:
        result.refuse("Nothing was written: %s" % problem)
        return result
    result.changed = len(done)
    result.details["repairs"] = [{"name": name, "was": was, "now": now} for name, was, now in done]
    return result


def _apply(library, name, address, location, places, map_lacks, machine, result):
    if map_lacks:
        try:
            result.details["map"]["written"] = bool(machine.add(name, location))
        except ValueError as problem:
            result.refuse("Nothing was written to the library: the machine's map could not be written: %s" % problem)
            return result
    try:
        report = adoption.adopt(library.path, name, address, places)
    except (adoption.Refused, paths.RootsError, ValueError) as problem:
        result.refuse("Nothing was written to the library: %s%s" % (
            problem, " (The machine's map now lists the root; that is harmless to a library without it.)"
            if result.details["map"].get("written") else ""))
        return result
    result.details["adopted"] = report
    result.details["backup"] = report["backup"]
    result.details["change"] = report["change"]
    result.changed = sum(counts["convert"] for counts in report["tables"].values())
    return result


def check(library):
    """What is wrong with the library's roots, as counts: [] when nothing, else a sentence for
    each -- first, when this machine's map cannot place a root, that, naming the file and the
    line to add. Reads only; tagpup.store.checks is the doctor's account of the same."""
    _there(library)
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        if not store_roots.has_table(conn):
            return []
        try:
            return adoption.verify(conn, store_roots.roots_for(conn))
        except ValueError as problem:   # paths.RootsError, config.MachineMapError
            return [str(problem)]
    finally:
        conn.close()


def problem(library):
    """Why the library's photos cannot be read on this machine, or None: it holds a root and
    this machine's map (machine_roots.json) is unreadable or does not place it. The message
    names the file and the line to add. What an entry point asks when it opens a library, so
    that the owner is told at once and not by the first photo that fails to open -- the store
    refuses every path of such a library either way (paths.UnmappedRoot); it is never read as
    an empty one."""
    if not os.path.exists(library.path):
        return None
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        if not store_roots.has_table(conn):
            return None
        try:
            store_roots.roots_for(conn)
        except ValueError as why:   # paths.RootsError, config.MachineMapError
            return str(why)
        for name in store_roots.logical(conn):
            if name not in store_roots.roots_for(conn).locations:
                return store_roots.roots_for(conn).what_to_add(name)
        return None
    finally:
        conn.close()


class SandboxError(Refused):
    """A measurement sandbox that cannot be made safe: a root of its library copy it did not place,
    a place outside it, a map that is not its own."""


def unplaced(library, machine):
    """The names of the library's roots that `machine`'s map does not place: what a sandbox that
    runs the library's copy must find empty before it starts a server."""
    placed = machine.roots()
    return [entry["name"] for entry in store_roots.listing(library.path) if not placed.get(entry["name"])]


def place_in_sandbox(library, sandbox, machine, folder_for=None):
    """Make a copy of `library`, run in the folder `sandbox` as its TAGPUP_HOME, safe: write the
    sandbox's OWN machine map (`machine`, whose file must lie in `sandbox`) placing each root of the
    copy at `folder_for(name)` -- by default `sandbox/roots/<name>`, made empty -- and never at the
    real photos. A converted library copy would otherwise point, through the machine's map, at the
    real files: a sandbox server would read them, and a write it made (a tag, a rename) would reach
    the owner's photos. Returns {root name: its place}; {} for a library with no roots, which writes
    no map.

    SandboxError, nothing written to the real map, for a map file outside the sandbox, a place
    outside it, and -- after placing -- any root of the copy the map does not place."""
    if not paths.is_under(machine.path(), sandbox):
        raise SandboxError("the sandbox's machine map is %s, which is not inside the sandbox (%s): placing "
                           "roots there would change the real map" % (machine.path(), sandbox))
    placed = {}
    for entry in store_roots.listing(library.path):
        name = entry["name"]
        place = folder_for(name) if folder_for is not None else os.path.join(sandbox, "roots", name)
        if not paths.is_under(place, sandbox):
            raise SandboxError("root %s would be placed at %s, outside the sandbox (%s): the copy would reach "
                               "real photos" % (name, place, sandbox))
        placed[name] = paths.stored(place)
    for name, place in placed.items():
        os.makedirs(place, exist_ok=True)
        machine.add(name, place)
    missing = unplaced(library, machine)
    if missing:
        raise SandboxError("the sandbox's map (%s) does not place %s: its copy of the library would refuse every "
                           "photo, or reach the real ones" % (machine.path(), ", ".join(missing)))
    return placed


def pending(library):
    """The migrations the library has not had (it needs migration 18 to adopt a root)."""
    return [m.name for m in schema.pending(library.path)]
