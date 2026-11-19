"""Both apps' folder routes: adding folders to a library through its index queue (tagpup.jobs.indexing), what is
being indexed, removing a folder, the folders a library holds and a folder's subfolders (docs/plans/manage-folders.md).

One blueprint registered in both apps (tagpup.web.app): TagPup's Manage Folders and TagTuner's Add and Remove
folder ask the same routes, so a rule of them -- a removal refused while the folder is being indexed -- is written once.
"""
import os

from flask import Blueprint, jsonify, request

from tagpup import runtime as runtimes
from tagpup.core import paths
from tagpup.jobs import suggestions as suggestion_jobs
from tagpup.services import faces as faces_service
from tagpup.services import libraries as library_actions
from tagpup.services import photos as photo_actions
from tagpup.web import responses, state

routes = Blueprint("folders", __name__)


@routes.get("/api/folder/index-active")
def folder_index_active():
    """What is being indexed now, and what waits behind it (tagpup.jobs.indexing). The
    per-folder status can only answer about a folder the page knows of; a page that has
    just loaded knows none."""
    return jsonify(state.runtime().index_queues.of(state.require()).active())


@routes.get("/api/folder/index-status")
def folder_index_status():
    """Where a folder's index has got (IndexQueue.status)."""
    library = state.require()
    wanted = request.args.get("path")
    if not wanted:
        return responses.error(400, "Missing path parameter")
    return jsonify(state.runtime().index_queues.of(library).status(wanted))


@routes.post("/api/folder/index-start")
def folder_index_start():
    """Queue one or more folders to be added to this library (tagpup.jobs.indexing).
    Accepts `folder_path` (one) or `folder_paths` (several). Each index is followed by a read-only matching of the
    faces it made when it recorded a photo, as every index is (tagpup.services.indexing.index_folder)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    requested = body.get("folder_paths")
    if requested is None:
        requested = [body.get("folder_path")] if body.get("folder_path") else []
    if not isinstance(requested, list):
        return responses.error(400, "folder_paths must be a list")
    # Adding a folder, as TagPup's Add does (tagpup.services.libraries.add).
    result = library_actions.add(library, requested, lambda folders: state.runtime().index_queues.of(library).start(
        folders, runtimes.index_folder(library)))
    suggestion_jobs.dropped_by_add(library, requested)
    if result.refused:
        return responses.refused(result)
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
        return responses.error(400, "Nothing to cancel")
    result = state.runtime().index_queues.of(library).cancel(wanted or [], everything=cancel_all)
    return jsonify({"success": True, **result.details})


@routes.post("/api/folder/remove")
def folder_remove():
    """Take a folder's photos and faces out of this library
    (tagpup.services.faces.remove_folder). The photo files are never touched.

    Refused, 409, while the folder, a folder above it or one below it waits in this library's index queue or is being
    indexed (IndexQueue.overlaps): the run would find its folder no longer the library's at its next row and fail
    part-way. Another library's queue is its own: a library holding the same folder removes it."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder_path = body.get("folder_path")
    if not folder_path:
        return responses.error(400, "Missing folder_path")
    busy = state.runtime().index_queues.of(library).overlaps(folder_path)
    if busy:
        if busy["status"] == "running":
            return responses.error(409, "Not removed: %s is being indexed in this library just now. Remove the "
                                        "folder when its index has finished." % busy["folder"])
        return responses.error(409, "Not removed: %s waits to be indexed in this library. Cancel it, or remove the "
                                    "folder when its index has finished." % busy["folder"])
    result = faces_service.remove_folder(library, folder_path)
    if not result.ok:
        # Its rows went (photos_removed says how many), but a step after them failed: the reply says which.
        return responses.error(500, result.message(), **result.details)
    return jsonify(dict(result.details, success=True))


@routes.get("/api/folder/indexed")
def folder_indexed():
    """The folders this library holds photos in (tagpup.services.photos.indexed_folders):
    what Remove Folder offers, a folder gone from disk included (#47)."""
    library = state.require()
    return jsonify({"folders": photo_actions.indexed_folders(library)})


@routes.get("/api/folder/subfolders")
def folder_subfolders():
    """The immediate subfolders of a folder, so a parent can be expanded. Indexing
    already recurses, but as one opaque job with one progress bar for the lot; listing
    the children lets each be queued on its own, which is what makes progress legible
    and lets one bad folder be left out rather than sinking the whole run."""
    library = state.require()
    wanted = request.args.get("path")
    if not wanted:
        return responses.error(400, "Missing path parameter")
    # Stored form before anything is joined onto it: a parent typed with forward
    # slashes otherwise yields children spelled with both separators at once.
    parent = paths.stored(wanted)
    refused = responses.folder_refused(parent)
    if refused:
        return refused
    try:
        entries = photo_actions.subfolders(parent)
    except OSError as e:
        # A folder that is there but will not be listed: Windows refused it (no permission). Not the server's fault.
        return responses.error(403, "TagPup could not list the folders in %s: %s" % (parent, e.strerror or e))
    if entries is None:
        return responses.refused(photo_actions.folder_refusal(parent, "unanswered"))
    entries = sorted(entries)

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
