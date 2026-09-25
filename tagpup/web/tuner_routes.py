"""TagTuner's routes (docs/SPEC_TAGTUNER.md): photos by folder, tag and person, faces
and their matches, Identify Faces, indexing and the tag merges.

Each route is thin: it reads the request, calls a service, and shapes the reply. What a
server keeps for a library between requests lives in `tagpup.web.state`: here, the
Identify Faces caches and progress (tagpup.jobs.identify) and whether the library is
being clustered. Each was a dict keyed by a thread-local "active library" in the old
server, set at the top of every request and again by hand on every background thread
(docs/findings.md, #44); a value is now looked up by the Library the request names, and
a thread is handed its Library when it starts.

Errors keep the shapes the page reads. A refusal the page shows is JSON
(tagpup.web.responses.error); a bad or missing parameter and a missing face are plain
error pages, as send_error sent them.
"""
import logging
import os
import threading
import urllib.parse
from contextlib import contextmanager

from flask import Blueprint, abort, jsonify, make_response, request

from tagpup import config as tagpup_config
from tagpup.core import paths
from tagpup.core.result import Conflict, NotFound
from tagpup.jobs import identify as identify_jobs
from tagpup.jobs import indexing as indexing_jobs
from tagpup.services import faces as faces_service
from tagpup.services import identify as identify_service
from tagpup.services import indexing
from tagpup.services import people as people_service
from tagpup.services import photos as photo_actions
from tagpup.services import tags as tags_service
from tagpup.web import desktop, responses, state, tagpup_routes

logger = logging.getLogger(__name__)

routes = Blueprint("tuner", __name__)

#: Each library's cached Identify Faces answers: the queue, the grids and the matrix of
#: named faces (tagpup.jobs.identify.GridCache).
identify_cache = state.PerLibrary(lambda library: identify_jobs.GridCache())

#: How far along each grid being built for a library has got (tagpup.jobs.identify
#: .BuildProgress): written by the request doing the work, read by a status request on
#: another thread.
identify_progress = state.PerLibrary(lambda library: identify_jobs.BuildProgress())

#: Set while a library's faces are being clustered. Assignments are refused meanwhile:
#: clustering rewrites the names they would be setting.
clustering = state.PerLibrary(lambda library: threading.Event())

@contextmanager
def while_clustering(library):
    """Refuse assignments in `library` while this is held. The library is named, not
    taken from the request, so it holds on a thread no request set up."""
    flag = clustering.of(library)
    flag.set()
    try:
        yield
    finally:
        flag.clear()


def folder_indexer(library):
    """How this server adds a folder to `library`: through the CLI
    (tagpup.services.indexing.index_folder), refusing assignments while cluster-faces
    runs, since it rewrites the names they would be setting. The queue once ran it
    unguarded."""
    def index(folder, cluster, report):
        return indexing.index_folder(library, folder, tagpup_config.CODE_ROOT,
                                     cluster=cluster, report=report,
                                     while_clustering=lambda: while_clustering(library))
    return index


def _refuse(status, message):
    """Answer with the JSON error (tagpup.web.responses.error) from wherever the
    refusal is found, helpers included."""
    abort(make_response(responses.error(status, message)))


def _library_there(library):
    """Is the library's file there? The reads answer nothing for one that is not, as
    they always did, rather than failing the page."""
    return os.path.exists(library.path)


def _named(library):
    """Every named face as unit vectors, kept per state of the faces table."""
    return lambda: identify_jobs.named_faces(library, identify_cache.of(library))


def _int_arg(name, what):
    """A query parameter that must be an integer, or the 400 the old handlers sent."""
    value = request.args.get(name)
    if not value:
        abort(400, description="Missing '%s' parameter" % what)
    try:
        return int(value)
    except ValueError:
        abort(400, description="Invalid '%s' parameter" % what)


def clustering_refusal(library):
    """The 409 a write that sets faces' names is answered with while `library`'s faces
    are being clustered, or None. TagTuner's writes are refused here before each POST;
    the tree's routes, which both apps serve, ask it for a rename and a delete."""
    if library is not None and clustering.of(library).is_set():
        return jsonify({"success": False,
                        "error": "Server is currently clustering faces. Please try again later."}), 409
    return None


@routes.before_request
def refuse_writes_while_clustering():
    if request.method != "POST":
        return None
    return clustering_refusal(state.current())


# ---- Photos ------------------------------------------------------------------------------

