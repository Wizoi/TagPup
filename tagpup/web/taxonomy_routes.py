"""The tag tree's routes, which both apps serve alike: the tag editor is one module both
pages open (web/common/tag-editor.js; docs/ARCHITECTURE.md, phase 7.6).

They were TagPup's alone, on its blueprint, while TagTuner had no way to edit the tree.
Each is thin: it reads the request, calls tagpup.services.tags, and shapes the reply the
editor reads.

A rename or a delete rewrites photos, so what TagPup's page was last shown of a folder
no longer holds (tagpup.web.tagpup_routes.folders). That cache is the process's, not an
app's: an edit made from TagTuner's page clears it as one made from TagPup's does.
"""
from flask import Blueprint, jsonify, request

from tagpup import config as tagpup_config
from tagpup.core.result import NotFound
from tagpup.services import tags as tags_service
from tagpup.web import responses, state, tagpup_routes

routes = Blueprint("taxonomy", __name__)


def _forget_scans(library):
    """Photos were rewritten: TagPup's cached scans of this library describe them as they
    were."""
    tagpup_routes.folders.of(library).clear()


@routes.get("/api/taxonomy/tree")
def taxonomy_tree():
    try:
        return jsonify(tags_service.tree(state.require()))
    except Exception as e:
        return responses.error(500, str(e))


def _tree_edit(edit):
    """Run an edit of the tag tree (tagpup.services.tags) on the request's library:
    (its Result, None) or (None, the error reply) -- 404 for a node that is not there,
    400 for a request refused, 500 for anything else."""
    try:
        result = edit(state.require())
    except NotFound as missing:
        return None, responses.error(404, str(missing))
    except Exception as e:
        return None, responses.error(500, str(e))
    if result.refused:
        return None, responses.error(400, result.refused)
    return result, None


@routes.post("/api/taxonomy/create")
def taxonomy_create():
    body = request.get_json(silent=True) or {}
    result, failed = _tree_edit(lambda library: tags_service.create(
        library, body.get("name", ""), body.get("parent_id"), body.get("has_face", 0)))
    if failed:
        return failed
    return jsonify({"success": True, "id": result.details["id"], "tag": result.details["tag"]})


@routes.post("/api/taxonomy/update")
def taxonomy_update():
    body = request.get_json(silent=True) or {}
    tag_id = body.get("id")
    if tag_id is None:
        return responses.error(400, "Missing 'id' parameter")
    _result, failed = _tree_edit(lambda library: tags_service.set_flags(
        library, tag_id, body.get("has_face"), body.get("hidden_from_autocomplete")))
    if failed:
        return failed
    return jsonify({"success": True})


@routes.post("/api/taxonomy/delete-check")
def taxonomy_delete_check():
    body = request.get_json(silent=True) or {}
    tag_id = body.get("tag_id")
    if tag_id is None:
        return responses.error(400, "Missing 'tag_id' parameter")
    try:
        usage = tags_service.usage(state.require(), tag_id)
    except NotFound as missing:
        return responses.error(404, str(missing))
    except Exception as e:
        return responses.error(500, str(e))
    return jsonify(dict(usage, success=True))


@routes.post("/api/taxonomy/delete-confirm")
def taxonomy_delete_confirm():
    body = request.get_json(silent=True) or {}
    tag_id, action = body.get("tag_id"), body.get("action")
    if tag_id is None or not action:
        return responses.error(400, "Missing parameters")
    result, failed = _tree_edit(lambda library: tags_service.delete(
        library, tag_id, action, body.get("target_tag"), tagpup_config.exiftool_path()))
    if failed:
        return failed
    _forget_scans(state.require())
    reply = {"success": result.ok, "photos_affected": result.details["photos_affected"],
             "photos_rewritten": result.details["photos_rewritten"]}
    if not result.ok:
        reply["error"] = result.message()
    return jsonify(reply)


@routes.post("/api/taxonomy/rename")
def taxonomy_rename():
    body = request.get_json(silent=True) or {}
    tag_id, new_name = body.get("tag_id"), str(body.get("new_name") or "").strip()
    if tag_id is None or not new_name:
        return responses.error(400, "Missing parameters")
    result, failed = _tree_edit(lambda library: tags_service.rename(
        library, tag_id, new_name, tagpup_config.exiftool_path()))
    if failed:
        return failed
    _forget_scans(state.require())
    reply = {"success": True, "photos_affected": result.details["photos_affected"],
             "photos_rewritten": result.details["photos_rewritten"]}
    # The tree has the new name; a photo that could not be rewritten keeps the old.
    if not result.ok:
        reply["warning"] = result.message()
    return jsonify(reply)
