"""Remove face rows that describe a face already recorded for the same photo.

Two paths can record the same face. TagPup detects faces when it shows a photo's
Detected Faces strip and saves any it does not already have; the indexer detects them
during a pass. Normally the second one finds the first and skips, because both look up
by photo path.

They stop agreeing when a photo's path changes. A folder renamed outside TagPup was
browsed under its new names -- so faces were saved against those -- and the old index
rows were later re-pointed at the same files, bringing their own faces with them.
Both sets are now filed under one path, and every face in that folder shows twice.

The copies are not equal. The older row carries the curation: names assigned by hand,
faces deliberately excluded. The newer one is a bare re-detection. So the rule is keep
whichever row knows something, and a group whose copies disagree about a name is left
alone for a person to look at rather than resolved by guesswork.

scripts/dedupe_faces.py and the MCP server's tool both call `dedupe_faces`, on the
maintenance scaffold (tagpup.services.maintenance).
"""
import collections

from tagpup.core import paths
from tagpup.services import maintenance
from tagpup.store import db
from tagpup.store import faces as store_faces


def knows_something(row):
    """How much curation a face row carries. Higher wins.

    A decision a person made outranks everything: it is the one thing clustering
    cannot make again. A name used to count for most, so a name clustering gave beat
    a face somebody had marked nobody, and the decision was the row thrown away.
    """
    _id, _path, _box, name, name_source, excluded = row
    score = 0
    if name_source == "manual":
        score += 4
    if excluded:
        score += 2
    if name:
        score += 1
    return score


def find_duplicates(conn):
    """(redundant, disputed) among the faces of the library open on `conn`.

    `redundant` is the face rows (tagpup.store.faces.decisions) that copy a better-curated
    one; `disputed` is (photo path, the names, the face ids) of each face whose copies
    disagree, left for a person.
    """
    rows = store_faces.decisions(conn)

    # By paths.key: the same box on the same file is one face however each copy
    # spelled the photo's path.
    groups = collections.defaultdict(list)
    for row in rows:
        groups[(paths.key(row[1]), str(row[2]))].append(row)

    redundant, disputed = [], []
    for copies in groups.values():
        if len(copies) < 2:
            continue
        photo_path = copies[0][1]
        face_ids = sorted(r[0] for r in copies)

        names = {r[3] for r in copies if r[3]}
        if len(names) > 1:
            # Two names for one face is a disagreement, not a duplicate.
            disputed.append((photo_path, sorted(names), face_ids))
            continue
        if names and any(r[5] for r in copies):
            # So is a name beside an exclusion. Keeping either throws the other
            # away, and the exclusion used to be the one that went.
            disputed.append((photo_path, sorted(names) + ["(excluded)"], face_ids))
            continue

        # Best-curated first; oldest id breaks a tie, since it is the one every other
        # table and every bookmark already refers to.
        copies.sort(key=lambda r: (-knows_something(r), r[0]))
        for row in copies[1:]:
            redundant.append(row)

    return redundant, disputed


def _read(library):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return find_duplicates(conn)
    finally:
        conn.close()


def _plan(library):
    redundant, disputed = _read(library)
    return maintenance.Plan(
        size=len(redundant),
        counts={"redundant": len(redundant),
                "redundant_named": sum(1 for r in redundant if r[3]),
                "redundant_excluded": sum(1 for r in redundant if r[5]),
                "disputed": len(disputed)},
        ids={"redundant": [r[0] for r in redundant],
             "disputed": [face_ids for _path, _names, face_ids in disputed]},
        reveal={"redundant": [(r[0], r[1]) for r in redundant],
                "disputed": [(photo_path, names) for photo_path, names, _ids in disputed]},
        work=[r[0] for r in redundant])


def remove(library, face_ids):
    """Delete the faces `face_ids`, under the library's write lock. Returns rows deleted."""
    def delete(conn):
        return store_faces.delete(conn, face_ids)

    return db.write_with_connection(library.path, delete,
                                    label="remove %d duplicate face row(s)" % len(face_ids))


def _write(library, planned, result):
    result.changed = remove(library, planned.work)


def _remaining(library):
    redundant, disputed = _read(library)
    return {"redundant": len(redundant), "disputed": len(disputed)}


def dedupe_faces(library, apply=False):
    """Plan, and with `apply` make, the removal of every face row in `library` that
    copies another (same photo, same box) and knows no more than the copy kept. Faces
    whose copies disagree about a name, or a name and an exclusion, are counted and left
    alone. A Result, on the maintenance scaffold: `changed` is face rows deleted."""
    return maintenance.run(library, "dedupe-faces", _plan, _write, apply=apply, remaining=_remaining)
