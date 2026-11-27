"""TagTuner's routes (docs/SPEC_TAGTUNER.md): photos by folder, tag and person, faces
and their matches, Identify Faces, indexing and the tag merges.

Each route is thin: it reads the request, calls a service, and shapes the reply. What a
server keeps for a library between requests lives in `tagpup.web.state`: here, the
Identify Faces caches and progress (tagpup.jobs.identify). Each was a dict keyed by a thread-local "active library" in the old
server, set at the top of every request and again by hand on every background thread
(docs/findings.md, #44); a value is now looked up by the Library the request names, and
a thread is handed its Library when it starts.

Errors keep the shapes the page reads. A refusal the page shows is JSON
(tagpup.web.responses.error); a bad or missing parameter and a missing face are plain
error pages, as send_error sent them.
"""
import logging

from flask import Blueprint, abort, jsonify, request

from tagpup import config as tagpup_config
from tagpup import runtime as runtimes
from tagpup.jobs import identify as identify_jobs
from tagpup.jobs import naming_faces
from tagpup.services import face_assignment, face_people
from tagpup.services import faces as faces_service
from tagpup.services import identify as identify_service
from tagpup.services import people as people_service
from tagpup.services import photos as photo_actions
from tagpup.services import roots as roots_service
from tagpup.services import roots_location
from tagpup.services import tags as tags_service
from tagpup.web import desktop, face_routes, responses, roots_gate, roots_ingress, state
from tagpup.web import lifecycle as lifecycles
from tagpup.web import libraries as web_libraries

logger = logging.getLogger(__name__)

routes = Blueprint("tuner", __name__)

#: Each library's cached Identify Faces answers (tagpup.jobs.identify.GridCache): the process's,
#: kept in tagpup.web.face_routes, which the reads of faces both apps serve use.
identify_cache = face_routes.identify_cache

#: How far along each grid being built for a library has got (tagpup.jobs.identify
#: .BuildProgress): written by the request doing the work, read by a status request on
#: another thread.
identify_progress = state.PerLibrary(lambda library: identify_jobs.BuildProgress())

#: The names this app's caches are kept under in the process's idle registry (idle_caches).
POOL, IDENTIFY = "New Person pool", "identify caches"


def _building():
    """Is a grid being built for any library? Its caches are held meanwhile."""
    for library_key in identify_progress.libraries():
        progress = identify_progress.held(library_key)
        if progress is not None and progress.building():
            return True
    return False


def idle_caches(idle):
    """Register what this app keeps only while it is used in the process's idle registry
    (tagpup.core.idle.IdleCaches, the runtime's): New Person's pool of nameless faces --
    about 185 MB on photo_index -- and the rest of Identify Faces' caches -- the queue,
    each grid, the named faces' matrix -- each read again at its next use, never while a
    grid is being built."""
    def release_pools():
        for library_key in identify_cache.libraries():
            cache = identify_cache.held(library_key)
            if cache is not None:
                cache.drop("unnamed_faces")
    idle.register(POOL, release_pools)
    idle.register(IDENTIFY, identify_cache.release, in_use=_building)
    identify_cache.on_use = lambda: idle.used(IDENTIFY)


def _used(name):
    state.runtime().idle.used(name)


_refuse = face_routes.refuse


_library_there = face_routes.library_there
_named = face_routes.named
_decided = face_routes.decided
_int_arg = face_routes.int_arg
_read_face_ids = face_routes.read_face_ids


#: What a write of faces, tags or rows is answered with while "Name faces from tags" gives names (#871, #872).
NAMING_REFUSAL = "Names are being given from tags; this waits until it finishes."


def naming_refusal(library):
    """The 409 a write that sets faces' names, tags or rows is answered with while names are being given from the tags in
    `library` (tagpup.jobs.naming_faces.writing), or None. TagTuner's writes are refused here before each POST; the tree's
    routes, which both apps serve, ask it for a rename and a delete, and tagpup.web.name_faces_routes for the writes of
    TagPup that the naming must not be written over."""
    if library is not None and naming_faces.writing(library):
        return responses.error(409, NAMING_REFUSAL)
    return None


