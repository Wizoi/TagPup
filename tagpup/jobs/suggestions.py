"""Suggesting tags for a folder's photos: each library's runs, and where each has got.

What each photo was offered is kept in the library (tagpup.store.suggestions), by the
photo's id: a deleted photo takes its suggestions, and a renamed one keeps them
(docs/findings.md, #64). A run's own state -- preparing, running, how far -- stays in
this process until background jobs have a home of their own (docs/ARCHITECTURE.md). A folder
with saved suggestions and no run here says "completed", with them.

TagPup held all of this on its request handler; the run is here now. What it runs -- the
folder's photos, and the model that suggests for each -- is handed to it (`work`): the
models are the process's, built by tagpup.runtime and given to each run by the route
that starts it. A module-level slot the web launcher filled held them before
(docs/findings.md, #112).

A run goes "preparing" -> "running" -> "completed", or "error". Nothing but a start
changes a folder that is preparing or running, and a start leaves one alone.

A run in a folder the library does not hold only looks (Just look; `Work.looking`, decided
by the route from the library's own answer at the start, never by the page). It analyses
each photo against what the library holds -- its vectors, its named faces, its tag tree,
all read -- and keeps what it finds in this process's memory (`SuggestionRuns.looks`), never
in the library: no suggestion, face, crop, vector or photo row is made, and nothing is
recorded of a photo that does not decode. That memory is bounded -- MAX_LOOKED_PHOTOS a
folder, MAX_LOOKED_FOLDERS a library, the oldest folder dropped -- and let go when the
library is forgotten or unused for the idle period (release_looks). A folder added to the
library while its run is going leaves the run in memory; the next start is a persisted run,
which drops what was in memory. docs/ARCHITECTURE.md, "Analyse-only Suggest".
"""
import collections
import concurrent.futures
import copy
import logging
import os
import threading

import numpy as np

from tagpup.core import paths
from tagpup.services import libraries, suggester
from tagpup.services import suggestions as saved

logger = logging.getLogger(__name__)

#: Photos suggested for at once.
WORKERS = min(4, os.cpu_count() or 1)

#: What a run that only looks keeps in memory: photos' suggestions in a folder, and folders
#: in a library (the oldest not under way is dropped). A suggestion is a few KB.
MAX_LOOKED_PHOTOS = 2000
MAX_LOOKED_FOLDERS = 3

#: Called with a library's key when its looking runs' memory is used, to say the process's idle registry
#: so (the web fills it in: tagpup.web.tagpup_routes.idle_caches): each library's is its own entry.
on_looks_use = None

_runs = {}
_runs_lock = threading.Lock()


def work_for(library, photos, models, looking=False, held_now=None):
    """What a run over one folder of `library` runs (SuggestionRuns.start): `photos()`,
    the folder's photos as {key: metadata with "path"}, and the model `models` readies
    for the library -- something with `begin(library)` (tagpup.runtime.Runtime), which
    returns what a run calls (`suggest`, `offered`, `consensus`, `model_key`; see
    SuggestionRuns.start). Everything the run's thread needs is handed to it here; it
    never asks which library a request was for (docs/findings.md, #44). Without models,
    a run over a folder with photos fails with a message saying so. `looking`: the folder is
    not the library's, and the run keeps what it finds in memory and adds nothing to the
    library (decided by the route, from the library's own answer). `held_now()`, for such a run: does
    the library hold every folder of it now (suggest_how no longer says to look)? Asked when the run ends: a folder added meanwhile, whose
    indexing finished first, is the library's and what the run found is let go (#557)."""

    class Work:
        def photos(self):
            return photos()

        def begin(self):
            if models is None:
                raise RuntimeError("Suggest has no model: this app was made without a runtime "
                                   "(tagpup.web.app.create_app(runtime=...))")
            return models.begin(library, remember=False) if looking else models.begin(library)

    Work.looking = looking
    Work.held_now = staticmethod(held_now) if held_now else None
    return Work()


def _end(model):
    """Tell what began the run that it is over (tagpup.runtime.RunModel.end): the photo
    index and the models it held may be let go. A model without `end` holds nothing."""
    end = getattr(model, "end", None)
    if callable(end):
        try:
            end()
        except Exception as e:
            logger.error("Could not end a suggestion run: %s", e)


def runs_for(library):
    """This process's runs for a library, made the first time they are asked for."""
    with _runs_lock:
        runs = _runs.get(library.key)
        if runs is None:
            runs = _runs[library.key] = SuggestionRuns(library.path, library.key)
        return runs


