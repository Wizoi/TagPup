"""Naming faces, and taking them out of identity work: TagTuner's Identify Faces.

Each photo's people follow its faces through the faces store, which rebuilds them at
every write (tagpup.store.people.rebuild). Each action writes through the library's
write lock: most of them wrote on a connection of their own.

TagTuner caches its Identify Faces grids against a fingerprint of the faces table. The
actions that take faces out of the pool -- naming, excluding -- write through
tagpup.store.faces.accounted_write and return `fingerprints` (before, after) and
`face_ids` in details, from which the page's server takes those faces off its cached
grids instead of rebuilding them. The rest move the fingerprint, and a grid built before
them is rebuilt.
"""
import json
import logging
import os

import numpy as np

from tagpup.core import clustering, vocabulary
from tagpup.core.result import Conflict, NotFound, Result
from tagpup.store import db, faces, photos

logger = logging.getLogger(__name__)

#: Why a face is excluded when no reason is given.
DEFAULT_REASON = "not a person"

#: Why the faces of a cluster TagTuner's page was told to ignore are excluded.
IGNORED_CLUSTER = "ignored cluster"

#: Why a face may be excluded: the four TagTuner's page offers (EXCLUDE_REASONS in
#: gui/app.js, the first its default), and the one it sets itself when a cluster is
#: ignored. The page keeps a copy, which tests/test_rules_have_one_owner.py holds to
#: this. The reason used to be free text, and collected "fuzzy" beside "bad crop"
#: (docs/findings.md, #53, #74).
EXCLUSION_REASONS = (DEFAULT_REASON, "stranger", "bad crop", "duplicate", IGNORED_CLUSTER)


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
        return faces.unname(conn, face_ids, source)

    result.changed = db.write_with_connection(library.path, unname, label="unname faces")
    return result


def unname_photo(library, photo_path):
    """Take the names off every face in a photo. Unmatch All on a photo."""
    _library_there(library)
    result = Result(attempted=1)

    def unname(conn):
        return faces.unname_photo(conn, photo_path)

    result.changed = db.write_with_connection(library.path, unname, label="unname a photo's faces")
    return result


def exclude(library, face_ids, reason=None):
    """Take faces out of identity work: a crowd's passers-by, a crop that is no face.
    Ignoring a face or a cluster.

    Left in, they cluster, vote, and drag centroids around. Excluding is reversible and
    keeps the row, so the face still exists on the photo; it stops being a candidate for
    anyone. Any name it carried goes, with name_source left 'manual' so re-clustering
    cannot quietly put one back.

    `changed`: the rows excluded, not the ids sent. details: `face_ids`, `fingerprints`.
    Refused for a reason not in EXCLUSION_REASONS; none is DEFAULT_REASON.
    """
    _library_there(library)
    result = Result(attempted=len(face_ids))
    reason = (reason or "").strip().lower() or DEFAULT_REASON
    if reason not in EXCLUSION_REASONS:
        result.refuse("'%s' is not a reason to exclude a face: use one of %s."
                      % (reason, ", ".join(EXCLUSION_REASONS)))
        return result
    with faces.accounted_write(library.path, "exclude faces") as write:
        result.changed = faces.exclude(write.conn, face_ids, reason)
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
    face it most resembles, when it may be named unasked (clustering.names_unasked), unless that name is already
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
        # As alike as naming a face with no one looking allows (tagpup.core.clustering).
        if clustering.names_unasked(float(similarities[best])):
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
            for face_id, name in faces_proposed:
                if proposed_names.count(name) > 1 or name in taken:
                    continue
                # A bulk guess, not a per-face human decision, so it is left as an
                # automatic assignment that re-clustering may revise.
                count += faces.name_if_unnamed(conn, face_id, name)
        return count

    result.changed = db.write_with_connection(library.path, match, label="automatch faces")
    return result


def _library_there(library):
    if not os.path.exists(library.path):
        raise NotFound("Database not found")


# ---- What TagPup's photo panel shows -------------------------------------------------------

def panel(library, photo_path):
    """The faces detected on one photo, for the strip under its details: {"faces",
    "total", "unmatched"}, each face with its box, area, name, prob, exclusion, and for
    an unnamed one the closest name elsewhere in the library and how alike.

    TagPup ran face recognition invisibly: the suggester matched faces and surfaced
    only a name pill, so there was no way to see which face was unrecognised while
    tagging. The similarity is to the nearest single resolved face of that person,
    the measure TagTuner's suggestion list uses, so the two agree on how confident a
    match looks. Below the value every screen offers a name from
    (tagpup.core.clustering.is_offered) the nearest name is noise, not a candidate:
    it was 0.5 here, which two strangers in three reach (docs/findings.md, #75).
    Named first, then by confidence, then largest first: the faces needing attention
    are the ones the eye should land on.
    """
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        rows = faces.in_photo_for_panel(conn, photo_path)
        if not rows:
            return {"faces": [], "total": 0, "unmatched": 0}
        known_names, known_vectors = [], []
        for name, emb in faces.named_embeddings_elsewhere(conn, photo_path):
            vec = _unit(emb)
            if vec is not None:
                known_names.append(name)
                known_vectors.append(vec)
    finally:
        conn.close()
    known = np.array(known_vectors, dtype=np.float32) if known_vectors else None

    found = []
    for face_id, box_json, name, prob, emb, excluded, reason in rows:
        try:
            box = json.loads(box_json) if box_json else []
        except Exception:
            box = []
        suggestion = similarity = None
        vec = _unit(emb) if known is not None and not excluded else None
        if vec is not None:
            sims = known @ vec
            best = int(np.argmax(sims))
            if clustering.is_offered(float(sims[best])):
                suggestion, similarity = known_names[best], round(float(sims[best]), 4)
        found.append({
            "id": face_id, "box": box,
            "area": (box[2] - box[0]) * (box[3] - box[1]) if len(box) >= 4 else 0,
            "name": name, "prob": prob, "excluded": bool(excluded), "excluded_reason": reason,
            "suggestion": suggestion if name is None else None,
            "similarity": similarity if name is None else None,
        })
    found.sort(key=lambda f: (f["name"] is None, -(f["similarity"] or 0.0), -f["area"]))
    return {"faces": found, "total": len(found),
            "unmatched": sum(1 for f in found if f["name"] is None and not f["excluded"])}