@routes.before_request
def refuse_face_writes_while_naming():
    """A blueprint's before_request: a POST is refused while the library's names are given from tags (naming_refusal).
    TagTuner's routes, and TagPup's writes of the faces on the open photo (photo_face_routes)."""
    if request.method != "POST":
        return None
    return naming_refusal(state.current())


# ---- Photos ------------------------------------------------------------------------------

@routes.get("/api/photos")
def photos():
    """The photos with a face still unnamed; with `show_matched=true` (the Show matched
    toggle), the photos whose faces are all named as well."""
    library = state.require()
    mode = request.args.get("mode", "folder-match")
    if not _library_there(library) or mode not in ("folder-match", "unmatched"):
        return jsonify([])
    show_matched = request.args.get("show_matched") == "true"
    return jsonify(identify_service.photos_waiting(library, show_matched=show_matched))


@routes.get("/api/photo-details")
def photo_details():
    library = state.require()
    wanted = request.args.get("path")
    if not wanted:
        abort(400, description="Missing 'path' parameter")
    if not _library_there(library):
        abort(404, description="Database not found")
    photo_path = wanted
    return jsonify(identify_service.photo_details(library, photo_path, _named(library)))


@routes.get("/api/photo-file")
def photo_file():
    # As stored, and not kept: the page draws face boxes over it in the stored pixels'
    # coordinates (tagpup.web.responses.photo). `upright=1` turns a copy as a person
    # sees it, for a view that draws no boxes: the Tags view's cards (#31).
    return responses.photo(request.args.get("path"), request.args.get("size"),
                           upright=request.args.get("upright") == "1")


@routes.get("/api/face-crop")
def face_crop():
    """A face's crop (tagpup.services.photos.face_crop)."""
    library = state.require()
    face_id = _int_arg("id", "id")
    try:
        crop = photo_actions.face_crop(library, face_id)
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error serving face crop for ID %s: %s", face_id, e)
        abort(500, description="Error cropping face: %s" % e)
    return responses.image(crop, "image/jpeg")


# ---- People and tags -----------------------------------------------------------------------

@routes.get("/api/people")
def people():
    """The people with a person tag, each `{id, name, tag}` (tagpup.services.people.records); ?include_hidden=1 adds those
    hidden from autocomplete: it answers "does this person exist?", which a hidden person does; without it, what is offered
    while typing."""
    library = state.require()
    return jsonify(people_service.records(library, include_hidden=request.args.get("include_hidden", "0") == "1"))


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
    with state.fields(library) as fields:
        result = tags_service.merge(library, body.get("from"), body.get("into") or body.get("to"), fields,
                                    retire=bool(body.get("retire")), apply=bool(body.get("apply")))
    if result.refused:
        return responses.refused(result)
    reply = dict(result.details)
    if not result.ok:
        reply["error"] = result.message()
    return jsonify(reply)


def not_renamed(result):
    """The reply to a rename that moved nothing in the tree because photos could not be rewritten: `success` false, `renamed`
    false, the sentence, and the photos that failed (`failed`, [{"path", "why"}], the first 50; `failed_count` all of them). The
    pages say "Not renamed" and keep the old name."""
    return jsonify({"success": False, "renamed": False, "error": result.message(),
                    "photos_affected": result.details.get("photos_affected", 0),
                    "photos_rewritten": result.details.get("photos_rewritten", 0),
                    "failed": result.details.get("failed_photos", []), "failed_count": result.details.get("failed_count", 0)})