def running():
    """How many suggestion runs are under way in this process, in any library: what an
    update of the always-on process waits for (tagpup.web.lifecycle)."""
    with _runs_lock:
        every = list(_runs.values())
    count = 0
    for runs in every:
        with runs.lock:
            count += sum(1 for status in runs.statuses.values()
                         if status.get("status") in ("preparing", "running"))
    return count


def release_looks(library_key=None):
    """Let go of what runs that only looked kept in memory, for each folder not under way, in the
    library `library_key` (its Library.key) or, without one, in every library (the idle registry's
    call: tagpup.web.tagpup_routes). How many folders."""
    with _runs_lock:
        every = [runs for key, runs in _runs.items() if library_key is None or key == library_key]
    return sum(runs.release_looks() for runs in every)


def running_in(library_key):
    """How many suggestion runs are under way in one library."""
    with _runs_lock:
        runs = _runs.get(library_key)
    if runs is None:
        return 0
    with runs.lock:
        return sum(1 for status in runs.statuses.values() if status.get("status") in ("preparing", "running"))


def under_way():
    """The suggestion runs under way in this process, for the Activity page: [{"library",
    "folder" (its name), "status", "completed", "total"}]."""
    with _runs_lock:
        every = list(_runs.values())
    found = []
    for runs in every:
        library = os.path.splitext(os.path.basename(runs.db_path))[0]
        with runs.lock:
            for key, status in runs.statuses.items():
                if status.get("status") in ("preparing", "running"):
                    folder = runs.folders.get(key) or key
                    found.append({"library": library, "folder": os.path.basename(folder.rstrip("/\\")),
                                  "status": status["status"], "completed": status.get("completed", 0),
                                  "total": status.get("total", 0)})
    return found


def forget(library):
    """Drop a library's runs from memory, and the prompts a looking run kept for it. A run still
    going finishes into nothing."""
    with _runs_lock:
        _runs.pop(library.key, None)
    suggester.forget_looking_text_embeddings(library.path)


def touch_looks(library_key):
    """Say the idle registry that what runs that only looked kept in memory, in the library a request is
    for, is in use, when any is kept: every request of the page counts, so an owner reviewing what was
    found is not stranded by the idle release (#547) -- and another library's, which the owner is not
    working in, is not kept alive by it (#558). Cheap: no library is asked."""
    if on_looks_use is None or not library_key:
        return
    with _runs_lock:
        runs = _runs.get(library_key)
    if runs is not None and runs.looks:
        on_looks_use(library_key)


def drop_looks(library_key, folder, subfolders=True, held=None):
    """A folder is being ADDED to the library (`library_key`: its Library.key): what a run that only
    looked kept for it, and with `subfolders` -- as added, a folder and everything under it -- for each
    folder under it, is let go, so the status answers from the library and the next Suggest is the run
    that saves (#545). Only where a folder is added, never for a sync or an index of new files: a held
    folder does not hold its subfolders (#556). `held(folder)`, when given, is asked of each: one the library
    does not hold yet keeps its look. A folder with a run under way is left (its run ends by asking
    again: SuggestionRuns.run_looking). How many folders."""
    with _runs_lock:
        runs = _runs.get(library_key) if library_key else None
    return runs.drop_looks(folder, subfolders, held) if runs is not None else 0


