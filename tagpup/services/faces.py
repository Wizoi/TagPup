"""Naming faces, and taking them out of identity work: TagTuner's Identify Faces.

Each action keeps each photo's list of people in step with its faces
(tagpup.store.photos.update_people), and writes through the library's write lock: most
of them wrote on a connection of their own.

TagTuner caches its Identify Faces grids against a fingerprint of the faces table. The
actions that take faces out of the pool -- naming, excluding -- write through
tagpup.store.faces.accounted_write and return `fingerprints` (before, after) and
`face_ids` in details, from which the page's server takes those faces off its cached
grids instead of rebuilding them. The rest move the fingerprint, and a grid built before
them is rebuilt.
"""
import logging
import os

import numpy as np

from tagpup.core import paths, vocabulary
from tagpup.core.result import Conflict, NotFound, Result
from tagpup.store import db, faces, photos

logger = logging.getLogger(__name__)

#: How like a named face an unnamed one must be for automatch to give it that name.
AUTOMATCH_SIMILARITY = 0.8


def name_face(library, face_id, person_name):
    """Name one face. Clicking a suggestion, or typing a name, on a face card.

    A person chose this, so it is recorded as a manual decision: re-clustering re-derives
    every name from scratch and must not discard it. Refused when somebody else's face in
    the photo already carries the name; a Conflict for a face that has been excluded.
    """
    result = Result(attempted=1)
    person_name = (person_name or "").strip()
    problem = vocabulary.problem_with_name(person_name)
    if problem:
        result.refuse(problem)
        return result
    _library_there(library)
    with faces.accounted_write(library.path, "name a face") as write:
        row = write.conn.execute("SELECT photo_path, name, excluded FROM faces WHERE id = ?",
                                 (face_id,)).fetchone()
        if not row:
            raise NotFound("Face ID not found")
        photo_path, old_name, excluded = row
        # An excluded face has been ruled out of identity work; naming it leaves a face
        # that is both ruled out and claimed, which no view shows and no Undo reaches.
        if excluded:
            raise Conflict("Cannot match: this face is excluded. Restore it first to name it.")
        if old_name and vocabulary.key(old_name) == vocabulary.key(person_name):
            return result
        if _named_elsewhere_in_photo(write.conn, photo_path, person_name, face_id):
            result.refuse("Cannot match: '%s' is already tagged on another face in this photo."
                          % person_name)
            return result
        write.conn.execute("UPDATE faces SET name = ?, name_source = 'manual' WHERE id = ?",
                           (person_name, face_id))
        photos.update_people(write.conn, photo_path, gained=[person_name],
                             lost=[old_name] if old_name and old_name != person_name else [])
        result.changed = 1
    result.details.update(face_ids=[face_id], fingerprints=(write.before, write.after))
    return result


