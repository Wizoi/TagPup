"""Making a library, and bringing one up to date: what the picker's Create does, and
what opening a library by its URL does first. And whether a library holds a folder: the
one answer every entry point asks before work that would make rows for its photos.

The old server made a library by opening it through PhotoIndex and then seeding its
tag tree, two old modules the web layer may not import (tests/test_layers.py). A new
library's tables come from tagpup.store.schema, its first nodes -- one face root,
People -- from tagpup.store.taxonomy.seed, and its settings, the defaults, from
tagpup.services.settings.
"""
import logging
import os
import threading
import time

from tagpup.core import paths, validation
from tagpup.core.library import Library, picker_name
from tagpup.core.result import DAMAGED_PHOTOS, NOT_IN_LIBRARY, Result
from tagpup.files import images, recycle_bin, shares
from tagpup.services import damaged_photos, settings
from tagpup.services import roots as roots_service
from tagpup.store import added_folders, db, photos, schema, taxonomy
from tagpup.store import folders as store_folders

logger = logging.getLogger(__name__)



#: Raised for a library made by a newer version of TagPup than this one (tagpup.store.schema.NewerLibrary):
#: its text is the sentence to show the owner. Every entry point answers it with that sentence.
NewerLibrary = schema.NewerLibrary


def bring_up_to_date(db_path):
    """The library's tables, made or migrated (tagpup.store.schema.ensure). Returns the
    names of the migrations applied: none, usually. NewerLibrary for a library made by a newer
    version of TagPup than this one."""
    return schema.ensure(db_path)


reading_newer = schema.reading_newer
newer_note = schema.newer_note
RECOVERY_COMMANDS = schema.RECOVERY_COMMANDS


def newer_problem(db_path):
    """The sentence saying the library at `db_path` is newer than this version of TagPup, or None
    (tagpup.store.schema.newer_problem). Reads only: for an entry point that does not open it
    through bring_up_to_date."""
    return schema.newer_problem(db_path)


#: How many libraries the startup thread has still to bring up to date (bring_up_to_date_in_background):
#: work beside the requests, which /api/server shows and a drain waits for (tagpup.web.lifecycle.long_work).
_bringing = 0
_bringing_guard = threading.Lock()

#: The libraries (by key) the startup thread has not yet looked at: a request for one is answered "updating" at once
#: rather than run the migration on, or wait on the lock of, a server thread (tagpup.web.libraries).
_waiting = {}


def _key(db_path):
    return paths.key(db_path)


def updating(db_path):
    """Is the library at `db_path` being brought up to date by this process just now -- migrating, or still to be looked
    at by the startup thread? A cheap read; a request for it waits (503) instead of holding a thread on the write lock."""
    with _bringing_guard:
        if _waiting.get(_key(db_path), 0) > 0:
            return True
    return schema.migrating(db_path)


def bringing_up_to_date():
    """How many libraries this process's startup thread has still to bring up to date."""
    with _bringing_guard:
        return _bringing


def bring_up_to_date_in_background(libraries_served):
    """Bring each library the server serves to the current schema on a thread of its own, started as the
    server starts (docs/findings.md, #661): a migration -- 21 takes 17.5 s on photo_index cold -- runs
    then, not in the first request that names the library. The server answers meanwhile: /api/server,
    which the supervisor's hand-over asks, names no library, and says the thread is busy; a drain waits
    for it (bringing_up_to_date, #664), so a new version does not end the server part-way through a
    migration. A request for the library meanwhile is answered 503 "updating" at once (`updating`; the pages
    wait that out, #666), not parked on the write lock: sixteen parked threads left /api/server no thread. A library that cannot be brought up to date now -- held by another
    program past the busy timeout -- is logged and left for its first request. A library whose file is
    not there is left alone: bringing it up to date would make it, and the picker offers the default
    library in an empty data folder (#100). Returns the thread."""
    global _bringing
    there = [library for library in libraries_served if os.path.exists(library.path)]
    with _bringing_guard:
        _bringing += len(there)
        for library in there:
            _waiting[_key(library.path)] = _waiting.get(_key(library.path), 0) + 1

    def run():
        global _bringing
        for library in there:
            started = time.time()
            try:
                applied = bring_up_to_date(library.path)
                if applied:
                    logger.info("Brought %s up to date at start (%s) in %.1fs", library.path, ", ".join(applied),
                                time.time() - started)
            except Exception as e:
                logger.warning("Could not bring %s up to date at start: %s", library.path, e)
            finally:
                with _bringing_guard:
                    _bringing -= 1
                    _waiting[_key(library.path)] -= 1

    thread = threading.Thread(target=run, name="BringUpToDateThread", daemon=True)
    thread.start()
    return thread


