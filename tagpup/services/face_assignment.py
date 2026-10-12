"""A bulk assignment of faces: what a click on Assign, Unmatch, Ignore cluster or Re-examine means, decided and refused as a whole, then
carried out as ONE batch of photo files (docs/ARCHITECTURE.md, "One batch model"; DECISIONS 2026-10-11).

Naming, taking the names off or ruling out the faces of many photos is one decision per face AND the photo's person tag, as a single face's
is (tagpup.services.face_people). So it is the journaled change `file_changes.write_fields` plans: the file step of each photo is the
person's tag written or taken off, and the photo's faces follow it in the transaction that marks the file done (tagpup.services.face_batch,
tagpup.store.face_batches), so a photo and its faces never disagree -- forward, settled after a crash, or undone from History. The batch
has the claim, the queue, the cap, Cancel, the strip's progress and the hand-over of every other (tagpup.services.batches); there is no
job of its own to resume or to undo as one: a batch stopped, or one that left some photos, is asked for again, and History undoes the
change it wrote.

* **The plan** (`plan_name`, `plan_unname`, `plan_exclude`, `plan_guesses`) reads and refuses as a whole, before a file is touched: two faces
  of one photo for one person, the person already on another face of it, a person the tree files in two places, a reason that is not
  one. It records each face as it is read (`old`) and as it is to be (`new`), by the journal's own rule: the face is written only
  if it is still what the plan read, and left as it is, and said, if somebody changed it since.
* **The faces with nothing to write** -- Unmatch or Ignore on faces that carry no name -- touch no photo and are written by the faces'
  own write, after the batch, a few hundred a transaction (their photos are not read). A cancelled or refused batch leaves them as they are.
* **More than batches.MAX_BATCH photos** that need their files looked at are refused whole, nothing changed, saying so: the faces
  of nameless photos are not counted.
"""
import logging
import os

from tagpup.core import paths, validation
from tagpup.core.result import Conflict, Refused, Result
from tagpup.core.vocabulary import Ref
from tagpup.services import batches, face_people, file_changes, libraries, tagging
from tagpup.services import faces as faces_service
from tagpup.services import people as people_service
from tagpup.services.face_batch import DESCRIPTIONS, EXCLUDE, GUESS, NAME, UNNAME, Work  # noqa: F401  (the ops, for the routes)
from tagpup.store import db, faces, person_ids, taxonomy

logger = logging.getLogger(__name__)

#: Faces written a transaction when they touch no photo.
PLAIN_CHUNK = 500


class Plan:
    """What a click means. `work` is the faces with a photo to write (face_batch.Work); `plain` the faces with none."""

    def __init__(self, op, work, plain=(), **more):
        self.op, self.work, self.plain = op, work, list(plain)
        self.person_name = self.person_id = self.reason = None
        self.skipped_excluded, self.problems = [], []
        self.__dict__.update(more)


def _new(old, **changes):
    """A face's decision after the change: `old` with `changes` (a column the library lacks is left out)."""
    return dict(old, **{column: value for column, value in changes.items() if column in old})


def _read(library, read):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return read(conn)
    except person_ids.PersonProblem as problem:
        people_service.translate(problem)
    finally:
        conn.close()


def plan_name(library, face_ids, person):
    """The plan of naming `face_ids` as `person` (the id of their node, or a tag path or a name): Refused, nothing done, for a name two
    people have, a group, two faces of one photo, the person on another face of the photo. `skipped_excluded` are the faces left alone
    because they are ruled out; a face already under the person is no part of it."""
    check = faces_service.nameable(library, face_ids, person)
    if check.refused:
        raise Refused(check.refused)
    ref, person_name = faces_service.who_is(library, person, Result())
    tag = face_people.Filer(library).tag(ref)
    if tag is None:
        raise Refused(face_people.not_filed(person_name))
    wanted = check.details["matched_ids"]
    found, target = _read(library, lambda conn: (faces.planning(conn, wanted), person_ids.target(conn, ref)))
    work = Work(NAME)
    work.people = [ref]
    for face_id in wanted:
        if face_id in found:
            _photo, path, old = found[face_id]
            work.face(path, face_id, old, _new(old, name=target[1], tag_id=target[0], name_source="manual"))
            work.add[paths.key(path)] = [tag]
    return Plan(NAME, work, person_name=person_name, person_id=ref if isinstance(ref, int) else None,
                skipped_excluded=list(check.details["skipped_excluded"]))