@routes.get("/api/photos")
def photos():
    """The photos with a face still unnamed. `show_matched` is sent by the page and not
    read (docs/SPEC_TAGTUNER.md, Matched Photos Toggle)."""
    library = state.require()
    mode = request.args.get("mode", "folder-match")
    if not _library_there(library) or mode not in ("folder-match", "unmatched"):
        return jsonify([])
    return jsonify(identify_service.photos_waiting(library))


@routes.get("/api/photo-details")
def photo_details():
    library = state.require()
    wanted = request.args.get("path")
    if not wanted:
        abort(400, description="Missing 'path' parameter")
    if not _library_there(library):
        abort(404, description="Database not found")
    photo_path = urllib.parse.unquote(wanted)
    return jsonify(identify_service.photo_details(library, photo_path, _named(library)))


@routes.get("/api/photo-file")
def photo_file():
    # As stored, and not kept: the page draws face boxes over it in the stored pixels'
    # coordinates (tagpup.web.responses.photo).
    return responses.photo(request.args.get("path"), request.args.get("size"), upright=False)


@routes.get("/api/face-crop")
def face_crop():
    """A face's crop (tagpup.services.photos.face_crop)."""
    library = state.require()
    face_id = _int_arg("id", "id")
    try:
        crop = photo_actions.face_crop(library, face_id)
    except NotFound as missing:
        abort(404, description=str(missing))
    except Exception as e:
        logger.error("Error serving face crop for ID %s: %s", face_id, e)
        abort(500, description="Error cropping face: %s" % e)
    return responses.image(crop, "image/jpeg")


# ---- People and tags -----------------------------------------------------------------------

@routes.get("/api/people")
def people():
    """Everyone the library knows, the people keywords name included
    (tagpup.services.people.names). ?include_hidden=1 adds those hidden from
    autocomplete: it answers "does this person exist?", which a hidden person does."""
    library = state.require()
    include_hidden = request.args.get("include_hidden", "0") == "1"
    return jsonify(people_service.names(library, keywords_too=True, include_hidden=include_hidden))


@routes.get("/api/people-with-counts")
def people_with_counts():
    library = state.require()
    if not _library_there(library):
        return jsonify([])
    return jsonify(people_service.with_counts(library))


@routes.get("/api/tags/list")
def tags_list():
    """Every tag this library knows, with what it touches (tagpup.services.tags.listing).
    People are left out unless ?people=1."""
    library = state.require()
    if not _library_there(library):
        return jsonify({"tags": [], "buckets": {}})
    include_people = str(request.args.get("people", "0")).lower() in ("1", "true", "yes")
    return jsonify(tags_service.listing(library, include_people))


@routes.get("/api/tags/photos")
def tag_photos():
    """The photos carrying one tag, newest first."""
    library = state.require()
    tag = (request.args.get("tag") or "").strip()
    if not tag:
        _refuse(400, "Missing tag")
    if not _library_there(library):
        return jsonify({"tag": tag, "photos": [], "total": 0})
    return jsonify(tags_service.photos_carrying(library, tag))


@routes.post("/api/tags/merge")
def tags_merge():
    """Rename a tag, or merge it into another, everywhere it lives
    (tagpup.services.tags.merge). Defaults to a dry run: `apply` must be sent
    explicitly, and without it nothing is written and the plan comes back. Every
    destructive script in this repo works that way, and it has caught real mistakes
    before they reached photos."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    try:
        result = tags_service.merge(
            library, body.get("from"), body.get("into") or body.get("to"),
            tagpup_config.exiftool_path(), retire=bool(body.get("retire")),
            apply=bool(body.get("apply")))
    except Exception as e:
        logger.error("Error merging tag %r: %s", body.get("from"), e)
        _refuse(500, str(e))
    if result.refused:
        _refuse(400, result.refused)
    if body.get("apply"):
        tagpup_routes.forget_scans(library)
    reply = dict(result.details)
    if not result.ok:
        reply["error"] = result.message()
    return jsonify(reply)


@routes.post("/api/person/rename")
def person_rename():
    """Rename a person everywhere (tagpup.services.tags.rename_person)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    try:
        result = tags_service.rename_person(library, body.get("old_name"), body.get("new_name"),
                                            tagpup_config.exiftool_path())
    except NotFound as missing:
        abort(404, description=str(missing))
    except Exception as e:
        logger.error("Error renaming a person: %s", e)
        abort(500, description="Internal error: %s" % e)
    if result.refused:
        _refuse(400, result.refused)
    tagpup_routes.forget_scans(library)
    reply = {"success": True, "photos_affected": result.details["photos_affected"],
             "photos_rewritten": result.details["photos_rewritten"]}
    if not result.ok:
        reply["warning"] = result.message()
    return jsonify(reply)


