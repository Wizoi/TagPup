"""The faces of a bulk assignment, in the journal and in step with its photo files (docs/ARCHITECTURE.md, "One batch model";
tagpup.services.face_batch, which plans them and hands them to the change of photo files).

A bulk assignment (assign, take names off, ignore, Re-examine) is ONE change of photo files whose faces are rows of the same
change: the file step is the person's tag written or taken off, and the faces of that photo follow it in the transaction that marks
the file `done` -- or, going back, `undone` -- so a photo and its faces cannot disagree, whether the change runs forward, is settled
after a crash or is undone from History.

Each face the change is to write is recorded, in the plan's own transaction, as a row of the journal's `change_rows`: its decision
columns (faces.DECISION) as the plan read them (`old`) and as they are to be (`new`), for the columns it changes and the ones that
must stay as they are (excluded 0). That is the journal's own rule, applied here: a change is applied only where the rows are what
the change expects.

* **Forward** (`follow`): a face that still holds what the plan read is given what it was to hold. A face that does not -- named
  or ruled out by somebody since, its person merged away -- is left as it is and its record is removed (the journal says what the
  change did). A guess the tag itself made on the face (the one-face rule of tagpup.store.face_tags) is the face's old state, and
  one it made on another face of the photo is taken back.
* **Back** (`follow`, not forward): a face that holds what the change left is put back as the plan read it; one that does not --
  somebody renamed it since -- is left, and counted.
* **Tidy** (`tidy`): when the change is finished, the records of faces whose photo was never written (cancelled, a conflict) are
  removed. They were only the plan.

Nothing here names a person or a path: counts, ids and columns.
"""
import json

from tagpup.store import faces, journal, person_ids, schema

#: What the change calls a row it records.
KIND = "face decision"

CHUNK = 500


def record(conn, change_id, intents):
    """Record the faces a change is to write: `intents` is [(face id, old, new)], the columns of faces.DECISION as the plan read them
    and as they are to be. In the transaction that plans the change."""
    journal.record_rows(conn, change_id, [
        journal.RowChange("update", "faces", (face_id,), dict(old), dict(new), KIND) for face_id, old, new in intents])


def recorded(conn, change_id, face_ids):
    """{face id: (old, new)} of the faces among `face_ids` the change `change_id` records."""
    found = {}
    face_ids = list(face_ids)
    for start in range(0, len(face_ids), CHUNK):
        chunk = face_ids[start:start + CHUNK]
        for row_key, column, old, new in conn.execute(
                # By the face's key: the planner took the change's index and read the change's every row for each photo.
                "SELECT row_key, column_name, old, new FROM change_rows INDEXED BY idx_change_rows_row"
                " WHERE table_name = 'faces' AND row_key IN (%s) AND change_id = ?" % ",".join("?" * len(chunk)),
                [schema.row_key((face_id,)) for face_id in chunk] + [change_id]):
            before, after = found.setdefault(json.loads(row_key)[0], ({}, {}))
            before[column], after[column] = old, new
    return found


def _forget(conn, change_id, face_ids):
    face_ids = list(face_ids)
    for start in range(0, len(face_ids), CHUNK):
        chunk = face_ids[start:start + CHUNK]
        conn.execute("DELETE FROM change_rows INDEXED BY idx_change_rows_row WHERE table_name = 'faces' AND change_id = ?"
                     " AND row_key IN (%s)" % ",".join("?" * len(chunk)), [change_id] + [schema.row_key((face_id,)) for face_id in chunk])


def tidy(conn, change_id):
    """The change is finished: remove the records of faces whose photo has no file `done` in it. Called in the transaction that
    finishes the change (tagpup.store.file_journal.finish)."""
    ids = {json.loads(key)[0] for (key,) in conn.execute(
        "SELECT DISTINCT row_key FROM change_rows INDEXED BY idx_change_rows_change WHERE table_name = 'faces' AND change_id = ?",
        (change_id,))}
    if not ids:
        return
    written = {face_id for (face_id,) in conn.execute(
        "SELECT f.id FROM change_files c JOIN faces f ON f.photo_id = c.photo_id WHERE c.change_id = ? AND c.state = 'done'",
        (change_id,))}
    _forget(conn, change_id, ids - written)


def _holds(current, expected):
    """Does a face hold `expected` (a record's columns)? The cached name beside a person's id is not compared
    (tagpup.store.journal.cache_columns)."""
    cache = journal.cache_columns("faces", expected)
    return all(current.get(column) == value for column, value in expected.items() if column not in cache)


def _same_person(face, new):
    """Does the face carry the person `new` (a record's columns) names? By the node's id: a name alone is nobody (owner,
    2026-10-11)."""
    return new.get("name") is not None and new.get("tag_id") is not None and new["tag_id"] == face.get("tag_id")


def _guess_of(current, old, new):
    """Is the face's state the plan's old one plus the guess of the person it is to be given, made by the tag just written (the
    one-face rule of tagpup.store.face_tags)? A guess has no decider."""
    return (old.get("name") is None and current.get("name_source") is None and not current.get("excluded")
            and _same_person(current, new))


class Followed:
    """What a photo's file did to its faces: the ids `written` (put as the change says), `left` (not what the change expected), and
    `unpooled` (written ids that left identity work: named or ruled out)."""

    def __init__(self):
        self.written, self.left, self.unpooled = [], [], []


def follow(conn, change_id, photo_id, forward):
    """The faces of photo `photo_id` that change `change_id` records, written to what the change says (`forward`) or back to what
    the plan read, in the caller's transaction. Followed, or None when the change records none of this photo's."""
    ids = [face_id for (face_id,) in conn.execute(
        "SELECT id FROM faces INDEXED BY idx_faces_photo_id WHERE photo_id = ?", (photo_id,))]
    wanted = recorded(conn, change_id, ids)
    if not wanted:
        return None
    now = faces.decisions_of(conn, ids)
    done, writes, known = Followed(), {}, None
    for face_id, (old, new) in wanted.items():
        current = now.get(face_id)
        start, end = (old, new) if forward else (new, old)
        if current is not None and (_holds(current, start) or (forward and _guess_of(current, old, new))):
            values = dict(end)
            if forward and new.get("name") is not None:
                # The person as the tree has them now: their node's id and name, or -- a name no person is filed under -- the
                # name, which the first face written makes a node of (the faces after it are given its id, not left by name).
                try:
                    known = known or person_ids.read(conn)
                    tag_id, values["name"] = person_ids.target(
                        conn, new["tag_id"] if new.get("tag_id") is not None else new["name"], known)
                    if "tag_id" in values:
                        values["tag_id"] = tag_id
                except person_ids.PersonProblem:
                    current = None          # the person is gone, is a group, or a second person has the name: nobody is named
            if current is not None:
                writes[face_id] = values
                done.written.append(face_id)
                if forward and (values.get("excluded") or values.get("name") is not None):
                    done.unpooled.append(face_id)
                continue
        done.left.append(face_id)
    if forward:
        _forget(conn, change_id, done.left)
        # The tag just written can have named ANOTHER face of the photo by the one-face rule: a person chose this one.
        guessed = {face_id: current.get("tag_id") for face_id, current in now.items()
                   if face_id not in wanted and current.get("name") is not None and current.get("name_source") is None
                   and any(_same_person(current, new) for _old, new in wanted.values())}
        if guessed:
            faces.revert_automatic(conn, guessed)
    if writes:
        faces.put_decisions(conn, writes)
    return done
