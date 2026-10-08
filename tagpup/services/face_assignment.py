"""What a bulk assignment of faces is made of: a plan, written once, and the steps it is carried out in (docs/ARCHITECTURE.md,
"Faces and the photo's person tag", #907; run by tagpup.jobs.face_assignments).

Naming faces, taking their names off or ruling them out for many photos from one click -- Identify Faces' Assign, Unmatch
and Ignore cluster, and Re-examine this folder -- are one decision per face AND the photo's person tag, as a single face's is
(tagpup.services.face_people, the one owner of that rule). A selection of hundreds of faces means hundreds of photo files to
write, so it is not one request's work: this module makes the PLAN, and the job runs it a step at a time.

* **A plan** (it holds the people's NAMES -- `person_name`, and `names` for the faces a removal had -- so it is kept in the
  library's cache folder only as long as the job's record is, 30 days, as a bulk edit's state names its people) is what the click
  meant, decided up front, read-only, and refused as a whole for what the single write refuses
  (two faces of one photo, the person already on another face of the photo, a person the tree files in two places): `op`
  (name, unname, exclude or guess), the steps -- each a few faces, the faces of one photo never split between two -- and for
  unname/exclude `names`, {face id: the name it carried when the click was made}. It is kept (`write_plan`) BEFORE the first
  step is run, so that a stop at any point leaves what to finish.
* **A step** (`run_step`) is one chunk of face_people's, which keeps the rule of its direction: naming writes the tag first and
  names the faces of the photos it could write; unnaming and ruling out change the faces first and take the tags off after
  (the photos whose person no face carries any more); Re-examine writes its guesses under the faces' write lock and then
  writes the tags of every face of the step that carries its planned name, which is also what completes a step run again.
  Every step is idempotent, which is what makes a resume safe: a face named already is not named again, a tag the photo holds
  is not written again, a person the photo no longer holds is not taken off again.
"""
import logging
import os

from tagpup.core import validation
from tagpup.core import paths
from tagpup.core.result import Refused, Result
from tagpup.files import job_files
from tagpup.services import face_people
from tagpup.services import journal as journal_service
from tagpup.services import faces as faces_service
from tagpup.store import db, faces, file_journal, person_ids

logger = logging.getLogger(__name__)

NAME, UNNAME, EXCLUDE, GUESS = "name", "unname", "exclude", "guess"
OPS = (NAME, UNNAME, EXCLUDE, GUESS)

#: What a run says it is, in the Activity page and the status: never a person's name.
DESCRIPTIONS = {NAME: "assign faces to a person", UNNAME: "take the names off faces", EXCLUDE: "ignore faces",
                GUESS: "re-examine a folder"}

#: Faces (photos, for Re-examine) in a step that writes photo files: one journaled change of photo files each (History lists
#: them), the lock of changes of photo files taken for the step only. As face_people.CHUNK.
CHUNK = face_people.CHUNK

#: Faces in a step that touches no photo (a face that carried no name): the faces' own write, in a few transactions.
PLAIN_CHUNK = 500


def _chunks(items, size):
    items = list(items)
    return [items[start:start + size] for start in range(0, len(items), size)]


def _plan(op, steps, **more):
    plan = {"op": op, "steps": steps, "person_name": None, "reason": None, "undo": False, "names": {}, "sources": {},
            "skipped_excluded": [], "looked_at": 0}
    plan.update(more)
    plan["faces"] = sum(len(step) for step in steps)
    return plan


def plan_name(library, face_ids, person_name):
    """The plan of naming `face_ids` as `person_name`: Refused, nothing done, for what faces_service.name_faces refuses.
    `skipped_excluded` are the faces left alone because they are ruled out; a face already under the name is no step."""
    person_name = (person_name or "").strip()
    check = faces_service.nameable(library, face_ids, person_name)
    if check.refused:
        raise Refused(check.refused)
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        branch = person_ids.read(conn).why_not(person_name) == "branch"
    finally:
        conn.close()
    if face_people.Filer(library).tag(person_name) is None and not branch:
        raise Refused(face_people.not_filed(person_name))
    if branch:
        # A branch of the tree is never a person (owner, 2026-10-04): a photo is not tagged with the branch.
        raise Refused("%s is a branch of the tag tree, not a person: name a person under it." % person_name)
    # A step names one face of a photo; the check refused two in a photo, so the order of the faces is free.
    return _plan(NAME, _chunks(check.details["matched_ids"], CHUNK), person_name=person_name,
                 skipped_excluded=list(check.details["skipped_excluded"]))


def _prior(library, face_ids):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return faces.decided_by_id(conn, face_ids)
    finally:
        conn.close()


def _removal_steps(library, face_ids):
    """(steps, {face id: (name, name_source)}): the faces that carry a name are the steps that read and write photos, a few at a
    time; the others, which touch no photo, many at a time, first. What the named ones were is kept for the job's Undo."""
    face_ids = list(dict.fromkeys(face_ids))
    names = _prior(library, face_ids)
    plain = [face_id for face_id in face_ids if face_id not in names]
    named = [face_id for face_id in face_ids if face_id in names]
    return _chunks(plain, PLAIN_CHUNK) + _chunks(named, CHUNK), names