# ---- Faces: who they resemble --------------------------------------------------------------

@routes.get("/api/face-matches")
def face_matches():
    library = state.require()
    face_id = _int_arg("id", "id")
    if not _library_there(library):
        return jsonify([])
    try:
        return jsonify(identify_service.face_matches(library, face_id, _named(library)))
    except NotFound:
        abort(404, description="Face not found")


@routes.get("/api/face-matches-unmatched")
def face_matches_unmatched():
    library = state.require()
    face_id = _int_arg("id", "id")
    if not _library_there(library):
        return jsonify({"matches": []})
    try:
        return jsonify(identify_service.unnamed_like(library, face_id))
    except NotFound:
        abort(404, description="Face not found")


@routes.get("/api/person-faces")
def person_faces():
    library = state.require()
    name = request.args.get("name")
    if not name:
        abort(400, description="Missing 'name' parameter")
    name = urllib.parse.unquote(name)
    limit, page = 100, 1
    try:
        if request.args.get("limit") is not None:
            limit = int(request.args.get("limit"))
    except Exception:
        pass
    try:
        if request.args.get("page") is not None:
            page = int(request.args.get("page"))
    except Exception:
        pass
    if not _library_there(library):
        return jsonify({"faces": [], "total_count": 0, "has_more": False})
    return jsonify(identify_service.person_faces(library, name, limit, page))


@routes.get("/api/faces/excluded")
def faces_excluded():
    """List excluded faces so they can be reviewed and restored."""
    library = state.require()
    if not _library_there(library):
        return jsonify({"faces": [], "total_count": 0})
    return jsonify(identify_service.excluded(library, faces_service.DEFAULT_REASON))


# ---- Identify Faces ---------------------------------------------------------------------------

@routes.get("/api/unmatched-faces/people")
def unmatched_faces_people():
    library = state.require()
    if not _library_there(library):
        return jsonify([])
    return jsonify(identify_jobs.queue(library, identify_cache.of(library)))


@routes.get("/api/unmatched-faces/person-matches")
def unmatched_faces_person_matches():
    library = state.require()
    name = request.args.get("name")
    if not name:
        abort(400, description="Missing 'name' parameter")
    name = urllib.parse.unquote(name)
    if not _library_there(library):
        return jsonify({"faces": [], "total_count": 0, "has_more": False})
    return jsonify(identify_jobs.grid(library, identify_cache.of(library),
                                      identify_progress.of(library), name))


@routes.get("/api/unmatched-faces/build-status")
def unmatched_faces_build_status():
    """How far along is the grid somebody is waiting for? Deliberately cheap and
    deliberately not cached: it is polled while another thread does the slow work, and
    it touches no database at all."""
    library = state.require()
    name = request.args.get("name")
    if not name:
        abort(400, description="Missing 'name' parameter")
    return jsonify(identify_progress.of(library).of(urllib.parse.unquote(name)))


# ---- Faces: the writes -------------------------------------------------------------------------

def _faces_write(library, action):
    """Run a face action (tagpup.services.faces) on the request's library, and answer
    what went wrong: 404 for a face or a library that is not there, 409 for a face
    that cannot be named as things stand, 400 for a request refused, 500 for anything
    else. Returns its Result.

    The faces an action took out of the identify pool come off the cached grids,
    rather than making the next click rebuild them: the action says which, and the
    fingerprints either side of its write (tagpup.store.faces.accounted_write).
    """
    try:
        result = action(library)
    except NotFound as missing:
        abort(404, description=str(missing))
    except Conflict as conflict:
        _refuse(409, str(conflict))
    except Exception as e:
        logger.error("Error in a face action: %s", e)
        abort(500, description="Internal error: %s" % e)
    if result.refused:
        _refuse(400, result.refused)
    fingerprints = result.details.get("fingerprints")
    if fingerprints and result.changed:
        identify_cache.of(library).forget_faces(result.details["face_ids"], *fingerprints)
    return result


def _read_face_ids(body):
    """Accept either face_ids (list) or a single face_id, as ints; None when neither."""
    face_ids = body.get("face_ids")
    if face_ids is None and body.get("face_id") is not None:
        face_ids = [body.get("face_id")]
    if not face_ids or not isinstance(face_ids, list):
        return None
    try:
        return [int(x) for x in face_ids]
    except (ValueError, TypeError):
        return None