def dropped_by_add(library, folders, subfolders=True):
    """The routes' call after `tagpup.services.libraries.add`: drop the looks of each folder added
    (drop_looks), the library's own answer deciding which are held now. A library that cannot be
    asked keeps every look."""
    def held(folder):
        return libraries.holds(library, folder)

    dropped = 0
    for folder in folders:
        if not isinstance(folder, str) or not folder:
            continue
        try:
            dropped += drop_looks(library.key, folder, subfolders, held)
        except Exception as e:
            logger.warning("Could not tell whether %s holds %s; its look is kept: %s", library.name, folder, e)
    return dropped


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

    def __init__(self, db_path, key=None):
        self.db_path = db_path
        #: The library's key (Library.key), told to on_looks_use.
        self.key = key
        self.lock = threading.Lock()
        self.statuses = {}
        #: {folder key: {photo as the run was handed it: entry}}: what runs that only look
        #: (a folder the library does not hold) found, in the shape the library keeps. A
        #: folder here is answered from memory, never from the library. Oldest first.
        self.looks = collections.OrderedDict()
        #: {folder key: {paths.key of a photo: the path its run was handed}}: the
        #: spelling the page knows each photo by, which the library's row need not share.
        self.spellings = {}
        #: {folder key: the folder as its run was handed it}: what the Activity page names.
        self.folders = {}

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
            looked = self._looked(paths.key(folder))
        if looked is not None:
            return self._looked_status(run, looked)
        found = self._saved(folder, photos)
        if run is not None:
            return plain(dict(run, suggestions=found))
        if not found:
            return {"status": "idle"}
        done = sum(1 for entry in found.values() if _succeeded(entry))
        return {"status": "completed", "completed": done, "total": len(found), "suggestions": found}

    def suggestions(self, folder, photos=None):
        """What a folder's runs suggested, each photo to its entry, or None."""
        with self.lock:
            looked = self._looked(paths.key(folder))
        if looked is not None:
            return plain(looked) or None
        return self._saved(folder, photos) or None

    # ---- A folder only looked at -----------------------------------------------------

    def _looked(self, key):
        """A copy of what runs that only looked kept for the folder, or None when none did.
        Under the lock. The entries are replaced, never changed, so a copy of the dict is
        a snapshot."""
        if key not in self.looks:
            return None
        if on_looks_use and self.key:
            on_looks_use(self.key)
        return dict(self.looks[key])

    @staticmethod
    def _looked_status(run, looked):
        found = plain(looked)
        if run is not None:
            return dict(plain(run), suggestions=found, in_memory=True)
        if not found:
            return {"status": "idle"}
        done = sum(1 for entry in found.values() if _succeeded(entry))
        return {"status": "completed", "completed": done, "total": len(found), "suggestions": found,
                "in_memory": True}

    def release_looks(self):
        """Let go of what runs that only looked kept, for every folder not under way, and
        the state of their runs. How many folders."""
        with self.lock:
            let_go = [key for key in self.looks
                      if self.statuses.get(key, {}).get("status") not in ("preparing", "running")]
            for key in let_go:
                self._drop_look(key)
        return len(let_go)

    def drop_looks(self, folder, subfolders=True, held=None):
        """Let go of the look of `folder`, and with `subfolders` of every folder under it, not under way
        and (when `held` is given) held by the library now. How many."""
        top = paths.key(folder)
        with self.lock:
            asked = [(key, self.folders.get(key) or key) for key in self.looks
                     if (key == top or (subfolders and paths.is_under(key, top)))
                     and self.statuses.get(key, {}).get("status") not in ("preparing", "running")]
        # The library is asked outside the lock a poll waits on.
        let_go = [key for key, where in asked if held is None or held(where)]
        with self.lock:
            let_go = [key for key in let_go if self.statuses.get(key, {}).get("status") not in ("preparing", "running")]
            for key in let_go:
                self._drop_look(key)
        return len(let_go)

    def renamed(self, renames):
        """Photos of a looked-at folder were renamed on disk (a Smart Rename that wrote the files only):
        `renames` {old path: new path}. What is kept for each is kept under its new name, in the folders
        looked at; nothing is analysed again."""
        by_key = {paths.key(old): paths.stored(new) for old, new in renames.items()}
        with self.lock:
            for key, memory in self.looks.items():
                if not any(paths.key(photo) in by_key for photo in memory):
                    continue
                moved = {}
                for photo, entry in memory.items():
                    new = by_key.get(paths.key(photo))
                    if new is not None and isinstance(entry.get("raw_suggestions"), dict) \
                            and "path" in entry["raw_suggestions"]:
                        entry = dict(entry, raw_suggestions=dict(entry["raw_suggestions"], path=new))
                    moved[new or photo] = entry
                self.looks[key] = moved
                self.spellings.pop(key, None)

    def _drop_look(self, key):
        """Forget a folder's looking run. Under the lock."""
        self.looks.pop(key, None)
        self.statuses.pop(key, None)
        self.spellings.pop(key, None)
        self.folders.pop(key, None)

    def _keep_looking_at(self, key):
        """Make room for a folder to be looked at: the oldest folders not under way beyond
        MAX_LOOKED_FOLDERS are dropped. Under the lock."""
        self.looks.setdefault(key, {})
        self.looks.move_to_end(key)
        while len(self.looks) > MAX_LOOKED_FOLDERS:
            for old in self.looks:
                if old != key and self.statuses.get(old, {}).get("status") not in ("preparing", "running"):
                    self._drop_look(old)
                    break
            else:
                return

    # ---- Starting and running -------------------------------------------------------

    def start(self, folder, work):
        """Start a run over a folder on a thread of its own, unless one is going. Returns
        the status it is in: "preparing" or "running".

        `work` gives the run what it runs: `photos()`, the folder's photos as
        {key: metadata with "path"}, and `begin()`, which readies the model and returns
        something with `suggest(path, metadata)`, `offered(suggestion)` -> (tags, people,
        title) as the panel shows them, `consensus(suggestions)`, and `model_key` -- and,
        if it holds something for the run, `end()`, called when the run is over.
        """
        key = paths.key(folder)
        looking = bool(getattr(work, "looking", False))
        # A photo whose suggestion failed is tried again, so it is not done yet.
        if looking:
            with self.lock:
                done = sum(1 for found in self.looks.get(key, {}).values() if _succeeded(found))
        else:
            done = sum(1 for found in self._saved(folder).values() if _succeeded(found))
        with self.lock:
            status = self.statuses.get(key)
            if status and status.get("status") in ("preparing", "running"):
                return status["status"]
            self.statuses[key] = {"status": "preparing", "completed": done, "total": 0}
            self.folders[key] = paths.stored(folder)
            if looking:
                self._keep_looking_at(key)
            else:
                # The folder is the library's: what was found in memory while it was not is
                # let go, and this run keeps its own in the library.
                self.looks.pop(key, None)
        threading.Thread(target=self.run_looking if looking else self.run, args=(folder, work),
                         name="FolderSuggestionsThread", daemon=True).start()
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
            try:
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
            finally:
                _end(model)
        except Exception as e:
            logger.exception("Error running suggestions for %s: %s", folder, e)
            # Always said, even with the entry gone, so the page stops polling instead of
            # spinning on "preparing" for ever.
            with self.lock:
                entry = self.statuses.get(key) or {"completed": 0, "total": 0}
                entry.update(status="error", message=str(e))
                self.statuses[key] = entry

    def run_looking(self, folder, work):
        """`run` for a folder the library does not hold: the same steps, and what each photo
        is suggested is kept in memory (`looks`), not in the library. Photos suggested for
        already are not suggested for again; at most MAX_LOOKED_PHOTOS of a folder are kept,
        and the status says when some were left out. When it is done the status says what the
        library had too little of to compare with (`notes`), and how many photos could not be
        read (`unread`)."""
        folder = paths.stored(folder)
        key = paths.key(folder)
        try:
            photos = work.photos()
            if not photos:
                logger.warning("No photos found in %s.", folder)
                self._say_error(key, "No images found in this folder. Nothing was changed.")
                return
            with self.lock:
                self.spellings[key] = {paths.key(meta["path"]): paths.stored(meta["path"])
                                       for meta in photos.values()}
                self._keep_looking_at(key)
                memory = dict(self.looks[key])
            done_before = {paths.key(photo): found for photo, found in memory.items()}
            todo = [photo for photo in photos
                    if not _succeeded(done_before.get(paths.key(photos[photo]["path"])))]
            # A photo failed before is tried again and has its place; a new one needs room.
            fresh = [photo for photo in todo if paths.key(photos[photo]["path"]) not in done_before]
            room = max(0, MAX_LOOKED_PHOTOS - len(memory))
            left_out = set(fresh[room:])
            todo = [photo for photo in todo if photo not in left_out]
            with self.lock:
                self.statuses.setdefault(key, {"status": "preparing", "completed": 0, "total": 0}).update(
                    status="preparing", total=len(photos) - len(left_out), completed=len(photos) - len(todo) - len(left_out))

            try:
                model = work.begin()
            except Exception as e:
                raise RuntimeError("The models could not be made ready, so no photo was analysed and nothing "
                                   "was changed: %s" % e) from e
            try:
                with self.lock:
                    self.statuses[key]["status"] = "running"
                made = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
                    for done in concurrent.futures.as_completed(
                            [pool.submit(self._suggest_in_memory, key, photos[photo], model) for photo in todo]):
                        if done.result() is not None:
                            made.append(done.result())

                if made and key in self.statuses:
                    self._take_consensus_in_memory(key, photos, model)
                # What the library lacks is read from it: outside the lock a poll waits on.
                found = self._what_was_found(key, photos, model, left_out)
                with self.lock:
                    entry = self.statuses[key]
                    entry.update(found)
                    entry["status"] = "completed"
                self._let_go_if_held(key, work)
            finally:
                _end(model)
        except Exception as e:
            logger.exception("Error analysing the photos of %s without saving: %s", folder, e)
            said = str(e).strip()
            # Whatever failed -- the scan, the models -- a run that only looks has written nothing to the library.
            if "nothing was changed" not in said.lower():
                said = "%s%s Nothing was changed." % (said, "" if said.endswith((".", "!", "?")) else ".")
            self._say_error(key, said)

    def _let_go_if_held(self, key, work):
        """A folder added while the run was going, whose indexing finished first, is the library's now: what the
        run found stays out of memory, so the status answers from the library (#557). The library is asked
        outside the lock; one that cannot be asked keeps the look."""
        held_now = getattr(work, "held_now", None)
        if held_now is None:
            return
        try:
            held = held_now()
        except Exception as e:
            logger.warning("Could not tell whether the library holds the folder now; its look is kept: %s", e)
            return
        if held:
            with self.lock:
                self._drop_look(key)

    def _say_error(self, key, message):
        """Always said, even with the entry gone, so the page stops polling."""
        with self.lock:
            entry = self.statuses.get(key) or {"completed": 0, "total": 0}
            entry.update(status="error", message=message)
            self.statuses[key] = entry

    def _what_was_found(self, key, photos, model, left_out):
        """What a finished looking run adds to its status: `notes`, sentences for the page, and
        `unread`, how many of this folder's photos have no suggestion for want of being read."""
        notes = []
        lacks = getattr(model, "what_it_lacks", None)
        try:
            said = lacks() if callable(lacks) else []
            notes.extend(said if isinstance(said, list) else [])
        except Exception as e:
            logger.warning("Could not tell what the library lacks: %s", e)
        wanted = {paths.key(meta["path"]) for meta in photos.values()}
        with self.lock:
            memory = dict(self.looks.get(key, {}))
        failed = [found for photo, found in memory.items() if paths.key(photo) in wanted and not _succeeded(found)]
        if failed:
            notes.append("%d photo(s) could not be analysed, nothing was recorded of them (%s)."
                         % (len(failed), failed[0].get("error", "unreadable")))
        if left_out:
            notes.append("%d photo(s) were left out: a look keeps the suggestions of at most %d photos of a folder "
                         "in memory. Add the folder to the library to suggest for all of them."
                         % (len(left_out), MAX_LOOKED_PHOTOS))
        return {"notes": notes, "unread": len(failed)}

    def _suggest_in_memory(self, key, meta, model):
        """`_suggest` that keeps the suggestion in memory only. None when the folder's run is gone or the
        photo could not be suggested for (its entry says why, and is tried again by the next run)."""
        if key not in self.statuses:
            return None
        photo = paths.stored(meta["path"])
        try:
            suggestion = model.suggest(photo, meta)
            tags, people, title = model.offered(suggestion)
            found = {"tags": tags, "people": people, "title": title,
                     "raw_suggestions": suggestion, "raw_before_consensus": True}
        except Exception as e:
            logger.error("Error suggesting for %s: %s", photo, e)
            suggestion = None
            found = {"tags": [], "people": [], "title": None,
                     "raw_suggestions": {"suggested_tags": []}, "error": str(e) or type(e).__name__}
        with self.lock:
            if key not in self.statuses or key not in self.looks:
                return None
            self.looks[key][photo] = plain(found)
            self.statuses[key]["completed"] += 1
        return suggestion

    def _take_consensus_in_memory(self, key, photos, model):
        """`_take_consensus`, over what is in memory."""
        try:
            in_run = {paths.key(meta["path"]) for meta in photos.values()}
            with self.lock:
                kept = list(self.looks.get(key, {}).items())
            raw = [dict(copy.deepcopy(found["raw_suggestions"]), path=photo)
                   for photo, found in kept
                   if paths.key(photo) in in_run and _succeeded(found) and found.get("raw_before_consensus")
                   and isinstance(found.get("raw_suggestions"), dict) and found["raw_suggestions"].get("path")]
            if len(raw) < 2:
                return
            offered = [(paths.stored(s["path"]), plain(model.offered(s))) for s in model.consensus(raw)]
            with self.lock:
                memory = self.looks.get(key)
                if memory is None:
                    return
                for photo, (tags, people, title) in offered:
                    if photo in memory:
                        memory[photo] = dict(memory[photo], tags=tags, people=people, title=title)
        except Exception as e:
            logger.error("Error taking folder consensus: %s", e)

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
