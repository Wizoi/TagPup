"""What both apps serve about faces: who a face resembles, the face to show for each person, the
faces a hover shows of one, and the writes of a single face -- name, unname, exclude.

TagTuner's Identify Faces asked for these first; TagPup's Organize shows the faces on the open
photo (docs/ARCHITECTURE.md, "Faces on the photo") and its Suggest chips show people, and each asks
the same thing of the same service -- tagpup.services.identify, through tagpup.jobs.identify's
caches -- so the numbers on one page are the other's. The writes of one face are the same views too, and
each is one decision of the face AND the photo's person tag (tagpup.services.face_people). The process serves both apps, so the cache
of named faces is one, kept here; a write of either app moves the faces table's fingerprint, and
what was cached against the old one is read again.
"""
import logging
import os

from flask import Blueprint, abort, jsonify, make_response, request

from tagpup.core.result import Conflict, NotFound, Refused
from tagpup.jobs import face_assignments
from tagpup.jobs import identify as identify_jobs
from tagpup.services import face_assignment, face_people
from tagpup.services import identify as identify_service
from tagpup.web import responses, state, tagpup_routes

logger = logging.getLogger(__name__)

routes = Blueprint("faces", __name__)

#: Each library's cached Identify Faces answers: the queue, the grids and the matrix of
#: named faces (tagpup.jobs.identify.GridCache).
identify_cache = state.PerLibrary(lambda library: identify_jobs.GridCache())


def refuse(status, message):
    """Answer with the JSON error (tagpup.web.responses.error) from wherever the
    refusal is found, helpers included."""
    abort(make_response(responses.error(status, message)))


def library_there(library):
    """Is the library's file there? The reads answer nothing for one that is not, as
    they always did, rather than failing the page."""
    return os.path.exists(library.path)


def named(library):
    """Every named face as unit vectors, kept per state of the faces table."""
    return lambda: identify_jobs.named_faces(library, identify_cache.of(library))


def decided(library):
    """The faces a person decided as unit vectors: what automatch compares with."""
    return lambda: identify_jobs.decided_faces(library, identify_cache.of(library))


def int_arg(name, what):
    """A query parameter that must be an integer, or the 400 the old handlers sent."""
    value = request.args.get(name)
    if not value:
        abort(400, description="Missing '%s' parameter" % what)
    try:
        return int(value)
    except ValueError:
        abort(400, description="Invalid '%s' parameter" % what)


@routes.get("/api/face-matches")
def face_matches():
    """The five people a face most resembles, each with the similarity and its band
    (tagpup.services.identify.face_matches)."""
    library = state.require()
    face_id = int_arg("id", "id")
    if not library_there(library):
        return jsonify([])
    try:
        return jsonify(identify_service.face_matches(library, face_id, named(library)))
    except NotFound:
        abort(404, description="Face not found")


@routes.get("/api/people-faces")
def people_faces():
    """{name: face id}: the face most like each person, for the people list shown by face
    (tagpup.services.identify.representative_faces). A person with no readable face is
    absent; the crop is /api/face-crop?id=."""
    library = state.require()
    if not library_there(library):
        return jsonify({})
    return jsonify(identify_jobs.representative_faces(library, identify_cache.of(library)))


@routes.get("/api/people-face-samples")
def people_face_samples():
    """{name: [face id]}: up to four faces of each person, for the hover that shows who a
    suggested name is (tagpup.services.identify.face_samples): the faces a person decided
    first, the one most like the person's first. A person with no readable face is absent;
    the crops are /api/face-crop?id=. One answer for everyone, cached against the decided
    faces' stamp."""
    library = state.require()
    if not library_there(library):
        return jsonify({})
    return jsonify(identify_jobs.face_samples(library, identify_cache.of(library)))


# ---- The writes of one face ------------------------------------------------------------------
#
# Views, not routes: TagTuner (tagpup.web.tuner_routes) and TagPup's Organize (tagpup.web.photo_face_routes)
# each register them in their own blueprint, which refuses them while the library's faces are being clustered.


def faces_write(library, action):
    """Run a face action (tagpup.services.faces) on the request's library, and answer
    what went wrong: 404 for a face or a library that is not there, 409 for a face
    that cannot be named as things stand, 400 for a request refused, 500 for anything
    else. Returns its Result.

    The faces an action took out of the identify pool come off the cached grids,
    rather than making the next click rebuild them: the action says which, and the
    fingerprints either side of its write (tagpup.store.faces.accounted_write).
    """
    try:
        result = action(library)
    except NotFound as missing:
        abort(404, description=str(missing))
    except Conflict as conflict:
        refuse(409, str(conflict))
    except Exception as e:
        logger.error("Error in a face action: %s", e)
        abort(500, description="Internal error: %s" % e)
    if result.refused:
        refuse(400, result.refused)
    if not result.ok:
        refuse(500, result.message())
    fingerprints = result.details.get("fingerprints")
    if fingerprints and result.changed:
        identify_cache.of(library).forget_faces(result.details["face_ids"], *fingerprints)
    return result


