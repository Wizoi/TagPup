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
from flask import Blueprint, request

from tagpup.web import face_routes, state, tuner_routes

routes = Blueprint("photo_faces", __name__)


@routes.before_request
def refuse_writes_while_clustering():
    if request.method != "POST":
        return None
    return tuner_routes.clustering_refusal(state.current())


routes.add_url_rule("/api/face/match", view_func=face_routes.face_match, methods=["POST"])
routes.add_url_rule("/api/face/unmatch", view_func=face_routes.face_unmatch, methods=["POST"])
routes.add_url_rule("/api/faces/exclude", view_func=face_routes.faces_exclude, methods=["POST"])
