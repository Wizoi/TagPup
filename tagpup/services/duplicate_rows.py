"""One file, more than one photo row: the extra rows moved onto one and deleted (docs/findings.md, #479).

A file reached by two spellings -- a share and the drive it is a folder of, a junction, a hard link, a path a root
was adopted under in two ways -- was indexed under each, and the library holds it twice: the faces, the vectors and
the tags of one picture on two rows, which Identify Faces and the counts see as two photos. This finds the rows that
are PROVABLY one file, and only those: the same file on this machine, by the file system's own identity (the volume
and the file index of `os.stat`; the resolved path where a volume gives none), among the rows that share a name and a
size. A copy of a file -- another file with the same name, size and time, in another folder -- is not one file and is
never touched: each row describes a file of its own that is on disk. Counted as `copies`, and said.

Of the rows of one file the one kept is the one under a root (`@root/relative`: the same on every machine), else the
one that knows more (a name or an exclusion a person gave), else the oldest id, which every other table already refers
to. What the others hold is moved onto it first, only where it lacks it (a face already there -- the same box -- is not
moved again: re-pointing rows at a path that had rows made 233 duplicate faces): a face with no counterpart; a decision
(a name given by hand, an exclusion) a counterpart lacks; a vector under a model the kept row has none of. Then the
row is deleted. A set whose rows disagree -- another name for the same face, other tags or captions -- is left alone
and counted: a person looks at it. All of it one change of the journal (tagpup.services.maintenance), undoable.

    tagpup_cli.py --db photo_index dedupe-spelled-rows            # a dry run: counts, never a name or a path
    tagpup_cli.py --db photo_index dedupe-spelled-rows --apply
"""
import collections
import json
import os

from tagpup.core import paths
from tagpup.services import maintenance
from tagpup.store import db, journal
from tagpup.store import roots as store_roots

OPERATION = "dedupe_spelled_rows"


def identity_of(native):
    """What the file system knows the file at `native` by: (volume, file index) -- two names of one file share it --
    or, where a volume gives no file index, its resolved path. None when the file cannot be read."""
    try:
        stat = os.stat(native)
    except OSError:
        return None
    if stat.st_ino:
        return ("file", stat.st_dev, stat.st_ino)
    return ("path", os.path.normcase(os.path.realpath(native)))


def _decided(face):
    """Did a person decide something about the face (id, box, name, name_source, excluded, excluded_reason)?"""
    return face[3] == "manual" or bool(face[4])


def _knows(conn, photo_id):
    """How much a person told the library of a photo: faces they named or excluded."""
    return conn.execute("SELECT COUNT(*) FROM faces WHERE photo_id = ? AND (name_source = 'manual' OR excluded = 1)",
                        (photo_id,)).fetchone()[0]


def _faces(conn, photo_id):
    """[(id, box as a list, name, name_source, excluded, excluded_reason)] of a photo's faces: no embedding, no crop."""
    return [(face_id, json.loads(box) if box else None, name, source, excluded, reason)
            for face_id, box, name, source, excluded, reason in conn.execute(
                "SELECT id, box, name, name_source, excluded, excluded_reason FROM faces WHERE photo_id = ? ORDER BY id",
                (photo_id,))]


def _same_decision(a, b):
    return (a[2], a[3], bool(a[4])) == (b[2], b[3], bool(b[4]))


def find(conn):
    """The rows of one file in the library open on `conn`: (sets, left). `sets` is a list of dicts, one for each
    file held more than once: `keep` and `drop`, (photo id, the row's path as stored, native path) each, `edits`
    the work to do (kind, the arguments of a journal edit) and `moves`, the counts of it. `left` counts what was
    looked at and left alone: copies, missing, disputed, differ."""
    rows = conn.execute("SELECT id, path, size, tags, captions FROM photos WHERE size IS NOT NULL").fetchall()
    native = store_roots.natives(conn, [(r[1],) for r in rows], 0)
    by_name = collections.defaultdict(list)
    for (photo_id, stored, size, tags, captions), (found,) in zip(rows, native):
        by_name[(paths.key(os.path.basename(found)), size)].append((photo_id, stored, found, tags, captions))
    left = collections.Counter()
    sets = []
    for candidates in by_name.values():
        if len(candidates) < 2:
            continue
        by_file, unknown = collections.defaultdict(list), 0
        for each in candidates:
            identity = identity_of(each[2])
            if identity is None:
                unknown += 1
            else:
                by_file[identity].append(each)
        left["missing"] += unknown
        if len(by_file) > 1:
            # More than one file among rows that share a name and a size: copies, each with its own row.
            left["copy_groups"] += 1
            left["copies"] += sum(len(group) for group in by_file.values())
        for group in by_file.values():
            if len(group) > 1:
                found = _plan_set(conn, group)
                if isinstance(found, str):
                    left[found] += 1
                else:
                    sets.append(found)
    return sets, dict(left)


