"""TagPup's routes (docs/SPEC_TAGPUP_GUI.md): the folder view, a photo's metadata,
its faces, Suggest and the bulk writes. The tag tree's routes are both apps'
(tagpup.web.taxonomy_routes).

Each route is thin: it reads the request, calls a service, and shapes the reply. What
this server keeps for a library between requests is a `tagpup.core.per_library.PerLibrary`
here: the folder cache. The suggestion runs and the index queue keep theirs in
`tagpup.jobs`, and the people cache is the store's, keyed by the tree's generation.

The old server kept the folder cache in a dict that resolved the library through a
thread-local set at the top of each request, and set again by hand on every background
thread; a thread that forgot was served another library's cache (docs/findings.md,
#44). Here a background thread is handed its Library when it starts.
"""
import logging
import os
import re
import string
import threading

from flask import Blueprint, jsonify, request

from tagpup import config as tagpup_config
from tagpup.core import fields, paths, suggesting, vocabulary
from tagpup.core.result import NotFound
from tagpup.jobs import indexing as indexing_jobs
from tagpup.jobs import suggestions as suggestion_jobs
from tagpup.services import faces as face_actions
from tagpup.services import indexing
from tagpup.services import people as people_service
from tagpup.services import photos as photo_actions
from tagpup.services import tagging as tagging_actions
from tagpup.services import tags as tags_service
from tagpup.web import desktop, responses, state

logger = logging.getLogger(__name__)

routes = Blueprint("tagpup", __name__)

#: Folder suggestions offered while a path is typed, at most.
AUTOCOMPLETE_LIMIT = 15

#: How long the browser may keep a photo it was sent: the page puts the photo's mtime in
#: the URL, so a rotated photo is asked for again.
PHOTO_CACHE_SECONDS = 86400


# ---- What the server keeps for each library --------------------------------------------

class FolderCache:
    """What the page was last shown of each folder it opened: {folder key: {photo key:
    page record}}. Written by a scan, kept true by every write the page makes through
    this server, and read by Suggest for the folder's photos.

    A scan stores its whole recursive walk under the scanned folder's key, so a photo in
    a subfolder is filed under an ancestor, not under its own directory. The writers
    looked it up under paths.key(dirname(photo)) and never found it: its record went on
    showing what it held before the write. `entries_for` finds every map holding a
    photo, since a folder and a subfolder of it may both have been scanned.
    """

    def __init__(self):
        self._maps = {}
        self._lock = threading.Lock()

    def get(self, folder):
        with self._lock:
            return self._maps.get(paths.key(folder))

    def put(self, folder, photos):
        with self._lock:
            self._maps[paths.key(folder)] = photos

    def pop(self, folder):
        with self._lock:
            return self._maps.pop(paths.key(folder), None)

    def clear(self):
        with self._lock:
            self._maps.clear()

    def entries_for(self, photo_path):
        """Every map holding this photo, as (map, record) pairs."""
        photo_key = paths.key(photo_path)
        with self._lock:
            maps = list(self._maps.items())
        found = []
        for folder_key, photos in maps:
            if not paths.is_under(photo_path, folder_key):
                continue
            record = photos.get(photo_key)
            if record is not None:
                found.append((photos, record))
        return found


folders = state.PerLibrary(lambda library: FolderCache())


def forget_scans(library):
    """Photos of `library` were rewritten: its cached scans describe them as they were.
    The cache is the process's, so whichever app rewrote them calls this -- the tree's
    routes, and TagTuner's tag merge and person rename."""
    folders.of(library).clear()


def _folder_photos(library, folder):
    """The folder's photos as the page was shown them, scanned now if it has not been:
    what a suggestion run works from. Called on the run's own thread, with the library
    it was handed."""
    cache = folders.of(library)
    found = cache.get(folder)
    if not found:
        logger.info("Folder cache empty for %s; scanning it.", folder)
        found = photo_actions.scan_folder(library, folder, state.exiftool(library))
        cache.put(folder, found)
    return found


def _folder_indexer(library):
    """How this server adds a folder to a library: through the CLI
    (tagpup.services.indexing.index_folder). Then the folder's cached scan is dropped,
    since rows were written even when clustering failed afterwards. Runs on the queue's
    thread, with the library it was handed."""
    def index(folder, cluster, report):
        try:
            return indexing.index_folder(library, folder, tagpup_config.CODE_ROOT,
                                         cluster=cluster, report=report)
        finally:
            folders.of(library).pop(folder)
    return index


