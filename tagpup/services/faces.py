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
import collections
import json
import logging
import os

import numpy as np

from tagpup.core import clustering, paths, validation, vocabulary
from tagpup.core.result import Conflict, NotFound, Result
from tagpup.services import photos as photo_files
from tagpup.services import thumbnails
from tagpup.store import db, face_tags, faces, faces_detected, faces_pending, photos
from tagpup.store import embeddings as store_embeddings
from tagpup.store import folders as store_folders

logger = logging.getLogger(__name__)

#: Why a face is excluded when no reason is given, why an ignored cluster's are, and every
#: reason one may be: the "exclusion reason" kind's (tagpup.core.validation).
DEFAULT_REASON = validation.DEFAULT_EXCLUSION_REASON
IGNORED_CLUSTER = validation.IGNORED_CLUSTER
EXCLUSION_REASONS = validation.EXCLUSION_REASONS


def name_face(library, face_id, person_name):
    """Name one face. Clicking a suggestion, or typing a name, on a face card.

    A person chose this, so it is recorded as a manual decision: re-clustering re-derives
    every name from scratch and must not discard it -- also when the face already carries
    this name as an automatic one (a person confirming a guess: `changed` 1). Refused when
    somebody else's face in the photo already carries the name; a Conflict for a face that
    has been excluded.
    """
    result = Result(attempted=1)
    person_name = (person_name or "").strip()
    problem = validation.problem("name", person_name)
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
        # Already this person: nothing to change but who decided -- an automatic name (clustering's, or a
        # photo's tag's, #788) a person now confirms becomes their decision, in the spelling it has.
        if old_name and vocabulary.key(old_name) == vocabulary.key(person_name):
            result.changed = faces.confirm(write.conn, face_id)
            result.details.update(face_ids=[face_id], fingerprints=(write.before, write.after))
            return result
        if faces.named_elsewhere_in_photo(write.conn, photo_path, person_name, face_id):
            result.refuse("Cannot match: '%s' is already tagged on another face in this photo."
                          % person_name)
            return result
        faces.name(write.conn, [face_id], person_name)
        result.changed = 1
    result.details.update(face_ids=[face_id], fingerprints=(write.before, write.after))
    return result


def check_nameable(library, face_id, person_name, refused):
    """What name_face would refuse, asked before anything else is written (face_people writes the photo's tag first): the
    photo's path and the name the face carries now (or None) when `face_id` can be named `person_name` -- also when it carries
    the name already -- and None when it cannot, `refused` (a Result) saying why. NotFound for a face that is not there, Conflict for one excluded. Reads only;
    name_face asks again under the write lock, which is the check that holds."""
    problem = validation.problem("name", person_name)
    if problem:
        refused.refuse(problem)
        return None
    _library_there(library)
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        row = faces.rows(conn, [face_id]).get(face_id)
        if not row:
            raise NotFound("Face ID not found")
        photo_path, old_name, excluded = row
        if excluded:
            raise Conflict("Cannot match: this face is excluded. Restore it first to name it.")
        if (not (old_name and vocabulary.key(old_name) == vocabulary.key(person_name))
                and faces.named_elsewhere_in_photo(conn, photo_path, person_name, face_id)):
            refused.refuse("Cannot match: '%s' is already tagged on another face in this photo." % person_name)
            return None
        return photo_path, old_name
    finally:
        conn.close()


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
    problem = validation.problem("name", person_name)
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
    refused = validation.problem("exclusion reason", reason)
    if refused:
        result.refuse(refused)
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
    Automatch on a photo. `named()` gives (ids, names, matrix) of the faces a person decided
    as unit vectors (tagpup.services.identify.decided_faces) -- never one automatch or
    clustering named that no keyword bears out, or a guess would be taken for a reference
    -- and is asked only when there are faces to match; a matrix of None names nobody.

    `changed`: the faces named. details as _automatch's."""
    return _automatch(library, named, photo_path=photo_path)