def plan_unname(library, face_ids, undo=False):
    """The plan of taking the names off `face_ids`; `undo`: an assignment taken back, the faces left unreviewed."""
    steps, names = _removal_steps(library, face_ids)
    return _plan(UNNAME, steps, undo=bool(undo), names={str(face_id): name for face_id, (name, _s) in names.items()},
                 sources={str(face_id): source for face_id, (_n, source) in names.items()})


def plan_exclude(library, face_ids, reason=None):
    """The plan of ruling `face_ids` out (Exclude selected, Ignore cluster). Refused for a reason that is not one."""
    reason = (reason or "").strip().lower() or faces_service.DEFAULT_REASON
    problem = validation.problem("exclusion reason", reason)
    if problem:
        raise Refused(problem)
    steps, names = _removal_steps(library, face_ids)
    return _plan(EXCLUDE, steps, reason=reason, names={str(face_id): name for face_id, (name, _s) in names.items()},
                 sources={str(face_id): source for face_id, (_n, source) in names.items()})


def plan_guesses(library, folder, named):
    """The plan of Re-examine on `folder`: the guesses propose_guesses makes, in steps of whole photos. `named()` gives the
    decided faces. Decided again under the write lock when each step is run, as an automatch always is."""
    looked_at, proposed = faces_service.propose_guesses(library, named, folder=folder)
    photos = sorted(proposed)
    steps = [[[face_id, name] for photo in group for face_id, name in proposed[photo]]
             for group in _chunks(photos, CHUNK)]
    return _plan(GUESS, steps, looked_at=looked_at, folder=folder)


# ---- Running a step -----------------------------------------------------------------------------------------

def _names(plan):
    return {int(face_id): name for face_id, name in plan["names"].items()}


def named_still(library, plan, face_ids):
    """The faces among `face_ids` that still carry the name `plan` (a naming or Re-examine's) gave them: a face a person renamed
    since is theirs."""
    wanted = {face_id: name for step in plan["steps"] for face_id, name in (
        [(face_id, plan["person_name"]) for face_id in step] if plan["op"] == NAME else step)}
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        now = faces.rows(conn, list(face_ids))
    finally:
        conn.close()
    return [face_id for face_id in face_ids if face_id in now and now[face_id][1]
            and now[face_id][1].lower() == str(wanted.get(face_id, "")).lower()]


def unname(library, face_ids):
    """The faces an assignment named, back to unreviewed (not "nobody"): the rows changed."""
    return faces_service.unname_faces(library, face_ids, undo=True).changed if face_ids else 0


