"""TagPup's writes of the faces on the open photo (docs/ARCHITECTURE.md, "Faces on the photo").

Organize shows boxes over the open photo and a small panel for the one clicked: name it, take its
name off, rule it out. These are TagTuner's own views of one face (tagpup.web.face_routes) -- the
same service functions (tagpup.services.faces), so a face named from a box is the face TagTuner's
grid names, and the matching a box's suggestions show is TagTuner's (`/api/face-matches`, also
served here by face_routes). They are registered in this blueprint, which refuses them while the
library's faces are being clustered as TagTuner's does, and is TagPup's alone.

What the page adds is the photo's person tag: choosing a person for a face writes the tag through
the page's own save of the photo's keywords, and then names the face here, so that the tag and the
face agree. No route here writes a keyword.
"""
import logging

from flask import Blueprint, jsonify, request

from tagpup.services import faces as face_actions
from tagpup.web import face_routes, responses, state, tuner_routes

logger = logging.getLogger(__name__)

routes = Blueprint("photo_faces", __name__)


@routes.before_request
def refuse_writes_while_clustering():
    if request.method != "POST":
        return None
    return tuner_routes.clustering_refusal(state.current())


@routes.get("/api/photo-faces")
def photo_faces():
    """Faces detected on one photo, with the best identity guess for each
    (tagpup.services.faces.panel), from the matrix of named faces the boxes' list reads."""
    library = state.require()
    photo_path = request.args.get("path")   # as Flask decoded it, once (docs/findings.md, #32)
    if not photo_path:
        return responses.error(400, "Missing 'path' parameter")
    try:
        return jsonify(face_actions.panel(library, photo_path, face_routes.named(library)))
    except Exception as e:
        logger.error("Error listing faces for %s: %s", photo_path, e)
        return responses.error(500, str(e))


routes.add_url_rule("/api/face/match", view_func=face_routes.face_match, methods=["POST"])
routes.add_url_rule("/api/face/unmatch", view_func=face_routes.face_unmatch, methods=["POST"])
routes.add_url_rule("/api/faces/exclude", view_func=face_routes.faces_exclude, methods=["POST"])