def automatch_folder(library, folder, named, rehearse=False):
    """Automatch every photo in a folder, and the folders under it. As automatch_photo.
    Re-examine this folder, in TagTuner.

    `rehearse`: decide by the same rules and write nothing -- what Re-examine asks before
    it names anyone. `changed` is then 0 and details say what would be named. Applying
    afterwards decides again: faces named, excluded or renamed in between change it.

    details: as _automatch's; applied, `remaining_counts` as well, each photo's faces still
    unnamed, keyed as stored.
    """
    result = _automatch(library, named, folder=folder, rehearse=rehearse)
    if rehearse:
        return result
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

    A folder a parent's add still covers (added with its subfolders: tagpup.store.folders)
    would stay the library's, and the next Suggest there would make it so again: it is put
    in the library's ignored folders, a journaled change of its settings, as the owner's
    own Ignore is (tagpup.services.settings.change). details["ignored"] says whether it was.

    details: `photos_removed`, `faces_removed`, `manual_lost`, `excluded_lost`, `ignored`.
    """
    from tagpup.services import settings   # settings reaches sync, which reaches the faces
    result = Result(attempted=1)

    def remove(conn):
        return photos.remove_under(conn, folder)

    # Their thumbnails go with them (tagpup.services.thumbnails): the ids are read before the rows go.
    gone = thumbnails.ids_of(library, folder=folder)
    result.details.update(db.write_with_connection(library.path, remove, label="remove a folder"))
    result.changed = result.details["photos_removed"]
    thumbnails.forget(library, gone)
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        still = store_folders.holds(conn, folder)
    finally:
        conn.close()
    result.details["ignored"] = False
    if still:
        ignored = settings.of(library).ignored + [paths.stored(folder)]
        change = settings.change(library, {settings.IGNORED: validation.FOLDER_SEPARATOR.join(ignored)})
        if change.ok:
            result.details["ignored"] = True
        else:
            result.fail(folder, "Removed, but a folder above it was added with its subfolders and it could not "
                                "be put in the ignored folders: %s" % change.message())
    logger.info("Removed %d photo(s) and %d face(s) under %s",
                result.details["photos_removed"], result.details["faces_removed"], folder)
    return result


def _automatch(library, named, photo_path=None, folder=None, rehearse=False):
    """Automatch the unnamed faces in a photo, or under a folder. A face is given the name of the named
    face it most resembles, when it may be named unasked (clustering.names_unasked), unless that name is already
    in its photo or proposed for two faces there.

    The faces are compared before the write lock is taken -- building the named matrix
    can take half a second, comparing thousands of faces a few seconds more -- and a face
    named or excluded meanwhile is left as it is, as is a face whose name no face carries
    any longer by the time of the write: its person was renamed, or unnamed everywhere,
    after the matrix was read, and writing the old spelling would bring back somebody
    who no longer exists.

    `rehearse`: decide on a read-only connection and write nothing.

    details: `dry_run`; `faces`, the faces named (or that would be); `photos`, the photos
    they are in; `people`, {name: faces}; `renamed`, the faces left because their person's
    name had gone. The same keys whether rehearsed or applied, so the page can say how the
    two differ. Applied, `named_ids` too, {face id: name} of the faces written."""
    _library_there(library)
    result = Result(details={"dry_run": bool(rehearse), "faces": 0, "photos": 0, "people": {}, "renamed": 0})
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

    proposed = _closest_named(unnamed, names, matrix)
    if not proposed:
        return result

    def decide(conn):
        """[(face_id, name, photo)] to name, and how many were left for a name gone."""
        given = faces.names_given(conn, {name for found in proposed.values() for _fid, name in found})
        chosen, gone = [], 0
        for face_photo, faces_proposed in proposed.items():
            # Only the photos a name is proposed for, each by an index. Reading every
            # named face under the folder scanned the whole table inside the write lock,
            # and every other face action waited (docs/findings.md, #45).
            taken = faces.names_in_photo(conn, face_photo)
            proposed_names = [name for _fid, name in faces_proposed]
            for face_id, name in faces_proposed:
                if proposed_names.count(name) > 1 or name in taken:
                    continue
                if name not in given:
                    gone += 1
                    continue
                chosen.append((face_id, name, face_photo))
        return chosen, gone

    def match(conn):
        # The write lock first, then the reads it decides by: a rename committed by the
        # other app or the CLI between the guard and the first UPDATE was written under
        # the old spelling, the connection being in no transaction until it wrote
        # (docs/findings.md, #645).
        db.begin(conn, immediate=True)
        chosen, gone = decide(conn)
        # A bulk guess, not a per-face human decision, so it is left as an automatic
        # assignment that re-clustering may revise. Only the faces still unnamed and in
        # play are named, name_if_unnamed's guard, and only those are counted: in one write
        # whose photos are rebuilt once (faces.name_unnamed; docs/findings.md, #659).
        named = set(faces.name_unnamed(conn, {face_id: name for face_id, name, _photo in chosen}))
        return ([(name, face_photo) for face_id, name, face_photo in chosen if face_id in named], gone,
                {face_id: name for face_id, name, _photo in chosen if face_id in named})

    if rehearse:
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            chosen, gone = decide(conn)
        finally:
            conn.close()
        done = [(name, face_photo) for _fid, name, face_photo in chosen]
    else:
        done, gone, named_ids = db.write_with_connection(library.path, match, label="automatch faces")
        result.changed = len(done)
        result.details["named_ids"] = named_ids
    result.details.update(faces=len(done), photos=len({face_photo for _name, face_photo in done}),
                          people=dict(collections.Counter(name for name, _photo in done)), renamed=gone)
    if not rehearse:
        result.details["photos_named"] = sorted({face_photo for _name, face_photo in done})
    return result


#: How many unnamed faces are compared with every named face at once: a block of named x
#: this many similarities, 70 MB for 35,000 named faces.
COMPARE_BLOCK = 512


def _closest_named(unnamed, names, matrix):
    """{photo: [(face_id, name)]} for each of `unnamed` (id, embedding, photo) whose closest
    named face it may be named after unasked. Compared a block at a time: one product per
    face made Re-examine on a folder of 2,600 photos and 9,400 unnamed faces, against
    36,000 named ones, take 32 to 47 seconds; a block at a time, 2 to 5. A face whose
    embedding is not the matrix's width is compared with nobody, where it failed the
    whole folder."""
    width = matrix.shape[1]
    usable = [(face_id, blob, face_photo) for face_id, blob, face_photo in unnamed
              if blob and len(blob) == width * 4]
    proposed = {}
    for start in range(0, len(usable), COMPARE_BLOCK):
        block = usable[start:start + COMPARE_BLOCK]
        vectors = np.stack([np.frombuffer(blob, dtype=np.float32) for _fid, blob, _photo in block])
        # A row per unnamed face: the closest named face is found along a row, in memory
        # order. Down a column of the transposed product the argmax alone took 2.6 s.
        similarities = vectors @ matrix.T
        best = np.argmax(similarities, axis=1)
        for row, (face_id, _blob, face_photo) in enumerate(block):
            closest = int(best[row])
            # As alike as naming a face with no one looking allows (tagpup.core.clustering).
            if clustering.names_unasked(float(similarities[row, closest])):
                proposed.setdefault(face_photo, []).append((face_id, names[closest]))
    return proposed


def _library_there(library):
    if not os.path.exists(library.path):
        raise NotFound("Database not found")


# ---- What TagPup's photo panel shows -------------------------------------------------------

def panel(library, photo_path):
    """The faces detected on one photo, for the strip under its details and the boxes over
    it: {"faces", "total", "unmatched", "size", "turned"}, each face with its box, area, name,
    prob, exclusion, and for an unnamed one the closest name elsewhere in the library and how
    alike. `size` is [width, height] of the pixels the boxes are in, or None, and `turned`
    says the photo declares an EXIF Orientation its boxes do not follow (photos.box_shape).

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
            "unmatched": sum(1 for f in found if f["name"] is None and not f["excluded"]),
            **photo_files.box_shape(photo_path)}


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