def create(db_path):
    """Make the library at `db_path`: its folder, its tables, and its first nodes. A
    library already there is brought up to date and its tree left alone.

    Refused, and nothing made, when its name -- the file's, without ".db" or a test
    prefix -- may not name a library (tagpup.core.validation). changed: 1 when the
    library was made, 0 when it was there already."""
    result = Result(attempted=1)
    problem = validation.problem("library name", picker_name(os.path.basename(db_path)))
    if problem:
        result.refuse(problem)
        return result
    existed = os.path.exists(db_path)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    try:
        bring_up_to_date(db_path)
    except NewerLibrary as e:
        result.refuse(str(e))   # a library of that name is there, from a newer TagPup: not touched
        return result
    taxonomy.seed(db_path)
    if not existed:
        # A new library holds the defaults from the start (tagpup.services.settings): it
        # was made with nothing else, whatever an old config.ini beside it says.
        settings.stamp(Library(db_path), None)
    result.changed = 0 if existed else 1
    return result



# ---- Does a library hold a folder -------------------------------------------------------
#
# The owner opened a folder of another library's in TagPup with kr-track selected, and
# Suggest made 25 rows for its photos in kr-track without asking; the folder watcher then
# kept the folder in step, read its tags and grew kr-track's tag tree from them
# (2026-09-28). Which folders are the library's is tagpup.store.folders' to say; these
# ask it, for a page to ask before anything is done in one.

def _read(db_path, read, default):
    """`read(conn)` on a read-only connection to the library at `db_path`, or `default`
    for one that is not there, which holds nothing. A library that is there and cannot
    be read just now raises: taken for one holding nothing, a moment's lock answered a
    write or a Suggest in a folder it holds with 409, sending the owner to Add it."""
    if not os.path.exists(db_path):
        return default
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return read(conn)
    finally:
        conn.close()


def not_held(library, photo_paths, ignored=None, leave_out_ignored=True):
    """The folders of `photo_paths` that are not the library's (tagpup.store.folders),
    each once, as stored, in path order: those it must be asked to add before any row is
    made for their photos (tagpup.store.photos.ensure_row). One query a folder, not a
    photo.

    `ignored` are the library's ignored folders, read from its settings unless given. An
    ignored folder is never the library's; unless not `leave_out_ignored` it is left out
    here too -- not to be offered, as sync passes it over (tagpup.services.sync). Writes
    keep them (refuse_writes): a photo there is not the library's to write."""
    wanted = {}
    for photo_path in photo_paths:
        folder = os.path.dirname(paths.stored(photo_path))
        wanted.setdefault(paths.key(folder), folder)
    if not wanted:
        return []
    ordered = [wanted[key] for key in sorted(wanted)]

    def read(conn):
        found = store_folders.ignored(conn) if ignored is None else list(ignored)
        # A write keeps a folder with rows of its own held whatever is ignored (leave_out_ignored False):
        # the rows are the library's and the settings promise they stay in step (#521).
        return [folder for folder in ordered
                if not (leave_out_ignored and store_folders.is_ignored(folder, found))
                and not (store_folders.holds(conn, folder, found)
                         or (not leave_out_ignored and store_folders.has_rows(conn, folder)))]

    there = [f for f in ordered if not (leave_out_ignored and ignored and store_folders.is_ignored(f, ignored))]
    return _read(library.path, read, there)


def holds(library, folder, ignored=None):
    """Does the library hold `folder` itself -- it has a row directly in it, or it was added -- now? Its
    own answer (tagpup.store.folders.holds); a library that cannot be read just now raises, as not_held does."""
    return not not_held(library, [os.path.join(folder, "-")], ignored, leave_out_ignored=False)


def held_under(library, folder):
    """How many photos under `folder`, at any depth, the library holds; 0 for one not there."""
    return _read(library.path, lambda conn: photos.count_under(conn, folder), 0)


def _under_any(folder, folders):
    return any(paths.same(folder, other) or paths.is_under(folder, other) for other in folders)


def is_ignored(photo_path, ignored):
    """Is the photo in one of the library's `ignored` folders, or under one?"""
    return store_folders.is_ignored(os.path.dirname(paths.stored(photo_path)), ignored)


