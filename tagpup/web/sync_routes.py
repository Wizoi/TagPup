"""Sync, and when a library was last in step with its folders, which both apps serve
alike (tagpup.services.sync; docs/ARCHITECTURE.md, phase 8).

GET answers what the pages show -- "last in step: ..." -- and the last applied sync, as
counts and times; never a path, since folders name people. POST runs a sync of the
library the URL names (tagpup.runtime.sync): a dry run unless the body says apply, which
says what it found and would write; applied, the rows are written as one change of the
journal, the new files' folders go on this process's index queue (TagTuner's indexing
panel shows them), and the run is recorded. After an apply that changed rows, the folder
scans TagPup keeps are let go, as after any rewrite of photos.
"""
import logging

from flask import Blueprint, jsonify, request

from tagpup import runtime as runtimes
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
              "counts": result.details["counts"], "skipped": len(result.skipped),
              "errors": len(result.errors)}
    if not result.ok:
        answer["error"] = result.message()
    # Refused, nothing was written: 400, as every route answers a refusal.
    return jsonify(answer), (400 if result.refused else 200)