@routes.post("/api/person/rename")
def person_rename():
    """Rename a person everywhere (tagpup.services.tags.rename_person): the one person picked -- `person_id`, or `old_name` for a
    page not reloaded since the update, refused naming the candidates when two people are called it."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    try:
        with state.fields(library) as fields:
            result = tags_service.rename_person(library, face_routes.person_arg(body.get("person_id"), body.get("old_name")),
                                                body.get("new_name"), fields)
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error renaming a person: %s", e)
        abort(500, description="Internal error: %s" % e)
    if result.refused:
        return responses.refused(result)
    if not result.ok and not result.changed:
        return not_renamed(result)
    reply = {"success": True, "photos_affected": result.details["photos_affected"],
             "photos_rewritten": result.details["photos_rewritten"]}
    if not result.ok:
        reply["warning"] = result.message()
        reply["failed"] = result.details.get("failed_photos", [])
    return jsonify(reply)


# ---- Faces: who they resemble --------------------------------------------------------------

@routes.get("/api/face-matches-unmatched")
def face_matches_unmatched():
    library = state.require()
    face_id = _int_arg("id", "id")
    if not _library_there(library):
        return jsonify({"matches": []})
    try:
        _used(POOL)
        return jsonify(identify_service.unnamed_like(
            library, face_id, lambda: identify_jobs.unnamed_faces(library, identify_cache.of(library)), named=_named(library)))
    except responses.ANSWERED as why:
        return responses.of(why)


@routes.get("/api/person-faces")
def person_faces():
    library = state.require()
    person = face_routes.person_arg(request.args.get("person_id"), request.args.get("name"))
    if person is None:
        abort(400, description="Missing 'name' parameter")
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
    try:
        # A read: the person by their id; a name one person has is that person, a name two people have is refused naming their
        # paths, a name nobody is called holds nothing.
        person = people_service.for_reading(library, person)
        if person is None:
            return jsonify({"faces": [], "total_count": 0, "has_more": False})
        return jsonify(identify_service.person_faces(library, person, limit, page))
    except responses.ANSWERED as why:
        return responses.of(why)


@routes.get("/api/faces/excluded")
def faces_excluded():
    """List excluded faces so they can be reviewed and restored."""
    library = state.require()
    if not _library_there(library):
        return jsonify({"faces": [], "total_count": 0})
    return jsonify(identify_service.excluded(library, faces_service.DEFAULT_REASON))


# ---- Identify Faces ---------------------------------------------------------------------------

def _grid_key():
    """What a request calls the grid it asks for or the progress of: the id of the person's node when the page has it, else the
    name (a bucket)."""
    return request.args.get("person_id") or request.args.get("name")


@routes.get("/api/unmatched-faces/people")
def unmatched_faces_people():
    library = state.require()
    if not _library_there(library):
        return jsonify([])
    # The queue is cached against the faces and photos; who is shared is the tree's, read now, on a copy.
    waiting = [dict(each) for each in identify_jobs.queue(library, identify_cache.of(library))]
    return jsonify(people_service.annotate(library, waiting))


@routes.get("/api/unmatched-faces/person-matches")
def unmatched_faces_person_matches():
    library = state.require()
    person = face_routes.person_arg(request.args.get("person_id"), request.args.get("name"))
    if person is None:
        abort(400, description="Missing 'name' parameter")
    if not _library_there(library):
        return jsonify({"faces": [], "total_count": 0, "has_more": False})
    asked = _grid_key()
    try:
        # A read: the person by their id, a name one person has as that person (an old page; typed or external), a name two
        # people have refused naming their paths, a name nobody is called holds nothing.
        person = people_service.for_reading(library, person)
    except responses.ANSWERED as why:
        return responses.of(why)
    if person is None:
        return jsonify({"faces": [], "total_count": 0, "has_more": False})
    return jsonify(identify_jobs.grid(library, identify_cache.of(library),
                                      identify_progress.of(library), person, progress_key=asked))


@routes.get("/api/unmatched-faces/build-status")
def unmatched_faces_build_status():
    """How far along is the grid somebody is waiting for? Deliberately cheap and
    deliberately not cached: it is polled while another thread does the slow work, and
    it touches no database at all."""
    library = state.require()
    key = _grid_key()
    if not key:
        abort(400, description="Missing 'name' parameter")
    return jsonify(identify_progress.of(library).of(key))


# ---- Faces: the writes -------------------------------------------------------------------------

# The writes of one face, which TagPup's Organize makes as well (tagpup.web.photo_face_routes): one view each
# (tagpup.web.face_routes), registered in each app's blueprint, which refuses them while clustering.
routes.add_url_rule("/api/face/match", view_func=face_routes.face_match, methods=["POST"])
routes.add_url_rule("/api/face/unmatch", view_func=face_routes.face_unmatch, methods=["POST"])
routes.add_url_rule("/api/faces/exclude", view_func=face_routes.faces_exclude, methods=["POST"])
_faces_write = face_routes.faces_write


@routes.post("/api/faces/match-bulk")
def faces_match_bulk():
    """Name many faces as one person (tagpup.services.faces.name_faces)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    face_ids, person = body.get("face_ids"), face_routes.person_arg(body.get("person_id"), body.get("person_name"))
    if not face_ids or not isinstance(face_ids, list) or person is None:
        abort(400, description="Missing or invalid face_ids or person_name")
    try:
        face_ids = [int(fid) for fid in face_ids]
    except (ValueError, TypeError):
        abort(400, description="Invalid parameters format")
    try:
        plan = face_assignment.plan_name(library, face_ids, person)
    except responses.ANSWERED as why:
        return responses.of(why)
    outcome = face_routes.assigned(library, plan)
    return face_routes.with_warning(
        {"success": True,
         "person": people_service.annotate(library, [{"name": plan.person_name, "person_id": plan.person_id}])[0]["person"],
         "matched": len(outcome.applied), "matched_ids": outcome.applied, "skipped_excluded": plan.skipped_excluded,
         "tags_written": outcome.files, "not_named": outcome.left, "change": outcome.change}, outcome)


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
    outcome = face_routes.assigned(library, face_assignment.plan_unname(library, face_ids))
    return face_routes.with_warning({"success": True, "changed": len(outcome.applied) + outcome.plain,
                                     "tags_removed": outcome.files, "change": outcome.change, "left": outcome.left}, outcome)


