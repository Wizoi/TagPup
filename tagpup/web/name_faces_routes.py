"""The routes of the button "Name faces from tags", which both apps serve alike (docs/ARCHITECTURE.md, "Name faces from tags", #789).

TagTuner's Folder Matches header and gear, and TagPup's Organize folder view, start the same job
(tagpup.jobs.naming_faces): its plan is read first and shown as a question, and only the answer Yes writes. The page polls
`status`; a job it did not start (another page, another tab, a page that was reloaded) is found by `current`. The job works on the
whole library, or, when the page asks (`only_folder`), on the folder it has open and its subfolders (#987); `scope` gives the
counts the dialog's choice between the two shows. Either way it answers this PC only, as the library's views do.

Every reply is `{"success": true, ...}` or the JSON error both pages read. A job that is already working answers 409 with its
status in `job`, so a page that asked again shows it instead.
"""
import logging

from flask import Blueprint, jsonify, request

from tagpup.core import paths
from tagpup.core.result import Conflict, NotFound, Refused
from tagpup.jobs import naming_faces
from tagpup.web import responses, security, state, tuner_routes

logger = logging.getLogger(__name__)

routes = Blueprint("name_faces", __name__)


@routes.before_request
def _this_pc_only():
    if request.path.startswith("/api/name-faces") and not security.from_this_pc(request.remote_addr):
        return responses.error(403, "Naming faces works on the whole library and answers this PC only")
    return None


#: The writes the job's plan and grouping must not be written over (#871): adding or indexing folders, a sync's apply, a bulk edit,
#: a photo's keywords and the bulk tag writes, and deleting photos. Answered 409 while names are given (the one owner of the
#: refusal is tuner_routes.clustering_refusal, which TagTuner's own writes ask too); reads are never refused.
GUARDED = frozenset((
    "/api/folder/index-start", "/api/folder/add", "/api/sync/review/include", "/api/library/bulk/start",
    "/api/library/bulk/resume", "/api/photo/save-metadata", "/api/photos/bulk-tags", "/api/folder/auto-apply",
    "/api/photo/delete", "/api/sync"))


def refuse_writes_while_naming():
    """app.before_request: a write of GUARDED is refused while the library's names are being written."""
    if request.method != "POST":
        return None
    library = state.current()
    at = request.path.find("/api/")
    if library is None or at < 0 or request.path[at:] not in GUARDED:
        return None
    if request.path[at:] == "/api/sync" and (request.get_json(silent=True) or {}).get("apply") is not True:
        return None     # a rehearsal reads
    return tuner_routes.clustering_refusal(library, only_naming=True)


def _busy(library):
    """What runs in this process that the job must not run beside, as sentences: the index queue, Suggest, a sync and a Verify
    (tagpup.web.tuner_routes), and whatever is already clustering the library's faces (the flag TagTuner's writes honour)."""
    said = list(tuner_routes._busy_in(library))
    if tuner_routes.clustering.of(library).is_set():
        said.append("the faces are being clustered")
    return said


def _hold(library):
    """The context held from the Yes to the end: writes of faces are refused meanwhile (tagpup.web.tuner_routes.while_clustering)."""
    return lambda: tuner_routes.while_clustering(library)


def _reply(call):
    """`call()`'s Job as the page reads it, or its refusal as the JSON error."""
    try:
        return call()
    except NotFound as why:
        return responses.error(404, str(why))
    except naming_faces.AlreadyWorking as why:
        return responses.error(409, str(why), job=why.job)
    except Conflict as why:
        return responses.error(409, str(why))
    except (Refused, ValueError, paths.RootsError) as why:
        return responses.error(400, str(why))
    except Exception as why:
        logger.error("Name faces from tags: %s", why, exc_info=True)
        return responses.error(500, "Naming faces could not be started or read; the server's log says why.")


def _job_number(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError("job must be a whole number.") from None


@routes.post("/api/name-faces/start")
def name_faces_start():
    """Read the plan of naming faces from tags, as a job (tagpup.jobs.naming_faces.start): nothing is written. `folder`, the folder
    the page has open, is the one whose faces the result counts; with `only_folder` true the plan, and so the apply, are limited to
    it and its subfolders (a folder the library holds no photo under is refused, 400). A plan already waiting for its answer is
    returned as it is, when it is for the same scope; else 409 with that plan in `job`."""
    library = state.require()
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    folder = body.get("folder") if isinstance(body.get("folder"), str) and body.get("folder") else None

    def start():
        job = naming_faces.start(library, folder, hold=_hold(library), busy=lambda: _busy(library),
                                 only_folder=body.get("only_folder") is True)
        return jsonify({"success": True, "status": job.status()})
    return _reply(start)


@routes.get("/api/name-faces/status")
def name_faces_status():
    """How the job is getting on (tagpup.jobs.naming_faces.status): its state, step, percent, plan and what it wrote. From memory
    if this process runs it, else from the library's record of the run (a restart says so). Without `job`, the library's latest."""
    library = state.require()

    def read():
        wanted = request.args.get("job")
        found = naming_faces.status(library, _job_number(wanted) if wanted else None)
        if found is None:
            raise NotFound("There is no such job in this library.")
        return jsonify({"success": True, "status": found})
    return _reply(read)


@routes.get("/api/name-faces/scope")
def name_faces_scope():
    """What the dialog's choice between "only this folder" and the whole library shows (tagpup.jobs.naming_faces.scope), counts
    only: `library` and `folder` ({named, unnamed, photos}; null with `why` when the library holds no photo under `folder` or none
    is given) and `job`, the one a page opening the dialog should pick up. Reads only."""
    library = state.require()
    return _reply(lambda: jsonify({"success": True, **naming_faces.scope(library, request.args.get("folder") or None)}))


@routes.get("/api/name-faces/current")
def name_faces_current():
    """The job a page opening the library should pick up: working, or waiting for its answer, in this process; `{"status": null}`
    when there is none."""
    library = state.require()
    return _reply(lambda: jsonify({"success": True, "status": naming_faces.current(library)}))


@routes.post("/api/name-faces/confirm")
def name_faces_confirm():
    """The answer Yes to the plan of job `job`: write its names (one change of the library's journal) and, if `group` is true,
    group the rest of the faces by who they look like and name those groups from the tags (tagpup.services.identities)."""
    library = state.require()
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}

    def confirm():
        job = naming_faces.confirm(library, _job_number(body.get("job")), group=body.get("group") is True,
                                   busy=lambda: _busy(library))
        return jsonify({"success": True, "status": job.status()})
    return _reply(confirm)


@routes.post("/api/name-faces/cancel")
def name_faces_cancel():
    """Stop job `job`, or answer No to its plan: nothing is written by a Cancel before the names are written, and a write
    begun finishes whole (tagpup.jobs.naming_faces, "What Cancel does"). One that has ended is no error."""
    library = state.require()
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}

    def cancel():
        return jsonify({"success": True, "status": naming_faces.cancel(library, _job_number(body.get("job")))})
    return _reply(cancel)
