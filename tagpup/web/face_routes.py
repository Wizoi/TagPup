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

from tagpup.core.result import Conflict, NotFound
from tagpup.jobs import identify as identify_jobs
from tagpup.services import face_people
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


def writer_for(library, body=None):
    """Where the photo's person tags are written for a face write (tagpup.services.face_people.Writer): the library's
    ExifTool, TagPup's folder records told of each chunk as it is written. None when the request says the PAGE writes the
    tags itself (`page_writes_tags`: TagPup's boxes, with their queue, their undo and their placement question); the
    answer then says which to take off (`untag`)."""
    if (body or {}).get("page_writes_tags"):
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


def face_match():
    """Name one face AND put the person on its photo, the tag first (tagpup.services.face_people.name_face)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    face_id, person_name = body.get("face_id"), body.get("person_name")
    if face_id is None or not person_name:
        abort(400, description="Missing face_id or person_name")
    try:
        face_id, person_name = int(face_id), str(person_name).strip()
    except (ValueError, TypeError):
        abort(400, description="Invalid parameters")
    writer = writer_for(library)
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
    writer = writer_for(library, body)
    result = faces_write(library, lambda lib: face_people.unname_face(lib, face_id, writer))
    return jsonify({"success": True, "changed": result.changed, **tags_reply(result)})


def faces_exclude():
    """Take faces out of identity work, and the tags of the people they were named off their photos
    (tagpup.services.face_people.exclude)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    face_ids = _read_face_ids(body)
    if face_ids is None:
        abort(400, description="Missing or invalid face_ids")
    reason = body.get("reason")   # none: the service's default
    writer = writer_for(library, body)
    result = faces_write(library, lambda lib: face_people.exclude(lib, face_ids, reason, writer))
    # The rows changed, not the ids sent: an id that is not in the table was never
    # excluded, and saying it was is how a write reports success on nothing.
    return jsonify({"success": True, "excluded": result.changed, **tags_reply(result)})