@routes.post("/api/photo/unmatch-all")
def photo_unmatch_all():
    """Take the names off every face in a photo, and the tags of the people they named
    (tagpup.services.face_people.unname_photo)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    photo_path = body.get("photo_path")
    if not photo_path:
        abort(400, description="Missing photo_path")
    writer = face_routes.writer_for(library, bool(body.get("page_writes_tags")))
    result = _faces_write(library, lambda lib: face_people.unname_photo(lib, photo_path, writer))
    return jsonify({"success": True, **face_routes.tags_reply(result)})


@routes.post("/api/photo/automatch")
def photo_automatch():
    """Automatch a photo's faces, and put the people it named on the photo
    (tagpup.services.face_people.automatch_photo)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    photo_path = body.get("photo_path")
    if not photo_path:
        abort(400, description="Missing photo_path")
    writer = face_routes.writer_for(library)
    result = _faces_write(
        library, lambda lib: face_people.automatch_photo(lib, photo_path, _decided(lib), writer))
    return jsonify({"success": True, "matched_count": result.changed, **face_routes.tags_reply(result)})


@routes.post("/api/folder/automatch")
def folder_automatch():
    """Automatch a folder's faces (tagpup.services.faces.automatch_folder): Re-examine
    this folder. With "dry_run": true, what it would do, and nothing written."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder_path = body.get("folder_path")
    if not folder_path:
        abort(400, description="Missing folder_path")
    # Anything but no flag, or a false one, rehearses: "false" as text is the safe mistake.
    rehearse = bool(body.get("dry_run"))
    if rehearse:
        result = _faces_write(
            library, lambda lib: faces_service.automatch_folder(lib, folder_path, _decided(lib), rehearse=True))
        details = result.details
        return jsonify({"success": True, "dry_run": True, "matched_count": result.changed,
                        "faces": details.get("faces", 0), "photos": details.get("photos", 0),
                        "people": details.get("people", {}), "renamed": details.get("renamed", 0)})
    # Applied: the guesses AND the people's tags, as one batch. The faces and photos are what was written, not proposed.
    outcome = face_routes.assigned(library, face_assignment.plan_guesses(library, folder_path, _decided(library)))
    named = outcome.applied
    written = faces_service.written_report(library, named, folder_path)
    people, photos_named, remaining = written["people"], written["photos"], written["remaining_counts"]
    return face_routes.with_warning(
        {"success": True, "dry_run": False, "matched_count": len(named), "faces": len(named), "photos": len(photos_named),
         "people": people, "renamed": 0, "remaining_counts": remaining, "photos_named": photos_named,
         "tags_written": outcome.files, "change": outcome.change}, outcome)


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


# ---- Folders: browsing (adding and removing them: tagpup.web.folder_routes, both apps') ------------------

@routes.get("/api/browse-folder")
def browse_folder():
    return jsonify({"path": desktop.ask_for_folder()})


# ---- Roots: where this machine keeps each root of the library ----------------------------------
#
# The library's rows say `@pictures/2024/a.jpg`; this machine's map (machine_roots.json) says where
# `pictures` is. These routes show each root and move a root: the map is edited, never
# a row (tagpup.services.roots_location; docs/ARCHITECTURE.md, "Roots and machines"). They answer this
# PC only, as the Activity page does (tagpup.web.security.THIS_PC_ONLY).

def _machine():
    """This machine's map, handed to the services (they do not import tagpup.config)."""
    return roots_service.Machine(tagpup_config.machine_roots, tagpup_config.add_machine_root,
                                 tagpup_config.machine_roots_path, tagpup_config.set_location)


