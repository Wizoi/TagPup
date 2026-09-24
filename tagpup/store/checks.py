"""What a library holds, and whether it keeps its own rules: read-only, counts first.

tools/doctor.py reports these, scripts/verify_workflow.py compares them before and after
a run, and the MCP phase (ARCHITECTURE.md) is to answer them as tools. Each check returns
how many rows break the rule and a few of them, so a report can say how much without
saying whose.
"""
import collections
import json
import os

from tagpup.core import paths
from tagpup.store import generations, schema

#: One rule and what breaks it. `examples` are paths, ids or tags, a few at most.
Check = collections.namedtuple("Check", "name count examples")

#: How many of the rows that break a rule a check keeps to show.
EXAMPLES = 5


def summary(conn):
    """{photos, faces, named, manual, excluded, untagged}: what the library holds."""
    photos, untagged = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(CASE WHEN tags IS NULL OR tags = '[]' THEN 1 ELSE 0 END), 0)"
        " FROM photos").fetchone()
    faces, named, manual, excluded = conn.execute(
        "SELECT COUNT(*), COUNT(name), COALESCE(SUM(CASE WHEN name_source = 'manual' THEN 1 ELSE 0 END), 0),"
        " COALESCE(SUM(excluded), 0) FROM faces").fetchone()
    return dict(photos=photos, faces=faces, named=named, manual=manual, excluded=excluded,
                untagged=untagged)


def _check(name, broken):
    broken = list(broken)
    return Check(name, len(broken), broken[:EXAMPLES])


def schema_current(conn):
    """Migrations not yet applied (tagpup.store.schema)."""
    current = schema.version(conn)
    return _check("migrations not applied", [m.name for m in schema.MIGRATIONS if m.version > current])


def generations_counted(conn):
    """Generations the triggers are not keeping."""
    have = {name for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'generations'")}
    if not have:
        return _check("generations not counted", generations.NAMES)
    counted = {name for (name,) in conn.execute("SELECT name FROM generations")}
    return _check("generations not counted", [n for n in generations.NAMES if n not in counted])


def faces_without_a_photo(conn):
    """Faces whose photo has no row. Faces need one to point at, and phase 4 gives them
    one by id."""
    rows = {paths.key(p) for (p,) in conn.execute("SELECT path FROM photos")}
    return _check("faces with no photo row", sorted(
        {p for (p,) in conn.execute("SELECT DISTINCT photo_path FROM faces") if paths.key(p) not in rows}))


def named_and_excluded(conn):
    """Faces both named and excluded: ruled out and claimed, which no view shows."""
    return _check("faces named and excluded", [i for (i,) in conn.execute(
        "SELECT id FROM faces WHERE excluded = 1 AND name IS NOT NULL ORDER BY id")])


def face_names_missing_from_people(conn):
    """Names on a photo's faces that its people do not list (docs/findings.md, #42)."""
    people = {}
    for path, people_json in conn.execute("SELECT path, people FROM photos"):
        try:
            people[paths.key(path)] = set(json.loads(people_json or "[]"))
        except (TypeError, ValueError):
            people[paths.key(path)] = set()
    broken = []
    for photo_path, name in conn.execute(
            "SELECT photo_path, name FROM faces WHERE name IS NOT NULL AND excluded = 0 ORDER BY photo_path"):
        listed = people.get(paths.key(photo_path))
        if listed is not None and name not in listed:
            broken.append(photo_path)
    return _check("face names missing from their photo's people", broken)


def orphan_nodes(conn):
    """Tag-tree nodes whose parent is not in the tree."""
    return _check("tree nodes whose parent is missing", [tag for (tag,) in conn.execute(
        "SELECT c.tag FROM tag_taxonomy c LEFT JOIN tag_taxonomy p ON p.id = c.parent_id"
        " WHERE c.parent_id IS NOT NULL AND p.id IS NULL ORDER BY c.tag")])


def one_file_two_rows(conn):
    """Photos with more than one row, their paths differing only as paths.key ignores."""
    seen = collections.Counter(paths.key(p) for (p,) in conn.execute("SELECT path FROM photos"))
    return _check("photos with two rows", sorted(k for k, n in seen.items() if n > 1))


def missing_files(conn):
    """Rows whose file is not on disk, as (folder, rows, whether the folder is there).
    Reported, not broken: a folder on an unplugged drive looks the same as a deleted one
    (ARCHITECTURE.md, phase 8)."""
    by_folder = collections.Counter()
    for (path,) in conn.execute("SELECT path FROM photos"):
        if not os.path.exists(path):
            by_folder[os.path.dirname(path)] += 1
    return [(folder, count, os.path.isdir(folder)) for folder, count in sorted(by_folder.items())]


#: The rules a library keeps, in the order a report lists them.
RULES = (schema_current, generations_counted, faces_without_a_photo, named_and_excluded,
         face_names_missing_from_people, orphan_nodes, one_file_two_rows)


def run(conn):
    """Every rule's Check, in RULES order."""
    return [rule(conn) for rule in RULES]
