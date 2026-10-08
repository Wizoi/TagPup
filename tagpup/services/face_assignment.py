"""What a bulk assignment of faces is made of: a plan, written once, and the steps it is carried out in (docs/ARCHITECTURE.md,
"Faces and the photo's person tag", #907; run by tagpup.jobs.face_assignments).

Naming faces, taking their names off or ruling them out for many photos from one click -- Identify Faces' Assign, Unmatch
and Ignore cluster, and Re-examine this folder -- are one decision per face AND the photo's person tag, as a single face's is
(tagpup.services.face_people, the one owner of that rule). A selection of hundreds of faces means hundreds of photo files to
write, so it is not one request's work: this module makes the PLAN, and the job runs it a step at a time.

* **A plan** is what the click meant, decided up front, read-only, and refused as a whole for what the single write refuses
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

from tagpup.core import validation
from tagpup.core.result import Refused
from tagpup.files import job_files
from tagpup.services import face_people
from tagpup.services import faces as faces_service
from tagpup.store import db, faces, person_ids

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
    plan = {"op": op, "steps": steps, "person_name": None, "reason": None, "undo": False, "names": {},
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


def _named(library, face_ids):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return faces.named_by_id(conn, face_ids)
    finally:
        conn.close()


def _removal_steps(library, face_ids):
    """(steps, {face id: name}): the faces that carry a name are the steps that read and write photos, a few at a time; the
    others, which touch no photo, many at a time, first."""
    face_ids = list(dict.fromkeys(face_ids))
    names = _named(library, face_ids)
    plain = [face_id for face_id in face_ids if face_id not in names]
    named = [face_id for face_id in face_ids if face_id in names]
    return _chunks(plain, PLAIN_CHUNK) + _chunks(named, CHUNK), names


def plan_unname(library, face_ids, undo=False):
    """The plan of taking the names off `face_ids`; `undo`: an assignment taken back, the faces left unreviewed."""
    steps, names = _removal_steps(library, face_ids)
    return _plan(UNNAME, steps, undo=bool(undo), names={str(face_id): name for face_id, name in names.items()})


def plan_exclude(library, face_ids, reason=None):
    """The plan of ruling `face_ids` out (Exclude selected, Ignore cluster). Refused for a reason that is not one."""
    reason = (reason or "").strip().lower() or faces_service.DEFAULT_REASON
    problem = validation.problem("exclusion reason", reason)
    if problem:
        raise Refused(problem)
    steps, names = _removal_steps(library, face_ids)
    return _plan(EXCLUDE, steps, reason=reason, names={str(face_id): name for face_id, name in names.items()})


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