def _plan_set(conn, group):
    """The set of rows of one file as a plan -- or the word for why it is left alone ("differ", "disputed")."""
    if len({(tags, captions) for _i, _s, _n, tags, captions in group}) > 1:
        return "differ"
    ordered = sorted(group, key=lambda each: (not each[1].startswith("@"), -_knows(conn, each[0]), each[0]))
    keep, drop = ordered[0], ordered[1:]
    kept_faces = _faces(conn, keep[0])
    edits, moves = [], collections.Counter()
    models = {model for (model,) in conn.execute("SELECT model FROM embeddings WHERE photo_id = ?", (keep[0],))}
    for each in drop:
        for face in _faces(conn, each[0]):
            twin = next((kept for kept in kept_faces if kept[1] == face[1]), None)
            if twin is None:
                edits.append(journal.update("faces", (face[0],), {"photo_id": each[0]}, {"photo_id": keep[0]},
                                            kind="face moved"))
                moves["faces_moved"] += 1
                kept_faces.append(face)
            elif _decided(face) and not _decided(twin):
                edits.append(journal.update("faces", (twin[0],),
                                            {"name": twin[2], "name_source": twin[3], "excluded": twin[4],
                                             "excluded_reason": twin[5]},
                                            {"name": face[2], "name_source": face[3], "excluded": face[4],
                                             "excluded_reason": face[5]}, kind="decision carried"))
                moves["decisions_carried"] += 1
            elif _decided(face) and _decided(twin) and not _same_decision(face, twin):
                return "disputed"
        for model, mtime, size, vector in conn.execute(
                "SELECT model, mtime, size, vector FROM embeddings WHERE photo_id = ?", (each[0],)).fetchall():
            if model not in models:
                models.add(model)
                edits.append(journal.insert("embeddings", {"photo_id": keep[0], "model": model, "mtime": mtime,
                                                           "size": size, "vector": vector}, kind="vector carried"))
                moves["vectors_carried"] += 1
        edits.append(journal.delete("photos", (each[0],), {"path": paths.stored(each[2])}, kind="row removed"))
    return {"keep": keep, "drop": drop, "edits": edits, "moves": moves}


def _plan(library):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        try:
            sets, left = find(conn)
        except paths.RootsError as problem:
            return maintenance.Plan(refused="This machine does not place the library's roots, so which rows are one file "
                                            "cannot be told: %s" % problem)
        moves = collections.Counter()
        for each in sets:
            moves.update(each["moves"])
    finally:
        conn.close()
    dropped = [each for found in sets for each in found["drop"]]
    return maintenance.Plan(
        size=len(dropped),
        counts={"files_held_twice": len(sets), "rows_to_remove": len(dropped),
                "kept_under_a_root": sum(1 for found in sets if found["keep"][1].startswith("@")),
                "kept_not_under_a_root": sum(1 for found in sets if not found["keep"][1].startswith("@")),
                "faces_moved": moves["faces_moved"], "decisions_carried": moves["decisions_carried"],
                "vectors_carried": moves["vectors_carried"],
                "copy_groups_left_alone": left.get("copy_groups", 0), "copies_left_alone": left.get("copies", 0),
                "missing_files_left_alone": left.get("missing", 0),
                "sets_whose_rows_differ": left.get("differ", 0), "sets_disputed": left.get("disputed", 0)},
        ids={"kept": [found["keep"][0] for found in sets], "removed": [each[0] for each in dropped]},
        reveal={"sets": [{"keep": found["keep"][2], "remove": [each[2] for each in found["drop"]]} for found in sets]},
        work=sets)


def _edits(planned):
    return [edit for found in planned.work for edit in found["edits"]]


def _remaining(library):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        sets, _left = find(conn)
        return {"files_held_twice": len(sets)}
    finally:
        conn.close()


def dedupe_spelled_rows(library, apply=False):
    """Plan, and with `apply` make, the merging of the rows of every file the library holds more than once. A Result
    on the maintenance scaffold: `changed` is the rows the change wrote (see the module's docstring). Applied as one
    change of the journal, undoable."""
    return maintenance.run(library, OPERATION, _plan, _edits, apply=apply, remaining=_remaining)
