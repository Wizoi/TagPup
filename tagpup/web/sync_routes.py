"""Sync, and when a library was last in step with its folders, which both apps serve
alike (tagpup.services.sync; docs/ARCHITECTURE.md, phase 8).

GET answers what the pages show -- "last in step: ..." -- and the last applied sync, as
counts and times; never a path, since folders name people. POST runs a sync of the
library the URL names (tagpup.runtime.sync): a dry run unless the body says apply, which
says what it found and would write; applied, the rows are written as one change of the
journal, the new files' folders go on this process's index queue (TagTuner's indexing
panel shows them), and the run is recorded. After an apply that changed rows, the folder
scans TagPup keeps are let go, as after any rewrite of photos.

The folders to review -- under the library's roots, holding photos and no indexed photo,
not ignored -- are GET /api/sync/review, with their paths: the page that asks lists them,
as Remove Folder's list does. Include indexes one with its subfolders on this process's
index queue; Ignore adds it to the library's ignored folders, a journaled change of its
settings.
"""
import logging

from flask import Blueprint, jsonify, request

from tagpup import runtime as runtimes
from tagpup.services import settings as settings_service
from tagpup.services import sync as sync_service
from tagpup.web import responses, state, tagpup_routes

logger = logging.getLogger(__name__)
routes = Blueprint("sync", __name__)


@routes.get("/api/sync")
def sync_state():
    library = state.require()
    try:
        found = sync_service.last(library)
    except Exception as e:
        return responses.error(500, str(e))
    return jsonify({"library": library.name, **found})


@routes.post("/api/sync")
def sync_library():
    library = state.require()
    body = request.get_json(silent=True) or {}
    apply = body.get("apply") is True
    # Checked by the service (tagpup.core.validation), as the index routes' folder is.
    folder = body.get("folder") or None
    try:
        result = runtimes.sync(library, folder=folder, apply=apply)
    except Exception as e:
        logger.error("Could not sync %s: %s", library.name, e, exc_info=True)
        return responses.error(500, str(e))
    if apply and result.changed:
        tagpup_routes.forget_scans(library)
    answer = {"success": result.ok, "dry_run": not apply, "refused": result.refused,
              "attempted": result.attempted, "changed": result.changed,
              "changed_by_kind": result.details.get("changed", {}), "queued": result.details.get("queued", 0),
              "in_step": result.details["in_step"], "change": result.details.get("change"),
              "counts": result.details["counts"], "behind": result.details.get("behind", 0),
              "skipped": len(result.skipped), "warnings": result.details.get("warnings", []),
              "errors": len(result.errors)}
    if not result.ok:
        answer["error"] = result.message()
    # Refused, nothing was written: 400, as every route answers a refusal.
    return jsonify(answer), (400 if result.refused else 200)


@routes.get("/api/sync/review")
def sync_review():
    library = state.require()
    try:
        found = runtimes.review(library)
    except Exception as e:
        logger.error("Could not list the folders to review in %s: %s", library.name, e, exc_info=True)
        return responses.error(500, str(e))
    return jsonify({"library": library.name, "count": len(found["folders"]), **found})


@routes.post("/api/sync/review/include")
def sync_review_include():
    library = state.require()
    body = request.get_json(silent=True) or {}
    result = runtimes.include(library, body.get("folder"))
    if result.refused:
        return responses.error(400, result.refused)
    return jsonify({"success": True, "queued": result.changed})


@routes.post("/api/sync/review/ignore")
def sync_review_ignore():
    library = state.require()
    body = request.get_json(silent=True) or {}
    result = settings_service.ignore_folder(library, body.get("folder"))
    if result.refused:
        return responses.error(400, result.refused)
    return jsonify({"success": True, "changed": result.changed, "change": result.details.get("change")})
