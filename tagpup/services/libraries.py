"""Making a library, and bringing one up to date: what the picker's Create does, and
what opening a library by its URL does first. And whether a library holds a folder: the
one answer every entry point asks before work that would make rows for its photos.

The old server made a library by opening it through PhotoIndex and then seeding its
tag tree, two old modules the web layer may not import (tests/test_layers.py). A new
library's tables come from tagpup.store.schema, its first nodes -- one face root,
People -- from tagpup.store.taxonomy.seed, and its settings, the defaults, from
tagpup.services.settings.
"""
import os

from tagpup.core import paths, validation
from tagpup.core.library import Library, picker_name
from tagpup.core.result import NOT_IN_LIBRARY, Result
from tagpup.files import images
from tagpup.services import settings
from tagpup.store import db, photos, schema, taxonomy

#: Photos made rows for in one write when a folder is added.
ADMIT_BATCH = 2000


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
# (2026-09-28). A folder is a library's when the library holds a photo directly in it
# (tagpup.store.photos.holds_folder); these say which folders are, for a page to ask
# before anything is done in one.

def _read(db_path, read, default):
    """`read(conn)` on a read-only connection to the library at `db_path`, or `default`
    for one that is not there or cannot be read just now."""
    if not os.path.exists(db_path):
        return default
    try:
        conn = db.connect(db.readonly_uri(db_path), uri=True)
    except Exception:
        return default
    try:
        return read(conn)
    except Exception:
        return default
    finally:
        conn.close()


def not_held(library, photo_paths, ignored=()):
    """The folders of `photo_paths` the library holds no photo directly in, each once, as
    stored, in path order: those it must be asked to add before any row is made for their
    photos (tagpup.store.photos.ensure_row). One query a folder, not a photo.

    A folder that is one of `ignored` or under one -- the library's ignored folders -- is
    left out: neither held nor to be offered, as sync passes it over
    (tagpup.services.sync). Writes pass none: a photo there is not the library's to
    write (refuse_writes)."""
    folders = {}
    for photo_path in photo_paths:
        folder = os.path.dirname(paths.stored(photo_path))
        folders.setdefault(paths.key(folder), folder)
    if ignored:
        folders = {key: folder for key, folder in folders.items() if not _under_any(folder, ignored)}
    if not folders:
        return []
    ordered = [folders[key] for key in sorted(folders)]
    return _read(library.path, lambda conn: [f for f in ordered if not photos.holds_folder(conn, f)], ordered)


def held_under(library, folder):
    """How many photos under `folder`, at any depth, the library holds; 0 for one not there."""
    return _read(library.path, lambda conn: photos.count_under(conn, folder), 0)


def _under_any(folder, folders):
    return any(paths.same(folder, other) or paths.is_under(folder, other) for other in folders)


def is_ignored(photo_path, ignored):
    """Is the photo in one of the library's `ignored` folders, or under one?"""
    return bool(ignored) and _under_any(os.path.dirname(paths.stored(photo_path)), ignored)


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
        count = held_under(other, folder)
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


def not_in(library, folder, ignored=()):
    """Why nothing that makes rows -- Suggest, whose faces, vectors and suggestions each
    need their photo's row -- may be done in `folder` for `library`, or None when the
    library holds every folder of photos under it but its `ignored` folders, which the
    work leaves out. "<folder> is not in <library>", for a person to read. One walk of
    the folder, no file read."""
    unheld = not_held(library, images.photos_under(folder), ignored)
    if not unheld:
        return None
    folder = paths.stored(folder)
    name = picker_name(os.path.basename(library.path))
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
    unheld = not_held(library, photo_paths)
    if not unheld:
        return False
    name = picker_name(os.path.basename(library.path))
    more = "" if len(unheld) == 1 else " (and %d more folder(s))" % (len(unheld) - 1)
    result.refuse("%s is not in %s%s. Add it to %s first." % (unheld[0], name, more, name))
    result.details[NOT_IN_LIBRARY] = unheld
    return True


def admit(library, photo_paths):
    """Make a row for each photo of `photo_paths` that has none, the path and nothing read
    (tagpup.store.photos.admit): their folders become the library's. Returns the rows made."""
    photo_paths = list(photo_paths)
    made = 0
    for start in range(0, len(photo_paths), ADMIT_BATCH):
        batch = photo_paths[start:start + ADMIT_BATCH]
        made += db.write_with_connection(library.path, lambda conn, batch=batch: photos.admit(conn, batch),
                                         label="add %d photo(s)" % len(batch))
    return made


def add(library, folders, queue):
    """Add `folders` to the library, as asked: "Add to <library>" in TagPup, TagTuner's Add
    Folder. Each folder's photos, at any depth, get a row at once -- the path and nothing
    read -- so the folder is the library's from then on, and Suggest may work in it before
    the index reaches it; then `queue(folders)` (the index queue's start) indexes them
    with their subfolders, reading each row from its file. The only way, beside the
    indexer itself, that a row is made in a folder the library did not hold.

    A folder that is no full path or not on disk gets no rows, and is handed on for the
    queue to report. A Result: `changed`, the rows made; details, the queue's (`queued`,
    `already_queued`, `invalid`, `pending`), `admitted`, the rows made, and `photos`, the
    photos found; refused as the queue refuses, and then nothing is made."""
    result = Result(attempted=len(folders))
    found = []
    for folder in folders:
        if isinstance(folder, str) and not validation.problem("folder", folder) and os.path.isdir(folder):
            found += images.photos_under(folder)
    # Before the queue: its worker may start at once, and a Suggest asked for as soon as
    # this returns must find the folder the library's. The queue refuses only when no
    # folder is on disk, when none was walked and nothing is made.
    made = admit(library, found)
    outcome = queue(folders)
    result.details.update(outcome.details)
    result.details.update(admitted=made, photos=len(found))
    result.changed = made
    for what, why in outcome.skipped:
        result.skip(what, why)
    for what, why in outcome.errors:
        result.fail(what, why)
    if outcome.refused:
        result.refuse(outcome.refused)
    return result
