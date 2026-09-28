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

from tagpup.core import paths, validation
from tagpup.core.library import Library, picker_name
from tagpup.core.result import NOT_IN_LIBRARY, Result
from tagpup.files import images
from tagpup.services import settings
from tagpup.store import added_folders, db, photos, schema, taxonomy
from tagpup.store import folders as store_folders

logger = logging.getLogger(__name__)



def bring_up_to_date(db_path):
    """The library's tables, made or migrated (tagpup.store.schema.ensure). Returns the
    names of the migrations applied: none, usually."""
    return schema.ensure(db_path)


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
    bring_up_to_date(db_path)
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
        return [folder for folder in ordered
                if not (leave_out_ignored and store_folders.is_ignored(folder, found))
                and not store_folders.holds(conn, folder, found)]

    there = [f for f in ordered if not (leave_out_ignored and ignored and store_folders.is_ignored(f, ignored))]
    return _read(library.path, read, there)


def held_under(library, folder):
    """How many photos under `folder`, at any depth, the library holds; 0 for one not there."""
    return _read(library.path, lambda conn: photos.count_under(conn, folder), 0)


def _under_any(folder, folders):
    return any(paths.same(folder, other) or paths.is_under(folder, other) for other in folders)


def is_ignored(photo_path, ignored):
    """Is the photo in one of the library's `ignored` folders, or under one?"""
    return store_folders.is_ignored(os.path.dirname(paths.stored(photo_path)), ignored)


def membership(library, folder, roots=(), ignored=(), others=()):
    """What `library` holds of `folder`, for the page to say before anything is done in it:
    {"library" (its name, as the address bar has it), "folder" (as stored), "photos" (on
    disk under it, at any depth), "photos_held" (the library's rows under it),
    "photos_ignored" (photos in its ignored folders, never offered),
    "photos_not_held" (photos in folders under it the library holds none directly in,
    ignored folders left out),
    "folders_not_held", "first_not_held" (the first such folder, or None), "has_roots",
    "under_roots" (the folder is one of `roots` or under one), "ignored" (likewise, of
    `ignored`), "others": [{"library", "photos"}] -- each library of `others` holding
    photos under it, and how many}. One walk of the folder, no file read; the other
    libraries are only read."""
    folder = paths.stored(folder)
    on_disk = images.photos_under(folder)
    unheld = not_held(library, on_disk, ignored)
    unheld_keys = {paths.key(each) for each in unheld}
    elsewhere = []
    for other in others:
        if other == library:
            continue
        try:
            count = held_under(other, folder)
        except Exception as e:
            # Another library's count is for the dialog's information only.
            logger.warning("Could not read %s to count its photos under a folder: %s", other.name, e)
            continue
        if count:
            elsewhere.append({"library": picker_name(os.path.basename(other.path)), "photos": count})
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
        "others": elsewhere,
    }


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
        return ("%s is ignored in %s (its Library settings): it is not %s's."
                % (paths.stored(folder), name, name))
    unheld = not_held(library, images.photos_under(folder), ignored)
    if not unheld:
        return None
    folder = paths.stored(folder)
    if len(unheld) == 1 and paths.same(unheld[0], folder):
        return "%s is not in %s. Add it to %s first." % (folder, name, name)
    return ("%d folder(s) under %s are not in %s, %s first. Add the folder to %s first."
            % (len(unheld), folder, name, unheld[0], name))


def refuse_writes(result, library, photo_paths):
    """Refuse `result` -- a write of photo files, nothing written yet -- when the library
    does not hold the folder of any photo of `photo_paths`: "<folder> is not in
    <library>. Add it to <library> first." Returns True when refused. The one check every
    write of a photo makes (tagging, rotating, renaming, a time shift, a delete): writing
    kr-track's tags into the photos of a folder photo_index holds, through kr-track, was
    the same mistake as Suggest making rows there. `details[NOT_IN_LIBRARY]` holds the
    folders, which a web route answers with 409."""
    unheld = not_held(library, photo_paths, leave_out_ignored=False)
    if not unheld:
        return False
    name = picker_name(os.path.basename(library.path))
    more = "" if len(unheld) == 1 else " (and %d more folder(s))" % (len(unheld) - 1)
    result.refuse("%s is not in %s%s. Add it to %s first." % (unheld[0], name, more, name))
    result.details[NOT_IN_LIBRARY] = unheld
    return True


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