#: What a face model is told apart by: the settings it detects with (tagpup.ml.faces.SETTINGS).
DETECTOR_SETTINGS = ("min_face_size", "confidence_threshold", "mtcnn_thresholds")


def detector_of(model):
    """The name the detections of face model `model` are recorded under
    (tagpup.store.faces_detected): its settings. A setting it does not hold reads as None."""
    settings = {}
    for key in DETECTOR_SETTINGS:
        value = getattr(model, key, None)
        if isinstance(value, tuple):
            value = list(value)
        if not isinstance(value, (int, float, str, list, type(None))) or isinstance(value, bool):
            value = None
        settings[key] = value
    return faces_detected.detector(settings)


def ran(detected):
    """Did the detection that returned `detected` run to the end (tagpup.ml.faces.NotDetected)?"""
    return not getattr(detected, "failed", False)


def _record_detection(conn, photo_path, detector, detected):
    """Record that `detector` ran on the photo and what it found, unless it failed. The
    caller commits."""
    if detector is None or not ran(detected):
        return 0
    return faces_detected.record(conn, photo_path, detector, store_embeddings.stamp_of(photo_path), len(detected))


def record_detected(db_path, photo_path, detected, detector=None):
    """Record the faces found in a photo only when it has none yet. Returns rows inserted.

    Never deletes: replace_detected clears the photo's rows first, which would discard
    manual names and exclusions. This is for callers that detected faces as a side
    effect of doing something else (the suggester) and want to keep the work without
    disturbing anything already recorded.

    Given the `detector` that ran (detector_of), that it ran is recorded -- faces found or
    not -- so the photo is not detected again (tagpup.store.faces_detected, #773); a
    detection that failed (ran) is not recorded.

    On a connection of its own: callers run inside worker pools, and sharing one sqlite
    connection across threads is how "objects created in a thread" errors and lock
    contention start. Raised on inside the write, not swallowed: its retry waits out a
    locked database, and returning 0 before the retry saw the error lost a photo's faces
    for good.
    """
    if not ran(detected):
        return 0
    if not detected:
        # Detection ran and found no face: no face to record, and no longer anything still
        # to detect (store.faces_pending); that it ran is recorded, given its detector.
        if detector is None:
            conn = db.connect(db.readonly_uri(db_path), uri=True)
            try:
                marked = faces_pending.count(conn)
            finally:
                conn.close()
            if not marked:
                return 0

        def none_found(conn):
            faces_pending.clear(conn, [photo_path])
            _record_detection(conn, photo_path, detector, detected)
        db.write_with_connection(db_path, none_found, label="faces detected in %s" % os.path.basename(photo_path))
        return 0

    def insert(conn):
        # Detection ran: the photo's faces are no longer still to detect (store.faces_pending).
        faces_pending.clear(conn, [photo_path])
        _record_detection(conn, photo_path, detector, detected)
        if faces.count_for_photo(conn, photo_path) > 0:
            return 0  # already recorded; leave it alone
        inserted = 0
        for face in detected:
            if face.get("embedding") is None:
                continue
            _insert_detected(conn, photo_path, face)
            inserted += 1
        if inserted:
            face_tags.name_paths(conn, [photo_path])   # the photo's person tag names its face (#788)
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


