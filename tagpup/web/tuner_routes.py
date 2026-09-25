"""TagTuner's routes (docs/SPEC_TAGTUNER.md): photos by folder, tag and person, faces
and their matches, Identify Faces, indexing and the tag merges.

Each route is thin: it reads the request, calls a service, and shapes the reply. What a
server keeps for a library between requests lives in `tagpup.web.state`.
"""
from flask import Blueprint

routes = Blueprint("tuner", __name__)
