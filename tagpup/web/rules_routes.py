"""/api/rules: what may be set, as data, for the pages to check before they send.

The rules are tagpup.core.validation's, which every service checks what it is given
against. The pages asked their own copies first, and four were written twice; now
web/common/validate.js fetches these once and applies them, so no page keeps a copy
of a rule. The same for every library, so both apps serve it alike, as they do the
picker, and a page open on no library may ask it (web/common/api.js).
"""
from flask import Blueprint, jsonify

from tagpup.core import validation

routes = Blueprint("rules", __name__)


@routes.get("/api/rules")
def rules():
    """{"version", "kinds": {kind: {"rules": [...]}}}: every kind of input and its rules,
    the version changing whenever a rule does (tagpup.core.validation.published)."""
    return jsonify(validation.published())
