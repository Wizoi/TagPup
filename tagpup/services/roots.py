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
  only then), then, in one transaction under the library's write lock, takes the library's
  backup (the copy made within the last quarter hour covers it), converts every table,
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
import os
from dataclasses import dataclass
from typing import Callable

from tagpup.core import paths
from tagpup.core.result import NotFound, Result
from tagpup.store import adoption, db, schema
from tagpup.store import roots as store_roots


@dataclass(frozen=True)
class Machine:
    """What an entry point hands in of this machine: `roots()` the map as {name: (locations)},
    `add(name, location)` writes the root into it (True if written, False if it already places
    the root there), `path()` where the file is. tagpup.config's machine_roots,
    add_machine_root and machine_roots_path."""
    roots: Callable
    add: Callable
    path: Callable


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


def pending(library):
    """The migrations the library has not had (it needs migration 18 to adopt a root)."""
    return [m.name for m in schema.pending(library.path)]