def _removal(library, op, face_ids, **changes):
    """The plan of a removal: the faces that carry a person are written with their photos (the person's tag comes off a photo
    unless another face of it is them, which is decided here from the faces as they are); the others are `plain`."""
    face_ids = list(dict.fromkeys(face_ids))

    def read(conn):
        found = faces.planning(conn, face_ids)
        named = {face_id: each for face_id, each in found.items() if each[2]["name"] is not None and not each[2]["excluded"]}
        return found, named, faces.carried_on(conn, {each[0] for each in named.values()})

    found, named, carried = _read(library, read)
    work, by_photo = Work(op), {}
    for face_id, (photo_id, path, old) in named.items():
        work.face(path, face_id, old, _new(old, **changes))
        by_photo.setdefault(photo_id, (path, []))[1].append(Ref(old.get("tag_id"), old["name"]))
    for photo_id, (path, going) in by_photo.items():
        staying = {person_ids.key_of(person.id, person.name) for face_id, person in carried.get(photo_id, ())
                   if face_id not in named}
        losing = {person_ids.key_of(person.id, person.name): person for person in going}
        work.remove[paths.key(path)] = [person for key, person in losing.items() if key not in staying]
    return work, [face_id for face_id in face_ids if face_id in found and face_id not in named]


def plan_unname(library, face_ids):
    """The plan of taking the names off `face_ids`: each is then "nobody", a decision that re-clustering does not undo."""
    work, plain = _removal(library, UNNAME, face_ids, name=None, tag_id=None, name_source="manual")
    return Plan(UNNAME, work, plain)


def plan_exclude(library, face_ids, reason=None):
    """The plan of ruling `face_ids` out (Exclude selected, Ignore cluster). Refused for a reason that is not one."""
    reason = (reason or "").strip().lower() or faces_service.DEFAULT_REASON
    problem = validation.problem("exclusion reason", reason)
    if problem:
        raise Refused(problem)
    work, plain = _removal(library, EXCLUDE, face_ids, name=None, tag_id=None, name_source="manual", excluded=1,
                           excluded_reason=reason)
    return Plan(EXCLUDE, work, plain, reason=reason)


def plan_guesses(library, folder, named):
    """The plan of Re-examine on `folder`: the guesses propose_guesses makes, decided as an automatch decides them. `named()` gives
    the decided faces. The guesses are automatic names (no decider), which re-clustering may revise."""
    _looked_at, proposed = faces_service.propose_guesses(library, named, folder=folder)
    filer = face_people.Filer(library)

    def read(conn):
        chosen, _gone = faces_service.decide_guesses(conn, proposed)
        known = person_ids.read(conn)
        return (faces.planning(conn, [face_id for face_id, _person, _photo in chosen]), chosen,
                {person: person_ids.target(conn, person, known) for person in {person for _f, person, _p in chosen}})

    found, chosen, targets = _read(library, read) if proposed else ({}, [], {})
    work, problems = Work(GUESS), set()
    work.people = list(targets)
    for face_id, person, _photo in chosen:
        tag = filer.tag(person)
        if tag is None:
            problems.add(face_people.not_filed(face_people.shown(person)))
        if face_id not in found or tag is None:
            continue
        _photo_id, path, old = found[face_id]
        work.face(path, face_id, old, _new(old, name=targets[person][1], tag_id=targets[person][0]))
        work.add.setdefault(paths.key(path), [])
        if tag not in work.add[paths.key(path)]:
            work.add[paths.key(path)].append(tag)
    return Plan(GUESS, work, problems=sorted(problems))


# ---- What an older version left ----------------------------------------------------------------------------------------

_swept = set()


def forget_old_jobs(library):
    """Let go of the plans and states an older version kept of its face assignments, in the library's cache folder
    (Library.bulk_jobs): they name people, and nothing reads them now. Once a process for each library; never raises."""
    key = paths.key(library.path)
    if key in _swept:
        return
    _swept.add(key)
    try:
        for name in os.listdir(library.bulk_jobs):
            job = name.partition(".")[0]
            if job.isdigit() and name.endswith((".plan.json", ".state.json", ".ids")) or ".tmp-" in name:
                os.remove(os.path.join(library.bulk_jobs, name))
        if not os.listdir(library.bulk_jobs):
            os.rmdir(library.bulk_jobs)
    except OSError as problem:
        if not isinstance(problem, FileNotFoundError):
            logger.warning("Could not let go of the old assignment records of %s: %s", library.name, problem)