def membership(library, folder, roots=(), ignored=()):
    """What `library` holds of `folder`, for the page to say before anything is done in it:
    {"library" (its name, as the address bar has it), "folder" (as stored), "photos" (on
    disk under it, at any depth), "photos_held" (the library's rows under it),
    "photos_ignored" (photos in its ignored folders, never offered),
    "photos_not_held" (photos in folders under it the library holds none directly in,
    ignored folders left out),
    "folders_not_held", "first_not_held" (the first such folder, or None),
    "permanent_delete" (the place has no Recycle Bin: since #694 a photo deleted there is not deleted for good but goes
    through this PC's Recycle Bin, tagpup.files.recycle_bin.delete_file; the name is kept) and "permanent_reason" (why, as a phrase:
    "on a network share", "on a removable drive", "on a substituted (SUBST) drive", "on a drive without a
    Recycle Bin"; None otherwise; tagpup.files.recycle_bin.no_bin_reason), "has_roots",
    "under_roots" (the folder is one of `roots` or under one), "ignored" (likewise, of
    `ignored`)}. One walk of the folder, no file read. Only `library` is opened: the page
    asking is in one library, which does not open, read or name another (2026-10-02)."""
    folder = paths.stored(folder)
    reason = recycle_bin.no_bin_reason(folder)
    on_disk = images.photos_under(folder)
    unheld = not_held(library, on_disk, ignored)
    unheld_keys = {paths.key(each) for each in unheld}
    return {
        "library": picker_name(os.path.basename(library.path)),
        "folder": folder,
        "photos": len(on_disk),
        "photos_held": held_under(library, folder),
        "photos_ignored": sum(1 for p in on_disk if is_ignored(p, ignored)),
        "photos_not_held": sum(1 for p in on_disk if paths.key(os.path.dirname(p)) in unheld_keys),
        "folders_not_held": len(unheld),
        "first_not_held": unheld[0] if unheld else None,
        "has_roots": bool(roots),
        "under_roots": _under_any(folder, roots),
        "ignored": _under_any(folder, ignored),
        "permanent_delete": reason is not None,
        "permanent_reason": reason,
    }


#: How long a membership walk may take before the answer is "could not check", in seconds, and how long a folder on a
#: network share gets to say it is there at all (shares.bounded).
MEMBERSHIP_DEADLINE = 12.0
MEMBERSHIP_SHARE_WAIT = 1.0

_walks = {}                  # {(library key, folder key): the _Walk under way}
_walks_guard = threading.Lock()


class _Walk:
    """One membership walk of one folder, under way or done: whoever asks for the same folder meanwhile is answered by it."""

    def __init__(self):
        self.done = threading.Event()
        self.answer = None
        self.error = None


def on_a_network_drive(folder):
    """Is `folder` on a network share (a UNC path or a mapped drive)? What a page's request must not look at itself: it may
    stop answering (tagpup.files.shares)."""
    return shares.on_a_network_drive(folder)


def could_not_check(folder, why):
    """The answer of membership_checked when the folder could not be walked in time: no counts, and why."""
    return {"folder": paths.stored(folder), "could_not_check": True, "why": why}


def membership_checked(library, folder, roots=(), ignored=(), deadline=None):
    """membership, for a page's request (phase 9c, findings #568): the walk is made on a thread of its own and waited for
    `deadline` seconds, and a second request for the same folder while one walk is under way is answered by it -- never a
    second walk of one folder, however often the view is opened. A folder on a network share (a UNC path or a mapped
    drive) must first say it is there within a second (tagpup.files.shares.bounded), else the answer is
    could_not_check("the network share is away") and no thread is left walking it. A walk that outlives the deadline
    answers could_not_check("it took too long") and goes on, for the next request to be answered by when it is done. A
    walk that raised raises here."""
    folder = paths.stored(folder)
    waited = MEMBERSHIP_DEADLINE if deadline is None else deadline
    if shares.on_a_network_drive(folder):
        state, there = shares.bounded(folder, lambda: os.path.isdir(folder), MEMBERSHIP_SHARE_WAIT)
        if state == "away":
            return could_not_check(folder, "the network share is away")
        if state == "error" or not there:
            return could_not_check(folder, "the folder could not be reached")
    key = (library.key, paths.key(folder))
    with _walks_guard:
        walk = _walks.get(key)
        mine = walk is None
        if mine:
            walk = _walks[key] = _Walk()
    if mine:
        def run():
            try:
                walk.answer = membership(library, folder, roots, ignored)
            except BaseException as problem:   # handed to whoever waits, not lost on this thread
                walk.error = problem
            finally:
                with _walks_guard:
                    if _walks.get(key) is walk:
                        del _walks[key]
                walk.done.set()
        threading.Thread(target=run, name="MembershipWalk", daemon=True).start()
    if not walk.done.wait(waited):
        return could_not_check(folder, "it took too long")
    if walk.error is not None:
        raise walk.error
    return walk.answer