def _photo_of_faces(library, face_ids):
    """{face id: photo path} of the faces that exist."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return {face_id: row[0] for face_id, row in faces.rows(conn, list(face_ids)).items()}
    finally:
        conn.close()


def put_back(library, plan, blocked=()):
    """The faces a removal (unname, exclude) took the names of, as they were: ruled out ones restored, then each named again by the
    name and the decider it had (a face named or ruled out since is left) -- except the faces of the photos in `blocked` (path
    keys: their tag could not be put back, so the person is not on the photo yet). The faces put back."""
    blocked = set(blocked)
    prior = prior_of(plan)
    where = _photo_of_faces(library, set(prior) | {face_id for step in plan["steps"] for face_id in step})
    free = {face_id for face_id, photo in where.items() if paths.key(photo) not in blocked}
    if plan["op"] == EXCLUDE:
        faces_service.restore(library, [face_id for step in plan["steps"] for face_id in step if face_id in free])
    prior = {face_id: each for face_id, each in prior.items() if face_id in free}
    return len(db.write_with_connection(library.path, lambda conn: faces.reinstate(conn, prior),
                                        label="put back the names a job took off"))


def _applied(library, change_ids):
    """The changes among `change_ids` the journal still has applied (not undone yet)."""
    found = []
    for change_id in change_ids:
        change = file_journal.change(library.path, change_id)
        if change is not None and change.status == "applied":
            found.append(change_id)
    return found


def _photos_of_changes(library, change_ids):
    """{path key: path} of the photos whose files the changes wrote (every file of them, whatever it is now)."""
    found = {}
    for change_id in change_ids:
        for row in file_journal.files_of(library.path, change_id):
            if not row.is_rename:
                found[paths.key(row.path)] = row.path
    return found


def undo(library, plan, state, exiftool_path):
    """Undo a finished or stopped job as one, whatever has been done of its undo before (every step reads the state and does what is
    left, so a retry when a share is back finishes it): a Result, `changed` the faces put back, details `files` (the files put
    back through the journal), `remaining` (the photos whose file could not be put back: their faces are left as they are, and
    the job stays undoable) and errors saying why.

    The order is the rule of its direction. An assignment's faces are unnamed first, then the tags it wrote taken off; a removal's
    tags are put back first, then its faces named again -- only the faces of the photos whose file really went back. The files
    go back through the journal's own undo of the changes THIS job wrote; a file the journal refuses (changed since) or a change
    already undone with files left is finished by the same machinery a single face uses (add_people, remove_people), which merges
    into the file and never overwrites it."""
    result = Result(details={"files": 0, "remaining": 0})
    writer = face_people.Writer(exiftool_path)
    op = plan["op"]
    changes = list(state.get("changes") or [])
    photos = _photos_of_changes(library, changes)
    ids = [int(face_id) for face_id in state.get("matched_ids") or []]
    items = []
    if op in (NAME, GUESS):
        # Faces first (the faces a person named since are theirs), then the tags this job wrote.
        named = named_still(library, plan, ids)
        wanted = {face_id: name for step in plan["steps"] for face_id, name in (
            [(face_id, plan["person_name"]) for face_id in step] if op == NAME else step)}
        # The people to take off are those the job named, whether or not their faces are still named (an earlier try of this undo
        # may have unnamed them): untag_plan keeps a person who is on a face of the photo now.
        where = _photo_of_faces(library, ids)
        items = [(where[face_id], wanted[face_id]) for face_id in ids if face_id in where and face_id in wanted]
        result.changed = unname(library, named)
    for change_id in reversed(_applied(library, changes)):
        try:
            undone = journal_service.undo(library, change_id, apply=True, exiftool_path=exiftool_path)
            result.details["files"] += undone.changed
        except Exception:
            logger.exception("Could not undo change %s", change_id)
    blocked = set()
    if op in (NAME, GUESS):
        plan_off = {path: tags for path, tags in face_people.untag_plan(library, items).items() if paths.key(path) in photos}
        if plan_off:
            removed = face_people.remove_people(library, plan_off, writer)
            if removed.refused:
                blocked |= {paths.key(path) for path in plan_off}
            blocked |= {paths.key(what) for what, _why in removed.errors + removed.skipped}
            for what, why in removed.errors:
                result.fail(os.path.basename(str(what)), why)
    else:
        prior_items = [(photo, name) for face_id, (name, _s) in prior_of(plan).items()
                       for photo in [_photo_of_faces(library, [face_id]).get(face_id)] if photo and paths.key(photo) in photos]
        if prior_items:
            tagged = face_people.add_people(library, prior_items, writer, stop_at_first_error=False)
            if tagged.refused:
                blocked |= {paths.key(photo) for photo, _name in prior_items}
            blocked |= {paths.key(what) for what, _why in tagged.errors + tagged.skipped}
            for what, why in tagged.errors:
                result.fail(os.path.basename(str(what)), why)
        result.changed = put_back(library, plan, blocked)
    result.details["remaining"] = len(blocked)
    return result


def prior_of(plan):
    """{face id: (name, name_source)} the faces of a removal had when its click was made: what its Undo puts back."""
    return {int(face_id): (name, plan.get("sources", {}).get(face_id)) for face_id, name in plan["names"].items()}


def run_step(library, plan, index, writer):
    """Run step `index` of `plan`, as many times as is wanted: a face_people Result. `writer` is where photo tags are written
    (face_people.Writer). Raises what the face writes raise (NotFound, Conflict); the job counts it as the step's error."""
    step, op = plan["steps"][index], plan["op"]
    if op == NAME:
        return face_people.name_faces(library, step, plan["person_name"], writer)
    if op == UNNAME:
        names = {face_id: name for face_id, name in _names(plan).items() if face_id in set(step)}
        return face_people.unname_faces(library, step, writer, undo=plan["undo"], names=names)
    if op == EXCLUDE:
        names = {face_id: name for face_id, name in _names(plan).items() if face_id in set(step)}
        return face_people.exclude(library, step, plan["reason"], writer, names=names)
    return face_people.name_guesses(library, _by_photo(library, step), writer)


def _by_photo(library, step):
    """{photo path: [(face id, name)]} for a Re-examine step, the photo each face is in now."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        where = faces.rows(conn, [face_id for face_id, _name in step])
    finally:
        conn.close()
    found = {}
    for face_id, name in step:
        if face_id in where:
            found.setdefault(where[face_id][0], []).append((face_id, name))
    return found


# ---- What a restart finds ------------------------------------------------------------------------------------

def write_plan(library, job, plan):
    job_files.write_plan(library.bulk_jobs, job, plan)


def read_plan(library, job):
    return job_files.read_plan(library.bulk_jobs, job)


def write_state(library, job, state):
    job_files.write_state(library.bulk_jobs, job, state)


def read_state(library, job):
    return job_files.read_state(library.bulk_jobs, job)


def forget(library, job):
    job_files.forget(library.bulk_jobs, job)


def sweep(library):
    return job_files.sweep(library.bulk_jobs)