def name_faces(library, face_ids, person_name):
    """Name many faces as one person. Assigning a group, or a selection, in the grids.

    An excluded face is left alone and reported; one already named this person, or not
    in the table, is nothing to do. Refused when two of the faces are in one photo, or
    the person is already on another face in one of the photos.

    details: `matched`, the rows named; `matched_ids`, the faces asked to be named;
    `skipped_excluded`; and, when anything was named, `face_ids` and `fingerprints`.
    """
    result = Result(attempted=len(face_ids))
    person_name = (person_name or "").strip()
    problem = vocabulary.problem_with_name(person_name)
    if problem:
        result.refuse(problem)
        return result
    _library_there(library)
    result.details.update(matched=0, matched_ids=[], skipped_excluded=[])
    with faces.accounted_write(library.path, "name faces in bulk") as write:
        conn = write.conn
        # Read the selected faces once. Their path and current name were fetched three
        # times over, one query per face per pass: a selection of fifty was a hundred and
        # fifty round trips for fifty rows.
        selected, excluded_ids = {}, set()
        for start in range(0, len(face_ids), 500):
            chunk = face_ids[start:start + 500]
            for row_id, photo_path, current_name, excluded in conn.execute(
                    "SELECT id, photo_path, name, excluded FROM faces WHERE id IN (%s)"
                    % ",".join("?" * len(chunk)), chunk):
                if excluded:
                    excluded_ids.add(row_id)
                else:
                    selected[row_id] = (photo_path, current_name)
        # An excluded face is left alone. Naming one leaves a face both ruled out and
        # claimed -- a page acting on a stale list of faces did exactly that.
        result.details["skipped_excluded"] = [fid for fid in face_ids if fid in excluded_ids]
        face_ids = [fid for fid in face_ids if fid in selected
                    and vocabulary.key(selected[fid][1]) != vocabulary.key(person_name)]
        if not face_ids:
            return result

        in_photo = {}
        for fid in face_ids:
            in_photo.setdefault(selected[fid][0], []).append(fid)
        for photo_path, fids in in_photo.items():
            if len(fids) > 1:
                result.refuse("Cannot match: Multiple selected faces in photo '%s' are being "
                              "assigned to '%s'." % (os.path.basename(photo_path), person_name))
                return result
            # By equality only. A LIKE retry this used to fall back on scanned every face
            # row per selected face -- nineteen seconds for a selection of fifty -- and
            # read an underscore in a file name as a wildcard.
            if _named_elsewhere_in_photo(conn, photo_path, person_name, fids[0]):
                result.refuse("Cannot match: '%s' is already tagged on another face in photo "
                              "'%s'." % (person_name, os.path.basename(photo_path)))
                return result

        matched = conn.execute(
            "UPDATE faces SET name = ?, name_source = 'manual' WHERE id IN (%s) AND excluded = 0"
            % ",".join("?" * len(face_ids)), [person_name] + face_ids).rowcount
        for photo_path, fids in in_photo.items():
            old_names = [selected[fid][1] for fid in fids if selected[fid][1] and selected[fid][1] != person_name]
            photos.update_people(conn, photo_path, gained=[person_name], lost=old_names)
        result.changed = matched
    result.details.update(matched=matched, matched_ids=face_ids, face_ids=face_ids,
                          fingerprints=(write.before, write.after))
    return result


def unname_face(library, face_id):
    """Take a face's name off. Unmatching a face card: a decision -- "this is nobody" --
    recorded as manual, so re-clustering does not put a name back."""
    _library_there(library)
    result = Result(attempted=1)

    def unname(conn):
        row = conn.execute("SELECT photo_path, name FROM faces WHERE id = ?", (face_id,)).fetchone()
        if not row:
            raise NotFound("Face ID not found")
        photo_path, old_name = row
        if old_name is None:
            return 0
        conn.execute("UPDATE faces SET name = NULL, name_source = 'manual' WHERE id = ?", (face_id,))
        photos.update_people(conn, photo_path, lost=[old_name])
        return 1

    result.changed = db.write_with_connection(library.path, unname, label="unname a face")
    return result


def unname_faces(library, face_ids, undo=False):
    """Take the names off many faces. `undo` puts them back as they were before an
    assignment -- unreviewed -- where unmatching is a decision recorded as manual."""
    _library_there(library)
    result = Result(attempted=len(face_ids))
    # Unmatching is a decision -- "this is nobody" -- recorded as manual. Undoing an
    # assignment is not: it puts the faces back as they were, unreviewed, and used to
    # leave them marked as deliberately nobody instead.
    source = None if undo else "manual"

    def unname(conn):
        named = {}
        for face_id in face_ids:
            row = conn.execute("SELECT photo_path, name FROM faces WHERE id = ?", (face_id,)).fetchone()
            if row and row[1]:
                named.setdefault(row[0], set()).add(row[1])
        changed = conn.execute("UPDATE faces SET name = NULL, name_source = ? WHERE id IN (%s)"
                               % ",".join("?" * len(face_ids)), [source] + list(face_ids)).rowcount
        for photo_path, old_names in named.items():
            photos.update_people(conn, photo_path, lost=sorted(old_names))
        return changed

    result.changed = db.write_with_connection(library.path, unname, label="unname faces")
    return result