def replace_detected(conn, photo_path, detected, detector=None):
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
        if detected:
            face_tags.name_paths(conn, [photo_path])   # (#788)
        faces_pending.clear(conn, [photo_path])
        _record_detection(conn, photo_path, detector, detected)
        conn.commit()
    except Exception as e:
        logger.error(f"Error saving faces for {photo_path}: {e}")
        conn.rollback()


def record_batch(conn, batch, overwrite=False, detector=None):
    """Record the faces found in a batch of photos ({path: faces}) in one transaction.

    By default a photo that already has face rows is left alone. Those rows carry
    assigned names, manual overrides, exclusions and cached crops, and re-detection
    produces none of that -- so replacing them silently discards curation. Re-indexing
    a folder used to do exactly that, which mattered little while only tagged photos
    were indexed and matters a great deal now that every photo is.

    Pass overwrite=True to force re-detection, accepting the loss. Without a connection,
    nothing. Given the `detector` that ran (detector_of), that it ran on each photo is
    recorded, faces found or not (tagpup.store.faces_detected, #773), unless it failed.
    """
    if conn is None or not batch:
        return
    try:
        db.begin(conn)
        recorded = []
        for photo_path, detected in batch.items():
            _record_detection(conn, photo_path, detector, detected)
            if not overwrite and faces.count_for_photo(conn, photo_path) > 0:
                continue
            faces.remove_for_photo(conn, photo_path)
            for face in detected:
                _insert_detected(conn, photo_path, face, name=face.get("name"))
            if detected:
                recorded.append(photo_path)
        # The photos' person tags name their faces (#788), once the batch's faces are all there.
        face_tags.name_paths(conn, recorded)
        # Detection ran on each photo of the batch: none is still to detect (store.faces_pending).
        faces_pending.clear(conn, list(batch))
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


def named_counts(library, folder=None):
    """{"named", "unnamed"}: the faces in play (not excluded) that have a name and those that have none, in the whole
    library or in the photos under `folder` at any depth. A look; a library that cannot be read raises."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        named, unnamed = faces.named_and_unnamed(conn, folder)
    finally:
        conn.close()
    return {"named": named, "unnamed": unnamed}


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