def suggest_how(library, folder, ignored=None):
    """How Suggest may run in `folder` for `library`: (why not, looking). `why not` is a
    sentence when the folder itself is one the library ignores (its Library settings): not
    its to suggest in. Otherwise None, and `looking`: True when some folder of photos under
    it is not the library's, so the run only looks -- it analyses the photos against what
    the library holds, keeps what it finds in memory and adds nothing to the library
    (tagpup.jobs.suggestions); False when the library holds every folder of photos under
    it but its ignored ones, which a run leaves out, and the run keeps what it suggests in
    the library, as always. The library's own answer, here and now, never a page's flag;
    a library that cannot be read just now raises, as not_held does, and nothing is run."""
    if ignored is None:
        ignored = _read(library.path, store_folders.ignored, [])
    if ignored and store_folders.is_ignored(folder, ignored):
        return _ignored_message(library, folder), False
    return None, bool(not_held(library, images.photos_under(folder), ignored))


def _ignored_message(library, folder):
    name = picker_name(os.path.basename(library.path))
    return ("%s is ignored in %s (its Library settings): it is not %s's."
            % (paths.stored(folder), name, name))


def not_in(library, folder, ignored=None):
    """Why nothing that makes rows -- Suggest, whose faces, vectors and suggestions each
    need their photo's row -- may be done in `folder` for `library`, or None when the
    library holds every folder of photos under it but its `ignored` folders (read from
    its settings unless given), which the work leaves out; an ignored folder itself is
    refused. "<folder> is not in <library>", for a person to read. One walk of the
    folder, no file read."""
    name = picker_name(os.path.basename(library.path))
    if ignored is None:
        ignored = _read(library.path, store_folders.ignored, [])
    if ignored and store_folders.is_ignored(folder, ignored):
        return _ignored_message(library, folder)
    unheld = not_held(library, images.photos_under(folder), ignored)
    if not unheld:
        return None
    folder = paths.stored(folder)
    if len(unheld) == 1 and paths.same(unheld[0], folder):
        return "%s is not in %s. Add it to %s first." % (folder, name, name)
    return ("%d folder(s) under %s are not in %s, %s first. Add the folder to %s first."
            % (len(unheld), folder, name, unheld[0], name))


def split(library, photo_paths):
    """(the photos of `photo_paths` in a folder the library holds, those in one it does not),
    each in the order given: decided here, now, by the library's own answer (tagpup.store.
    folders.holds) and never by what a page says. A photo of the first is written as
    always -- its row, the journal, what derives from it. A photo of the second is written
    to its file only (tagpup.services.file_only): Just look in TagPup. An ignored folder
    is not held unless it has rows (#521). A library that cannot be read just now raises, as not_held does: a moment's
    lock must not write rows for photos the library does not hold, nor skip them for ones
    it does."""
    unheld = {paths.key(folder) for folder in not_held(library, photo_paths, leave_out_ignored=False)}
    held, loose = [], []
    for photo_path in photo_paths:
        (loose if paths.key(os.path.dirname(paths.stored(photo_path))) in unheld else held).append(photo_path)
    return held, loose


def refuse_writes(result, library, photo_paths, damaged_ok=False):
    """Refuse `result` -- a write of photo files, nothing written yet -- when the library
    does not hold the folder of any photo of `photo_paths`: "<folder> is not in
    <library>. Add it to <library> first." Returns True when refused. The one check every
    write of a photo makes (tagging, rotating, renaming, a time shift, a delete): writing
    kr-track's tags into the photos of a folder photo_index holds, through kr-track, was
    the same mistake as Suggest making rows there. `details[NOT_IN_LIBRARY]` holds the
    folders, which a web route answers with 409.

    Refused too, unless `damaged_ok` (a delete), when a photo of `photo_paths` was found
    damaged or possibly an incomplete copy (tagpup.services.damaged_photos) and is
    unchanged since: nothing is written into it, as the page tells the owner.
    `details[DAMAGED_PHOTOS]` holds them, answered 409 as well."""
    unheld = not_held(library, photo_paths, leave_out_ignored=False)
    if not unheld:
        if damaged_ok:
            return False
        found, unanswered = damaged_photos.for_write(library, photo_paths)
        if refuse_unchecked(result, unanswered, photo_paths):
            return True
        if not found:
            return False
        more = "" if len(found) == 1 else " (and %d more)" % (len(found) - 1)
        result.refuse("%s%s was found damaged -- %s -- and nothing is written to it. Restore it from a backup, "
                      "then Check again." % (found[0]["name"], more, found[0]["reason"]))
        result.details[DAMAGED_PHOTOS] = [each["path"] for each in found]
        return True
    name = picker_name(os.path.basename(library.path))
    more = "" if len(unheld) == 1 else " (and %d more folder(s))" % (len(unheld) - 1)
    result.refuse("%s is not in %s%s. Add it to %s first." % (unheld[0], name, more, name))
    result.details[NOT_IN_LIBRARY] = unheld
    return True


