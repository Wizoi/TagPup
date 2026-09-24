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

from tagpup.core import clustering, vocabulary
from tagpup.core.result import Conflict, NotFound, Result
from tagpup.store import db, faces, photos

logger = logging.getLogger(__name__)

#: How like a named face an unnamed one must be for automatch to give it that name: the
#: value for naming a face with no one looking (tagpup.core.clustering).
AUTOMATCH_SIMILARITY = clustering.NAME_WITHOUT_ASKING

#: Why a face may be excluded: the four TagTuner's page offers (EXCLUDE_REASONS in
#: gui/app.js), and the one it sets itself when a cluster is ignored. The reason used to
#: be free text, and collected "fuzzy" beside "bad crop" (docs/findings.md, #53).
EXCLUSION_REASONS = ("not a person", "stranger", "bad crop", "duplicate", "ignored cluster")


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
        row = faces.rows(write.conn, [face_id]).get(face_id)
        if not row:
            raise NotFound("Face ID not found")
        photo_path, old_name, excluded = row
        # An excluded face has been ruled out of identity work; naming it leaves a face
        # that is both ruled out and claimed, which no view shows and no Undo reaches.
        if excluded:
            raise Conflict("Cannot match: this face is excluded. Restore it first to name it.")
        if old_name and vocabulary.key(old_name) == vocabulary.key(person_name):
            return result
        if faces.named_elsewhere_in_photo(write.conn, photo_path, person_name, face_id):
            result.refuse("Cannot match: '%s' is already tagged on another face in this photo."
                          % person_name)
            return result
        faces.name(write.conn, [face_id], person_name)
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
        for row_id, (photo_path, current_name, excluded) in faces.rows(conn, face_ids).items():
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
            if faces.named_elsewhere_in_photo(conn, photo_path, person_name, fids[0]):
                result.refuse("Cannot match: '%s' is already tagged on another face in photo "
                              "'%s'." % (person_name, os.path.basename(photo_path)))
                return result

        matched = faces.name(conn, face_ids, person_name)
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
        row = faces.rows(conn, [face_id]).get(face_id)
        if not row:
            raise NotFound("Face ID not found")
        photo_path, old_name, _excluded = row
        if old_name is None:
            return 0
        faces.unname(conn, [face_id])
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
        for photo_path, name, _excluded in faces.rows(conn, face_ids).values():
            if name:
                named.setdefault(photo_path, set()).add(name)
        changed = faces.unname(conn, face_ids, source)
        for photo_path, old_names in named.items():
            photos.update_people(conn, photo_path, lost=sorted(old_names))
        return changed

    result.changed = db.write_with_connection(library.path, unname, label="unname faces")
    return result


def unname_photo(library, photo_path):
    """Take the names off every face in a photo. Unmatch All on a photo."""
    _library_there(library)
    result = Result(attempted=1)

    def unname(conn):
        names = sorted(faces.names_in_photo(conn, photo_path))
        changed = faces.unname_photo(conn, photo_path)
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
    Refused for a reason not in EXCLUSION_REASONS; none is "not a person".
    """
    _library_there(library)
    result = Result(attempted=len(face_ids))
    reason = (reason or "").strip().lower() or "not a person"
    if reason not in EXCLUSION_REASONS:
        result.refuse("'%s' is not a reason to exclude a face: use one of %s."
                      % (reason, ", ".join(EXCLUSION_REASONS)))
        return result
    with faces.accounted_write(library.path, "exclude faces") as write:
        named = faces.rows(write.conn, face_ids).values()
        result.changed = faces.exclude(write.conn, face_ids, reason)
        for photo_path, old_name, _excluded in named:
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
        library.path, lambda conn: faces.restore(conn, face_ids), label="restore faces")
    return result


def automatch_photo(library, photo_path, named):
    """Name each unnamed face in a photo that closely resembles one named face.
    Automatch on a photo. `named()` gives (ids, names, matrix) of every named face as
    unit vectors -- the matrix TagTuner keeps -- and is asked only when there are faces
    to match; a matrix of None names nobody.

    `changed`: the faces named."""
    return _automatch(library, named, photo_path=photo_path)


def automatch_folder(library, folder, named):
    """Automatch every photo in a folder, and the folders under it. As automatch_photo.

    details: `remaining_counts`, each photo's faces still unnamed, keyed as stored.
    """
    result = _automatch(library, named, folder=folder)
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        result.details["remaining_counts"] = faces.unnamed_counts(conn, folder)
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

    def remove(conn):
        return photos.remove_under(conn, folder)

    result.details.update(db.write_with_connection(library.path, remove, label="remove a folder"))
    result.changed = result.details["photos_removed"]
    logger.info("Removed %d photo(s) and %d face(s) under %s",
                result.details["photos_removed"], result.details["faces_removed"], folder)
    return result


def _automatch(library, named, photo_path=None, folder=None):
    """Automatch the unnamed faces in a photo, or under a folder. A face is given the name of the named
    face it most resembles, at AUTOMATCH_SIMILARITY or above, unless that name is already
    in its photo or proposed for two faces there.

    The faces are compared before the write lock is taken -- building the named matrix
    can take half a second -- and a face named meanwhile is left as it is."""
    _library_there(library)
    result = Result()
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        unnamed = faces.unnamed(conn, photo_path=photo_path, folder=folder)
    finally:
        conn.close()
    result.attempted = len(unnamed)
    if not unnamed:
        return result
    _ids, names, matrix = named()
    if matrix is None:
        return result

    proposed = {}
    for face_id, blob, face_photo in unnamed:
        similarities = np.dot(matrix, np.frombuffer(blob, dtype=np.float32))
        best = int(np.argmax(similarities))
        if similarities[best] >= AUTOMATCH_SIMILARITY:
            proposed.setdefault(face_photo, []).append((face_id, names[best]))
    if not proposed:
        return result

    def match(conn):
        count = 0
        for face_photo, faces_proposed in proposed.items():
            # Only the photos a name is proposed for, each by an index. Reading every
            # named face under the folder scanned the whole table inside the write lock,
            # and every other face action waited (docs/findings.md, #45).
            taken = faces.names_in_photo(conn, face_photo)
            proposed_names = [name for _fid, name in faces_proposed]
            gained = set()
            for face_id, name in faces_proposed:
                if proposed_names.count(name) > 1 or name in taken:
                    continue
                # A bulk guess, not a per-face human decision, so it is left as an
                # automatic assignment that re-clustering may revise.
                changed = faces.name_if_unnamed(conn, face_id, name)
                if changed:
                    gained.add(name)
                    count += changed
            if gained:
                photos.update_people(conn, face_photo, gained=sorted(gained))
        return count

    result.changed = db.write_with_connection(library.path, match, label="automatch faces")
    return result


def _library_there(library):
    if not os.path.exists(library.path):
        raise NotFound("Database not found")
