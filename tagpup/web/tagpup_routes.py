"""TagPup's routes (docs/SPEC_TAGPUP_GUI.md): the folder view, a photo's metadata,
its faces, Suggest and the bulk writes. The tag tree's routes are both apps'
(tagpup.web.taxonomy_routes).

Each route is thin: it reads the request, calls a service, and shapes the reply. What
this server keeps for a library between requests is a `tagpup.core.per_library.PerLibrary`
here: the folder cache. The suggestion runs and the index queue keep theirs in
`tagpup.jobs`.

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

from flask import Blueprint, Response, jsonify, request

from tagpup import config
from tagpup import runtime as runtimes
from tagpup.core import fields, paths, renaming, vocabulary
from tagpup.core.library import picker_name
from tagpup.jobs import suggestions as suggestion_jobs
from tagpup.services import bulk_edit
from tagpup.services import convert as clip_conversion
from tagpup.services import file_changes, file_only
from tagpup.services import libraries as library_actions
from tagpup.services import library_view
from tagpup.services import people as people_service
from tagpup.services import photo_writes
from tagpup.services import photos as photo_actions
from tagpup.services import selection as selection_service
from tagpup.services import tagging as tagging_actions
from tagpup.services import tags as tags_service
from tagpup.services import thumbnails
from tagpup.web import desktop, responses, state

logger = logging.getLogger(__name__)

routes = Blueprint("tagpup", __name__)

#: Folder suggestions offered while a path is typed, at most.
AUTOCOMPLETE_LIMIT = 15

#: How long the browser may keep a photo it was sent: the page puts the photo's mtime in
#: the URL, so a rotated photo is asked for again.
PHOTO_CACHE_SECONDS = 86400

#: How long the browser may keep a thumbnail whose URL carries the file's stamp (`v`): a changed file has a
#: new URL, so a year is safe.
THUMB_CACHE_SECONDS = 31536000

#: What the query says when it means yes.
YES = ("1", "true", "yes", "on")


# ---- What the server keeps for each library --------------------------------------------

class FolderCache:
    """What the page was last shown of each folder it opened: {folder key: {photo key:
    page record}}. Written by a scan, kept true by every write the page makes through
    this server, and read by Suggest for the folder's photos.

    A scan is kept with the library's photos and taxonomy counters as they were before the scan
    (`stamp`, tagpup.services.photos.scan_stamp), and `get` hands it back only while they are
    where they were. That is the one way a scan goes stale (contract table row 11): every write
    of a held photo's row moves the photos counter, whoever makes it -- this server's writes,
    the tag tree's rewrites, an undo, a sync, the indexer, a CLI write, a snapshot restore
    -- a move of a root moves it too (its salt), and an edit of the tree moves the taxonomy one.
    The scan after one is read again from the library's rows, at the cost of a stat of each file.
    `peek` is `get` without that check, for a caller that wants the scan's spellings and not its
    content.

    A write to a photo of a folder the library does not hold is its file's only and moves no
    counter: its records are patched (`entries_for`), which is also what the reply of a time
    shift sends. A scan stores its whole recursive walk under the scanned folder's key, so a
    photo in a subfolder is filed under an ancestor, not under its own directory; `entries_for`
    finds every map holding a photo, since a folder and a subfolder of it may both have been
    scanned. A map's keys change only here (`move`, `forget`) and are read as a copy (`snapshot`), all under the lock:
    a scan of an ancestor never sees a rename half done (#2907).
    """

    def __init__(self, library=None):
        self._library = library
        self._maps = {}
        self._stamps = {}
        self._lock = threading.Lock()

    def stamp(self):
        """The library's photos and taxonomy counters now (photos.scan_stamp): taken before a scan, given to `put` with
        it, so a write that lands during the scan costs one more scan later and is never missed."""
        return photo_actions.scan_stamp(self._library) if self._library is not None else None

    def get(self, folder):
        key = paths.key(folder)
        with self._lock:
            held = self._maps.get(key)
            if held is None:
                return None
            kept = self._stamps.get(key)
        if kept == self.stamp():
            return self.snapshot(folder)
        with self._lock:
            if self._maps.get(key) is held:
                del self._maps[key]
                self._stamps.pop(key, None)
        return None

    def peek(self, folder):
        return self.snapshot(folder)

    def put(self, folder, photos, stamp=None):
        """Keep a scan; `stamp` is `self.stamp()` as it was before the scan (taken now when not given). Returns a copy of
        it taken under the lock, which is what the caller reads (`snapshot`)."""
        stamp = self.stamp() if stamp is None else stamp
        with self._lock:
            self._maps[paths.key(folder)] = photos
            self._stamps[paths.key(folder)] = stamp
            return dict(photos)

    def clear(self):
        with self._lock:
            self._maps.clear()
            self._stamps.clear()

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

    def snapshot(self, folder):
        """A copy of the folder's map taken under the lock, or None when none is held: what `get`, `peek` and `put` hand
        back, since a `move` re-keys the map itself and a reader walking it outside the lock would see it change size
        (#2907, #2910). The records in it are the map's own, so a patch of one (`entries_for`) shows in both; the live
        map is reached only through `entries_for`, `move`, `move_all` and `forget`."""
        with self._lock:
            held = self._maps.get(paths.key(folder))
            return None if held is None else dict(held)

    def move(self, old_path, new_path, record=None):
        """Re-key one photo's record, in every map holding it, under the lock: the record held (or `record`, when given:
        a write that made the file's record anew) under the new path's key. The one place a route changes a map's keys."""
        self.move_all([(old_path, new_path, record)])

    def forget(self, photo_path):
        """Take a photo's record out of every map holding it, under the lock (a photo deleted)."""
        photo_key = paths.key(photo_path)
        with self._lock:
            for folder_key, photos in self._maps.items():
                if paths.is_under(photo_path, folder_key):
                    photos.pop(photo_key, None)

    def move_all(self, moves):
        """`move` for [(old path, new path, record or None)] at once: every record is found, and taken out, before any is
        put back, so a run of numbered names shuffled among themselves, or one taking the name of a file moved aside,
        moves each record once."""
        with self._lock:
            found = []
            for old, new, record in moves:
                old_key = paths.key(old)
                for folder_key, photos in self._maps.items():
                    if paths.is_under(old, folder_key) and old_key in photos:
                        found.append((photos, new, record, photos.pop(old_key)))
            for photos, new, record, held in found:
                photos[paths.key(new)] = held if record is None else record


folders = state.PerLibrary(FolderCache)


@routes.before_request
def _a_page_is_looking():
    """Whatever the page asks counts as use of what Suggest only looked at and kept in memory in the library
    the page is in, so it is not let go under an owner who is reviewing it
    (tagpup.jobs.suggestions.touch_looks)."""
    library = state.current()
    suggestion_jobs.touch_looks(library.key if library is not None else None)

#: The name the folder scans are kept under in the process's idle registry.
FOLDER_SCANS = "folder scans"
#: ... and the suggestions of folders the library does not hold, which live in memory only.
LOOKED_SUGGESTIONS = "looked-at suggestions"


def idle_caches(idle):
    """Register the folder scans in the process's idle registry (tagpup.core.idle, the
    runtime's): each library's scanned folders, let go when unused for the idle period
    and scanned again at their next open; kept while a Suggest run is under way, which
    started from a scan."""
    idle.register(FOLDER_SCANS, folders.release, in_use=lambda: suggestion_jobs.running() > 0)
    folders.on_use = lambda: idle.used(FOLDER_SCANS)
    # What a Suggest in a folder the library does not hold found, kept in memory only.
    # One entry for each library, so one the owner is not working in is let go whatever the other is used for.
    def looks_used(library_key):
        name = "%s (%s)" % (LOOKED_SUGGESTIONS, library_key)
        idle.register(name, lambda: suggestion_jobs.release_looks(library_key),
                      in_use=lambda: suggestion_jobs.running_in(library_key) > 0)
        idle.used(name)
    suggestion_jobs.on_looks_use = looks_used


def _folder_photos(library, folder, force=False):
    """The folder's photos as the page was shown them: the one filler of the cache. Scanned now when they have not
    been, when the library moved since (FolderCache.get), or when `force` (the page's Refresh); an empty folder is
    walked again each time, so photos copied in after it was first opened show. Opening a folder, Suggest (on the
    run's own thread, with the library it was handed) and a time shift. A copy of the kept map (FolderCache.snapshot),
    safe to walk while a write re-keys the map."""
    cache = folders.of(library)
    found = None if force else cache.get(folder)
    if not found:
        stamp = cache.stamp()
        found = cache.put(folder, photo_actions.scan_folder(library, folder, state.exiftool(library)), stamp)
    return found


def _wanted_path():
    """The request's `path`, or None, as Flask decoded it. The old routes decoded it a
    second time, and a name holding "%41" became one holding "A" (docs/findings.md, #32)."""
    return request.args.get("path") or None


def _a_folder(folder):
    """Is `folder`, as a request sent it, a folder now? Through photos.folder_state, which waits a moment on a network
    share: what the folder box's suggestions ask. A route that acts on a folder refuses it by responses.folder_refused."""
    return isinstance(folder, str) and bool(folder) and photo_actions.folder_state(folder) == "there"


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
        return [letter + ":\\" for letter in string.ascii_uppercase
                if photo_actions.file_stamp(letter + ":\\") is not None]
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
        if _a_folder(base_dir):
            for name in photo_actions.subfolders(base_dir) or ():
                if not prefix or name.lower().startswith(prefix):
                    found.append(os.path.join(base_dir, name))
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
    refused = responses.folder_refused(folder)
    if refused:
        return refused
    folder = paths.stored(folder)
    return jsonify(_sorted(_folder_photos(library, folder, force)))


@routes.get("/api/folder/membership")
def folder_membership():
    """What this library holds of a folder (tagpup.services.libraries.membership): what the
    page asks as a folder opens, to ask before adding one the library does not hold. No
    other library is opened."""
    library = state.require()
    folder = _wanted_path()
    if not folder:
        return responses.error(400, "Missing 'path' parameter")
    # A folder on a network share is looked at by membership_checked, which tells a share away from a folder gone.
    refused = None if library_actions.on_a_network_drive(folder) else responses.folder_refused(folder)
    if refused:
        return refused
    settings = runtimes.peek_settings(library)
    # Bounded, and one walk of a folder at a time (findings #568): a page asks it of a library view's folder too.
    return jsonify(library_actions.membership_checked(library, folder, settings.roots, settings.ignored))


@routes.post("/api/folder/add")
def folder_add():
    """Add a folder to this library, as the person asked: "Add to <library>"
    (tagpup.services.libraries.add). It is the library's at once -- Suggest may start --
    and it is queued to be indexed, with its subfolders, behind any other."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder = body.get("folder_path")
    refused = responses.folder_refused(folder)
    if refused:
        return refused
    result = library_actions.add(library, [folder], lambda folders: state.runtime().index_queues.of(library).start(
        folders, runtimes.index_folder(library)))
    suggestion_jobs.dropped_by_add(library, [folder])
    if result.refused:
        return responses.refused(result)
    return jsonify({"success": result.ok, "status": "running", "library": picker_name(os.path.basename(library.path)),
                    "folder": paths.stored(folder), **result.details})


@routes.get("/api/folder/suggest-status")
def folder_suggest_status():
    library = state.require()
    folder = _wanted_path()
    if not folder:
        return responses.error(400, "Missing 'path' parameter")
    # Under the spellings this server's scan gave the page (tagpup.jobs.suggestions).
    status = suggestion_jobs.runs_for(library).status(folder, folders.of(library).peek(folder))
    return jsonify(people_service.annotate_suggestions(library, status))


@routes.post("/api/folder/suggest-start")
def folder_suggest_start():
    """Suggest tags for a folder's photos (tagpup.jobs.suggestions)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder = body.get("folder_path")
    refused = responses.folder_refused(folder)
    if refused:
        return refused
    folder = paths.stored(folder)
    # In a folder the library holds, Suggest keeps what it finds as rows -- faces, vectors,
    # what it offered -- for every photo it looks at. In one it does not hold it only looks:
    # the photos are analysed against what the library holds and what is found stays in
    # memory, nothing added to the library (tagpup.jobs.suggestions). Decided here, now, by the
    # library's own answer. Its ignored folders are passed over, as sync passes them (not held,
    # not offered); an ignored folder itself is not the library's to suggest in.
    ignored = runtimes.peek_settings(library).ignored
    try:
        refusal, looking = library_actions.suggest_how(library, folder, ignored)
    except Exception as e:
        # Whether the library holds the folder is not known: nothing is run, in memory or
        # in the library.
        logger.error("Could not ask %s whether it holds %s: %s", library.name, folder, e)
        return responses.error(503, "Could not ask %s what it holds just now (%s): nothing was started. Try again."
                               % (picker_name(os.path.basename(library.path)), e))
    if refusal:
        return responses.error(409, refusal)

    def photos():
        # A folder's photos and its clips (scan_folder): a clip is offered tags alone (the suggester's face step).
        in_ignored = library_actions.ignored_photos(ignored)
        return {key: meta for key, meta in _folder_photos(library, folder).items() if not in_ignored(meta["path"])}
    runtime = state.runtime()
    work = suggestion_jobs.work_for(library, photos, runtime, runtime.index_queues.of(library), looking=looking,
                                    held_now=(lambda: not library_actions.suggest_how(library, folder, ignored)[1])
                                    if looking else None)
    return jsonify({"success": True, "status": suggestion_jobs.runs_for(library).start(folder, work),
                    "in_memory": looking})


@routes.post("/api/folder/suggest-cancel")
def folder_suggest_cancel():
    """Stop a folder's Suggest (tagpup.jobs.suggestions.SuggestionRuns.cancel): at once while
    it waits for the folder's index or for the graphics card, else once the photos in hand
    are done; what it kept stays. `cancelled`: whether a run was told to stop."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder = body.get("folder_path")
    if not folder or not isinstance(folder, str):
        return responses.error(400, "Missing 'folder_path'")
    return jsonify({"success": True, "cancelled": suggestion_jobs.runs_for(library).cancel(folder)})


@routes.post("/api/folder/auto-apply")
def folder_auto_apply():
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder = body.get("folder_path")
    refused = responses.folder_refused(folder)
    if refused:
        return refused
    suggestions = suggestion_jobs.runs_for(library).suggestions(folder)
    if suggestions is None:
        return responses.error(400, "No suggestions found for this folder")
    photo_paths = body.get("photo_paths")
    if photo_paths:
        wanted = {paths.key(p) for p in photo_paths}
        suggestions = {k: v for k, v in suggestions.items() if paths.key(k) in wanted}
    # Apply exactly what the panel offered (tagpup.core.suggesting.offered_tags), a person filed as a
    # click on their chip files them (tagging.person_filer), the tree read once.
    try:
        # The page's records are told in the order the files were written.
        with file_changes.batch_exclusively(library, "apply suggestions", len(suggestions)):
            result = tagging_actions.apply_suggestions(library, suggestions, state.exiftool(library))
            if result.refused:
                return _refused_after_writing(library, result)
            records_written(library, result)
    except responses.ANSWERED as why:
        # Conflict: too many batches wait ahead (tagpup.services.batches); Refused: more photos than a write may name
        # (file_changes.too_many), or cancelled before its turn. Nothing was written.
        return responses.of(why)
    except Exception as e:
        logger.error("Error auto-applying suggestions: %s", e)
        return responses.error(500, str(e))
    if not result.ok:
        logger.error("Error auto-applying suggestions: %s", result.message())
        return responses.error(500, result.message(), written=_written_tags(result))
    return jsonify({"success": True, "written": _written_tags(result), **_where(result)})


@routes.post("/api/folder/time-shift")
def folder_time_shift():
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder = body.get("folder_path")
    camera_model = body.get("camera_model")
    shift_minutes = body.get("shift_minutes", 0)
    refused = responses.folder_refused(folder)
    if refused:
        return refused
    if shift_minutes == 0:
        return jsonify({"success": True, "message": "No shift applied (0 minutes)"})
    # Walked and written in the stored form, looked up by the key. This used to walk the
    # lower-cased key itself, so every path it found -- and every path it sent back to
    # the page -- was lower case.
    folder = paths.stored(folder)
    cache = folders.of(library)
    try:
        photos = _folder_photos(library, folder)
    except Exception as e:
        logger.error("Error scanning folder on the fly for time shift: %s", e)
        return responses.error(500, "Folder must be scanned first, and scan fallback failed: %s" % e)
    # The photos of the camera asked for (tagpup.core.fields): never a clip, whose Date Taken is not changed
    # (images.CLIP_NOT_EDITED); the page counts the same photos (rename.js onCamera).
    targets = [paths.stored(record["path"]) for record in photos.values()
               if record["media"] == "photo" and fields.on_camera(record.get("raw_metadata"), camera_model)]
    if not targets:
        return jsonify({"success": True, "message": "No photos matched the camera model"})
    try:
        result = photo_actions.shift_date_taken(library, targets, shift_minutes, state.exiftool(library))
        if result.refused:
            return responses.refused(result)
        if not result.ok:
            raise RuntimeError(result.message())
        for meta in result.details["records"]:
            path = paths.stored(meta["path"])
            # Every map holding the photo: this folder's, and an ancestor's scan that
            # walked into it.
            record = photo_actions.page_record(path, meta, meta["mtime"], meta["size"])
            cache.move(path, path, record)
            photos[paths.key(path)] = record
    except Exception as e:
        logger.error("Error applying time shift to %s: %s", folder, e)
        return responses.error(500, str(e))
    return jsonify({"success": True, "updated_photos": _sorted(photos if (held := cache.snapshot(folder)) is None else held),
                    "updated_count": result.changed, "requested_count": result.attempted,
                    **_where(result)})


@routes.post("/api/folder/rename-photos")
def folder_rename_photos():
    library = state.require()
    body = request.get_json(silent=True) or {}
    folder = body.get("folder_path")
    photo_paths = [paths.stored(p) for p in body.get("photo_paths", [])]
    grouping = (body.get("grouping") or "").strip()
    folder = paths.stored(folder) if folder else folder
    refused = responses.folder_refused(folder)
    if refused:
        return refused
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
            stamp = photo_actions.file_stamp(photo_path)
            return "mtime_%s" % stamp[0] if stamp else "9999"

        result = photo_actions.smart_rename(
            library, sorted(photo_paths, key=taken), grouping, state.rename_format(library),
            state.exiftool(library))
        if result.refused:
            return responses.refused(result)
        # What the server keeps follows every photo the rename wrote, and every file it moved aside, whatever stopped
        # it after (#2892): the records of each folder map holding one -- this folder's, and an ancestor's whose walk
        # took it in -- under its new name with its file's stamp, and Suggest's looks (#547). A held photo's saved
        # suggestions are kept by its id, and went with its row.
        renamed = result.details.get("renamed") or {}
        _followed_rename(library, [(old, new, old in renamed)
                                   for old, new in (result.details.get("updated_paths") or {}).items()]
                         + [(old, new, True) for old, new in (result.details.get("moved_aside") or {}).items()])
        if not result.ok and not renamed:
            return responses.error(500, result.message())
        # The folder as the page is about to show it, its clips with its photos: the scan kept, followed, while the
        # library has not moved; scanned again when it has (a held photo's row moved; FolderCache.get).
        try:
            photos = _folder_photos(library, folder)
        except Exception as again:
            if result.ok:
                raise
            # Some were renamed before it stopped (the second part of a mixed rename) and the folder cannot be
            # scanned just now: a plain error (the page's Refresh reads the files).
            logger.error("Could not read %s again after a part-way rename: %s", folder, again)
            return responses.error(500, result.message())
        if not result.ok:
            # The page is told which names changed, so it shows them instead of the old ones.
            return responses.error(500, result.message(), updated_paths=result.details["updated_paths"],
                                   updated_photos=_sorted(photos))
    except Exception as e:
        # The rename mostly raises when it renamed nothing, but it can raise with files already moved (the move-aside of
        # a file not asked for, outside rename_all's try; file_changes.rename failing in _record_renames after
        # rename_all returned): then no follow-up ran, the answer is a 500, and the kept scans serve the old names until
        # the library's counter moves, the idle period lets them go or the page's Refresh reads the files (#2906).
        logger.error("Error smart renaming photos: %s", e, exc_info=True)
        return responses.error(500, str(e))
    return jsonify({
        "success": True,
        "updated_paths": result.details["updated_paths"],
        "updated_photos": _sorted(photos),
        "index_rows_moved": result.details["index_rows_moved"],
        "index_skipped": [new for _, new in result.details["index_skipped"]],
        **_where(result),
    })


# ---- One photo ---------------------------------------------------------------------------

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
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error serving face crop %s: %s", face_id, e)
        return responses.error(500, str(e))
    return responses.image(crop, "image/jpeg")


@routes.get("/api/photo-file")
def photo_file():
    # Turned upright, and kept by the browser for a day (PHOTO_CACHE_SECONDS).
    return responses.photo(request.args.get("path"), request.args.get("size"),
                           upright=True, cache_seconds=PHOTO_CACHE_SECONDS)


@routes.get("/api/library/navigator")
def library_navigator():
    """The counts of one section of the navigator (tagpup.services.library_view.navigator)."""
    library = state.require()
    try:
        return jsonify(library_view.navigator(library, request.args.get("section")))
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error reading the navigator: %s", e, exc_info=True)
        return responses.error(500, str(e))


def _source_asked():
    """(kind, value, recursive, order, media) a library view's request names: the query of a GET, or the JSON body
    of a POST -- a union of 1,000 folders on a share is longer than an address the server reads (#696), so the page sends
    a union in a body. A folder is named by `folder`, which the Roots ingress resolves through the first place of its root
    like every path of a request (in the query or the body); the other kinds by `value` (a union's: its list, or that list
    as JSON text)."""
    if request.method == "POST":
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        kind = body.get("kind")
        value = (body.get("folder") or body.get("value")) if kind == "folder" else body.get("value")
        return kind, value, body.get("recursive") is True, body.get("order"), body.get("media")
    kind = request.args.get("kind")
    value = (request.args.get("folder") or request.args.get("value")) if kind == "folder" else request.args.get("value")
    return (kind, value, (request.args.get("recursive") or "").lower() in YES, request.args.get("order"),
            request.args.get("media"))


@routes.route("/api/library/ids", methods=["GET", "POST"])
def library_ids():
    """The whole ordered id list of a source, for a grid that jumps to the middle of it
    (tagpup.services.library_view.ids)."""
    library = state.require()
    kind, value, recursive, order, media = _source_asked()
    try:
        return jsonify(library_view.ids(library, kind, value, recursive, order=order, media=media))
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error reading the ids of a source: %s", e, exc_info=True)
        return responses.error(500, str(e))


@routes.get("/api/library/cards")
def library_cards():
    """The cards of the photos named by `ids` (at most 200), in that order; an id the library has no photo of is
    left out (tagpup.services.library_view.cards). Each card's file is looked at, once (`stale`: changed on disk, or
    missing), which the page marks on the card."""
    library = state.require()
    try:
        return jsonify({"cards": library_view.cards(library, library_view.read_ids(request.args.get("ids")),
                                                     check_disk=True)})
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error reading cards of the library: %s", e, exc_info=True)
        return responses.error(500, str(e))


@routes.get("/api/library/photo")
def library_photo():
    """One photo of the library, by id, as a folder's scan describes it -- tags, people, title, raw metadata -- from the
    library's row (tagpup.services.library_view.photo): what the details panel opens a card in a library view with."""
    library = state.require()
    wanted = request.args.get("id") or ""
    if not (wanted.isascii() and wanted.isdigit()):
        # Digits only, as `ids` of the cards route: int() would read 1_0 as 10 and +5 as 5.
        return responses.error(400, "id must be a whole number.")
    photo_id = int(wanted)
    try:
        return jsonify({"photo": library_view.photo(library, photo_id, lambda: state.exiftool(library))})
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error reading photo %s of the library: %s", photo_id, e, exc_info=True)
        return responses.error(500, str(e))


@routes.get("/api/library/find")
def library_find():
    """The id of the photo at `path`, to land on it in a library view when the move is made from the folder it is in
    (tagpup.services.library_view.find); `id` null when the library holds no photo there."""
    library = state.require()
    try:
        return jsonify({"id": library_view.find(library, request.args.get("path"))})
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error finding a photo of the library: %s", e, exc_info=True)
        return responses.error(500, str(e))


@routes.post("/api/library/selection/tally")
def library_selection_tally():
    """The tags and people a selection of photos carries, with how many photos carry each (phase 9d-1,
    tagpup.services.selection.tally): `selection` is a list of ids or a source minus the ids excluded."""
    library = state.require()
    body = request.get_json(silent=True)
    try:
        return jsonify(selection_service.tally(library, selection_service.read(
            library, body.get("selection") if isinstance(body, dict) else None)))
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error tallying a selection: %s", e, exc_info=True)
        return responses.error(500, str(e))


@routes.post("/api/library/selection/delete-check")
def library_selection_delete_check():
    """Where a Delete of a selection would send its files (#674, tagpup.services.selection.where_deleted): how many photos,
    and how many of them are in folders with no Recycle Bin and would be deleted for good -- what the question before a Delete
    says. Nothing is deleted."""
    library = state.require()
    body = request.get_json(silent=True)
    try:
        return jsonify(selection_service.where_deleted(library, selection_service.read(
            library, body.get("selection") if isinstance(body, dict) else None)))
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error checking where a delete would go: %s", e, exc_info=True)
        return responses.error(500, str(e))


@routes.post("/api/library/bulk-edit")
def library_bulk_edit():
    """Edit a selection of photos by id, in this request (tagpup.services.bulk_edit): `{"op": "tags"|"people"|"delete",
    "selection": SELECTION, "params": {...}}` answers when it is done, with what each photo came to: `changed`, `unchanged`,
    `skipped_missing`, `error_count` and the first 50 `errors`. At most 5,000 photos (a selection of more
    is refused, nothing read); about a twentieth of a second each, so a large one takes minutes and the page says so.
    The edit is read and checked first (a tag the rules refuse, a name two people have), then the selection is resolved
    on the server, then written as one journaled change; it goes on to its end if the page stops waiting."""
    library = state.require()
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    try:
        edit = bulk_edit.prepare(library, body.get("op"), body.get("params", {}))
        resolved = selection_service.resolve(library, selection_service.read(library, body.get("selection")))
        # A delete deletes exactly the photos its question named, or nothing (#691).
        bulk_edit.refuse_if_changed(edit, resolved.ids, selection_service.token_of,
                                    lambda ids: selection_service.deleted_for_good(library, ids))
        out = bulk_edit.run(library, edit, resolved.ids, state.exiftool(library))
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        # Not the text of the exception: it can name a path. The photos written before it keep the edit (History lists them).
        logger.error("Error in a bulk edit: %s", e, exc_info=True)
        return responses.error(500, "The bulk edit stopped (%s); the server's log says why. Photos already written keep the "
                                    "edit: make it again to do the rest." % type(e).__name__)
    return jsonify({"success": True, "op": edit.op, "total": len(resolved.ids), "requested": resolved.requested,
                    "missing": resolved.missing, "excluded": resolved.excluded, **out.reply()})


def _id_asked():
    """(the photo id the request's `id` names, None), or (None, the 400 reply): what the routes that answer a photo's
    picture or a video's bytes by id read."""
    wanted = request.args.get("id")
    if not wanted:
        return None, responses.error(400, "Missing 'id' parameter")
    try:
        return int(wanted), None
    except (ValueError, TypeError):
        return None, responses.error(400, "Invalid 'id' parameter")


@routes.get("/api/photo-thumb")
def photo_thumb():
    """A photo's thumbnail by id, from the cache (tagpup.services.thumbnails). The URL carries the file's stamp
    (`v`, the modified time the card holds), so a thumbnail whose `v` is the file's is kept by the browser for a
    year; any other is revalidated by its ETag. A clip's is its poster frame. A photo that cannot be shown is a small placeholder (header
    X-TagPup-Thumb says which: damaged, unreadable, last known); an id with no photo is a 404 sentence."""
    library = state.require()
    photo_id, refusal = _id_asked()
    if refusal is not None:
        return refusal
    try:
        thumb = thumbnails.serve(library, photo_id, config.locate_ffmpeg)
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error serving the thumbnail of photo %s: %s", photo_id, e, exc_info=True)
        return responses.error(500, "The thumbnail could not be made: %s" % e)
    reply = Response(b"" if thumb.etag and request.if_none_match.contains(thumb.etag) else thumb.content,
                     status=304 if thumb.etag and request.if_none_match.contains(thumb.etag) else 200,
                     mimetype="image/jpeg")
    reply.headers["X-TagPup-Thumb"] = thumb.kind
    stamped = False
    try:
        stamped = thumb.is_version(float(request.args.get("v")))
    except (TypeError, ValueError):
        pass
    if thumb.etag:
        reply.set_etag(thumb.etag)
    reply.headers["Cache-Control"] = ("private, max-age=%d, immutable" % THUMB_CACHE_SECONDS
                                      if thumb.kind == thumbnails.OK and thumb.etag and stamped else "no-cache")
    return reply


@routes.get("/api/video-file")
def video_file():
    """A video's bytes by its id, a range at a time as the browser asks (tagpup.services.photos.video, video_bytes;
    tagpup.web.responses.ranged): 206 with at most VIDEO_SPAN bytes of a range (no range is the first), 416 for a range
    that cannot be answered. The URL carries the file's modified time (`v`), so the browser may keep the pieces a day,
    as a photo; Last-Modified is the file's. 404 for an id that is no video of the library or whose file is gone, 400 for a missing or non-numeric
    one, 503 with Retry-After when the file cannot be reached just now (a share away), 409 "This video is being deleted."
    while a delete of its file is under way (tagpup.files.recycle_bin.being_deleted). A stream that cannot go on (the
    file changed, the share stopped answering, its delete began) ends short, and the browser asks again. To this PC only (403)."""
    library = state.require()
    photo_id, refusal = _id_asked()
    if refusal is not None:
        return refusal
    try:
        video = photo_actions.video(library, photo_id)
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error reading video %s: %s", photo_id, e, exc_info=True)
        return responses.error(500, "The video could not be read (%s); the server's log says why." % type(e).__name__)
    reply = responses.ranged(request.range, video.size, video.content_type,
                             lambda start, length: photo_actions.video_bytes(video, start, length),
                             photo_actions.VIDEO_SPAN)
    if reply.status_code != 416:
        reply.headers["Cache-Control"] = "max-age=%d" % PHOTO_CACHE_SECONDS
        reply.last_modified = video.mtime
    return reply


@routes.get("/api/clip/convert-plan")
def clip_convert_plan():
    """What a clip needs before a browser plays it (tagpup.services.photos.clip_plan, tagpup.files.convertible):
    {"action": "NOTHING" | "REENCODE", "sentence"}, asked by the photo panel when the browser did not
    play it (web/tagpup/media.js). Errors as /api/video-file's (404, 400, 409, 503 with Retry-After), and 503 with
    ffprobe's sentence when it could not read the file just now, 400 with it when it cannot read it as a video. Reads
    the file's header through ffprobe, bounded; writes nothing. To this PC only (403)."""
    library = state.require()
    photo_id, refusal = _id_asked()
    if refusal is not None:
        return refusal
    try:
        return jsonify(photo_actions.clip_plan(library, photo_id, config.locate_ffmpeg))
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error asking what video %s needs to play: %s", photo_id, e, exc_info=True)
        return responses.error(500, "What the video needs to play could not be read (%s); the server's log says why."
                               % type(e).__name__)


@routes.post("/api/clip/convert")
def clip_convert_start():
    """The panel's Convert (`mode` "convert", a clip that does not play) and Make smaller (MP4) (`mode` "shrink", one that
    does) of one clip, by its id: tagpup.services.convert.start, a batch of one on a thread of the server, answered at once
    with its `ticket` (the Activity strip lists it, Cancel by it); /api/clip/converted?ticket= says how it ended. Body
    {"id", "mode", "stamp": {"mtime", "size"}}. The panel's confirm is convert-plan's `replaces`."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    photo_id, mode = body.get("id"), body.get("mode")
    if type(photo_id) is not int or mode not in clip_conversion.MODES:
        return responses.error(400, 'id must be a whole number, and mode "convert" or "shrink".')
    stamp, why = _stamp_of(body.get("stamp"))
    if why or stamp is None:
        return responses.error(400, why or 'stamp must be {"mtime": number, "size": number}.')
    try:
        result = clip_conversion.start(library, state.exiftool(library), config.locate_ffmpeg, photo_id, mode, stamp,
                                       done=lambda source, how: _clip_converted(library, source, how))
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error starting the conversion of video %s: %s", photo_id, e, exc_info=True)
        return responses.error(500, "The video could not be converted (%s); the server's log says why." % type(e).__name__)
    if result.refused:
        return responses.refused(result)
    return jsonify({"success": True, "ticket": result.details["ticket"], "library": library.name})


@routes.get("/api/clip/converted")
def clip_converted():
    """How the conversion `ticket` (POST /api/clip/convert) ended: {"done": false} while it is under way; else {"done":
    true} and what _clip_converted made of it. 404 for a ticket this server has none of."""
    library = state.require()
    ticket = request.args.get("ticket", "")
    if not (ticket.isascii() and ticket.isdigit()):
        return responses.error(400, "ticket must be a whole number")
    try:
        ended = clip_conversion.outcome(library, int(ticket))
    except responses.ANSWERED as why:
        return responses.of(why)
    return jsonify({"done": False} if ended is None else {"done": True, **ended})


def _clip_converted(library, source, how):
    """What the panel reads of a conversion of the clip at `source` that ended, `how` (convert.start's `done`, on its
    thread): convert_clip's Result, or what it raised. Converted: the folder maps follow it to its MP4 and take its stamp,
    and Suggest's looks are kept under the new name, as a Smart Rename does: {"success": true, "new_path", "renamed",
    "size_before", "size_after", "mtime", "size", "original_kept"?}. Refused: {"success": false, "error",
    "changed_on_disk"?, "size_before"?, "size_after"?}."""
    if isinstance(how, BaseException):
        if isinstance(how, responses.ANSWERED):
            return {"success": False, "error": str(how)}
        logger.error("Error converting %s: %s", source, how, exc_info=how)
        return {"success": False, "error": "The video could not be converted (%s); the server's log says why."
                % type(how).__name__}
    sizes = {key: how.details[key] for key in ("size_before", "size_after") if key in how.details}
    if not how.changed:
        return {"success": False, "error": how.message(), **responses.STATUS[how.refused_kind][1], **sizes}
    new_path = how.details["path"]
    renamed = not paths.same(source, new_path)
    left = photo_actions.file_stamp(new_path)
    _followed_rename(library, [(source, new_path, renamed)], stamps={paths.key(new_path): left})
    reply = {"success": True, "new_path": new_path, "renamed": renamed, **sizes, **_stamp_reply(left)}
    if how.details.get("original_kept"):
        reply["original_kept"] = how.details["original_kept"]
    return reply


@routes.post("/api/photo/open-explorer")
def photo_open_explorer():
    body = request.get_json(silent=True) or {}
    photo_path = body.get("path")
    refused = responses.file_refused(photo_path)
    if refused:
        return refused
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
    refused = responses.file_refused(photo_path)
    if refused:
        return refused
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
    refused = responses.file_refused(photo_path)
    if refused:
        return refused
    photo_path = paths.stored(photo_path)
    try:
        # Anything but an explicit direction is refused by the service, with the
        # "rotate direction" kind's message, rather than taken for a right turn.
        result = photo_actions.rotate(library, photo_path, direction, state.exiftool(library))
        if result.refused:
            return responses.refused(result)
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
    return jsonify({"success": True, "mtime": result.details["mtime"], "size": result.details["size"], **_where(result),
                    **_pictures_refused(result)})


@routes.post("/api/photo/delete")
def photo_delete():
    library = state.require()
    body = request.get_json(silent=True) or {}
    photo_path = body.get("path")
    refused = responses.file_refused(photo_path)
    if refused:
        return refused
    try:
        result = photo_actions.delete(library, photo_path, body.get("for_good") is True)
        if result.refused:
            # A file its drive's Recycle Bin would not keep: named (`not_kept`), for the page to ask, then send `for_good`.
            not_kept = result.details.get("not_kept")
            return (responses.refused(result, not_kept=not_kept, bin_full=result.details.get("bin_full", 0)) if not_kept
                    else responses.refused(result))
        if not result.ok:
            return responses.error(500, result.message())
        folders.of(library).forget(photo_path)
    except Exception as e:
        logger.error("Error deleting image %s: %s", photo_path, e)
        return responses.error(500, str(e))
    # What happened, never what was meant: a file on a network share has no Recycle Bin of its own, and went through this PC (#694).
    through = bool(result.details.get("through_this_pc"))
    name = picker_name(os.path.basename(library.path))
    reason = result.details.get("no_bin_reason")
    copy = result.details.get("copy")
    for_good = bool(result.details.get("for_good"))
    where = ("It was %s, which has no Recycle Bin: a copy went to this PC's Recycle Bin and the original was deleted. Restored, the "
             "copy goes to %s, not back to where it was." % (reason, os.path.dirname(copy or "") or "the Downloads folder")
             if through else "Deleted for good, as you confirmed: %s." % result.details.get("why_for_good")
             if for_good else "Moved to the Recycle Bin.")
    reply = {"success": True, "through_this_pc": through, "no_bin_reason": reason, "restores_to": copy, "for_good": for_good,
             **_where(result)}
    if result.details.get(file_only.FILE_ONLY):
        # A photo of a folder the library does not hold: the file only.
        reply["message"] = "%s Nothing in %s changed: it does not hold this folder." % (where, name)
    elif through or for_good:
        reply["message"] = where
    return jsonify(reply)


def _stamp_of(sent):
    """((mtime, size) a write names, or None; why it cannot be read): the `stamp` of a request, {"mtime", "size"}, is
    the file's stamp as the record the page built the write from had it (docs: findings #533); absent is no check."""
    if sent is None:
        return None, None
    ok = (isinstance(sent, dict) and all(isinstance(sent.get(key), (int, float)) and not isinstance(sent.get(key), bool)
                                         for key in ("mtime", "size")))
    if not ok:
        return None, "stamp must be {\"mtime\": number, \"size\": number}."
    return (float(sent["mtime"]), int(sent["size"])), None


def _base_of(sent):
    """(the `base` a write names -- {"tags": [str], "title": str}, or None for a null one --, why it cannot be read)."""
    if sent is None:
        return None, None
    ok = (isinstance(sent, dict) and isinstance(sent.get("tags", []), list)
          and all(isinstance(tag, str) for tag in sent.get("tags", [])) and isinstance(sent.get("title", ""), str))
    if not ok:
        return None, 'base must be {"tags": [text], "title": text}.'
    return {"tags": sent.get("tags", []), "title": sent.get("title", "")}, None


def _stamp_reply(stamp):
    """{"mtime", "size"} of a file's `stamp`, (mtime, size) as it was just looked at (None: none, said as nothing), for
    the page's record: the stamp its next write names."""
    return {"mtime": stamp[0], "size": stamp[1]} if stamp else {}


def _followed_rename(library, moves, stamps=None):
    """What the server keeps of photos follows a write of them that may have renamed them: `moves`, [(old path, new
    path, renamed)], `renamed` as the write says (a Smart Rename of one photo or of a folder's, a file that rename moved
    aside, a save renaming a photo after its title, a clip's conversion to its MP4): the one follow-up of them all
    (#2852, #2892). Renamed, Suggest's looks are kept under the new name (#547, #2415) and every folder map holding the
    photo, found under the name it had -- the folder's, and an ancestor's whose recursive walk took it in --, holds its
    record under the new path (a rename stays in the same directory). Every record is found before any moves, so a run
    that shuffles numbered names among themselves, or takes the name of a file moved aside, moves each record once.
    Renamed or not, each record takes the file's stamp now: the write wrote the file, and the next save from the record
    names that stamp (one look at each file a map holds). Memory only, nothing of the library's: a crash loses it with
    the maps, which are scanned again. `library` is the one the write was made in, handed on: the conversion's
    follow-up runs on its own thread, after the request that asked for it was answered. `stamps`, {paths.key(new
    path): (mtime, size) or None}, the files the caller looked at already since the write, not looked at again (None:
    there was none to see). The records, in the order of `moves`, for a write that tells them more of what it wrote."""
    moves = list(moves)
    renames = {old: new for old, new, renamed in moves if renamed}
    if renames:
        suggestion_jobs.runs_for(library).renamed(renames)
    cache = folders.of(library)
    found = [(old, new, renamed, cache.entries_for(old)) for old, new, renamed in moves]
    records = []
    for _old, new, renamed, entries in found:
        for _held, record in entries:
            if renamed:
                record["path"] = new
                record["filename"] = os.path.basename(new)
            records.append(record)
        seen = stamps or {}
        written_stamp = (seen[paths.key(new)] if paths.key(new) in seen else photo_actions.file_stamp(new)) if entries \
            else None
        for _held, record in (entries if written_stamp else ()):
            record["mtime"], record["size"] = written_stamp
    # The keys move last, under the cache's lock, all of them at once (#2907).
    cache.move_all([(old, new, None) for old, new, renamed, entries in found if renamed and entries])
    return records


@routes.get("/api/photo/smart-rename-parts")
def photo_smart_rename_parts():
    """What the photo's name holds of a Smart Rename name, for the box beside it: its grouping and number."""
    library = state.require()
    photo_path = request.args.get("path")
    if not photo_path:
        return responses.error(400, "Missing path")
    parts = renaming.parts_of(state.rename_format(library), os.path.splitext(os.path.basename(photo_path))[0])
    return jsonify({"grouping": parts[0] if parts else "", "number": parts[1] if parts else None})


@routes.post("/api/photo/smart-rename")
def photo_smart_rename():
    library = state.require()
    body = request.get_json(silent=True) or {}
    photo_path = body.get("path")
    if not photo_path:
        return responses.error(400, "Missing path")
    photo_path = paths.stored(photo_path)
    grouping = (body.get("grouping") or "").strip()
    try:
        result = photo_actions.smart_rename_one(library, photo_path, grouping, state.rename_format(library),
                                                state.exiftool(library))
        if result.refused:
            return responses.refused(result)
        if not result.ok:
            return responses.error(500, result.message())
        new_path = result.details["new_path"]
        renamed = bool(result.details["renamed"])
        # Keeping the name it had wrote the file too (photos.preserve_names): the records take its stamp all the same.
        left = photo_actions.file_stamp(new_path)
        _followed_rename(library, [(photo_path, new_path, renamed)], stamps={paths.key(new_path): left})
    except Exception as e:
        logger.error("Error smart renaming %s: %s", photo_path, e, exc_info=True)
        return responses.error(500, str(e))
    return jsonify({"success": True, "new_path": new_path, "renamed": renamed,
                    "updated_paths": result.details["updated_paths"], **_where(result), **_stamp_reply(left)})


def _set_fields(sent):
    """({"title", "tags", "date_taken", "stamp", "base"} of one photo's save, as tagging.save_photo takes them, None), or
    (None, why it cannot be read): of `save-metadata`'s body, and of a `set`'s entry. A page names what it read (`base`,
    null for a photo it could not read); a call with neither `base` nor `stamp` is no check."""
    stamp, why = _stamp_of(sent.get("stamp"))
    if why:
        return None, why
    base = tagging_actions.NO_BASE
    if "base" in sent or stamp is not None:
        base, why = _base_of(sent.get("base"))
        if why:
            return None, why
    return {"title": sent.get("title"), "tags": sent.get("tags", []), "date_taken": sent.get("date_taken"),
            "stamp": stamp, "base": base}, None


@routes.post("/api/photo/save-metadata")
def photo_save_metadata():
    """One photo's title, tags and Date Taken as a panel saves it: a caller of the one entry point (_write, op `set`) that
    answers as it always did, until the pages send to /api/photos/write (plan one-write-path, cut 2c deletes it)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    photo_path = body.get("path")
    refused = responses.file_refused(photo_path)
    if refused:
        return refused
    photo_path = paths.stored(photo_path)
    own, why = _set_fields(body)
    if why:
        return responses.error(400, why)
    try:
        result, stamps = _write(library, photo_writes.SET, {photo_path: (None, own)})
        if result.refused:
            return responses.refused(result)
        if not result.ok:
            logger.error("Error saving metadata for %s: %s", photo_path, result.message())
            return responses.error(500, result.message())
    except Exception as e:
        logger.error("Error saving metadata for %s: %s", photo_path, e)
        return responses.error(500, str(e))
    reply = {"success": True, "new_path": result.details["new_path"], **_where(result), **_stamp_reply(stamps[photo_path]),
             "base": result.details.get("base"), **_faces_unnamed(result), **_pictures_refused(result)}
    if result.details["taken"]:
        reply["taken"] = result.details["taken"]   # the date as the library spells it: the page shows this, spells nothing
    if result.details["index_warning"]:
        reply["index_warning"] = result.details["index_warning"]
    if result.details.get("rename_failed"):
        reply["rename_failed"] = result.details["rename_failed"]   # the caption was saved; the photo was not renamed after it
    return jsonify(reply)


@routes.post("/api/photos/bulk-tags")
def photos_bulk_tags():
    """Each photo's own tags added and taken off, as the page's queue sends a chunk (web/tagpup/pending.js): a caller of
    the one entry point (_write, op `tags`) that answers as it always did, stopping at the first photo it cannot write,
    until the page sends to /api/photos/write (plan one-write-path, cut 2c deletes it)."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    # A body without them is refused by the shape.
    changes, why = _tag_changes(body.get("changes"))
    if why:
        return responses.error(400, why)
    try:
        # What is added is checked, not what is removed (tagpup.services.tagging).
        # `group`: the token of the one action of the page's this request is a chunk of (History's one line).
        result, stamps = _write(library, photo_writes.TAGS, changes, group=body.get("group"), stop_at_first_error=True)
        if result.refused:
            return _refused_reply(result)
    except responses.ANSWERED as why:
        # Conflict: too many batches wait ahead (tagpup.services.batches); Refused: more photos than a write may name
        # (file_changes.too_many), or cancelled before its turn. Nothing was written.
        return responses.of(why)
    except Exception as e:
        logger.error("Error in bulk tags write: %s", e)
        return responses.error(500, str(e))
    if not result.ok:
        # The photos before the one that failed are written: the page is told which,
        # so that its records say what the files hold.
        logger.error("Error in bulk tags write: %s", result.message())
        return responses.error(500, result.message(), written=_written_tags(result))
    return jsonify({"success": True, "written": _written_tags(result), **_skipped_photos(result),
                    "stamps": {path: _stamp_reply(stamp) for path, stamp in stamps.items()},
                    **_where(result),
                    **_faces_unnamed(result)})


@routes.post("/api/photos/write")
def photos_write():
    """The one entry point of a page's write of photo files (plan one-write-path; tagpup.services.photo_writes): `op`
    (`tags` or `set`), `group`, `changes` {path: {"id": photo id or null, the op's fields}}. Answered in one envelope
    whatever the op (_envelope): 200 when the write ran, a photo that failed among its `errors` and the others written;
    a refusal as its kind is answered (responses.refused: 409 `changed_on_disk` for a stale record or an id no longer at
    its path), the envelope beside the error."""
    library = state.require()
    body = request.get_json(silent=True) or {}
    op = body.get("op")
    if op not in photo_writes.OPERATIONS:
        return responses.error(400, "op must be one of: %s." % ", ".join(photo_writes.OPERATIONS))
    changes, why = _tag_changes(body.get("changes"), with_id=True) if op == photo_writes.TAGS \
        else _set_change(body.get("changes"))
    if why:
        return responses.error(400, why)
    if op == photo_writes.SET:
        refused = responses.file_refused(next(iter(changes)))
        if refused:
            return refused
    try:
        result, stamps = _write(library, op, changes, group=body.get("group"))
    except responses.ANSWERED as why:
        return responses.of(why)
    except Exception as e:
        logger.error("Error in a write of photo files: %s", e, exc_info=True)
        return responses.error(500, str(e))
    reply = _envelope(op, changes, result, stamps)
    if result.refused:
        return responses.refused(result, **reply)
    return jsonify(reply)


def _write(library, op, changes, group=None, stop_at_first_error=False):
    """The one write of photo files a page asks (POST /api/photos/write, and the routes that call it until the page sends
    there): the service's write (tagpup.services.photo_writes) and, once, here, what the server keeps of the photos told
    what it wrote (_told). A batch (over batches.CHUNK photos) takes its turn before the lock, and more than a write may
    name is refused before it (file_changes.batch_exclusively); the records are told under the lock, in the order the
    files were written. (the Result, {path: (mtime, size) or None} of each photo written). Raises responses.ANSWERED."""
    with file_changes.batch_exclusively(library, photo_writes.OPERATIONS[op], len(changes)):
        result = photo_writes.write(library, op, changes, state.exiftool(library),
                                    state.rename_format(library) if op == photo_writes.SET else None,
                                    group=group, stop_at_first_error=stop_at_first_error)
        return result, _told(library, op, changes, result)


def _told(library, op, changes, result):
    """The server's records of the photos `result` wrote take what their files hold now: a tag write's (records_written,
    the photos written before a failure or a stop included), a save's followed to the name a caption gave it
    (_followed_rename) with its title. {path as asked: its stamp after the write} of each photo written."""
    if op == photo_writes.TAGS:
        return records_written(library, result) if result.details.get("written") else {}
    if not result.ok:
        return {}
    ((asked, (_id, own)),) = changes.items()
    photo_path = paths.stored(asked)
    new_path, renamed, tags = result.details["new_path"], result.details["renamed"], result.details["tags"]
    # The stamp the write recorded, taken after it; a rename, or a file not written, is looked at once.
    left = result.details.get("stamp") or photo_actions.file_stamp(new_path)
    for record in _followed_rename(library, [(photo_path, new_path, renamed)], stamps={paths.key(new_path): left}):
        raw_meta = photo_actions.record_written(
            library, record, new_path, tags, result.details["flat"], result.details["hierarchical"],
            result.details["dates"])
        record["tags"] = vocabulary.extract_tags(raw_meta)
        record["captions"] = [own["title"]] if own["title"] else []
        record["title"] = own["title"]
    return {asked: left}


def _envelope(op, changes, result, stamps):
    """The one reply of POST /api/photos/write, whatever its op: `success` (nothing failed or was refused), `changed`,
    `attempted`, `skipped_missing` and `skipped` [{"path", "why"}], `error_count` and `errors` [{"path", "id", "why"}],
    `stopped` ({"after", "of"} when a stop ended it part-way, else null), `change` (the journal's, null for a file-only
    write), `file_only` and `with_rows`, `written` {path: {"tags", "mtime", "size"}, and a set's "base", "new_path",
    "taken", "rename_failed"}, and `pictures_refused` and the faces it changed when there are any. What was written, not
    attempted."""
    ids = {paths.key(path): photo_id for path, (photo_id, _own) in changes.items()}
    details = result.details
    if op == photo_writes.TAGS:
        written = {path: {"tags": list(tags), **_stamp_reply(stamps.get(path))}
                   for path, (tags, _flat, _hierarchical) in details.get("written", {}).items()}
    else:
        written = {asked: {"tags": details["tags"], "base": details["base"], "new_path": details["new_path"],
                           "taken": details["taken"], "rename_failed": details.get("rename_failed"),
                           **_stamp_reply(stamp)} for asked, stamp in stamps.items()}
    return {"success": result.ok, "changed": result.changed, "attempted": result.attempted,
            "skipped_missing": details.get(tagging_actions.SKIPPED_MISSING, 0),
            "skipped": [{"path": what, "why": why} for what, why in result.skipped],
            "error_count": len(result.errors),
            "errors": [{"path": what, "id": ids.get(paths.key(what)), "why": why} for what, why in result.errors],
            "stopped": details.get("stopped"), "change": details.get("change"), **_where(result), "written": written,
            **_pictures_refused(result), **_faces_unnamed(result)}


#: What one photo of a bulk tag write's `changes` may say: the tags to add and the tags to take off.
CHANGE_KEYS = ("add", "remove")

#: What one photo of a write's `set` may say, beside its `id` (tagpup.services.photo_writes).
SET_KEYS = ("title", "tags", "date_taken", "stamp", "base")


def _photo_id(sent):
    """(the photo id a write names a photo by, None for none; why it cannot be read)."""
    if sent is None or (isinstance(sent, int) and not isinstance(sent, bool)):
        return sent, None
    return None, "id must be a photo id or null."


def _tag_changes(changes, with_id=False):
    """({path: (id or None, {"add": [tags to add], "remove": [tags to take off]})}, None) of a tag write's `changes`,
    {path: {"add": [tag], "remove": [tag]}} (a list left out is none; `with_id`, the entry may name the photo's "id"), or
    (None, the sentence that refuses it). Nothing is checked of the tags here: what is added is the service's to check
    (tagpup.services.tagging.change_tags_of)."""
    shape = ('Send changes as {path: {%s"add": [tags], "remove": [tags]}}, one entry for each photo.'
             % ('"id": photo id or null, ' if with_id else ""))
    if not isinstance(changes, dict):
        return None, shape
    if not changes:
        return None, "The bulk tag write names no photo. " + shape
    allowed = set(CHANGE_KEYS) | ({"id"} if with_id else set())
    found = {}
    named = set()
    for path, change in changes.items():
        if not path or not isinstance(change, dict) or set(change) - allowed:
            return None, shape
        if paths.key(path) in named:
            return None, "A photo is named once in a request: %s is named twice. " % os.path.basename(path) + shape
        named.add(paths.key(path))
        lists = tuple(change.get(key, []) for key in CHANGE_KEYS)
        if not all(isinstance(tags, list) and all(isinstance(tag, str) for tag in tags) for tags in lists):
            return None, shape
        photo_id, why = _photo_id(change.get("id"))
        if why:
            return None, why + " " + shape
        found[path] = (photo_id, dict(zip(CHANGE_KEYS, lists)))
    return found, None


def _set_change(changes):
    """({path: (id or None, its fields: _set_fields)}, None) of a `set`'s `changes`, ONE photo's, or (None, the sentence
    that refuses it). A set names the `base` its record read, always: save-metadata's call with neither stamp nor base
    (no check) has no page that sends it, and is not carried here."""
    shape = ('Send changes as {path: {"id": photo id or null, "title": text, "tags": [tags], "date_taken": text, '
             '"stamp": {"mtime": number, "size": number}, "base": {"tags": [tags], "title": text}}}, one photo.')
    if not isinstance(changes, dict) or len(changes) != 1:
        return None, shape
    ((path, change),) = changes.items()
    if not path or not isinstance(change, dict) or set(change) - {"id", *SET_KEYS} or "base" not in change:
        return None, shape
    photo_id, why = _photo_id(change.get("id"))
    if why:
        return None, why + " " + shape
    own, why = _set_fields(change)
    if why:
        return None, why
    return {path: (photo_id, own)}, None


def _faces_unnamed(result):
    """What a write of tags says of the faces it changed, [{"id", "name"}] each: `named_faces`, those the one-face rule named
    (#788); `unnamed_faces`, those a person taken off unnamed (#908), for the page's Undo to name again; `renamed_faces`, those a
    person put back named again; `faces_problem`, a sentence, when they could not be followed. The page reads the open photo's
    faces again when one is listed (web/tagpup/photo.js facesFollowReply). Only what happened, nothing when no face was touched."""
    said = {}
    if result.details.get("named_faces"):
        said["named_faces"] = result.details["named_faces"]
    if result.details.get("unnamed_faces"):
        said["unnamed_faces"] = result.details["unnamed_faces"]
    if result.details.get("renamed_faces"):
        said["renamed_faces"] = result.details["renamed_faces"]
    if result.details.get("faces_problem"):
        said["faces_problem"] = result.details["faces_problem"]
    return said


def _where(result):
    """What a write says of where it wrote (tagpup.services.file_only): {"file_only": files
    written with no row, because the library does not hold their folder, "with_rows": files
    written with their rows}. What was written, not attempted."""
    return {file_only.FILE_ONLY: result.details.get(file_only.FILE_ONLY, 0),
            file_only.WITH_ROWS: result.details.get(file_only.WITH_ROWS, 0)}


def _pictures_refused(result):
    """{"pictures_refused": n} when n writes of this request changed the picture's bytes, were discarded and made again
    (tagpup.services.file_changes; DECISIONS 2026-11-03), else {}: the page says so in a sentence."""
    refused = result.details.get("pictures_refused")
    return {"pictures_refused": refused} if refused else {}


def _skipped_photos(result):
    """The photos a bulk tag write left out because their files are gone (tagpup.services.tagging.leave_out_missing):
    {"skipped_missing": m, "skipped": [{"path", "why"}]}."""
    return {"skipped_missing": result.details.get(tagging_actions.SKIPPED_MISSING, 0),
            "skipped": [{"path": what, "why": why} for what, why in result.skipped if why.startswith("missing, ")]}


def _written_tags(result):
    """{path: the tags it holds now} of each photo a bulk write wrote, or found holding
    them already (`written` in its details), for the page."""
    return {path: list(tags) for path, (tags, _flat, _hierarchical) in result.details["written"].items()}


def _refused_after_writing(library, result):
    """The reply to Apply All refused: the cached records of what it wrote before it was say so (records_written), as
    for a failure, and the reply says which (_refused_reply)."""
    if result.details.get("written"):
        records_written(library, result)
    return _refused_reply(result)


def _refused_reply(result):
    """The reply to a bulk write that was refused. One that stopped half-way (its roots changed) has written
    some files: the page is told which -- the one owner of that answer for the bulk tag write and Apply All
    (findings #878); the records were told by the write's caller."""
    if result.details.get("written"):
        return responses.refused(result, written=_written_tags(result), change=result.details.get("change"))
    return responses.refused(result)


def _stamps_after(result):
    """{path: (mtime, size), or None} of each photo a bulk write wrote or found holding its tags (`written` in its
    details): the stamp the write recorded its row with, taken after the write (file_changes.write_fields' `stamps`; a
    None there was looked at and gave none, and is not looked at again), else the file's now, looked at once (a file of
    a folder the library does not hold, one that held the tags already, a write that says no stamps)."""
    recorded = result.details.get("stamps") or {}
    return {path: recorded[paths.key(path)] if paths.key(path) in recorded else photo_actions.file_stamp(path)
            for path in result.details["written"]}


def records_written(library, result):
    """The page's records of the photos a bulk write wrote (`written` in its details).
    The photos written before any failure are written; the page's copy of them has to
    say so either way. Their stamps after the write (_stamps_after), for the reply."""
    cache = folders.of(library)
    stamps = _stamps_after(result)
    for path, (tags, flat, hierarchical) in result.details["written"].items():
        stamp = stamps[path]
        for _held, record in cache.entries_for(path):
            photo_actions.record_written(library, record, path, tags, flat, hierarchical)
            if stamp:
                # The cached scan carries the file's stamp now, or the next save from it is refused.
                record["mtime"], record["size"] = stamp
    return stamps


# ---- Autocomplete -----------------------------------------------------------------

@routes.get("/api/tags")
def tags():
    try:
        return jsonify(tags_service.autocomplete(state.require()))
    except Exception as e:
        return responses.error(500, str(e))


@routes.get("/api/people")
def people():
    """The people with a person tag, each `{id, name, tag}` (tagpup.services.people.records); ?include_hidden=1 adds those
    hidden from autocomplete."""
    try:
        library = state.require()
        return jsonify(people_service.records(library, include_hidden=request.args.get("include_hidden") == "1"))
    except Exception as e:
        return responses.error(500, str(e))