@routes.post("/api/face/match")
def face_match():
    """Name one face (tagpup.services.faces.name_face)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    face_id, person_name = body.get("face_id"), body.get("person_name")
    if face_id is None or not person_name:
        abort(400, description="Missing face_id or person_name")
    try:
        face_id, person_name = int(face_id), str(person_name).strip()
    except (ValueError, TypeError):
        abort(400, description="Invalid parameters")
    _faces_write(library, lambda lib: faces_service.name_face(lib, face_id, person_name))
    return jsonify({"success": True})


@routes.post("/api/face/unmatch")
def face_unmatch():
    """Take a face's name off (tagpup.services.faces.unname_face)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    face_id = body.get("face_id")
    if face_id is None:
        abort(400, description="Missing face_id")
    try:
        face_id = int(face_id)
    except (ValueError, TypeError):
        abort(400, description="Invalid face_id")
    _faces_write(library, lambda lib: faces_service.unname_face(lib, face_id))
    return jsonify({"success": True})


@routes.post("/api/faces/match-bulk")
def faces_match_bulk():
    """Name many faces as one person (tagpup.services.faces.name_faces)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    face_ids, person_name = body.get("face_ids"), body.get("person_name")
    if not face_ids or not isinstance(face_ids, list) or not person_name:
        abort(400, description="Missing or invalid face_ids or person_name")
    try:
        face_ids, person_name = [int(fid) for fid in face_ids], str(person_name).strip()
    except (ValueError, TypeError):
        abort(400, description="Invalid parameters format")
    result = _faces_write(library, lambda lib: faces_service.name_faces(lib, face_ids, person_name))
    return jsonify({"success": True, "matched": result.details["matched"],
                    "matched_ids": result.details["matched_ids"],
                    "skipped_excluded": result.details["skipped_excluded"]})


@routes.post("/api/faces/unmatch-bulk")
def faces_unmatch_bulk():
    """Take the names off many faces (tagpup.services.faces.unname_faces)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    face_ids = body.get("face_ids")
    if not face_ids or not isinstance(face_ids, list):
        abort(400, description="Missing or invalid face_ids")
    try:
        face_ids = [int(fid) for fid in face_ids]
    except (ValueError, TypeError):
        abort(400, description="Invalid face_ids format")
    undo = bool(body.get("undo"))
    _faces_write(library, lambda lib: faces_service.unname_faces(lib, face_ids, undo=undo))
    return jsonify({"success": True})


@routes.post("/api/photo/unmatch-all")
def photo_unmatch_all():
    """Take the names off every face in a photo (tagpup.services.faces.unname_photo)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    photo_path = body.get("photo_path")
    if not photo_path:
        abort(400, description="Missing photo_path")
    _faces_write(library, lambda lib: faces_service.unname_photo(lib, photo_path))
    return jsonify({"success": True})


@routes.post("/api/photo/automatch")
def photo_automatch():
    """Automatch a photo's faces (tagpup.services.faces.automatch_photo)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    photo_path = body.get("photo_path")
    if not photo_path:
        abort(400, description="Missing photo_path")
    result = _faces_write(
        library, lambda lib: faces_service.automatch_photo(lib, photo_path, _named(lib)))
    return jsonify({"success": True, "matched_count": result.changed})


@routes.post("/api/folder/automatch")
def folder_automatch():
    """Automatch a folder's faces (tagpup.services.faces.automatch_folder)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder_path = body.get("folder_path")
    if not folder_path:
        abort(400, description="Missing folder_path")
    result = _faces_write(
        library, lambda lib: faces_service.automatch_folder(lib, folder_path, _named(lib)))
    return jsonify({"success": True, "matched_count": result.changed,
                    "remaining_counts": result.details.get("remaining_counts", {})})


@routes.post("/api/faces/exclude")
def faces_exclude():
    """Take faces out of identity work (tagpup.services.faces.exclude)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    face_ids = _read_face_ids(body)
    if face_ids is None:
        abort(400, description="Missing or invalid face_ids")
    reason = body.get("reason")   # none: the service's default
    result = _faces_write(library, lambda lib: faces_service.exclude(lib, face_ids, reason))
    # The rows changed, not the ids sent: an id that is not in the table was never
    # excluded, and saying it was is how a write reports success on nothing.
    return jsonify({"success": True, "excluded": result.changed})