def _busy(library, name=None):
    """What runs for `library` in this process (tagpup.web.lifecycle.running_for) and, for root `name`, in each other
    library of the home that holds it too: the machine's map is one for them all."""
    said = lifecycles.running_for(library)
    if name:
        for other in roots_location.sharing(library, name, runtimes.home_libraries(web_libraries.test_mode())):
            said += ["%s (in %s, which uses this root too)" % (each, other.name) for each in lifecycles.running_for(other)]
    return said


def _roots_answer(call):
    """`call()`, its refusals as the page reads them: a map that cannot be used (config.MachineMapError) or a root it does
    not place (paths.RootsError) 400 -- this dialog is where they are put right, so no banner is raised -- and what the
    service refused as every route answers it (tagpup.web.responses.of)."""
    try:
        return call()
    except ValueError as e:
        _refuse(400, str(e))
    except responses.ANSWERED as why:
        responses.abort_with(why)


def _root_named(body):
    name = body.get("root")
    if not isinstance(name, str) or not name:
        _refuse(400, "Missing root: the name of one of the library's roots")
    return name


@routes.get("/api/roots")
def roots_list():
    """Each root of the library and where this machine keeps it."""
    library = state.require()
    found = _roots_answer(lambda: roots_location.overview(library, _machine(), runtimes.home_libraries(web_libraries.test_mode())))
    found["busy"] = _busy(library)
    return jsonify(found)


def _moved(library, name):
    body = request.get_json(silent=True) or {}
    machine = _machine()
    apply = body.get("dry_run") is False
    result = _roots_answer(lambda: roots_location.change_location(
        library, name, body.get("location"), machine, apply=apply, override=body.get("override") is True,
        expected=body.get("from") or None, busy=lambda: _busy(library, name),
        others=runtimes.home_libraries(web_libraries.test_mode())))
    if result.changed:
        # The map moved: what this process kept of the old places is let go -- the connections
        # find the new map by themselves within a second, and every cache keyed by the library's
        # generations is built again (tagpup.store.generations), TagPup's folder scans among them.
        roots_gate.forget(library)
        roots_ingress.forget(library)
    answer = {"success": result.refused is None, "dry_run": not apply, "changed": result.changed,
              "error": result.refused, **{key: value for key, value in result.details.items() if key != "dry_run"}}
    return jsonify(answer), (200 if result.refused is None else 409 if result.details.get("conflict") else 400)


@routes.post("/api/roots/change-location")
def roots_change_location():
    """Move a root to `location` on this machine. A dry run unless `dry_run` is false: the sample
    of what the new place holds, and why it would be refused. `from` is the place the page saw."""
    library = state.require()
    name = _root_named(request.get_json(silent=True) or {})
    return _moved(library, name)