def _wanted_path():
    """The request's `path`, or None, as Flask decoded it. The old routes decoded it a
    second time, and a name holding "%41" became one holding "A" (docs/findings.md, #32)."""
    return request.args.get("path") or None


def _sorted(photos):
    """A folder's records in the order the page shows them."""
    return sorted(photos.values(), key=photo_actions.taken_order)


# ---- The folder ---------------------------------------------------------------------------

@routes.get("/api/browse-folder")
def browse_folder():
    try:
        return jsonify({"path": desktop.ask_for_folder()})
    except Exception as e:
        return responses.error(500, str(e))


@routes.get("/api/autocomplete-folder")
def autocomplete_folder():
    """Folders on this machine that start with what was typed, for the path box. A
    desktop question, like the folder dialog, so it stays in the web layer."""
    typed = request.args.get("path")
    if not typed:
        return jsonify([])
    return jsonify(_folder_suggestions(typed.strip()))


def _folder_suggestions(typed):
    if not typed:
        return [letter + ":\\" for letter in string.ascii_uppercase if os.path.exists(letter + ":\\")]
    typed = os.path.expandvars(typed)
    if re.match(r"^[a-zA-Z]$", typed):
        return [typed.upper() + ":\\"]
    if re.match(r"^[a-zA-Z]:$", typed):
        return [typed.upper() + "\\"]
    norm = os.path.normpath(typed)
    if typed.endswith(("\\", "/")):
        base_dir, prefix = norm, ""
    else:
        base_dir, prefix = os.path.dirname(norm), os.path.basename(norm).lower()
    found = []
    try:
        if os.path.isdir(base_dir):
            for name in os.listdir(base_dir):
                full = os.path.join(base_dir, name)
                if os.path.isdir(full) and (not prefix or name.lower().startswith(prefix)):
                    found.append(full)
    except Exception:
        pass
    return found[:AUTOCOMPLETE_LIMIT]


@routes.get("/api/folder/scan")
def folder_scan():
    library = state.require()
    folder = _wanted_path()
    if not folder:
        return responses.error(400, "Missing 'path' parameter")
    force = (request.args.get("force") or "false").lower() == "true"
    if not os.path.isdir(folder):
        return responses.error(400, "Path is not a valid directory: %s" % folder)
    folder = paths.stored(folder)
    cache = folders.of(library)
    # An empty folder is walked again each time: photos copied in after it was first
    # opened never showed until Refresh, since its empty scan was kept like any other.
    photos = None if force else cache.get(folder)
    if not photos:
        photos = photo_actions.scan_folder(library, folder, state.exiftool(library))
        cache.put(folder, photos)
    return jsonify(_sorted(photos))


@routes.get("/api/folder/index-status")
def folder_index_status():
    library = state.require()
    folder = _wanted_path()
    if not folder:
        return responses.error(400, "Missing path parameter")
    return jsonify(indexing_jobs.queue_for(library).status(folder))


@routes.post("/api/folder/index-start")
def folder_index_start():
    """Queue a folder to be added to this library (tagpup.jobs.indexing). The page no
    longer asks: adding folders is TagTuner's (docs/findings.md, #34). Clustering
    re-derives every face name in the library, not only this folder's, and can discard
    manual corrections, so it is opt-in."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    result = indexing_jobs.queue_for(library).start(
        [body.get("folder_path")], _folder_indexer(library), cluster=bool(body.get("cluster", False)))
    if result.refused:
        return responses.error(400, result.message())
    return jsonify({"success": True, "status": "running", **result.details})


@routes.get("/api/folder/suggest-status")
def folder_suggest_status():
    library = state.require()
    folder = _wanted_path()
    if not folder:
        return responses.error(400, "Missing 'path' parameter")
    # Under the spellings this server's scan gave the page (tagpup.jobs.suggestions).
    return jsonify(suggestion_jobs.runs_for(library).status(folder, folders.of(library).get(folder)))


@routes.post("/api/folder/suggest-start")
def folder_suggest_start():
    """Suggest tags for a folder's photos (tagpup.jobs.suggestions)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder = body.get("folder_path")
    if not folder or not os.path.isdir(folder):
        return responses.error(400, "Invalid folder path")
    folder = paths.stored(folder)
    work = suggestion_jobs.work_for(library, lambda: _folder_photos(library, folder), state.runtime())
    return jsonify({"success": True, "status": suggestion_jobs.runs_for(library).start(folder, work)})


