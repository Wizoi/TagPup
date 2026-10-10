"""The routes of the names to review, which both apps serve alike (docs/ARCHITECTURE.md, "People by id, stage 2", "Names to review";
docs/findings.md, #985).

A name that faces or photos' people hold and no person's tag is: TagTuner's Review People opens the list from a first row shown only
when something waits, TagPup's Activity page shows the count. Reading is free; every choice is the owner's click, a rehearsal first
(`apply` false: what would change, nothing written) and then one journaled change of the faces. Both answer this PC alone, as the
people's other lists do not need to but a choice that names people from another machine should not.

Every reply is `{"success": true, ...}` or the JSON error both pages read: 404 for a person or a group that is no longer there (the page
tells the owner and reloads), 400 for a choice that does not fit the entry or a name already settled, 409 while the faces are being
clustered or named from tags.
"""
import logging

from flask import Blueprint, jsonify, request

from tagpup.core.result import NotFound, Refused
from tagpup.services import name_review
from tagpup.web import responses, security, state, tagpup_routes, tuner_routes

logger = logging.getLogger(__name__)

routes = Blueprint("name_review", __name__)


@routes.before_request
def _this_pc_only():
    if request.path.startswith("/api/names-to-review") and not security.from_this_pc(request.remote_addr):
        return responses.error(403, "Settling names answers this PC only")
    return None


@routes.get("/api/names-to-review")
def names_to_review():
    """The names to settle (tagpup.services.name_review.entries): `entries`, `count` (the number Review People's first row shows),
    `dismissed` (set aside), `groups` (the places a person can be made) and `stale_group_rows`. `?count=1` answers `{count}` alone.
    ?dismissed=1 lists the set-aside names too."""
    library = state.require()
    try:
        if request.args.get("count") == "1":
            return jsonify({"success": True, "count": name_review.count(library)})
        found = name_review.entries(library, include_dismissed=request.args.get("dismissed") == "1")
    except Exception as why:
        logger.error("Names to review: %s", why, exc_info=True)
        return responses.error(500, "The library's names could not be read; the server's log says why.")
    return jsonify({"success": True, **found})


def _id(raw, what):
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise Refused("%s must be a whole number." % what)
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise Refused("%s must be a whole number." % what) from None


@routes.post("/api/names-to-review/resolve")
def names_to_review_resolve():
    """One choice for one name (tagpup.services.name_review.resolve): `{key, action: make|link|unname|dismiss|restore, person_id,
    group_id, apply}`. Without `apply: true` it is a rehearsal: `sentence`, `faces` and `listed` say what would change, nothing is
    written. Applied, it is one change of the journal: `changed`, `change` (its id, for History), `undo`, `keywords_kept`,
    `person_tag` (a person made)."""
    library = state.require()
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    apply = body.get("apply") is True
    if apply:
        refusal = tuner_routes.clustering_refusal(library)
        if refusal is not None:
            return refusal
    try:
        result = name_review.resolve(library, body.get("key"), body.get("action"), person_id=_id(body.get("person_id"), "person_id"),
                                     group_id=_id(body.get("group_id"), "group_id"), apply=apply)
    except NotFound as why:
        return responses.error(404, str(why))
    except Refused as why:
        return responses.error(400, str(why))
    except Exception as why:
        logger.error("Names to review: %s", why, exc_info=True)
        return responses.error(500, "The choice could not be made and nothing was changed; the server's log says why.")
    if result.refused:
        return responses.error(400, result.refused)
    if apply:
        tagpup_routes.forget_scans(library)
    return jsonify({"success": True, "changed": result.changed, **result.details})
