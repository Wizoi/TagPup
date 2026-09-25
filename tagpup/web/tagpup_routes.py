"""TagPup's routes (docs/SPEC_TAGPUP_GUI.md): the folder view, a photo's metadata,
its faces, the taxonomy, Suggest and the bulk writes.

Each route is thin: it reads the request, calls a service, and shapes the reply. What a
server keeps for a library between requests lives in `tagpup.web.state`.
"""
from flask import Blueprint

routes = Blueprint("tagpup", __name__)
