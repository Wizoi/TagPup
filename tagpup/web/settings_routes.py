"""A library's settings, which both apps serve alike: what the settings dialog shows and
saves (web/common/settings-dialog.js; docs/ARCHITECTURE.md, phase 7.6).

GET answers each setting of the library the URL names, with its declaration -- label,
type, default, info text, whether it is locked and what changing it does -- in the
groups the dialog shows, and where the machine's ExifTool is, for a library that names
none. POST changes the settings it is sent, as one journaled change
(tagpup.services.settings.change): refused with the validator's message, and nothing
written, for a value that may not be set.
"""
from flask import Blueprint, jsonify, request

from tagpup import config as tagpup_config
from tagpup.services import settings as settings_service
from tagpup.web import responses, state

routes = Blueprint("settings", __name__)


@routes.get("/api/settings")
def library_settings():
    library = state.require()
    try:
        described = settings_service.described(state.settings(library))
    except Exception as e:
        return responses.error(500, str(e))
    described["library"] = library.name
    described["exiftool_found"] = tagpup_config.exiftool_path("")
    return jsonify(described)


@routes.post("/api/settings")
def change_settings():
    library = state.require()
    body = request.get_json(silent=True) or {}
    values = body.get("values")
    if not isinstance(values, dict):
        return responses.error(400, "Say which settings to change: {\"values\": {key: value}}.")
    try:
        state.settings(library)   # a library holding none is stamped before it is changed
        result = settings_service.change(library, values)
    except Exception as e:
        return responses.error(500, str(e))
    if result.refused:
        return responses.error(400, result.refused)
    return jsonify({"success": True, "changed": result.changed, "settings": result.details.get("changed", []),
                    "locked": result.details.get("locked", False), "change": result.details.get("change")})