# ---- Carrying it out ----------------------------------------------------------------------------------------------------

class Outcome:
    """What a run did, counted from what was written: `applied` the faces written with their photos (ids), `left` the faces of the
    plan that were not (a photo that could not be written, a face changed since, a batch stopped), `files` the photo files written,
    `plain` how many faces without a photo were written (`plain_ids` which), `change` the journal's change (None when nothing was planned), `problems` the
    sentences to say, `runs` the writes that took faces out of identity work ([(face ids, fingerprint before, fingerprint after)]) for
    the cached grids."""

    def __init__(self):
        self.applied, self.left, self.files, self.plain, self.change = [], [], 0, 0, None
        self.plain_ids = []
        self.problems, self.runs = [], []

    def warning(self):
        """The sentence a reply adds when it did not all happen, or None."""
        return " ".join(self.problems) or None


def _editor(work, known):
    """The `edit` of tagging.keywords_planner for a photo: the plan's tags to add (unless the file names the person already, by the id
    of their node) and the tags that name the people it is to lose, in every spelling the file holds."""
    def edit(path, held_tags):
        key = paths.key(path)
        add = [tag for tag in work.add.get(key, ()) if not face_people.already_names(held_tags, tag, known)]
        gone = [tag for person in work.remove.get(key, ()) for tag in face_people.person_tags(held_tags, person, known)]
        return add, gone
    return edit


def _write_plain(library, plan, outcome):
    """The faces with no photo to write: a few hundred a transaction, so others are let in between."""
    for start in range(0, len(plan.plain), PLAIN_CHUNK):
        chunk = plan.plain[start:start + PLAIN_CHUNK]
        if plan.op == EXCLUDE:
            done = faces_service.exclude(library, chunk, plan.reason)
            fingerprints = done.details.get("fingerprints")
            if fingerprints and done.changed:
                outcome.runs.append((done.details["face_ids"], *fingerprints))
        else:
            done = faces_service.unname_faces(library, chunk)
        outcome.plain += done.changed
        outcome.plain_ids.extend(chunk)


def run(library, plan, exiftool_path, told=None):
    """Carry out `plan` and wait for the end: the Outcome. The photos are one batch of `file_changes.write_fields` (claimed, queued,
    capped, stopped by Cancel or a hand-over, listed in the strip); `told(result)` is heard with what it wrote, for the pages' records.
    Refused (the cap, a folder the library does not hold) or Conflict (the queue is full), nothing written. Raises what the write raises;
    the change then holds what was done, whole file by file."""
    outcome, work = Outcome(), plan.work
    photos = work.photos()
    if photos:
        if len(photos) > batches.MAX_BATCH:
            raise Refused("These faces are on {:,} photos whose files have to be changed, and {:,} is the most that is done at once. "
                          "Nothing was changed: do it in parts.".format(len(photos), batches.MAX_BATCH))
        refusal = Result()
        if libraries.refuse_writes(refusal, library, photos):
            raise Refused(refusal.refused)
        known = taxonomy.people_vocabulary(library.path)
        change = None
        try:
            result = file_changes.write_fields(
                library, DESCRIPTIONS[plan.op], exiftool_path, photos, tagging.KEYWORD_READ,
                tagging.keywords_planner(library, _editor(work, known)),
                summary={"photos": len(photos), "faces": len(work.face_ids())}, stop_at_first_error=False, alongside=work)
            change = result.details.get("change")
        finally:
            work.release()
        if told is not None and result.details.get("written"):
            told(result)
        if result.refused and change is None:
            if not result.details.get("cancelled"):
                raise Conflict(result.refused)
            # Cancelled before its turn: nothing was written, and said as a stopped batch is (the faces stay where they were).
            outcome.left = work.face_ids()
            outcome.problems.append(result.refused)
            return outcome
        outcome.change = change
        outcome.files = result.changed
        outcome.applied = list(work.applied)
        written = set(work.applied)
        outcome.left = [face_id for face_id in work.face_ids() if face_id not in written]
        outcome.runs = work.runs()
        if result.errors:
            outcome.problems.append("%d photo(s) could not be written and were left as they were; the rest was done."
                                    % len(result.errors))
        if work.left:
            outcome.problems.append("%d face(s) had been changed since the click and were left as they are." % len(work.left))
        if result.refused:
            outcome.problems.append(result.refused)
            return outcome
    if plan.plain:
        _write_plain(library, plan, outcome)
    outcome.problems.extend(plan.problems)
    return outcome
