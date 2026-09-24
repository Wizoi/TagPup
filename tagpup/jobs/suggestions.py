"""Suggesting tags for a folder's photos: each library's runs, where each has got, and
what it suggested, kept in a file beside the library.

TagPup held all of this on its request handler: the runs in a class-level registry, the
file's name, load and save, and the run itself as a method. The run is here now; what
it runs -- the folder's photos, and the model that suggests for each -- is handed to it
(`work`), since the suggester and the CLIP model have not moved into the package yet.

A run goes "preparing" -> "running" -> "completed", or "error". Nothing but a start
changes a folder that is preparing or running, and a start leaves one alone.
"""
import concurrent.futures
import copy
import json
import logging
import os
import tempfile
import threading
import time

import numpy as np

from tagpup.core import paths

logger = logging.getLogger(__name__)

#: Saving after every photo rewrote every folder every photo; during a run the file is
#: brought up to date at most this often.
SAVE_INTERVAL = 2.0

#: Photos suggested for at once.
WORKERS = min(4, os.cpu_count() or 1)

_runs = {}
_runs_lock = threading.Lock()


def cache_file(db_path):
    """The one suggestions file of a library, and the only place it is named.

    One file per library, next to it. The main library keeps the unsuffixed name it has
    always had, so the file already on disk goes on loading.
    """
    name = os.path.splitext(os.path.basename(db_path))[0]
    if name == "photo_index":
        return os.path.join(os.path.dirname(db_path), "gui_suggestions_cache.json")
    return os.path.join(os.path.dirname(db_path), "gui_suggestions_cache_%s.json" % name)


def runs_for(library):
    """This process's runs for a library, made the first time they are asked for."""
    with _runs_lock:
        runs = _runs.get(library.key)
        if runs is None:
            runs = _runs[library.key] = SuggestionRuns(library.path)
        return runs


def forget(library):
    """Drop a library's runs from memory. A run still going finishes into nothing."""
    with _runs_lock:
        _runs.pop(library.key, None)


def plain(value):
    """`value` with numpy's numbers and arrays made plain, for JSON."""
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.integer):
        return int(value)
    return value