@routes.post("/api/folder/auto-apply")
def folder_auto_apply():
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder = body.get("folder_path")
    # No floor of its own. The list this writes has already been filtered to what the
    # page was shown; a second threshold here could only take away some of what was
    # offered, which is the behaviour that made Apply All ambiguous.
    threshold = body.get("threshold", 0.0)
    if not folder or not os.path.isdir(folder):
        return responses.error(400, "Invalid folder path")
    suggestions = suggestion_jobs.runs_for(library).suggestions(folder)
    if suggestions is None:
        return responses.error(400, "No suggestions found for this folder")
    photo_paths = body.get("photo_paths")
    if photo_paths:
        wanted = {paths.key(p) for p in photo_paths}
        suggestions = {k: v for k, v in suggestions.items() if paths.key(k) in wanted}
    # Apply exactly what the panel offered (tagpup.core.suggesting.offered_tags).
    additions = {path: suggesting.offered_tags(entry, threshold) for path, entry in suggestions.items()}
    try:
        result = tagging_actions.add_tags(library, additions, state.exiftool(library))
        if result.refused:
            return responses.error(400, result.refused)
        _records_written(library, result)
        if not result.ok:
            raise RuntimeError(result.message())
    except Exception as e:
        logger.error("Error auto-applying suggestions: %s", e)
        return responses.error(500, str(e))
    return jsonify({"success": True})


@routes.post("/api/folder/time-shift")
def folder_time_shift():
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder = body.get("folder_path")
    camera_model = body.get("camera_model")
    shift_minutes = body.get("shift_minutes", 0)
    if not folder or not os.path.isdir(folder):
        return responses.error(400, "Invalid folder path")
    if shift_minutes == 0:
        return jsonify({"success": True, "message": "No shift applied (0 minutes)"})
    # Walked and written in the stored form, looked up by the key. This used to walk the
    # lower-cased key itself, so every path it found -- and every path it sent back to
    # the page -- was lower case.
    folder = paths.stored(folder)
    cache = folders.of(library)
    photos = cache.get(folder)
    if not photos:
        try:
            photos = photo_actions.read_folder(library, folder, state.exiftool(library))
            cache.put(folder, photos)
        except Exception as e:
            logger.error("Error scanning folder on the fly for time shift: %s", e)
            return responses.error(500, "Folder must be scanned first, and scan fallback failed: %s" % e)
    # The photos of the camera asked for (tagpup.core.fields).
    targets = [paths.stored(record["path"]) for record in photos.values()
               if fields.on_camera(record.get("raw_metadata"), camera_model)]
    if not targets:
        return jsonify({"success": True, "message": "No photos matched the camera model"})
    try:
        result = photo_actions.shift_date_taken(library, targets, shift_minutes, state.exiftool(library))
        if result.refused:
            return responses.error(400, result.refused)
        if not result.ok:
            raise RuntimeError(result.message())
        for meta in result.details["records"]:
            path = paths.stored(meta["path"])
            # Every map holding the photo: this folder's, and an ancestor's scan that
            # walked into it.
            for held, previous in cache.entries_for(path):
                held[paths.key(path)] = photo_actions.page_record(
                    path, meta, meta.get("mtime", previous.get("mtime", 0.0)),
                    meta.get("size", previous.get("size", 0)))
    except Exception as e:
        logger.error("Error applying time shift to %s: %s", folder, e)
        return responses.error(500, str(e))
    return jsonify({"success": True, "updated_photos": list(photos.values()),
                    "updated_count": result.changed, "requested_count": result.attempted})


