"""Suggesting tags for a folder's photos: each library's runs, and where each has got.

What each photo was offered is kept in the library (tagpup.store.suggestions), by the
photo's id: a deleted photo takes its suggestions, and a renamed one keeps them
(docs/findings.md, #64). A run's own state -- preparing, running, how far -- stays in
this process until background jobs have a home of their own (ARCHITECTURE.md). A folder
with saved suggestions and no run here says "completed", with them.

TagPup held all of this on its request handler; the run is here now. What it runs -- the
folder's photos, and the model that suggests for each -- is handed to it (`work`), since
the suggester and the CLIP model have not moved into the package yet.

A run goes "preparing" -> "running" -> "completed", or "error". Nothing but a start
changes a folder that is preparing or running, and a start leaves one alone.
"""
import concurrent.futures
import copy
import logging
import os
import threading

import numpy as np

from tagpup.core import paths
from tagpup.services import suggestions as saved

logger = logging.getLogger(__name__)

#: Photos suggested for at once.
WORKERS = min(4, os.cpu_count() or 1)

_runs = {}
_runs_lock = threading.Lock()


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


def _succeeded(found):
    return isinstance(found, dict) and "error" not in found


class SuggestionRuns:
    """One library's folders' suggestion runs. `statuses` maps a folder's key to its run:
    {"status", "completed", "total"[, "message"]}. Changed only under `lock`."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.statuses = {}
        #: {folder key: {paths.key of a photo: the path its run was handed}}: the
        #: spelling the page knows each photo by, which the library's row need not share.
        self.spellings = {}

    def _saved(self, folder, photos=None):
        """{photo: entry} of what the library holds for a folder's photos, each under the
        spelling its run or `photos` (the folder's scan, {key: metadata with "path"}) was
        handed: the row's spelling may differ, and the page looks a photo up by the
        scan's (docs/findings.md, #92)."""
        with self.lock:
            spelled = dict(self.spellings.get(paths.key(folder), {}))
        for meta in (photos or {}).values():
            spelled[paths.key(meta["path"])] = paths.stored(meta["path"])
        return {spelled.get(paths.key(photo), photo): found
                for photo, found in saved.saved_in(self.db_path, folder).items()}

    # ---- What a page asks -----------------------------------------------------------

    def status(self, folder, photos=None):
        """A folder's run and its suggestions, or {"status": "idle"}: a copy, taken under
        the lock the workers write under -- serialising the live dict while four workers
        added to it failed the poll with "dictionary changed size during iteration".

        The run's state first, then the rows: a run takes its consensus before it says
        "completed", so rows read after are never older than the state. Read the other
        way round, a poll could pair "completed" with the rows from before consensus,
        which the page keeps (#93). `photos` is the folder's scan, whose spellings the
        rows are handed back under."""
        with self.lock:
            run = copy.deepcopy(self.statuses.get(paths.key(folder)))
        found = self._saved(folder, photos)
        if run is not None:
            return plain(dict(run, suggestions=found))
        if not found:
            return {"status": "idle"}
        done = sum(1 for entry in found.values() if _succeeded(entry))
        return {"status": "completed", "completed": done, "total": len(found), "suggestions": found}

    def suggestions(self, folder, photos=None):
        """What a folder's runs suggested, each photo to its entry, or None."""
        return self._saved(folder, photos) or None

    # ---- Starting and running -------------------------------------------------------

    def start(self, folder, work):
        """Start a run over a folder on a thread of its own, unless one is going. Returns
        the status it is in: "preparing" or "running".

        `work` gives the run what it runs: `photos()`, the folder's photos as
        {key: metadata with "path"}, and `begin()`, which readies the model and returns
        something with `suggest(path, metadata)`, `offered(suggestion)` -> (tags, people,
        title) as the panel shows them, `consensus(suggestions)`, and `model_key`.
        """
        key = paths.key(folder)
        # A photo whose suggestion failed is tried again, so it is not done yet.
        done = sum(1 for found in self._saved(folder).values() if _succeeded(found))
        with self.lock:
            status = self.statuses.get(key)
            if status and status.get("status") in ("preparing", "running"):
                return status["status"]
            self.statuses[key] = {"status": "preparing", "completed": done, "total": 0}
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
                    entry = self.statuses.get(key) or {"completed": 0, "total": 0}
                    entry.update(status="error", message="No images found in this folder.")
                    self.statuses[key] = entry
                return
            with self.lock:
                self.spellings[key] = {paths.key(meta["path"]): paths.stored(meta["path"])
                                       for meta in photos.values()}
            # By key: the row's spelling need not be the scan's (#92).
            done_before = {paths.key(photo): found for photo, found in self._saved(folder).items()}
            todo = [photo for photo in photos if not _succeeded(done_before.get(paths.key(photos[photo]["path"])))]
            with self.lock:
                self.statuses.setdefault(key, {"status": "preparing", "completed": 0, "total": 0}).update(
                    status="preparing", total=len(photos), completed=len(photos) - len(todo))

            model = work.begin()
            with self.lock:
                self.statuses[key]["status"] = "running"

            fresh = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
                for done in concurrent.futures.as_completed(
                        [pool.submit(self._suggest, key, photos[photo], model) for photo in todo]):
                    if done.result() is not None:
                        fresh.append(done.result())

            if fresh and key in self.statuses:
                self._take_consensus(folder, photos, model)
            with self.lock:
                self.statuses[key]["status"] = "completed"
        except Exception as e:
            logger.exception("Error running suggestions for %s: %s", folder, e)
            # Always said, even with the entry gone, so the page stops polling instead of
            # spinning on "preparing" for ever.
            with self.lock:
                entry = self.statuses.get(key) or {"completed": 0, "total": 0}
                entry.update(status="error", message=str(e))
                self.statuses[key] = entry

    def _suggest(self, key, meta, model):
        if key not in self.statuses:
            return None
        photo = paths.stored(meta["path"])
        try:
            suggestion = model.suggest(photo, meta)
            tags, people, title = model.offered(suggestion)
            # Kept as the suggester produced it, so consensus can be taken again over the
            # whole folder when more photos arrive.
            found = {"tags": tags, "people": people, "title": title,
                     "raw_suggestions": suggestion, "raw_before_consensus": True}
        except Exception as e:
            logger.error("Error suggesting for %s: %s", photo, e)
            suggestion = None
            # Marked as a failure, not stored as an empty suggestion: the next run retries
            # it instead of skipping it for good.
            found = {"tags": [], "people": [], "title": None,
                     "raw_suggestions": {"suggested_tags": []}, "error": str(e) or type(e).__name__}
        try:
            saved.keep(self.db_path, photo, plain(found), getattr(model, "model_key", None))
        except Exception as e:
            # Not kept, so the next run suggests for it again; the rest of this run and
            # its consensus go on (#95).
            logger.error("Could not keep the suggestions for %s: %s", photo, e)
            return None
        with self.lock:
            if key in self.statuses:
                self.statuses[key]["completed"] += 1
        return suggestion

    def _take_consensus(self, folder, photos, model):
        """Folder consensus over copies of the suggester's own output, so what is kept is
        never adjusted twice."""
        try:
            in_run = {paths.key(meta["path"]) for meta in photos.values()}
            # Each under the path its row has now: the suggester's output names the path
            # it was made for, and a photo renamed since was written back to nothing (#90).
            raw = [dict(copy.deepcopy(found["raw_suggestions"]), path=photo)
                   for photo, found in self._saved(folder).items()
                   if paths.key(photo) in in_run and _succeeded(found) and found.get("raw_before_consensus")
                   and isinstance(found.get("raw_suggestions"), dict) and found["raw_suggestions"].get("path")]
            if len(raw) < 2:
                return
            saved.offer(self.db_path, [(paths.stored(s["path"]), plain(model.offered(s))) for s in model.consensus(raw)],
                        label="folder consensus for %s" % os.path.basename(folder))
        except Exception as e:
            logger.error("Error taking folder consensus: %s", e)