class SuggestionRuns:
    """One library's folders' suggestion runs. `statuses` maps a folder's key to its run:
    {"status", "completed", "total", "suggestions", ...}, "suggestions" mapping each
    photo, as stored, to what was suggested for it. Changed only under `lock`."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.file = cache_file(db_path)
        self.lock = threading.Lock()
        self.statuses = {}
        self._loaded = False
        self._load_lock = threading.Lock()
        #: Serialises writes of the file. Its own lock, not `lock`, so a slow disk never
        #: holds up the pool threads recording suggestions.
        self._file_lock = threading.Lock()
        self._last_saved = None

    # ---- What a page asks -----------------------------------------------------------

    def status(self, folder):
        """A copy of a folder's run, or {"status": "idle"}. Taken under the lock the
        workers write under: serialising the live dict while four workers added to it
        failed the poll with "dictionary changed size during iteration"."""
        self.ensure_loaded()
        with self.lock:
            return plain(self.statuses.get(paths.key(folder), {"status": "idle"}))

    def suggestions(self, folder):
        """A copy of what a folder's runs suggested, each photo to its entry, or None."""
        self.ensure_loaded()
        with self.lock:
            status = self.statuses.get(paths.key(folder))
            if not status or "suggestions" not in status:
                return None
            return copy.deepcopy(status["suggestions"])

    # ---- Starting and running -------------------------------------------------------

    def start(self, folder, work):
        """Start a run over a folder on a thread of its own, unless one is going. Returns
        the status it is in: "preparing" or "running".

        `work` gives the run what it runs: `photos()`, the folder's photos as
        {key: metadata with "path"}, and `begin()`, which readies the model and returns
        something with `suggest(path, metadata)`, `offered(suggestion)` -> (tags, people,
        title) as the panel shows them, and `consensus(suggestions)`.
        """
        key = paths.key(folder)
        self.ensure_loaded()
        with self.lock:
            status = self.statuses.get(key)
            if status and status.get("status") in ("preparing", "running"):
                return status["status"]
            existing = (status or {}).get("suggestions", {})
            self.statuses[key] = {
                "status": "preparing",
                # A photo whose suggestion failed is tried again, so it is not done yet.
                "completed": sum(1 for s in existing.values() if isinstance(s, dict) and "error" not in s),
                "total": 0,
                "suggestions": existing,
            }
        threading.Thread(target=self.run, args=(folder, work), name="FolderSuggestionsThread",
                         daemon=True).start()
        return "running"

    def run(self, folder, work):
        """Suggest for each photo of a folder not yet suggested for, then take the folder's
        consensus, then say "completed". The body of a started run; callable directly.

        A photo whose suggestion raised is marked failed, not stored empty, so the next run
        tries it again. Consensus is taken over every photo of the folder with a suggestion
        of its own, not only this run's, and before "completed": the page stops polling on
        "completed" and keeps what it fetched then.
        """
        folder = paths.stored(folder)
        key = paths.key(folder)
        try:
            photos = work.photos()
            if not photos:
                logger.warning("No photos found in %s.", folder)
                with self.lock:
                    entry = self.statuses.get(key) or {"completed": 0, "total": 0, "suggestions": {}}
                    entry.update(status="error", message="No images found in this folder.")
                    self.statuses[key] = entry
                return
            with self.lock:
                entry = self.statuses.setdefault(
                    key, {"status": "preparing", "completed": 0, "total": 0, "suggestions": {}})
                existing = entry.setdefault("suggestions", {})

                def already_suggested(photo):
                    # A photo whose suggestion failed has an entry too, marked "error";
                    # counting it as done meant it was never tried again.
                    found = existing.get(paths.stored(photos[photo]["path"]))
                    return isinstance(found, dict) and "error" not in found

                todo = [photo for photo in photos if not already_suggested(photo)]
                entry.update(status="preparing", total=len(photos), completed=len(photos) - len(todo))
            self.save()

            model = work.begin()
            with self.lock:
                self.statuses[key]["status"] = "running"
            self.save()

            fresh = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
                for done in concurrent.futures.as_completed(
                        [pool.submit(self._suggest, key, photos[photo], model) for photo in todo]):
                    if done.result() is not None:
                        fresh.append(done.result())

            if fresh and key in self.statuses:
                self._take_consensus(key, photos, model)
            with self.lock:
                self.statuses[key]["status"] = "completed"
            self.save()
        except Exception as e:
            logger.exception("Error running suggestions for %s: %s", folder, e)
            # Always said, even with the entry gone, so the page stops polling instead of
            # spinning on "preparing" for ever.
            with self.lock:
                entry = self.statuses.get(key) or {"completed": 0, "total": 0, "suggestions": {}}
                entry.update(status="error", message=str(e))
                self.statuses[key] = entry
            self.save()

    def _suggest(self, key, meta, model):
        if key not in self.statuses:
            return None
        photo = paths.stored(meta["path"])
        try:
            suggestion = model.suggest(photo, meta)
            tags, people, title = model.offered(suggestion)
            entry = {
                "tags": tags, "people": people, "title": title,
                # Kept as the suggester produced it, so consensus can be taken again over
                # the whole folder when more photos arrive. Entries saved before this flag
                # hold scores consensus already adjusted, and are left out of it.
                "raw_suggestions": suggestion,
                "raw_before_consensus": True,
            }
        except Exception as e:
            logger.error("Error suggesting for %s: %s", photo, e)
            suggestion = None
            # Marked as a failure, not stored as an empty suggestion: the next run retries
            # it instead of skipping it for good.
            entry = {"tags": [], "people": [], "title": None,
                     "raw_suggestions": {"suggested_tags": []}, "error": str(e) or type(e).__name__}
        with self.lock:
            self.statuses[key]["suggestions"][photo] = entry
            self.statuses[key]["completed"] += 1
        self.save(min_interval=SAVE_INTERVAL)
        return suggestion

    def _take_consensus(self, key, photos, model):
        """Folder consensus over copies of the suggester's own output, so the stored
        suggestions are never adjusted twice, nor changed in place while a status request
        reads them."""
        try:
            in_folder = {paths.stored(meta["path"]) for meta in photos.values()}
            with self.lock:
                saved = self.statuses[key]["suggestions"]
                raw = [copy.deepcopy(entry["raw_suggestions"])
                       for photo, entry in saved.items()
                       if photo in in_folder and isinstance(entry, dict) and "error" not in entry
                       and entry.get("raw_before_consensus")
                       and isinstance(entry.get("raw_suggestions"), dict)
                       and entry["raw_suggestions"].get("path")]
            if len(raw) < 2:
                return
            adjusted = {paths.stored(s["path"]): model.offered(s) for s in model.consensus(raw)}
            with self.lock:
                for photo, (tags, people, title) in adjusted.items():
                    entry = saved.get(photo)
                    if entry is not None:
                        saved[photo] = {**entry, "tags": tags, "people": people, "title": title}
        except Exception as e:
            logger.error("Error taking folder consensus: %s", e)

    # ---- Photos renamed -------------------------------------------------------------

    def move_photos(self, renames):
        """File each renamed photo's suggestions under its new name, and save. `renames`
        maps old path to new, in any spelling. Returns how many moved.

        The page looks suggestions up by path, so a renamed photo showed none, the next
        Suggest ran it again from scratch, and the old entry stayed for ever.
        """
        self.ensure_loaded()
        by_key = {paths.key(old): paths.stored(new) for old, new in renames.items()}
        moved = 0
        with self.lock:
            for status in self.statuses.values():
                saved = status.get("suggestions") if isinstance(status, dict) else None
                if not saved:
                    continue
                # Taken out first, then put back, so names shuffled among themselves never
                # overwrite one another.
                leaving = {photo: saved.pop(photo) for photo in list(saved) if paths.key(photo) in by_key}
                for old, entry in leaving.items():
                    new = by_key[paths.key(old)]
                    raw = entry.get("raw_suggestions") if isinstance(entry, dict) else None
                    if isinstance(raw, dict) and "path" in raw:
                        raw["path"] = new
                    saved[new] = entry
                    moved += 1
        if moved:
            self.save()
        return moved

    # ---- The file -------------------------------------------------------------------

    def ensure_loaded(self):
        """Read the library's saved runs the first time anything uses them. Every reader
        and every save calls this, so no save can write only this session's folders over
        the file."""
        if self._loaded:
            return
        with self._load_lock:
            if not self._loaded:
                self._load()

    def _load(self):
        # Marked first: a file that fails to load is not tried again on every save.
        self._loaded = True
        if not os.path.exists(self.file):
            return
        try:
            with open(self.file, encoding="utf-8") as f:
                data = json.load(f)
            for status in data.values():
                if isinstance(status, dict) and status.get("status") in ("running", "preparing"):
                    status["status"] = "idle"
            data = rekey(data)
            # Only folders nothing has touched yet. This can run while a folder chosen
            # straight away already has a run going; overwriting it with the saved copy,
            # rewritten to "idle", told the page the run had stopped.
            with self.lock:
                for folder, status in data.items():
                    self.statuses.setdefault(folder, status)
            logger.info("Loaded suggestions cache from %s with %d folders.", self.file, len(data))
        except Exception as e:
            logger.error("Error loading suggestions cache: %s", e)

    def save(self, min_interval=0.0):
        """Write the library's runs to its file. True if the file was written.

        The file is replaced, never rewritten in place: rewritten in place by four pool
        threads at once, after every photo, two saves overlapping or the process stopping
        mid-write left a file that did not parse, and restored nothing for any folder.
        The snapshot is taken inside the file lock, so a save that started later is never
        overwritten by one that started earlier. `min_interval` skips the write if the
        file was written less than that many seconds ago: for saves during a run, whose
        last save passes nothing, so what it finished with is always what is on disk.
        """
        self.ensure_loaded()

        def too_soon():
            return bool(min_interval) and self._last_saved is not None \
                and time.monotonic() - self._last_saved < min_interval

        if too_soon():
            return False
        with self._file_lock:
            if too_soon():
                return False
            tmp_path = None
            try:
                with self.lock:
                    data = plain(self.statuses)
                directory = os.path.dirname(self.file) or "."
                os.makedirs(directory, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(prefix=os.path.basename(self.file) + ".", suffix=".tmp",
                                                dir=directory)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                for attempt in range(40):
                    try:
                        os.replace(tmp_path, self.file)
                        break
                    except PermissionError:
                        # Windows refuses while something has the file open to read.
                        if attempt == 39:
                            raise
                        time.sleep(0.05)
                tmp_path = None
                self._last_saved = time.monotonic()
                return True
            except Exception as e:
                logger.error("Error saving suggestions cache %s: %s", self.file, e)
                return False
            finally:
                if tmp_path is not None:
                    try:
                        os.remove(tmp_path)
                    except OSError as e:
                        logger.warning("Could not remove %s: %s", tmp_path, e)


def rekey(data):
    """Saved runs, keyed the way this code looks them up.

    Files written before paths.py hold folder keys in the old lower-case, forward-slash
    form, which paths.key() does not produce; loading them as they were would keep every
    saved run where nothing ever asks for it. A folder saved under two spellings becomes
    one entry, its suggestions merged. The photos inside are keyed by the path the page
    was sent, which is paths.stored().
    """
    rekeyed = {}
    for folder, status in data.items():
        if not isinstance(status, dict):
            continue
        suggestions = status.get("suggestions")
        if isinstance(suggestions, dict):
            status["suggestions"] = {paths.stored(p): s for p, s in suggestions.items()}
        key = paths.key(folder)
        existing = rekeyed.get(key)
        if existing is None:
            rekeyed[key] = status
        else:
            merged = existing.setdefault("suggestions", {})
            for p, s in (status.get("suggestions") or {}).items():
                merged.setdefault(p, s)
    return rekeyed