@routes.post("/api/folder/rename-photos")
def folder_rename_photos():
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder = body.get("folder_path")
    photo_paths = [paths.stored(p) for p in body.get("photo_paths", [])]
    grouping = (body.get("grouping") or "").strip()
    folder = paths.stored(folder) if folder else folder
    if not folder or not os.path.exists(folder):
        return responses.error(400, "Invalid folder path")
    if not photo_paths:
        return responses.error(400, "No photos selected for renaming")
    cache = folders.of(library)
    try:
        # In the order they were taken, by the Date Taken in the folder's cached scan; a
        # photo the cache does not know sorts by its file time, after every date. The
        # cache is keyed by paths.key: looking it up by the path itself never matched,
        # so every photo sorted by its file time instead.
        known = cache.get(folder) or {}

        def taken(photo_path):
            record = known.get(paths.key(photo_path))
            if record:
                return photo_actions.taken_order(record)
            try:
                return "mtime_%s" % os.path.getmtime(photo_path)
            except OSError:
                return "9999"

        result = photo_actions.smart_rename(
            library, sorted(photo_paths, key=taken), grouping, state.rename_format(library),
            state.exiftool(library))
        if result.refused:
            return responses.error(400, result.refused)
        if not result.ok:
            return responses.error(500, result.message())
        # Their saved suggestions are kept by the photo's id, and went with the rows.
        # The folder is read again from its files, as the page is about to show it.
        cache.pop(folder)
        photos = photo_actions.read_folder(library, folder, state.exiftool(library))
        cache.put(folder, photos)
    except Exception as e:
        logger.error("Error smart renaming photos: %s", e, exc_info=True)
        return responses.error(500, str(e))
    return jsonify({
        "success": True,
        "updated_paths": result.details["updated_paths"],
        "updated_photos": _sorted(photos),
        "index_rows_moved": result.details["index_rows_moved"],
        "index_skipped": [new for _, new in result.details["index_skipped"]],
    })


# ---- One photo ---------------------------------------------------------------------------

@routes.get("/api/photo-faces")
def photo_faces():
    """Faces detected on one photo, with the best identity guess for each
    (tagpup.services.faces.panel)."""
    library = state.require()
    photo_path = _wanted_path()
    if not photo_path:
        return responses.error(400, "Missing 'path' parameter")
    try:
        return jsonify(face_actions.panel(library, photo_path))
    except Exception as e:
        logger.error("Error listing faces for %s: %s", photo_path, e)
        return responses.error(500, str(e))


@routes.get("/api/face-crop")
def face_crop():
    """A face's crop (tagpup.services.photos.face_crop)."""
    library = state.require()
    wanted = request.args.get("id")
    if not wanted:
        return responses.error(400, "Missing 'id' parameter")
    try:
        face_id = int(wanted)
    except (ValueError, TypeError):
        return responses.error(400, "Invalid 'id' parameter")
    try:
        crop = photo_actions.face_crop(library, face_id)
    except NotFound as missing:
        return responses.error(404, str(missing))
    except Exception as e:
        logger.error("Error serving face crop %s: %s", face_id, e)
        return responses.error(500, str(e))
    return responses.image(crop, "image/jpeg")


@routes.get("/api/photo-file")
def photo_file():
    # Turned upright, and kept by the browser for a day (PHOTO_CACHE_SECONDS).
    return responses.photo(request.args.get("path"), request.args.get("size"),
                           upright=True, cache_seconds=PHOTO_CACHE_SECONDS)


@routes.post("/api/photo/open-explorer")
def photo_open_explorer():
    body = request.get_json(silent=True) or {}
    photo_path = body.get("path")
    if not photo_path or not os.path.exists(photo_path):
        return responses.error(400, "Invalid file path")
    try:
        desktop.show_in_explorer(photo_path)
    except Exception as e:
        logger.error("Error opening explorer for %s: %s", photo_path, e)
        return responses.error(500, str(e))
    return jsonify({"success": True})


@routes.post("/api/photo/open")
def photo_open():
    """Open a photo in the application Windows associates with its type."""
    body = request.get_json(silent=True) or {}
    photo_path = paths.stored(body.get("path") or "")
    if not desktop.is_openable(photo_path):
        return responses.error(400, "Not a photo file")
    try:
        desktop.open_photo(photo_path)
    except Exception as e:
        logger.error("Error opening %s: %s", photo_path, e)
        return responses.error(500, str(e))
    return jsonify({"success": True})


@routes.post("/api/photo/rotate")
def photo_rotate():
    library = state.require()
    body = request.get_json(silent=True) or {}
    photo_path = body.get("path")
    direction = body.get("direction")
    if not photo_path or not os.path.exists(photo_path):
        return responses.error(400, "Invalid file path")
    # Reject anything but an explicit direction rather than silently treating an
    # unrecognised value as a right turn.
    if direction not in ("left", "right"):
        return responses.error(400, "Direction must be 'left' or 'right'")
    photo_path = paths.stored(photo_path)
    try:
        result = photo_actions.rotate(library, photo_path, direction, state.exiftool(library))
        if not result.ok:
            logger.error("Error rotating image %s: %s", photo_path, result.message())
            return responses.error(500, result.message())
        for _held, record in folders.of(library).entries_for(photo_path):
            record["mtime"] = result.details["mtime"]
            record["size"] = result.details["size"]
    except Exception as e:
        logger.error("Error rotating image %s: %s", photo_path, e)
        return responses.error(500, str(e))
    # The new mtime, which versions the page's image URLs: thumbnails are cached for a
    # day, so without a new URL the grid kept the old turn.
    return jsonify({"success": True, "mtime": result.details["mtime"]})