def unname_photo(library, photo_path):
    """Take the names off every face in a photo. Unmatch All on a photo."""
    _library_there(library)
    result = Result(attempted=1)
    where, params = paths.sql_equals("photo_path", photo_path)

    def unname(conn):
        names = [name for (name,) in conn.execute(
            "SELECT DISTINCT name FROM faces WHERE " + where + " AND name IS NOT NULL", params)]
        # One equality, not an equality and then a LIKE as well: in LIKE an underscore
        # matches any character, so the LIKE pass also cleared every name in a photo
        # called IMG-1234.jpg when this one was IMG_1234.jpg.
        changed = conn.execute("UPDATE faces SET name = NULL, name_source = 'manual' WHERE " + where,
                               params).rowcount
        if names:
            photos.update_people(conn, photo_path, lost=names)
        return changed

    result.changed = db.write_with_connection(library.path, unname, label="unname a photo's faces")
    return result


def exclude(library, face_ids, reason="not a person"):
    """Take faces out of identity work: a crowd's passers-by, a crop that is no face.
    Ignoring a face or a cluster.

    Left in, they cluster, vote, and drag centroids around. Excluding is reversible and
    keeps the row, so the face still exists on the photo; it stops being a candidate for
    anyone. Any name it carried goes, with name_source left 'manual' so re-clustering
    cannot quietly put one back.

    `changed`: the rows excluded, not the ids sent. details: `face_ids`, `fingerprints`.
    """
    _library_there(library)
    result = Result(attempted=len(face_ids))
    placeholders = ",".join("?" * len(face_ids))
    with faces.accounted_write(library.path, "exclude faces") as write:
        named = write.conn.execute("SELECT id, photo_path, name FROM faces WHERE id IN (%s)"
                                   % placeholders, face_ids).fetchall()
        result.changed = write.conn.execute(
            "UPDATE faces SET excluded = 1, excluded_reason = ?, name = NULL, name_source = 'manual'"
            " WHERE id IN (%s)" % placeholders, [reason or "not a person"] + list(face_ids)).rowcount
        for _face_id, photo_path, old_name in named:
            if old_name:
                photos.update_people(write.conn, photo_path, lost=[old_name])
    result.details.update(face_ids=list(face_ids), fingerprints=(write.before, write.after))
    return result


def restore(library, face_ids):
    """Bring excluded faces back into identity work, unnamed and unclaimed.

    Only faces that are excluded: clearing name_source on any other face would unpin a
    manual name that re-clustering must not revise, and Undo after Ignore Cluster sends
    whatever ids it was given. `changed`: the faces restored.
    """
    _library_there(library)
    result = Result(attempted=len(face_ids))
    result.changed = db.write_with_connection(
        library.path,
        lambda conn: conn.execute(
            "UPDATE faces SET excluded = 0, excluded_reason = NULL, name_source = NULL"
            " WHERE id IN (%s) AND excluded = 1" % ",".join("?" * len(face_ids)), face_ids).rowcount,
        label="restore faces")
    return result


def automatch_photo(library, photo_path, named):
    """Name each unnamed face in a photo that closely resembles one named face.
    Automatch on a photo. `named()` gives (ids, names, matrix) of every named face as
    unit vectors -- the matrix TagTuner keeps -- and is asked only when there are faces
    to match; a matrix of None names nobody.

    `changed`: the faces named."""
    where, params = paths.sql_equals("photo_path", photo_path)
    return _automatch(library, where, params, named)


def automatch_folder(library, folder, named):
    """Automatch every photo in a folder, and the folders under it. As automatch_photo.

    details: `remaining_counts`, each photo's faces still unnamed, keyed as stored.
    """
    where, params = paths.sql_under("photo_path", folder)
    result = _automatch(library, where, params, named)
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        result.details["remaining_counts"] = dict(conn.execute(
            "SELECT photo_path, COUNT(*) FROM faces WHERE " + where + " AND name IS NULL"
            " GROUP BY photo_path", params).fetchall())
    finally:
        conn.close()
    return result


