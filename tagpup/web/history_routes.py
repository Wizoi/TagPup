"""A library's recent changes, and undoing one, which both apps serve alike: what the
gear's History dialog shows (web/common/history-dialog.js; docs/ARCHITECTURE.md, phase
7.5; docs/findings.md, #266).

Every bulk edit, file write and settings change is a change of the library's journal
(tagpup.services.journal), and until now only the CLI and the MCP server could list or
undo one. GET lists the library the URL names' last changes, newest first: what each
was, when, what it touched by count, and whether it stands to be undone. No values are
sent -- a change's rows can name people -- and the summary holds counts only. POST
rehearses the undo of one (`apply` false: nothing is written, and the answer says what
would be put back and what refused) or makes it (`apply` true): a change of rows only
where the rehearsal restores every row exactly, a change of photo files file by file,
each file no longer holding what the change left refused and named by photo id. After
an undo that changed something, the folder scans TagPup keeps are let go, as after any
rewrite of photos (tagpup_routes.forget_scans).
"""
import logging

from flask import Blueprint, jsonify, request

from tagpup.core.result import NotFound
from tagpup.services import journal as journal_service
from tagpup.services import settings as settings_service
from tagpup.web import responses, state, tagpup_routes

logger = logging.getLogger(__name__)
routes = Blueprint("history", __name__)

#: Changes listed when the page asks for no number, and the most it may ask for.
LIMIT = 20
MOST = 100


def undoable(entry):
    """May the dialog offer to undo this change? One applied, and not the stamp of the
    library's first settings, which is never undone (tagpup.services.settings.STAMPS).
    The undo itself decides the rest -- a newer change in the way, a file changed since
    -- and says why."""
    return entry["status"] == "applied" and entry["operation"] not in settings_service.STAMPS


def _listed(entry):
    return {"id": entry["id"], "operation": entry["operation"], "status": entry["status"],
            "created": entry["created"], "applied": entry["applied"], "undone": entry["undone"],
            "summary": entry["summary"], "rows": entry["rows"], "files": entry["files"],
            "undoable": undoable(entry)}


@routes.get("/api/history")
def library_history():
    library = state.require()
    try:
        limit = int(request.args.get("limit", LIMIT))
    except ValueError:
        return responses.error(400, "Say how many changes to list as a number: ?limit=20.")
    try:
        found = journal_service.history(library, limit=max(1, min(limit, MOST)))
    except Exception as e:
        return responses.error(500, str(e))
    return jsonify({"library": library.name, "retention_days": found["retention_days"],
                    "changes": [_listed(entry) for entry in found["changes"]]})


def _answer(result, change_id):
    rehearsal = result.details.get("rehearsal") or {}
    answer = {"success": result.ok, "dry_run": result.details.get("dry_run", True), "change": change_id,
              "attempted": result.attempted, "changed": result.changed, "refused": result.refused,
              "errors": [{"what": what, "why": why} for what, why in result.errors],
              "differences": list(rehearsal.get("differences") or []),
              "would_put_back": rehearsal.get("rows", 0)}
    if not result.ok:
        answer["error"] = result.message()
    return answer


@routes.post("/api/history/<int:change_id>/undo")
def undo_change(change_id):
    library = state.require()
    body = request.get_json(silent=True) or {}
    apply = body.get("apply") is True
    try:
        result = journal_service.undo(library, change_id, apply=apply, exiftool_path=state.exiftool(library))
    except NotFound as e:
        return responses.error(404, str(e))
    except Exception as e:
        logger.error("Could not undo change %d of %s: %s", change_id, library.name, e, exc_info=True)
        return responses.error(500, str(e))
    if apply and result.changed:
        tagpup_routes.forget_scans(library)
    # Refused, nothing was written: 400, as every route answers a refusal.
    return jsonify(_answer(result, change_id)), (400 if result.refused else 200)