def _forgetting(library):
    """What a step of an assignment tells the cached grids: the faces it took out of the pool, and the fingerprints either side."""
    def forgetting(done):
        fingerprints = done.details.get("fingerprints")
        if fingerprints and done.changed:
            identify_cache.of(library).forget_faces(done.details["face_ids"], *fingerprints)
    return forgetting


def assigned(library, plan):
    """Run `plan` (tagpup.services.face_assignment) as the job of tagpup.jobs.face_assignments and wait for its end: the Job. The
    request waits, the job does not depend on it -- a closed tab or a restart leaves a job that can be resumed. 409 while another
    assignment runs in the library, 400 for a plan refused (it was, before anything was written). The cached grids lose the faces
    each step took out of the pool as the step is done."""
    try:
        face_assignments.refuse_if_running(library)       # before the ExifTool path, which waits for the file lock
        job = face_assignments.start(library, plan, state.exiftool(library),
                                     told=lambda done: tagpup_routes.records_written(library, done),
                                     after_step=_forgetting(library))
    except Refused as why:
        refuse(400, str(why))
    except Conflict as why:
        refuse(409, str(why))
    job.finished.wait()
    return job


def trouble(outcome):
    """The sentence a bulk write adds when it did not all happen: the job stopped (cancelled, failed), or photos could not be
    written and their faces were left as they were. None when all of it was done."""
    if outcome["state"] != face_assignments.DONE:
        return outcome["message"]
    if outcome["error_count"]:
        return ("%d photo(s) or face(s) could not be written and were left as they were; the rest was done (see Activity)."
                % outcome["error_count"])
    if outcome["warnings"]:
        return outcome["warnings"][0]
    return None


def _read_face_ids(body):
    """Accept either face_ids (list) or a single face_id, as ints; None when neither."""
    face_ids = body.get("face_ids")
    if face_ids is None and body.get("face_id") is not None:
        face_ids = [body.get("face_id")]
    if not face_ids or not isinstance(face_ids, list):
        return None
    try:
        return [int(x) for x in face_ids]
    except (ValueError, TypeError):
        return None


def writer_for(library, page_writes_tags=False):
    """Where the photo's person tags are written for a face write (tagpup.services.face_people.Writer): the library's
    ExifTool, TagPup's folder records told of each chunk as it is written. None when the request says the PAGE writes the
    tags itself (`page_writes_tags`: TagPup's boxes, with their queue, their undo and their placement question); the
    answer then says which to take off (`untag`)."""
    if page_writes_tags:
        return None
    return face_people.Writer(state.exiftool(library), told=lambda done: tagpup_routes.records_written(library, done))


def tags_reply(result):
    """What a face write says of the photo's tags: how many files it wrote, which tags the page is to take off when it
    writes them itself, and, when a tag could not be taken off, a sentence (the faces are written all the same)."""
    reply = {}
    details = result.details
    if "tags_written" in details:
        reply["tags_written"] = details["tags_written"]
    if "tags_removed" in details:
        reply["tags_removed"] = details["tags_removed"]
    if details.get("untag"):
        reply["untag"] = details["untag"]
    if details.get("tag_problem"):
        reply["warning"] = ("The person is no longer on a face, but the tag could not be taken off the photo: %s"
                            % details["tag_problem"])
    return reply


@routes.get("/api/faces/job/current")
def faces_job_current():
    """The bulk assignment of faces a page opening the library should show: running, or the latest that stopped part-way and can
    be resumed (tagpup.jobs.face_assignments.current). `{"job": null}` for none."""
    library = state.require()
    return jsonify({"success": True, "job": face_assignments.current(library)})


@routes.get("/api/faces/job/status")
def faces_job_status():
    """How bulk assignment `job` is getting on (tagpup.jobs.face_assignments.status): counts and sentences, never a name."""
    library = state.require()
    handle = int_arg("job", "job")
    found = face_assignments.status(library, handle)
    if found is None:
        abort(404, description="There is no such assignment in this library")
    return jsonify({"success": True, "job": found})


