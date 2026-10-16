"""Which person was taken off which photo, read from the journal (docs/ARCHITECTURE.md, "Faces and the photo's person tag", #908).

Taking a person's tag off a photo unnames their faces as one journaled change (tagpup.services.face_people.OPERATION): the
rows of that change say, for each face, the name it had. A face a person called nobody (`name` NULL, `name_source` 'manual') that
such a change unnamed, and that has not been undone, is the record that the owner took THAT person off THIS photo on purpose.

This module only reads, and has no imports of the journal's module, so that the rules which name faces (tagpup.store.face_tags,
tagpup.services.faces) can ask it: they only ever BLOCK on it -- they do not name another face of the photo as that person while
the record stands -- and never name a face from it. The owner names the right face by hand, or undoes the removal in History,
which is the journaled inverse.
"""
import json

from tagpup.core.vocabulary import Ref

#: What the journal calls the change that unnamed the faces of a person taken off a photo.
OPERATION = "face unnamed for a person taken off the photo"


def _key(face_id):
    return json.dumps([face_id])


def unnaming_of(conn, files_change):
    """The id of the newest APPLIED change named OPERATION that unnamed the faces for the change of photo files `files_change`
    (its summary says which), or None. The journaled inverse of a removal's faces is to undo this change."""
    for change_id, summary in conn.execute(
            "SELECT id, summary FROM changes WHERE operation = ? AND status = 'applied' ORDER BY id DESC LIMIT 500",
            (OPERATION,)):
        if (json.loads(summary or "{}")).get("files_change") == files_change:
            return change_id
    return None


def faces_of(conn, change_id):
    """[(face id, the name it had)] of the faces change `change_id` (OPERATION) unnamed."""
    return [(json.loads(key)[0], old) for key, old in conn.execute(
        "SELECT row_key, old FROM change_rows WHERE change_id = ? AND table_name = 'faces' AND column_name = 'name'"
        " ORDER BY id", (change_id,)) if old]


def removed_people(conn, face_ids):
    """{face id: Ref} for each of `face_ids` -- faces a person called nobody -- that the newest APPLIED change named OPERATION
    unnamed: who they were (the node's id, which the change recorded as the face's `tag_id`, and the name; a named face holds
    an id, 2026-10-15). removed_names, with the ids."""
    found = {}
    for face_id, (change_id, name) in _newest(conn, face_ids).items():
        recorded = conn.execute(
            "SELECT old FROM change_rows WHERE change_id = ? AND table_name = 'faces' AND row_key = ? AND column_name = 'tag_id'",
            (change_id, _key(face_id))).fetchone()
        found[face_id] = Ref(int(recorded[0]), name)
    return found


def removed_names(conn, face_ids):
    """{face id: the name it had} for each of `face_ids` -- faces a person called nobody -- that the newest APPLIED change
    named OPERATION unnamed (an undone change does not count). One indexed read of the journal a face
    (idx_change_rows_row)."""
    return {face_id: name for face_id, (_change, name) in _newest(conn, face_ids).items()}


def _newest(conn, face_ids):
    """{face id: (the change, the name it had)} of the newest applied OPERATION change that unnamed each of `face_ids`."""
    found = {}
    for face_id in face_ids:
        row = conn.execute(
            "SELECT r.old, c.id FROM change_rows r JOIN changes c ON c.id = r.change_id WHERE r.table_name = 'faces'"
            " AND r.row_key = ? AND r.column_name = 'name' AND c.operation = ? AND c.status = 'applied'"
            " ORDER BY c.id DESC LIMIT 1", (_key(face_id), OPERATION)).fetchone()
        if row is not None and row[0]:
            found[face_id] = (row[1], row[0])
    return found