def remove_folder(library, folder):
    """Take a folder's photos and faces out of the library -- the folders under it too.
    The photo files are never touched. This does discard face work for those photos,
    manual names and exclusions included, so the Result says what it cost.

    details: `photos_removed`, `faces_removed`, `manual_lost`, `excluded_lost`.
    """
    result = Result(attempted=1)
    # Everything under the folder, at any depth, compared the way the filesystem compares.
    # Faces are matched on their own photo_path rather than through the photo rows, so a
    # face goes with its folder even where its photo row is missing or spelled apart.
    photos_where, photos_params = paths.sql_under("path", folder)
    faces_where, faces_params = paths.sql_under("photo_path", folder)

    def remove(conn):
        manual = conn.execute("SELECT COUNT(*) FROM faces WHERE " + faces_where
                              + " AND name_source = 'manual'", faces_params).fetchone()[0]
        excluded = conn.execute("SELECT COUNT(*) FROM faces WHERE " + faces_where
                                + " AND excluded = 1", faces_params).fetchone()[0]
        # Faces are deleted explicitly rather than by the cascade, which only runs where a
        # connection turned foreign keys on. The counts are what the deletes removed.
        faces_removed = conn.execute("DELETE FROM faces WHERE " + faces_where, faces_params).rowcount
        photos_removed = conn.execute("DELETE FROM photos WHERE " + photos_where, photos_params).rowcount
        return dict(photos_removed=photos_removed, faces_removed=faces_removed,
                    manual_lost=manual, excluded_lost=excluded)

    result.details.update(db.write_with_connection(library.path, remove, label="remove a folder"))
    result.changed = result.details["photos_removed"]
    logger.info("Removed %d photo(s) and %d face(s) under %s",
                result.details["photos_removed"], result.details["faces_removed"], folder)
    return result


def _automatch(library, where, params, named):
    """Automatch the unnamed faces `where` selects. A face is given the name of the named
    face it most resembles, at AUTOMATCH_SIMILARITY or above, unless that name is already
    in its photo or proposed for two faces there.

    The faces are compared before the write lock is taken -- building the named matrix
    can take half a second -- and a face named meanwhile is left as it is."""
    _library_there(library)
    result = Result()
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        unnamed = conn.execute("SELECT id, embedding, photo_path FROM faces WHERE " + where
                               + " AND name IS NULL AND excluded = 0", params).fetchall()
    finally:
        conn.close()
    result.attempted = len(unnamed)
    if not unnamed:
        return result
    _ids, names, matrix = named()
    if matrix is None:
        return result

    proposed = {}
    for face_id, blob, photo_path in unnamed:
        similarities = np.dot(matrix, np.frombuffer(blob, dtype=np.float32))
        best = int(np.argmax(similarities))
        if similarities[best] >= AUTOMATCH_SIMILARITY:
            proposed.setdefault(photo_path, []).append((face_id, names[best]))
    if not proposed:
        return result

    def match(conn):
        count = 0
        for photo_path, faces_proposed in proposed.items():
            # Only the photos a name is proposed for, each by an index. Reading every
            # named face under the folder scanned the whole table inside the write lock,
            # and every other face action waited (docs/findings.md, #45).
            taken = faces.names_in_photo(conn, photo_path)
            proposed_names = [name for _fid, name in faces_proposed]
            gained = set()
            for face_id, name in faces_proposed:
                if proposed_names.count(name) > 1 or name in taken:
                    continue
                # A bulk guess, not a per-face human decision, so it is left as an
                # automatic assignment that re-clustering may revise.
                changed = conn.execute("UPDATE faces SET name = ? WHERE id = ? AND name IS NULL"
                                       " AND excluded = 0", (name, face_id)).rowcount
                if changed:
                    gained.add(name)
                    count += changed
            if gained:
                photos.update_people(conn, photo_path, gained=sorted(gained))
        return count

    result.changed = db.write_with_connection(library.path, match, label="automatch faces")
    return result


def _named_elsewhere_in_photo(conn, photo_path, person_name, face_id):
    """Does another face in the photo already carry the name? By equality: a LIKE retry
    scanned every face row, and read an underscore in a file name as any character."""
    where, params = paths.sql_equals("photo_path", photo_path)
    return conn.execute("SELECT 1 FROM faces WHERE " + where + " AND name = ? AND id != ?",
                        params + (person_name, face_id)).fetchone() is not None


def _library_there(library):
    if not os.path.exists(library.path):
        raise NotFound("Database not found")
