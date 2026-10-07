"""The reads about faces both apps serve: who a face resembles, the face to show for each
person, and the faces a hover shows of one.

TagTuner's Identify Faces asked for these first; TagPup's Organize shows the faces on the open
photo (docs/ARCHITECTURE.md, "Faces on the photo") and its Suggest chips show people, and each asks
the same thing of the same service -- tagpup.services.identify, through tagpup.jobs.identify's
caches -- so the numbers on one page are the other's. The process serves both apps, so the cache
of named faces is one, kept here; a write of either app moves the faces table's fingerprint, and
what was cached against the old one is read again.
"""
import os

from flask import Blueprint, abort, jsonify, request

from tagpup.core.result import NotFound
from tagpup.jobs import identify as identify_jobs
from tagpup.services import identify as identify_service
from tagpup.web import state

routes = Blueprint("faces", __name__)

#: Each library's cached Identify Faces answers: the queue, the grids and the matrix of
#: named faces (tagpup.jobs.identify.GridCache).
identify_cache = state.PerLibrary(lambda library: identify_jobs.GridCache())


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