#: What a Result's details count of the photos a bulk write skipped as damaged.
SKIPPED_DAMAGED = "skipped_damaged"


def refuse_unchecked(result, unanswered, photo_paths):
    """Refuse `result` when a share holding a photo recorded damaged did not answer
    (damaged_photos.for_write): whether the photo is still damaged cannot be told, and a
    write is never let through on that. Returns True when refused."""
    if not unanswered:
        return False
    result.refuse("Can't check %s: try again. A photo there was found damaged, and whether it still is could not "
                  "be told." % ", ".join(unanswered))
    result.details[DAMAGED_PHOTOS] = [path for path in photo_paths
                                      if any(paths.is_under(path, share) for share in unanswered)]
    return True


def leave_out_damaged(result, library, photo_paths):
    """(`photo_paths` but the photos recorded damaged or possibly incomplete and unchanged
    since, [(path, why)] of those left out). A bulk write skips them and writes the rest;
    a write of one photo is refused instead (refuse_writes). (None, None), and `result`
    refused, when a share holding one did not answer (refuse_unchecked)."""
    found, unanswered = damaged_photos.for_write(library, photo_paths)
    if refuse_unchecked(result, unanswered, photo_paths):
        return None, None
    if not found:
        return list(photo_paths), []
    out = {paths.key(each["path"]) for each in found}
    return ([path for path in photo_paths if paths.key(path) not in out],
            [(each["path"], "damaged, nothing is written to it: %s" % each["reason"]) for each in found])


def with_skipped(result, left):
    """`result`, with the photos `left` out as damaged (leave_out_damaged) skipped and
    counted: details[SKIPPED_DAMAGED]."""
    result.attempted += len(left)
    for what, why in left:
        result.skip(what, why)
    result.details[SKIPPED_DAMAGED] = len(left)
    return result


def record_added(library, folders, subfolders=True):
    """Record each of `folders` that is a full path on disk as added to the library, with
    its subfolders unless not `subfolders` (tagpup.store.added_folders): the library's from
    now on, before the index has read a photo of it. No row is made: Suggest makes one for
    a photo it keeps something of, the indexer as it reads each. Returns how many were not
    added before."""
    wanted = [paths.stored(folder) for folder in folders
              if isinstance(folder, str) and not validation.problem("folder", folder) and os.path.isdir(folder)]
    if not wanted:
        return 0
    return db.write_with_connection(
        library.path, lambda conn: sum(added_folders.record(conn, folder, subfolders) for folder in wanted),
        label="add %d folder(s)" % len(wanted))


@roots_service.canonical_args("folders")
def add(library, folders, queue):
    """Add `folders` to the library, as asked: "Add to <library>" in TagPup, TagTuner's Add
    Folder. Each folder is recorded as added, with its subfolders (record_added), so it is
    the library's at once and Suggest may start before the index reaches it; then
    `queue(folders)` (the index queue's start) indexes them, the indexer making each row
    as it reads the photo. No row is made here: making one for every photo up front made
    tens of thousands of unstamped rows for a large tree, which sync read again while the
    indexer read them too.

    A folder that is no full path or not on disk is not recorded, and is handed on for the
    queue to report. A Result: `changed`, the folders not added before; details, the
    queue's (`queued`, `already_queued`, `invalid`, `pending`) and `added`, the same count;
    refused as the queue refuses."""
    result = Result(attempted=len(folders))
    # Before the queue: its worker may start at once, and a Suggest asked for as soon as
    # this returns must find the folder the library's.
    made = record_added(library, folders)
    outcome = queue(folders)
    result.details.update(outcome.details)
    result.details.update(added=made)
    result.changed = made
    for what, why in outcome.skipped:
        result.skip(what, why)
    for what, why in outcome.errors:
        result.fail(what, why)
    if outcome.refused:
        result.refuse(outcome.refused)
    return result