def _unit(embedding):
    """A face's stored embedding as a unit vector, or None for an empty one."""
    if not embedding:
        return None
    vec = np.frombuffer(embedding, dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else None


# ---- The faces detection finds ---------------------------------------------------------
#
# What indexing and Suggest record of the faces a face model found in a photo
# (tagpup.ml.faces), and the named faces a new one is compared with. PhotoIndex held a
# wrapper for each (docs/ARCHITECTURE.md, phase 5.5).

def _insert_detected(conn, photo_path, face, name=None):
    faces.insert(conn, photo_path, face.get("box", []),
                 np.array(face["embedding"], dtype=np.float32).tobytes(),
                 name=name, crop=face.get("crop_image"), prob=face.get("prob"))


def record_detected(db_path, photo_path, detected):
    """Record the faces found in a photo only when it has none yet. Returns rows inserted.

    Never deletes: replace_detected clears the photo's rows first, which would discard
    manual names and exclusions. This is for callers that detected faces as a side
    effect of doing something else (the suggester) and want to keep the work without
    disturbing anything already recorded.

    On a connection of its own: callers run inside worker pools, and sharing one sqlite
    connection across threads is how "objects created in a thread" errors and lock
    contention start. Raised on inside the write, not swallowed: its retry waits out a
    locked database, and returning 0 before the retry saw the error lost a photo's faces
    for good.
    """
    if not detected:
        return 0

    def insert(conn):
        if faces.count_for_photo(conn, photo_path) > 0:
            return 0  # already recorded; leave it alone
        inserted = 0
        for face in detected:
            if face.get("embedding") is None:
                continue
            _insert_detected(conn, photo_path, face)
            inserted += 1
        return inserted

    try:
        return db.write_with_connection(
            db_path, insert, label="recording faces for %s" % os.path.basename(photo_path))
    except Exception as e:
        # Only once the retries are spent. Losing the faces for a photo is not a
        # warning-shaped event: they are gone until it is indexed again.
        logger.error(
            "Detected faces for %s were NOT saved (%s). Re-index this folder to "
            "recover them.", photo_path, e
        )
        return 0


def replace_detected(conn, photo_path, detected):
    """Replace a photo's faces with freshly detected ones, and commit.

    This discards any names, manual overrides, exclusions and cached crops on the
    existing rows: it is for explicit re-detection. Logged, not raised. Without a
    connection, nothing.
    """
    if conn is None:
        return
    try:
        faces.remove_for_photo(conn, photo_path)
        for face in detected:
            _insert_detected(conn, photo_path, face, name=face.get("name"))
        conn.commit()
    except Exception as e:
        logger.error(f"Error saving faces for {photo_path}: {e}")
        conn.rollback()


def record_batch(conn, batch, overwrite=False):
    """Record the faces found in a batch of photos ({path: faces}) in one transaction.

    By default a photo that already has face rows is left alone. Those rows carry
    assigned names, manual overrides, exclusions and cached crops, and re-detection
    produces none of that -- so replacing them silently discards curation. Re-indexing
    a folder used to do exactly that, which mattered little while only tagged photos
    were indexed and matters a great deal now that every photo is.

    Pass overwrite=True to force re-detection, accepting the loss. Without a connection,
    nothing.
    """
    if conn is None or not batch:
        return
    try:
        db.begin(conn)
        for photo_path, detected in batch.items():
            if not overwrite and faces.count_for_photo(conn, photo_path) > 0:
                continue
            faces.remove_for_photo(conn, photo_path)
            for face in detected:
                _insert_detected(conn, photo_path, face, name=face.get("name"))
        conn.commit()
    except Exception as e:
        logger.error(f"Error saving faces batch to SQLite: {e}")
        conn.rollback()
        raise e


def clear_automatic_names(conn):
    """Clear the names clustering gave to faces, and commit; the store rebuilds the
    people of the photos they were in. Returns the number of faces whose name was cleared.

    Names given by hand (name_source = 'manual') are left alone: they are the person's
    decisions and the anchors clustering starts from. Clearing their name while leaving
    name_source = 'manual' turned every one of them into a binding "this is nobody".
    Without a connection, 0.
    """
    if conn is None:
        return 0
    try:
        cleared = faces.clear_automatic_names(conn)
        conn.commit()
        return cleared
    except Exception as e:
        logger.error(f"Error resetting face assignments in database: {e}")
        conn.rollback()
        raise e


def known_faces(db_path):
    """Every named face that is not excluded, as tagpup.core.clustering.KnownFaces: what a
    face is compared with to say who it is. It was each person's mean face, with no
    years (docs/findings.md, #71).

    Reads only the named faces, and of each photo only its Date Taken fields. Reading
    every face and its crop is 225,000 rows and ten seconds on a cold cache, and the
    suggester did it twice for every photo.
    """
    conn = db.connect(db_path, timeout=30.0)
    try:
        rows = faces.named_for_known(conn)
    finally:
        conn.close()
    return clustering.KnownFaces.of(
        (name, np.frombuffer(emb_bytes, dtype=np.float32), year, photo_path)
        for name, emb_bytes, photo_path, year in rows)