@routes.post("/api/faces/job/cancel")
def faces_job_cancel():
    """Stop bulk assignment `job` after the step under way (what is done stays done). With `let_go` true, or for one that is not
    running, let go of a stopped job instead: its record is removed and it is no longer offered."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    try:
        handle = int(body.get("job"))
        if body.get("let_go"):
            face_assignments.let_go(library, handle)
            return jsonify({"success": True, "job": None})
        return jsonify({"success": True, "job": face_assignments.cancel(library, handle)})
    except (TypeError, ValueError):
        abort(400, description="Missing or invalid job")
    except NotFound as missing:
        abort(404, description=str(missing))
    except Conflict as why:
        refuse(409, str(why))


@routes.post("/api/faces/job/undo")
def faces_job_undo():
    """Undo the whole of assignment `job` (tagpup.jobs.face_assignments.undo): the faces as they were and the photo files this
    job wrote put back through the journal. TagTuner's Undo after an assign, Ignore cluster or Exclude selected. 404 for none,
    400 for one undone already or whose record is gone, 409 while it runs."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    try:
        handle = int(body.get("job"))
    except (TypeError, ValueError):
        abort(400, description="Missing or invalid job")
    try:
        result = face_assignments.undo(library, handle, state.exiftool(library))
    except NotFound as missing:
        abort(404, description=str(missing))
    except Refused as why:
        refuse(400, str(why))
    except Conflict as why:
        refuse(409, str(why))
    reply = {"success": True, "faces": result.changed, "files": result.details.get("files", 0),
             "undone": result.details.get("undone", False), "remaining": result.details.get("remaining", 0)}
    if result.details.get("remaining"):
        reply["warning"] = ("%d photo(s) could not be put back (%s): their faces were left as they are, and Undo can be pressed "
                            "again when they can be." % (result.details["remaining"], result.errors[0][1] if result.errors else "the file"))
    elif result.errors:
        reply["warning"] = "%d thing(s) could not be put back: %s" % (len(result.errors), result.errors[0][1])
    return jsonify(reply)


@routes.post("/api/faces/job/resume")
def faces_job_resume():
    """Carry on bulk assignment `job` that stopped part-way, from the first step not done. Answers at once with its status; the
    page follows it (`status`)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    try:
        handle = int(body.get("job"))
    except (TypeError, ValueError):
        abort(400, description="Missing or invalid job")
    try:
        job = face_assignments.resume(library, handle, state.exiftool(library),
                                      told=lambda done: tagpup_routes.records_written(library, done),
                                      after_step=_forgetting(library))
    except NotFound as missing:
        abort(404, description=str(missing))
    except Refused as why:
        refuse(400, str(why))
    except Conflict as why:
        refuse(409, str(why))
    return jsonify({"success": True, "job": job.status()})


def face_match():
    """Name one face AND put the person on its photo, the tag first (tagpup.services.face_people.name_face). TagPup's
    page, which wrote the tag itself before it asked (`page_writes_tags`), is told which tags to take off if the face
    was another person's."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    face_id, person_name = body.get("face_id"), body.get("person_name")
    if face_id is None or not person_name:
        abort(400, description="Missing face_id or person_name")
    try:
        face_id, person_name = int(face_id), str(person_name).strip()
    except (ValueError, TypeError):
        abort(400, description="Invalid parameters")
    writer = writer_for(library, bool(body.get("page_writes_tags")))
    result = faces_write(library, lambda lib: face_people.name_face(lib, face_id, person_name, writer))
    # What the write changed, not what was asked: naming a face the name it has is no change.
    return jsonify({"success": True, "changed": result.changed, **tags_reply(result)})


def face_unmatch():
    """Take a face's name off, and the person's tag off the photo unless another face carries them
    (tagpup.services.face_people.unname_face)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    face_id = body.get("face_id")
    if face_id is None:
        abort(400, description="Missing face_id")
    try:
        face_id = int(face_id)
    except (ValueError, TypeError):
        abort(400, description="Invalid face_id")
    writer = writer_for(library, bool(body.get("page_writes_tags")))
    result = faces_write(library, lambda lib: face_people.unname_face(lib, face_id, writer))
    return jsonify({"success": True, "changed": result.changed, **tags_reply(result)})


def _exclude_bulk(library, face_ids, reason):
    """A selection ruled out (Exclude selected, Ignore cluster) as a job: the faces, and the people they were named taken off their
    photos unless another face carries them (#907)."""
    try:
        plan = face_assignment.plan_exclude(library, face_ids, reason)
    except Refused as why:
        refuse(400, str(why))
    outcome = assigned(library, plan).outcome()
    reply = {"success": True, "excluded": outcome["changed"], "tags_removed": outcome["tags_removed"], "job": outcome["job"]}
    if trouble(outcome):
        reply["warning"] = trouble(outcome)
    return jsonify(reply)


def faces_exclude():
    """Take faces out of identity work, and the tags of the people they were named off their photos
    (tagpup.services.face_people.exclude)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    face_ids = _read_face_ids(body)
    if face_ids is None:
        abort(400, description="Missing or invalid face_ids")
    reason = body.get("reason")   # none: the service's default
    if body.get("bulk"):
        return _exclude_bulk(library, face_ids, reason)
    writer = writer_for(library, bool(body.get("page_writes_tags")))
    result = faces_write(library, lambda lib: face_people.exclude(lib, face_ids, reason, writer))
    # The rows changed, not the ids sent: an id that is not in the table was never
    # excluded, and saying it was is how a write reports success on nothing.
    return jsonify({"success": True, "excluded": result.changed, **tags_reply(result)})