@routes.post("/api/photo/delete")
def photo_delete():
    library = state.require()
    body = request.get_json(silent=True) or {}
    photo_path = body.get("path")
    if not photo_path or not os.path.exists(photo_path):
        return responses.error(400, "Invalid file path")
    try:
        result = photo_actions.delete(library, photo_path)
        if not result.ok:
            return responses.error(500, result.message())
        for held, _record in folders.of(library).entries_for(photo_path):
            held.pop(paths.key(photo_path), None)
    except Exception as e:
        logger.error("Error deleting image %s: %s", photo_path, e)
        return responses.error(500, str(e))
    return jsonify({"success": True})


@routes.post("/api/photo/save-metadata")
def photo_save_metadata():
    library = state.require()
    body = request.get_json(silent=True) or {}
    photo_path = body.get("path")
    title = body.get("title")
    tags = body.get("tags", [])
    date_taken = body.get("date_taken")
    if not photo_path or not os.path.exists(photo_path):
        return responses.error(400, "Invalid file path")
    photo_path = paths.stored(photo_path)
    try:
        result = tagging_actions.save_photo(library, photo_path, title, tags, date_taken,
                                            state.exiftool(library), state.rename_format(library))
        if result.refused:
            return responses.error(400, result.refused)
        new_path, renamed, tags = result.details["new_path"], result.details["renamed"], result.details["tags"]
        # Every folder map holding the photo, found under the name it had (a rename
        # stays in the same directory).
        for held, record in folders.of(library).entries_for(photo_path):
            if renamed:
                held.pop(paths.key(photo_path), None)
                record["path"] = new_path
                record["filename"] = os.path.basename(new_path)
                held[paths.key(new_path)] = record
            raw_meta = photo_actions.record_written(
                library, record, new_path, tags, result.details["flat"], result.details["hierarchical"])
            if date_taken:
                fields.record_date_taken(raw_meta, date_taken)
            record["tags"] = vocabulary.extract_tags(raw_meta)
            record["captions"] = [title] if title else []
            record["title"] = title
    except Exception as e:
        logger.error("Error saving metadata for %s: %s", photo_path, e)
        return responses.error(500, str(e))
    reply = {"success": True, "new_path": new_path}
    if result.details["index_warning"]:
        reply["index_warning"] = result.details["index_warning"]
    return jsonify(reply)


@routes.post("/api/photos/bulk-tags")
def photos_bulk_tags():
    library = state.require()
    body = request.get_json(silent=True) or {}
    photo_paths = body.get("paths", [])
    add_tags = body.get("add_tags", [])
    remove_tags = body.get("remove_tags", [])
    if not photo_paths:
        return responses.error(400, "Missing paths list")
    try:
        # What is added is checked, not what is removed (tagpup.services.tagging).
        result = tagging_actions.change_tags(library, photo_paths, add_tags, remove_tags,
                                             state.exiftool(library))
        if result.refused:
            return responses.error(400, result.refused)
        _records_written(library, result)
        if not result.ok:
            raise RuntimeError(result.message())
    except Exception as e:
        logger.error("Error in bulk tags write: %s", e)
        return responses.error(500, str(e))
    return jsonify({"success": True})


def _records_written(library, result):
    """The page's records of the photos a bulk write wrote (`written` in its details).
    The photos written before any failure are written; the page's copy of them has to
    say so either way."""
    cache = folders.of(library)
    for path, (tags, flat, hierarchical) in result.details["written"].items():
        for _held, record in cache.entries_for(path):
            photo_actions.record_written(library, record, path, tags, flat, hierarchical)


# ---- Autocomplete -----------------------------------------------------------------

@routes.get("/api/tags")
def tags():
    try:
        return jsonify(tags_service.autocomplete(state.require()))
    except Exception as e:
        return responses.error(500, str(e))


@routes.get("/api/people")
def people():
    """The people offered while a name is typed (tagpup.services.people.names)."""
    try:
        return jsonify(people_service.names(state.require()))
    except Exception as e:
        return responses.error(500, str(e))