@routes.post("/api/faces/restore")
def faces_restore():
    """Bring excluded faces back, unnamed (tagpup.services.faces.restore)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    face_ids = _read_face_ids(body)
    if face_ids is None:
        abort(400, description="Missing or invalid face_ids")
    result = _faces_write(library, lambda lib: faces_service.restore(lib, face_ids))
    return jsonify({"success": True, "restored": result.changed})


# ---- Folders: adding, removing, browsing -------------------------------------------------------

@routes.get("/api/folder/index-active")
def folder_index_active():
    """What is being indexed now, and what waits behind it (tagpup.jobs.indexing). The
    per-folder status can only answer about a folder the page knows of; a page that has
    just loaded knows none."""
    return jsonify(indexing_jobs.queue_for(state.require()).active())


@routes.get("/api/folder/index-status")
def folder_index_status():
    library = state.require()
    wanted = request.args.get("path")
    if not wanted:
        _refuse(400, "Missing path parameter")
    return jsonify(indexing_jobs.queue_for(library).status(urllib.parse.unquote(wanted)))


@routes.post("/api/folder/index-start")
def folder_index_start():
    """Queue one or more folders to be added to this library (tagpup.jobs.indexing).
    Accepts `folder_path` (one) or `folder_paths` (several). Clustering is opt-in: it
    re-derives every name in the library, not only the folders being added."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    requested = body.get("folder_paths")
    if requested is None:
        requested = [body.get("folder_path")] if body.get("folder_path") else []
    if not isinstance(requested, list):
        _refuse(400, "folder_paths must be a list")
    result = indexing_jobs.queue_for(library).start(
        requested, folder_indexer(library), cluster=bool(body.get("cluster", False)))
    if result.refused:
        _refuse(400, result.message())
    return jsonify({"success": True, "status": "running", **result.details})


@routes.post("/api/folder/index-cancel")
def folder_index_cancel():
    """Drop folders that have not started (tagpup.jobs.indexing.IndexQueue.cancel). The
    folder being indexed is left alone."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    wanted = body.get("folder_paths")
    if wanted is None and body.get("folder_path"):
        wanted = [body.get("folder_path")]
    cancel_all = bool(body.get("all"))
    if not cancel_all and not wanted:
        _refuse(400, "Nothing to cancel")
    result = indexing_jobs.queue_for(library).cancel(wanted or [], everything=cancel_all)
    return jsonify({"success": True, **result.details})


@routes.post("/api/folder/remove")
def folder_remove():
    """Take a folder's photos and faces out of this library
    (tagpup.services.faces.remove_folder). The photo files are never touched."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder_path = body.get("folder_path")
    if not folder_path:
        _refuse(400, "Missing folder_path")
    try:
        result = faces_service.remove_folder(library, folder_path)
    except Exception as e:
        logger.error("Error removing folder %s: %s", folder_path, e)
        _refuse(500, str(e))
    return jsonify(dict(result.details, success=True))


@routes.get("/api/folder/subfolders")
def folder_subfolders():
    """The immediate subfolders of a folder, so a parent can be expanded. Indexing
    already recurses, but as one opaque job with one progress bar for the lot; listing
    the children lets each be queued on its own, which is what makes progress legible
    and lets one bad folder be left out rather than sinking the whole run."""
    library = state.require()
    wanted = request.args.get("path")
    if not wanted:
        _refuse(400, "Missing path parameter")
    # Stored form before anything is joined onto it: a parent typed with forward
    # slashes otherwise yields children spelled with both separators at once.
    parent = paths.stored(urllib.parse.unquote(wanted))
    if not os.path.isdir(parent):
        _refuse(400, "Not a folder: %s" % parent)
    try:
        entries = sorted(e for e in os.listdir(parent) if os.path.isdir(os.path.join(parent, e)))
    except OSError as e:
        _refuse(500, "Could not read %s: %s" % (parent, e))

    indexed_by_folder = photo_actions.indexed_by_folder(library)
    folders = []
    for name in entries:
        full = os.path.join(parent, name)
        count, subdirs = photo_actions.count_photos(full)
        folders.append({
            "path": full,
            "name": name,
            "images": count,
            "has_subfolders": subdirs,
            # Counted like `images`, which is every photo under the folder. This
            # counted only photos directly in it, so a folder of subfolders, fully
            # indexed, read "8756 image(s)" as though new and was preselected.
            "indexed": sum(n for folder, n in indexed_by_folder.items()
                           if folder == paths.key(full) or paths.is_under(folder, full)),
        })
    own_images, own_subdirs = photo_actions.count_photos(parent, recursive=False)
    return jsonify({
        "parent": parent,
        "folders": folders,
        "own_images": own_images,
        "own_indexed": indexed_by_folder.get(paths.key(parent), 0),
        "has_subfolders": own_subdirs,
    })


@routes.get("/api/browse-folder")
def browse_folder():
    try:
        return jsonify({"path": desktop.ask_for_folder()})
    except Exception as e:
        _refuse(500, str(e))
